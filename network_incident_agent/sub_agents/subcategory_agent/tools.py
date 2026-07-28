"""Tools: get_subcategories_from_state, set_top5_subcategories.

Classification is augmented with TiDB vector similarity against the curated
incident_catalog — the substrate proposes the closest known patterns, the LLM
ranks. (Devoteam's design left ranking to the model alone.)"""
import json
from google.adk.tools import ToolContext
from lib.tidb import vector_search


def get_subcategories_from_state(tool_context: ToolContext) -> str:
    """Return the candidate subcategories populated by the AllSubcategoriesAgent,
    plus the catalog patterns most similar to the incident description (vector
    search over incident_catalog) to inform ranking."""
    candidates = tool_context.state.get("candidate_subcategories", [])
    desc = tool_context.state.get("incident_description", "")
    catalog_hits = []
    if desc:
        try:
            rows = vector_search(desc, "incident_catalog", k=5)
            catalog_hits = [
                {"pattern_name": r["pattern_name"], "category": r["category"],
                 "similarity": round(float(r["similarity"]), 3)} for r in rows]
        except Exception as e:
            catalog_hits = [{"error": str(e)}]
    return json.dumps({"candidate_subcategories": candidates,
                       "nearest_catalog_patterns": catalog_hits}, default=str)


def set_top5_subcategories(tool_context: ToolContext, subcategories: list) -> str:
    """Store the ranked top-5 subcategories. The #1 entry becomes the chosen
    subcategory used by the Deviation and Rerank agents.

    Args:
        subcategories: ordered list of subcategory strings, most relevant first.
    """
    top5 = subcategories[:5]
    tool_context.state["top5_subcategories"] = top5
    if top5:
        tool_context.state["chosen_subcategory"] = top5[0]
    return json.dumps({"top5_subcategories": top5,
                       "chosen_subcategory": top5[0] if top5 else None})
