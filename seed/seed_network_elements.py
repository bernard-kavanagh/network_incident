"""Seed network_elements — the inventory (← seed_charger_registry.py).

    python seed/seed_network_elements.py --count 200
"""
import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.tidb import get_db  # noqa: E402

REGIONS = ["EMEA-West", "EMEA-North", "EMEA-South", "APAC-East", "NA-East"]
VENDORS = {"Ericsson": ["RBS6601", "Baseband6630", "Radio4449"],
           "Nokia": ["AirScale", "Flexi", "AEUB"],
           "Huawei": ["BBU5900", "RRU5502", "DBS3900"],
           "Cisco": ["ASR9000", "NCS540", "Catalyst9300"]}
TYPES = ["cell_site", "enodeb", "gnodeb", "bts", "router", "switch", "transport", "core"]
CRIT = ["gold", "silver", "silver", "bronze"]


def main(count: int):
    random.seed(42)
    db = get_db()
    rows = []
    for i in range(count):
        vendor = random.choice(list(VENDORS))
        model = random.choice(VENDORS[vendor])
        region = random.choice(REGIONS)
        site = f"{region[:3].upper()}-SITE-{i // 4:04d}"
        rows.append((
            f"NE-{i:05d}", site, random.choice(TYPES), vendor, model,
            f"{random.randint(18, 24)}.{random.randint(0, 9)}.{random.randint(0, 99)}",
            region,
            round(random.uniform(35, 60), 6), round(random.uniform(-10, 30), 6),
            f"20{random.randint(18, 23)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}",
            f"2025-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}",
            random.choice(CRIT),
        ))
    try:
        with db.cursor() as cur:
            cur.executemany(
                """INSERT INTO network_elements
                     (element_id, site_id, element_type, vendor, model, sw_version,
                      region, lat, lon, install_date, last_maintenance, criticality)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE site_id=VALUES(site_id)""", rows)
            db.commit()
        print(f"✅ Seeded {len(rows)} network elements.")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=200)
    main(ap.parse_args().count)
