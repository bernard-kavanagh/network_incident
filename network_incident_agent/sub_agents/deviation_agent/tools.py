"""Tool: get_ticket_counts_for_deviation.

Replaces Devoteam's BigQuery volume scan with a TiDB TiFlash (HTAP) aggregate.
The deviation logic lives in lib.analytics.volume_deviation so this tool and the
eval harness share one implementation."""
import json
from google.adk.tools import ToolContext
from lib.analytics import volume_deviation


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
    try:
        result = volume_deviation(subcategory, recent_days=recent_days, baseline_days=baseline_days)
    except Exception as e:
        return f"❌ SQL Error: {e}"
    tool_context.state["deviation_result"] = result
    return json.dumps(result, default=str)
