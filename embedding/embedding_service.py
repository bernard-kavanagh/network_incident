"""Embedding backfill service — fills NULL vector columns across the telecom
tables using the local all-MiniLM-L6-v2 model.

Ported from ev_charger_anomaly_detection/embedding/embedding_service.py (poll
fallback path). The Kafka/TiCDC path is intentionally omitted for the POC — the
fraud repo's lesson is that direct writes + polling backfill are sufficient
without Flink/Kafka. Same text-banding single-source-of-truth via lib.text_bander.

    python embedding/embedding_service.py --once          # one-shot backfill
    python embedding/embedding_service.py --poll           # continuous
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.tidb import get_db                       # noqa: E402
from lib.embeddings import embed_batch            # noqa: E402
from lib.text_bander import TEXT_BUILDERS, VECTOR_COLUMNS  # noqa: E402

# tables that carry vectors, with their id + vector columns
POLL_TABLES = [
    ("incident_catalog", "id"),
    ("tickets",          "ticket_id"),
    ("incidents",        "id"),
    ("incident_memory",  "id"),
    ("runbook_memory",   "id"),
    ("agent_reasoning",  "id"),
]


def embed_table_batch(table: str, id_col: str, batch_size: int) -> int:
    vec_col = VECTOR_COLUMNS[table]
    builder = TEXT_BUILDERS[table]
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute(
                f"SELECT * FROM {table} WHERE {vec_col} IS NULL "
                f"ORDER BY {id_col} ASC LIMIT {batch_size}")
            rows = cur.fetchall()
            if not rows:
                return 0
            texts = [builder(r) for r in rows]
            vecs = embed_batch(texts)
            for r, v in zip(rows, vecs):
                cur.execute(f"UPDATE {table} SET {vec_col} = %s WHERE {id_col} = %s",
                            (str(v), r[id_col]))
            db.commit()
            return len(rows)
    finally:
        db.close()


def run_once(batch_size: int):
    totals = {t: 0 for t, _ in POLL_TABLES}
    while True:
        pass_total = 0
        for table, id_col in POLL_TABLES:
            n = embed_table_batch(table, id_col, batch_size)
            totals[table] += n
            pass_total += n
            if n:
                print(f"  {table}: +{n} (total {totals[table]})")
        if pass_total == 0:
            break
    print("Backfill complete:", {k: v for k, v in totals.items() if v})


def run_poll(batch_size: int, interval: int):
    print(f"Polling every {interval}s for NULL-vector rows…")
    while True:
        for table, id_col in POLL_TABLES:
            try:
                n = embed_table_batch(table, id_col, batch_size)
                if n:
                    print(f"  {table}: +{n}")
            except Exception as e:
                print(f"  ⚠️  {table}: {e}", file=sys.stderr)
        time.sleep(interval)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Embedding backfill for telecom tables")
    p.add_argument("--once", action="store_true", help="single-pass backfill then exit")
    p.add_argument("--poll", action="store_true", help="poll continuously")
    p.add_argument("--batch-size", type=int, default=64)
    p.add_argument("--interval", type=int, default=30)
    args = p.parse_args()
    if args.poll:
        run_poll(args.batch_size, args.interval)
    else:
        run_once(args.batch_size)
