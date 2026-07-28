"""Tool: fetch_all_tickets_and_rerank.

THE headline substitution: replaces Vertex AI `semantic-ranker-default@latest`
with a single TiDB SQL query doing hybrid vector + FULLTEXT scoring. The ranking
logic lives in lib.tidb.hybrid_rerank so this tool and the eval harness share
one implementation (no drift)."""
import json
from google.adk.tools import ToolContext
from lib.tidb import hybrid_rerank
from network_incident_agent.config import RERANK_RETRIEVE_TOP_N, OUTPUT_N_TICKETS


def fetch_all_tickets_and_rerank(tool_context: ToolContext,
                                 incident_description: str = "",
                                 subcategory: str = "",
                                 alpha: float = 0.7) -> str:
    """Retrieve candidate tickets for the incident's subcategory and rank them by
    a hybrid of vector similarity and full-text relevance (TiDB-native).

    Args:
        incident_description: the user's incident text (defaults to state).
        subcategory: subcategory filter (defaults to the chosen one in state).
        alpha: weight on vector similarity vs full-text (0..1, default 0.7).

    Returns the top tickets as JSON: ticket_id, summary, resolution, scores.
    """
    incident_description = incident_description or tool_context.state.get("incident_description", "")
    subcategory = subcategory or tool_context.state.get("chosen_subcategory", "")
    if not incident_description:
        return "❌ No incident description provided."

    try:
        top = hybrid_rerank(incident_description, subcategory=subcategory or None,
                            top_n=OUTPUT_N_TICKETS, retrieve_n=RERANK_RETRIEVE_TOP_N, alpha=alpha)
    except Exception as e:
        return f"❌ SQL Error: {e}"
    tool_context.state["ranked_tickets"] = [t["ticket_id"] for t in top]
    return json.dumps(top, default=str)
