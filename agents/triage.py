"""Triage & Correlation Agent — Agent 1.

Substrate-driven (no LLM call needed — anomaly scoring is pure SQL + Python, the
thesis that "the platform does the work before the model is invoked"). Scans
recent uncorrelated alarms, groups them by element into time windows, scores the
anomaly AT INGESTION via the adapter, writes an `incidents` row (with
anomaly_breakdown for explainability), marks the alarms correlated, and records
an episodic checkpoint in agent_reasoning.

    python agents/triage.py --window-min 15 --min-alarms 5
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.tidb import get_db                          # noqa: E402
from lib.memory import write_reasoning_checkpoint    # noqa: E402
from adapters.network_incident import score_anomaly  # noqa: E402

SEV_RANK = {"critical": 4, "major": 3, "minor": 2, "warning": 1, "info": 0, "clear": 0}
SEV_NAME = {4: "critical", 3: "major", 2: "minor", 1: "warning", 0: "warning"}
TYPE_TO_SUBCAT = {
    "prbUtilHigh": "RAN-PRB-Congestion", "rrcSetupFail": "RAN-RRC-Failure",
    "sctpReset": "Transport-SCTP-Flap", "packetLoss": "Core-UPF-Packet-Loss",
    "tempHigh": "Environmental-HighTemp", "vswrAlarm": "Hardware-Card-Fault",
    "latencyHigh": "Transport-Microwave-Fade",
}


def correlate(window_min: int, min_alarms: int, lookback_hours: int) -> list:
    db = get_db()
    created = []
    try:
        cur = db.cursor()
        # Group uncorrelated alarms by element within the lookback.
        cur.execute(
            """SELECT element_id, site_id,
                      COUNT(*) AS alarm_count,
                      MIN(ts) AS win_start, MAX(ts) AS win_end,
                      MAX(severity) AS dummy
               FROM network_alarms
               WHERE correlated_incident IS NULL
                 AND ts >= NOW() - INTERVAL %s HOUR
               GROUP BY element_id, site_id
               HAVING COUNT(*) >= %s
               ORDER BY alarm_count DESC""",
            (lookback_hours, min_alarms))
        groups = cur.fetchall()

        for g in groups:
            elem = g["element_id"]
            cur.execute(
                """SELECT id, severity, alarm_type, metrics FROM network_alarms
                   WHERE element_id = %s AND correlated_incident IS NULL
                     AND ts >= NOW() - INTERVAL %s HOUR
                   ORDER BY ts ASC""", (elem, lookback_hours))
            alarms = cur.fetchall()
            if len(alarms) < min_alarms:
                continue

            # Merge KPI snapshots; worst value per metric drives anomaly scoring.
            merged = {}
            sev_max = 0
            type_counts = {}
            for a in alarms:
                sev_max = max(sev_max, SEV_RANK.get(a["severity"], 0))
                type_counts[a["alarm_type"]] = type_counts.get(a["alarm_type"], 0) + 1
                m = a["metrics"]
                if isinstance(m, str):
                    m = json.loads(m) if m else {}
                for k, v in (m or {}).items():
                    merged[k] = max(merged.get(k, 0), float(v))

            score, breakdown = score_anomaly(merged)
            dominant_type = max(type_counts, key=type_counts.get)
            subcat = TYPE_TO_SUBCAT.get(dominant_type, "RAN-PRB-Congestion")
            severity = SEV_NAME[sev_max]
            ref = f"INC-{elem}-{g['win_start']:%Y%m%d%H%M}"
            title = f"{subcat} on {elem} ({len(alarms)} correlated alarms)"
            desc = (f"Correlated {len(alarms)} alarms on {elem} "
                    f"(types: {type_counts}). Worst KPIs: {merged}. "
                    f"Anomaly {score} breakdown {breakdown}.")

            cur.execute(
                """INSERT INTO incidents
                     (incident_ref, element_id, site_id, window_start, window_end,
                      alarm_count, severity, category, subcategory, title, description,
                      anomaly_score, anomaly_breakdown, status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open')
                   ON DUPLICATE KEY UPDATE alarm_count=VALUES(alarm_count),
                       anomaly_score=VALUES(anomaly_score),
                       anomaly_breakdown=VALUES(anomaly_breakdown)""",
                (ref, elem, g["site_id"], g["win_start"], g["win_end"], len(alarms),
                 severity, subcat.split("-")[0].lower(), subcat, title, desc,
                 score, json.dumps(breakdown)))
            cur.execute(
                """UPDATE network_alarms SET correlated_incident = %s
                   WHERE element_id = %s AND correlated_incident IS NULL
                     AND ts >= NOW() - INTERVAL %s HOUR""",
                (ref, elem, lookback_hours))
            db.commit()

            write_reasoning_checkpoint(
                session_id=f"triage-{ref}",
                observation=f"{len(alarms)} alarms correlated on {elem}; anomaly={score} {breakdown}",
                hypothesis=f"Likely {subcat} (dominant alarm: {dominant_type})",
                resolution="confirmed" if score >= 0.5 else "dismissed",
                confidence=min(0.95, 0.5 + score / 2),
                incident_ref=ref, element_id=elem,
                evidence_refs=[a["id"] for a in alarms])
            created.append({"incident_ref": ref, "element": elem, "subcategory": subcat,
                            "severity": severity, "anomaly_score": score,
                            "alarms": len(alarms)})
    finally:
        db.close()
    return created


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-min", type=int, default=15)
    ap.add_argument("--min-alarms", type=int, default=5)
    ap.add_argument("--lookback-hours", type=int, default=6)
    a = ap.parse_args()
    created = correlate(a.window_min, a.min_alarms, a.lookback_hours)
    if not created:
        print("No correlatable alarm groups found.")
    for c in created:
        print(f"  🚨 {c['incident_ref']}  {c['severity']:8} {c['subcategory']:24} "
              f"anom={c['anomaly_score']:.3f}  ({c['alarms']} alarms)")
    print(f"\n✅ Triage created/updated {len(created)} incidents.")


if __name__ == "__main__":
    main()
