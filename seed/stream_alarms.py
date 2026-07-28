"""Stream network alarms into TiDB (← stream_telemetry.py, --format direct).

Writes raw alarm events with a KPI metrics snapshot directly to network_alarms
(no Kafka/Flink — the fraud repo's lesson). Can inject a correlated burst on one
element so the Triage agent has a real incident to correlate.

    python seed/stream_alarms.py --count 400 --burst-element NE-00007
"""
import argparse
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.tidb import get_db  # noqa: E402

ALARM_TYPES = [
    ("prbUtilHigh", "congestion", {"prb_util": (0.9, 0.99)}),
    ("rrcSetupFail", "radio", {"rrc_fail_rate": (0.06, 0.2)}),
    ("sctpReset", "transport", {"sctp_resets": (4, 25)}),
    ("packetLoss", "transport", {"packet_loss": (0.02, 0.12)}),
    ("tempHigh", "environmental", {"temp_c": (56, 75)}),
    ("vswrAlarm", "hardware", {"vswr": (1.6, 2.4)}),
    ("latencyHigh", "transport", {"latency_ms": (120, 400)}),
]
SEV_BY_TYPE = {"prbUtilHigh": "major", "rrcSetupFail": "major", "sctpReset": "major",
               "packetLoss": "major", "tempHigh": "minor", "vswrAlarm": "minor",
               "latencyHigh": "major"}


def _metrics(spec):
    return {k: round(random.uniform(*rng), 3) for k, rng in spec.items()}


def main(count, burst_element):
    random.seed()
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("SELECT element_id, site_id FROM network_elements LIMIT 500")
            elems = cur.fetchall()
    finally:
        db.close()
    if not elems:
        print("⚠️  No network_elements found — run seed_network_elements.py first.")
        return
    by_id = {e["element_id"]: e["site_id"] for e in elems}
    now = datetime.utcnow()
    rows = []

    for _ in range(count):
        e = random.choice(elems)
        atype, _cause, spec = random.choice(ALARM_TYPES)
        ts = now - timedelta(seconds=random.randint(0, 3600))
        rows.append((e["element_id"], e["site_id"], ts.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                     SEV_BY_TYPE[atype], atype, _cause, f"{atype} on {e['element_id']}",
                     "RF" if _cause == "radio" else _cause, None, json.dumps(_metrics(spec))))

    # Inject a correlated burst: many high-PRB alarms on one element in 10 min.
    if burst_element and burst_element in by_id:
        for i in range(18):
            ts = now - timedelta(minutes=random.randint(0, 10))
            m = _metrics({"prb_util": (0.95, 0.99), "rrc_fail_rate": (0.08, 0.18),
                          "latency_ms": (150, 320)})
            rows.append((burst_element, by_id[burst_element],
                         ts.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], "critical",
                         "prbUtilHigh", "congestion",
                         f"PRB saturation burst on {burst_element}", "RF", None, json.dumps(m)))

    db = get_db()
    try:
        with db.cursor() as cur:
            cur.executemany(
                """INSERT INTO network_alarms
                     (element_id, site_id, ts, severity, alarm_type, probable_cause,
                      specific_problem, managed_object, correlated_incident, metrics)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
            db.commit()
    finally:
        db.close()
    print(f"✅ Streamed {len(rows)} alarms"
          + (f" (incl. burst on {burst_element})" if burst_element else "") + ".")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=400)
    ap.add_argument("--burst-element", default="NE-00007")
    a = ap.parse_args()
    main(a.count, a.burst_element)
