"""Seed the curated incident_catalog (← seed_outage_catalog.py), plus the
semantic memory (incident_memory from the adapter SEED_CATALOG) and a few
procedural runbooks. Vectors are left NULL — the embedding service fills them.

    python seed/seed_incident_catalog.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.tidb import get_db                              # noqa: E402
from lib.memory import remember_pattern                 # noqa: E402
from adapters import network_incident as adapter        # noqa: E402

CATALOG = [
    ("PAT-RAN-001", "RAN PRB Congestion", "congestion", "major",
     "Sustained PRB utilisation > 90% with rising RRC setup failures; cell cannot admit new bearers.",
     ["prb_util>0.9", "rrc_fail_rate up", "throughput drop"],
     "Enable load balancing to neighbour cells; if persistent, add a carrier or schedule capacity upgrade."),
    ("PAT-RAN-002", "Sleeping Cell", "radio", "major",
     "Cell shows healthy alarms but zero RRC attempts and zero traffic for an extended period.",
     ["traffic=0", "rrc_attempts=0", "no fault alarm"],
     "Issue a remote cell reset (block/unblock). If unresolved, reset the baseband; dispatch if still down."),
    ("PAT-TRANS-001", "SCTP Association Flap", "transport", "major",
     "Repeated SCTP association resets between RAN node and core with no RF degradation.",
     ["sctp_resets>3", "no RF alarms", "intermittent S1/N2"],
     "Check transport/IP path and MTU; verify backhaul link errors; engage transport team — not radio."),
    ("PAT-TRANS-002", "Backhaul Fibre Degradation", "transport", "critical",
     "UPF/transport packet loss climbing with normal CPU; optical levels drifting on backhaul.",
     ["packet_loss up", "cpu normal", "optical Rx low"],
     "Inspect optical Rx/Tx levels on the backhaul path; clean/replace SFP; raise fibre ticket if Rx out of range."),
    ("PAT-PWR-001", "Site Power Loss", "power", "critical",
     "All elements at a site raise critical alarms after a mains-fail alarm; battery depletion imminent.",
     ["mains_fail", "site-wide critical", "battery discharge"],
     "Confirm grid status; dispatch genset/fuel; prioritise gold sites; expect outage when batteries deplete."),
    ("PAT-CORE-001", "AMF Overload", "core", "critical",
     "AMF CPU saturated; registration/attach failures spike across many cells simultaneously.",
     ["amf_cpu>0.9", "attach failures", "multi-cell impact"],
     "Scale AMF instances / shed load; check for signalling storm or misbehaving UE population."),
    ("PAT-ENV-001", "High Temperature", "environmental", "minor",
     "Cabinet temperature exceeds threshold; risk of thermal shutdown and spurious VSWR alarms.",
     ["temp_c>55", "cooling alarm", "possible vswr noise"],
     "Verify HVAC/fans; clear airflow obstructions; correlate any VSWR alarms with temperature before truck roll."),
    ("PAT-SEC-001", "Signalling DDoS", "security", "critical",
     "Abnormal surge of signalling/attach attempts from a narrow source range; control plane saturating.",
     ["attach surge", "narrow source range", "control-plane cpu up"],
     "Apply rate limiting / source filtering at the edge; engage security ops; preserve logs for forensics."),
]

RUNBOOKS = [
    ("RAN congestion mitigation for PRB overload",
     "PRB utilisation sustained above 0.9 with rising RRC setup failures (RAN congestion).",
     ["Confirm PRB utilisation >0.9 and rising RRC setup failure rate",
      "Enable/verify load balancing to neighbour cells",
      "Check for a single dominant heavy-traffic sector or event",
      "If sustained, activate an additional carrier or schedule capacity upgrade",
      "Monitor throughput and RRC success after mitigation"]),
    ("Remote cell reset for sleeping cell",
     "Cell healthy but zero traffic and zero RRC attempts (sleeping cell).",
     ["Verify zero traffic and zero RRC attempts over last 30 min",
      "Issue remote block then unblock on the cell",
      "Wait 5 min and confirm RRC attempts resume",
      "If still zero, reset the baseband unit",
      "If still down after reset, dispatch field engineer"]),
    ("Transport path check for SCTP flap",
     "Repeated SCTP resets with no RF degradation.",
     ["Confirm no RF alarms on the affected node",
      "Check backhaul link error counters and MTU settings",
      "Run continuity/latency test on the IP path to the core",
      "Engage transport team if link errors present",
      "Re-establish SCTP association and monitor for 15 min"]),
    ("Site power-loss response",
     "Mains-fail alarm followed by site-wide critical alarms.",
     ["Confirm grid/mains status with facilities",
      "Check battery charge level and estimated runtime",
      "Dispatch genset/fuel, prioritising gold-criticality sites",
      "Notify NOC and open a P1 incident",
      "Monitor battery; expect outage on depletion"]),
]


def seed_catalog(cur):
    rows = [(pid, name, cat, sev, rc, json.dumps(sym), res, None, None)
            for (pid, name, cat, sev, rc, sym, res) in
            [(c[0], c[1], c[2], c[3], c[4], c[5], c[6]) for c in CATALOG]]
    cur.executemany(
        """INSERT INTO incident_catalog
             (pattern_id, pattern_name, category, severity, root_cause, symptoms,
              resolution, affected_vendors, affected_models)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON DUPLICATE KEY UPDATE pattern_name=VALUES(pattern_name)""", rows)
    print(f"  ✅ incident_catalog: {len(rows)} patterns")


def seed_runbooks(cur):
    rows = [(title, "global", trigger, json.dumps(steps)) for title, trigger, steps in RUNBOOKS]
    cur.executemany(
        """INSERT INTO runbook_memory (title, scope, trigger_condition, steps)
           VALUES (%s,%s,%s,%s)""", rows)
    print(f"  ✅ runbook_memory: {len(rows)} runbooks")


def main():
    db = get_db()
    try:
        with db.cursor() as cur:
            seed_catalog(cur)
            seed_runbooks(cur)
            db.commit()
    finally:
        db.close()
    # Semantic memory via the write-control path (embeds immediately).
    print("  Seeding incident_memory from adapter SEED_CATALOG…")
    seeded = 0
    for entry in adapter.SEED_CATALOG:
        res = remember_pattern(entry["content"], category=entry["category"],
                               confidence=entry["confidence"], scope=entry["scope"])
        if res.startswith("✅"):
            seeded += 1
    print(f"  ✅ incident_memory: {seeded}/{len(adapter.SEED_CATALOG)} patterns")
    print("Done. Run the embedding service to fill catalog/runbook vectors.")


if __name__ == "__main__":
    main()
