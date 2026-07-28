"""TiDB connection + query primitives.

Single data-access layer for every agent and tool. Uses PyMySQL (as the EV
repo's embedding service does) with a DictCursor and TLS, so the exact same
code talks to TiDB Cloud now and to in-cluster TiDB (TiDB Operator) on GDC
bare metal later — only the .env connection values change.
"""
import json
import os
import time
import pymysql
from dotenv import load_dotenv

load_dotenv()

# Transient connection errors worth retrying (TiDB Serverless drops idle/cold
# connections; a fresh connect per call means the occasional blip on a long run).
_TRANSIENT_ERRNOS = {2013, 2006, 2003, 1105}
_MAX_ATTEMPTS = 3


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


def _run(sql: str, params: tuple, write: bool):
    """Execute with a small reconnect-retry on transient connection errors."""
    last = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        db = None
        try:
            db = get_db()
            with db.cursor() as cur:
                cur.execute(sql, params or ())
                if write:
                    db.commit()
                    return cur.rowcount
                return cur.fetchall()
        except pymysql.err.OperationalError as e:
            last = e
            if e.args and e.args[0] in _TRANSIENT_ERRNOS and attempt < _MAX_ATTEMPTS:
                time.sleep(0.5 * attempt)   # linear backoff
                continue
            raise
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass
    raise last


def query(sql: str, params: tuple = None) -> list:
    """Run a SELECT and return a list of dict rows."""
    return _run(sql, params, write=False)


def execute(sql: str, params: tuple = None) -> int:
    """Run a write and return affected rowcount."""
    return _run(sql, params, write=True)


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


def hybrid_rerank(query_text: str, subcategory: str = None, top_n: int = 10,
                  retrieve_n: int = 10, alpha: float = 0.7) -> list:
    """Hybrid vector + FULLTEXT rerank over `tickets` — the single SQL-native
    replacement for the Vertex semantic-ranker. Shared by the ADK rerank tool
    and the eval harness so they never drift.

    Score = alpha * vector_similarity + (1-alpha) * normalised_fulltext_score.
    Falls back to vector-only if the TiDB build lacks `fts_match_word`.
    Returns the top_n rows with `vec_sim` and `hybrid_score` fields.
    """
    from lib.embeddings import embed_str
    qvec = embed_str(query_text)
    filt = "WHERE subcategory = %s" if subcategory else ""
    sub_params = [subcategory] if subcategory else []

    sql = f"""
        SELECT ticket_id, subcategory, priority, summary, resolution, region, created_at,
               (1 - VEC_COSINE_DISTANCE(embedding, %s)) AS vec_sim,
               fts_match_word(%s, summary)              AS ft_score
        FROM tickets {filt}
        ORDER BY VEC_COSINE_DISTANCE(embedding, %s) ASC
        LIMIT %s
    """
    params = [qvec, query_text] + sub_params + [qvec, retrieve_n]
    try:
        rows = query(sql, tuple(params))
    except Exception:
        sql_v = f"""
            SELECT ticket_id, subcategory, priority, summary, resolution, region, created_at,
                   (1 - VEC_COSINE_DISTANCE(embedding, %s)) AS vec_sim, 0 AS ft_score
            FROM tickets {filt}
            ORDER BY VEC_COSINE_DISTANCE(embedding, %s) ASC LIMIT %s
        """
        rows = query(sql_v, tuple([qvec] + sub_params + [qvec, retrieve_n]))

    max_ft = max((float(r.get("ft_score") or 0) for r in rows), default=0) or 1.0
    for r in rows:
        vec = float(r.get("vec_sim") or 0)
        ft = float(r.get("ft_score") or 0) / max_ft
        r["vec_sim"] = round(vec, 4)
        r["hybrid_score"] = round(alpha * vec + (1 - alpha) * ft, 4)
    rows.sort(key=lambda x: x["hybrid_score"], reverse=True)
    return rows[:top_n]


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
