"""Telecom network-incident adapter.

The domain seam. The generic foundation (lib/) calls these functions to build
context tiers 1/2/4 and to seed the substrate. Everything telecom-specific
lives here; lib/ stays vertical-agnostic. To stand up a different one of the
50 agents, write a new adapter — the substrate and memory lifecycle are reused.
"""

# ---- Anomaly scoring at ingestion (← EV charger anomaly_breakdown) ----------
# Each feature returns a 0..1 sub-score; the incident anomaly_score is their
# weighted max. Stored as anomaly_breakdown JSON for explainability.
ANOMALY_FEATURES = {
    "prb_overload":   {"metric": "prb_util",      "threshold": 0.85, "weight": 1.0},
    "latency_spike":  {"metric": "latency_ms",    "threshold": 100,  "weight": 0.9, "scale": 300},
    "packet_loss":    {"metric": "packet_loss",   "threshold": 0.01, "weight": 0.9, "scale": 0.1},
    "sctp_flap":      {"metric": "sctp_resets",   "threshold": 3,    "weight": 0.8, "scale": 20},
    "rrc_failrate":   {"metric": "rrc_fail_rate", "threshold": 0.05, "weight": 0.7, "scale": 0.3},
    "temp_high":      {"metric": "temp_c",        "threshold": 55,   "weight": 0.6, "scale": 30},
    "vswr_high":      {"metric": "vswr",          "threshold": 1.5,  "weight": 0.7, "scale": 2.0},
}


def score_anomaly(metrics: dict) -> tuple:
    """Return (anomaly_score, breakdown dict) for a metrics snapshot.

    breakdown maps feature -> sub-score for any feature over threshold; the
    incident score is the max sub-score (worst single signal dominates)."""
    breakdown = {}
    for name, cfg in ANOMALY_FEATURES.items():
        val = metrics.get(cfg["metric"])
        if val is None:
            continue
        val = float(val)
        if val < cfg["threshold"]:
            continue
        scale = cfg.get("scale", 1.0)
        over = (val - cfg["threshold"]) / scale
        sub = min(1.0, max(0.0, over)) * cfg["weight"]
        if sub > 0:
            breakdown[name] = round(sub, 3)
    score = round(max(breakdown.values()), 3) if breakdown else 0.0
    return score, breakdown


# ---- Subcategory taxonomy (classification target) ---------------------------
SUBCATEGORIES = [
    "RAN-PRB-Congestion", "RAN-RRC-Failure", "RAN-Sleeping-Cell",
    "Transport-Fiber-Cut", "Transport-Microwave-Fade", "Transport-SCTP-Flap",
    "Core-AMF-Overload", "Core-UPF-Packet-Loss", "Core-DNS-Failure",
    "Power-Mains-Fail", "Power-Battery-Depletion", "Environmental-HighTemp",
    "Config-Drift", "Security-DDoS", "Hardware-Card-Fault",
]


# ============================================================================
# CONTEXT TIERS — consumed by lib.memory.assemble_context
# ============================================================================
def tier_1_entity(cursor, entity_ref):
    """T1 — profile + criticality of the focal network element."""
    if not entity_ref:
        return None, "no_entity"
    cursor.execute(
        """SELECT element_id, site_id, element_type, vendor, model, sw_version,
                  region, criticality FROM network_elements WHERE element_id = %s""",
        (entity_ref,))
    row = cursor.fetchone()
    if not row:
        return None, "not_found"
    return (f"[T1 element] {row['element_id']} type={row['element_type']} "
            f"vendor={row['vendor']}/{row['model']} sw={row['sw_version']} "
            f"site={row['site_id']} region={row['region']} crit={row['criticality']}"), "ok"


def tier_2_recent(cursor, entity_ref):
    """T2 — recent open incidents + recent critical/major alarms for the element."""
    if not entity_ref:
        return None, "no_entity"
    lines = []
    cursor.execute(
        """SELECT incident_ref, severity, subcategory, anomaly_score, status, window_start
           FROM incidents WHERE element_id = %s ORDER BY window_start DESC LIMIT 5""",
        (entity_ref,))
    for r in cursor.fetchall():
        lines.append(f"inc {r['incident_ref']} {r['severity']}/{r['subcategory']} "
                     f"anom={r['anomaly_score']} {r['status']}")
    cursor.execute(
        """SELECT severity, alarm_type, COUNT(*) c FROM network_alarms
           WHERE element_id = %s AND ts >= NOW() - INTERVAL 1 DAY
             AND severity IN ('critical','major')
           GROUP BY severity, alarm_type ORDER BY c DESC LIMIT 5""",
        (entity_ref,))
    for r in cursor.fetchall():
        lines.append(f"alarm {r['severity']} {r['alarm_type']} x{r['c']} (24h)")
    return (lines or None), ("ok" if lines else "empty")


def tier_4_prior(cursor, entity_ref):
    """T4 — prior resolved investigations for this element (episodic recall)."""
    if not entity_ref:
        return None, "no_entity"
    cursor.execute(
        """SELECT hypothesis, resolution, confidence, created_at FROM agent_reasoning
           WHERE element_id = %s AND resolution IN ('confirmed','promoted','escalated')
           ORDER BY created_at DESC LIMIT 4""",
        (entity_ref,))
    lines = [f"{r['resolution']} (conf={r['confidence']}): {(r['hypothesis'] or '')[:80]}"
             for r in cursor.fetchall()]
    return (lines or None), ("ok" if lines else "empty")


# ============================================================================
# SEED CATALOG — high-confidence canonical patterns for day-one shortcuts
# Loaded into incident_memory (idempotent by cosine distance).
# ============================================================================
SEED_CATALOG = [
    {"category": "pattern", "confidence": 0.92, "scope": "global",
     "content": "PRB utilisation sustained above 0.9 with rising RRC connection "
                "establishment failures indicates RAN congestion / overload; mitigate "
                "by enabling load balancing to neighbour cells or adding carriers."},
    {"category": "pattern", "confidence": 0.90, "scope": "global",
     "content": "Repeated SCTP association resets between eNodeB/gNodeB and the core "
                "with no RF degradation point to a transport/IP layer fault, not radio."},
    {"category": "pattern", "confidence": 0.90, "scope": "global",
     "content": "A cell reporting normal alarms but zero traffic and zero RRC attempts "
                "is a sleeping cell; a remote reset usually restores service."},
    {"category": "vendor_bug", "confidence": 0.88, "scope": "vendor:Ericsson",
     "content": "Ericsson baseband units on certain SW builds raise spurious VSWR major "
                "alarms after temperature swings; correlate with temp_c before truck roll."},
    {"category": "pattern", "confidence": 0.89, "scope": "global",
     "content": "Simultaneous critical alarms across all elements at one site, preceded "
                "by a mains-fail alarm, indicate site power loss; battery depletion follows."},
    {"category": "pattern", "confidence": 0.87, "scope": "global",
     "content": "UPF packet loss climbing with normal CPU suggests an upstream transport "
                "fibre degradation; check optical levels on the backhaul path."},
    {"category": "operational_rule", "confidence": 0.85, "scope": "global",
     "content": "Gold-criticality elements with critical incidents must be escalated to "
                "the on-call NOC engineer within 15 minutes regardless of auto-remediation."},
]
