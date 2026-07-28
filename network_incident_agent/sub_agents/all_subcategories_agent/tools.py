"""Tool: fetch_all_subcategories — replaces a BigQuery DISTINCT scan with a
TiDB TiFlash columnar query over the tickets table."""
import json
from google.adk.tools import ToolContext
from lib.tidb import query


def fetch_all_subcategories(tool_context: ToolContext, region: str = "", days: int = 90) -> str:
    """Retrieve the distinct incident subcategories present in the data for a
    time range (and optional region), with their ticket counts.

    Args:
        region: optional region filter (e.g. 'EMEA-West'); empty = all regions.
        days: look-back window in days (default 90).

    Returns a JSON list of {subcategory, ticket_count}. The result is also
    stored in session state for the SubcategoryAgent to rank within.
    """
    where = ["created_at >= NOW() - INTERVAL %s DAY", "subcategory IS NOT NULL"]
    params = [days]
    if region:
        where.append("region = %s")
        params.append(region)
    sql = f"""
        SELECT /*+ read_from_storage(tiflash[tickets]) */
               subcategory, COUNT(*) AS ticket_count
        FROM tickets
        WHERE {' AND '.join(where)}
        GROUP BY subcategory
        ORDER BY ticket_count DESC
    """
    try:
        rows = query(sql, tuple(params))
    except Exception as e:
        return f"❌ SQL Error: {e}"
    subs = [r["subcategory"] for r in rows]
    tool_context.state["candidate_subcategories"] = subs
    return json.dumps(rows, default=str)
