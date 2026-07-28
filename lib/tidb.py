"""TiDB connection + query primitives.

Single data-access layer for every agent and tool. Uses PyMySQL (as the EV
repo's embedding service does) with a DictCursor and TLS, so the exact same
code talks to TiDB Cloud now and to in-cluster TiDB (TiDB Operator) on GDC
bare metal later — only the .env connection values change.
"""
import json
import os
import pymysql
from dotenv import load_dotenv

load_dotenv()


def _config() -> dict:
    cfg = {
        "host": os.getenv("TIDB_HOST", "localhost"),
        "port": int(os.getenv("TIDB_PORT", "4000")),
        "user": os.getenv("TIDB_USER", "root"),
        "password": os.getenv("TIDB_PASSWORD", ""),
        "database": os.getenv("TIDB_DATABASE", "network_incident"),
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": True,
    }
    ssl_ca = os.getenv("TIDB_SSL_CA")
    if ssl_ca:
        cfg["ssl"] = {"ca": ssl_ca}
    return cfg


def get_db():
    """Return a fresh autocommit connection. Caller is responsible for close()."""
    return pymysql.connect(**_config())


def query(sql: str, params: tuple = None) -> list:
    """Run a SELECT and return a list of dict rows."""
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
    finally:
        db.close()


def execute(sql: str, params: tuple = None) -> int:
    """Run a write and return affected rowcount."""
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute(sql, params or ())
            db.commit()
            return cur.rowcount
    finally:
        db.close()


# ----------------------------------------------------------------------------
# Agent-facing tools (string returns, LLM-friendly) — ported from agent_tools.py
# ----------------------------------------------------------------------------

def execute_sql(sql: str) -> str:
    """Run a read-only analytical query (LLM tool). Destructive statements blocked."""
    upper = sql.upper()
    if any(kw in upper for kw in ("DROP ", "DELETE ", "TRUNCATE ", "ALTER ", "UPDATE ", "INSERT ")):
        return "❌ SAFETY BLOCK: only read-only queries are allowed via this tool."
    try:
        rows = query(sql)
        if not rows:
            return "No results found."
        return json.dumps(rows[:25], default=str)
    except Exception as e:
        return f"❌ SQL Error: {e}"


def vector_search(text: str, table: str, k: int = 5, where: str = None) -> list:
    """ANN search against a table's vector column using cosine distance.

    Returns dict rows with a `similarity` field (1 - cosine_distance).
    """
    from lib.embeddings import embed_str
    from lib.text_bander import VECTOR_COLUMNS

    vec_col = VECTOR_COLUMNS.get(table)
    if not vec_col:
        raise ValueError(f"no vector column registered for table '{table}'")

    qvec = embed_str(text)
    filt = f"WHERE {where}" if where else ""
    sql = f"""
        SELECT *, (1 - VEC_COSINE_DISTANCE({vec_col}, %s)) AS similarity
        FROM {table}
        {filt}
        ORDER BY VEC_COSINE_DISTANCE({vec_col}, %s) ASC
        LIMIT %s
    """
    return query(sql, (qvec, qvec, k))
