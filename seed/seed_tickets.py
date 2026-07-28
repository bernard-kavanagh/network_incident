"""Seed historical tickets — replaces Devoteam's BigQuery TABLE_ID.

Generates tickets across the subcategory taxonomy over the past ~75 days with a
steady baseline, then injects a deliberate recent SPIKE in one subcategory so
the DeviationAgent detects a significant z-score (an emerging outage). Vectors
are left NULL for the embedding service to fill.

    python seed/seed_tickets.py --baseline-per-day 6 --spike-subcategory RAN-PRB-Congestion
"""
import argparse
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.tidb import get_db                          # noqa: E402
from adapters.network_incident import SUBCATEGORIES  # noqa: E402

REGIONS = ["EMEA-West", "EMEA-North", "EMEA-South", "APAC-East", "NA-East"]
CATEGORY_OF = {  # map subcategory prefix -> category
    "RAN": "radio", "Transport": "transport", "Core": "core",
    "Power": "power", "Environmental": "environmental",
    "Config": "config", "Security": "security", "Hardware": "hardware",
}
SUMMARIES = {
    "RAN-PRB-Congestion": ("PRB utilisation saturated on cell, throughput collapsed",
                           "Cell {ne} reporting PRB util >0.95 during busy hour; users dropped, RRC setup failures climbing."),
    "RAN-RRC-Failure": ("RRC connection setup failure rate elevated",
                        "Element {ne} RRC setup success below 90%; intermittent attach failures reported."),
    "RAN-Sleeping-Cell": ("Cell carrying zero traffic with no fault alarm",
                          "Element {ne} shows zero RRC attempts for hours; suspected sleeping cell."),
    "Transport-Fiber-Cut": ("Backhaul fibre cut suspected, site isolated",
                            "Loss of signal on backhaul to {ne}; optical Rx absent; site unreachable."),
    "Transport-Microwave-Fade": ("Microwave link fade during weather event",
                                 "Link to {ne} degrading with rain fade; errored seconds rising."),
    "Transport-SCTP-Flap": ("SCTP association flapping to core",
                            "Repeated SCTP resets between {ne} and core with no RF alarms."),
    "Core-AMF-Overload": ("AMF overload causing multi-cell attach failures",
                          "AMF serving {ne} region saturated; registration failures across many cells."),
    "Core-UPF-Packet-Loss": ("UPF packet loss elevated on user plane",
                             "Packet loss on UPF path for {ne}; CPU normal, suspect transport."),
    "Core-DNS-Failure": ("DNS resolution failures impacting service",
                         "DNS errors affecting {ne}; APN resolution failing intermittently."),
    "Power-Mains-Fail": ("Mains failure at site, on battery",
                         "Mains-fail alarm at site of {ne}; running on battery, runtime limited."),
    "Power-Battery-Depletion": ("Battery depleted following extended mains outage",
                                "Battery for {ne} site depleted; elements going offline."),
    "Environmental-HighTemp": ("Cabinet over-temperature alarm",
                               "Temperature at {ne} cabinet exceeding 55C; cooling suspected failing."),
    "Config-Drift": ("Configuration drift detected after change window",
                     "Parameters on {ne} diverge from golden config after maintenance."),
    "Security-DDoS": ("Signalling DDoS surge detected",
                      "Abnormal attach surge toward {ne} from narrow source range; control plane stressed."),
    "Hardware-Card-Fault": ("Line card hardware fault raised",
                            "Hardware fault on {ne}; card reporting errors, redundancy degraded."),
}


def _row(sub, when, idx, elements):
    cat = CATEGORY_OF.get(sub.split("-")[0], "unknown")
    summ, desc = SUMMARIES.get(sub, (sub, sub + " on {ne}"))
    ne = random.choice(elements) if elements else f"NE-{random.randint(0,199):05d}"
    region = random.choice(REGIONS)
    pri = random.choice(["P1", "P2", "P2", "P3", "P3", "P4"])
    resolved = when + timedelta(hours=random.randint(1, 48))
    return (
        f"TKT-{when:%Y%m%d}-{idx:06d}", when.strftime("%Y-%m-%d %H:%M:%S"),
        resolved.strftime("%Y-%m-%d %H:%M:%S"), ne, None, region, cat, sub, pri,
        "closed", summ, desc.format(ne=ne),
        "Resolved per standard procedure; root cause documented.",
    )


def main(baseline_per_day, spike_subcategory, spike_multiplier, recent_days, baseline_days):
    random.seed(7)
    db = get_db()
    elements = []
    try:
        with db.cursor() as cur:
            cur.execute("SELECT element_id FROM network_elements LIMIT 500")
            elements = [r["element_id"] for r in cur.fetchall()]
    finally:
        db.close()

    now = datetime.utcnow()
    rows = []
    idx = 0
    total_days = baseline_days + recent_days
    for day in range(total_days):
        when_day = now - timedelta(days=total_days - day)
        is_recent = day >= baseline_days
        for sub in SUBCATEGORIES:
            count = max(0, int(random.gauss(baseline_per_day, baseline_per_day * 0.3)))
            if is_recent and sub == spike_subcategory:
                count = int(count * spike_multiplier)
            for _ in range(count):
                idx += 1
                when = when_day + timedelta(seconds=random.randint(0, 86399))
                rows.append(_row(sub, when, idx, elements))

    db = get_db()
    try:
        with db.cursor() as cur:
            cur.executemany(
                """INSERT INTO tickets
                     (ticket_id, created_at, resolved_at, element_id, site_id, region,
                      category, subcategory, priority, status, summary, description, resolution)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE summary=VALUES(summary)""", rows)
            db.commit()
    finally:
        db.close()
    print(f"✅ Seeded {len(rows)} tickets over {total_days} days "
          f"(spike: {spike_subcategory} x{spike_multiplier} in last {recent_days}d).")
    print("Run the embedding service to fill ticket vectors.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline-per-day", type=int, default=6)
    ap.add_argument("--spike-subcategory", default="RAN-PRB-Congestion")
    ap.add_argument("--spike-multiplier", type=float, default=6.0)
    ap.add_argument("--recent-days", type=int, default=3)
    ap.add_argument("--baseline-days", type=int, default=72)
    a = ap.parse_args()
    main(a.baseline_per_day, a.spike_subcategory, a.spike_multiplier,
         a.recent_days, a.baseline_days)
