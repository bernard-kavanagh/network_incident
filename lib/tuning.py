"""Runtime-tunable knobs for the cognitive foundation.

The whole point of the "cognitive foundation" is that engineers *observe
outcomes and adjust the gates/thresholds to control them*. For that to be
demonstrable, the knobs must be changeable at runtime and read at call-time —
not frozen as import-time constants. This module is the single source of truth:
`lib.memory`, `agents.remediation`, and `adapters.network_incident` all read
`TUNING.<field>` when they run, so `set_tuning(...)` changes take effect live
(that's what the eval sweep exploits to show engineer control).

Defaults come from env so deployments can still pin values in `.env`.
"""
import os
from dataclasses import dataclass, asdict, replace


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass
class Tuning:
    # routing gates (lib.memory.route_investigation) — shortcut vs explore
    routing_confidence_gate: float = _f("ROUTING_CONFIDENCE_GATE", 0.85)
    routing_similarity_gate: float = _f("ROUTING_SIMILARITY_GATE", 0.55)
    # semantic-memory custodial duties (lib.memory)
    dedup_distance_threshold: float = _f("DEDUP_DISTANCE_THRESHOLD", 0.15)
    write_control_min_confidence: float = _f("WRITE_CONTROL_MIN_CONFIDENCE", 0.80)
    context_budget_tokens: int = _i("CONTEXT_BUDGET_TOKENS", 3600)
    # remediation (agents.remediation) — apply runbook vs escalate
    runbook_relevance_floor: float = _f("RUNBOOK_RELEVANCE_FLOOR", 0.45)
    # anomaly scoring (adapters.network_incident.score_anomaly) — global multiplier
    # on per-feature thresholds; >1 makes triage stricter (fewer/lower anomalies),
    # <1 makes it more sensitive. Keeps the sweep to one interpretable knob.
    anomaly_threshold_scale: float = _f("ANOMALY_THRESHOLD_SCALE", 1.0)


# The live singleton every read-site consults at call-time.
TUNING = Tuning()


def set_tuning(**overrides):
    """Mutate the live tuning in place (so existing references see the change).
    Unknown keys raise, to catch typos in sweeps. Returns the tuning."""
    for k, v in overrides.items():
        if not hasattr(TUNING, k):
            raise KeyError(f"unknown tuning knob: {k}")
        setattr(TUNING, k, v)
    return TUNING


def reset_tuning():
    """Restore all knobs to their env/defaults."""
    fresh = Tuning()
    for k, v in asdict(fresh).items():
        setattr(TUNING, k, v)
    return TUNING


def snapshot() -> dict:
    """Return the current tuning as a plain dict (for reports)."""
    return asdict(TUNING)
