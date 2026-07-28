"""Tool: get_ticket_counts_for_deviation.

Replaces Devoteam's BigQuery volume scan with a TiDB TiFlash (HTAP) aggregate.
Computes the recent daily ticket volume for a subcategory versus its historical
daily baseline (mean + stddev) and returns a z-score so the agent can judge
whether an abnormal deviation (emerging outage) is under way."""
import json
from google.adk.tools import ToolContext
from lib.tidb import query


def get_ticket_counts_for_deviation(tool_context: ToolContext,
                                    subcategory: str = "",
                                    recent_days: int = 3,
                                    baseline_days: int = 60) -> str:
    """Detect a volume deviation for a subcategory.

    Args:
        subcategory: subcategory to analyse; if empty, uses the chosen one in state.
        recent_days: size of the recent window to evaluate (default 3).
        baseline_days: history used to build the daily baseline (default 60).

    Returns JSON with recent_avg_per_day, baseline_mean, baseline_stddev,
    z_score, and a deviation verdict. Uses TiFlash columnar reads.
    """
    subcategory = subcategory or tool_context.state.get("chosen_subcategory", "")
    if not subcategory:
        return "❌ No subcategory provided or in state."

    sql = """
        SELECT /*+ read_from_storage(tiflash[tickets]) */
               recent.recent_total / %s                       AS recent_avg_per_day,
               base.baseline_mean                              AS baseline_mean,
               base.baseline_stddev                            AS baseline_stddev
        FROM
          (SELECT COUNT(*) AS recent_total FROM tickets
            WHERE subcategory = %s
              AND created_at >= NOW() - INTERVAL %s DAY) recent,
          (SELECT AVG(daily) AS baseline_mean, STDDEV_SAMP(daily) AS baseline_stddev FROM
             (SELECT DATE(created_at) d, COUNT(*) daily FROM tickets
               WHERE subcategory = %s
                 AND created_at <  NOW() - INTERVAL %s DAY
                 AND created_at >= NOW() - INTERVAL %s DAY
               GROUP BY DATE(created_at)) per_day
          ) base
    """
    try:
        rows = query(sql, (recent_days, subcategory, recent_days,
                           subcategory, recent_days, baseline_days + recent_days))
    except Exception as e:
        return f"❌ SQL Error: {e}"
    if not rows:
        return json.dumps({"subcategory": subcategory, "verdict": "no_data"})

    r = rows[0]
    recent = float(r["recent_avg_per_day"] or 0)
    mean = float(r["baseline_mean"] or 0)
    std = float(r["baseline_stddev"] or 0)
    z = round((recent - mean) / std, 2) if std > 0 else None
    if z is None:
        verdict = "insufficient_baseline"
    elif z >= 3:
        verdict = "severe_deviation"
    elif z >= 2:
        verdict = "significant_deviation"
    elif z <= -2:
        verdict = "abnormally_low"
    else:
        verdict = "within_normal_range"

    result = {"subcategory": subcategory, "recent_avg_per_day": round(recent, 2),
              "baseline_mean": round(mean, 2), "baseline_stddev": round(std, 2),
              "z_score": z, "verdict": verdict}
    tool_context.state["deviation_result"] = result
    return json.dumps(result, default=str)
