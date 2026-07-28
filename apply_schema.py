"""Apply schema.sql to the configured TiDB database.

Splits on semicolons and executes statement-by-statement (TiDB executes one
DDL per call). Idempotent — schema.sql uses IF NOT EXISTS; re-running ALTER ...
SET TIFLASH REPLICA is a no-op once set.

    python apply_schema.py
"""
import re
import sys
from pathlib import Path

from lib.tidb import get_db


def split_statements(sql: str):
    # Strip line comments, then split on ';' at statement boundaries.
    no_comments = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in re.split(r";\s*\n", no_comments) if s.strip()]


def main():
    schema = Path(__file__).parent / "schema.sql"
    statements = split_statements(schema.read_text())
    db = get_db()
    ok = err = 0
    try:
        with db.cursor() as cur:
            for stmt in statements:
                try:
                    cur.execute(stmt)
                    ok += 1
                    label = re.sub(r"\s+", " ", stmt[:70])
                    print(f"  ✅ {label}…")
                except Exception as e:
                    err += 1
                    print(f"  ⚠️  {e}\n     in: {stmt[:80]}…", file=sys.stderr)
    finally:
        db.close()
    print(f"\nSchema applied: {ok} statements ok, {err} warnings/errors.")


if __name__ == "__main__":
    main()
