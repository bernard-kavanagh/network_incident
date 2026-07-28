"""Tool: fetch_all_tickets_and_rerank.

THE headline substitution: replaces Vertex AI `semantic-ranker-default@latest`
with a single TiDB SQL query doing hybrid scoring —
  - vector relevance:    1 - VEC_COSINE_DISTANCE(embedding, query_vec)   (HNSW ANN)
  - lexical relevance:    FULLTEXT match on summary + description
combined into one score. No external ranking service, no extra round-trip."""
import json
from google.adk.tools import ToolContext
from lib.embeddings import embed_str
from lib.tidb import query
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

    qvec = embed_str(incident_description)
    # Candidate pool: same subcategory if known, else recent tickets. Score with
    # vector cosine similarity + a FULLTEXT relevance signal, combined by alpha.
    filt = "WHERE subcategory = %s" if subcategory else ""
    params = []
    if subcategory:
        params.append(subcategory)
    sql = f"""
        SELECT ticket_id, subcategory, priority, summary, resolution, region, created_at,
               (1 - VEC_COSINE_DISTANCE(embedding, %s)) AS vec_sim,
               fts_match_word(%s, summary)              AS ft_score
        FROM tickets
        {filt}
        ORDER BY VEC_COSINE_DISTANCE(embedding, %s) ASC
        LIMIT %s
    """
    # fts_match_word returns relevance for TiDB FULLTEXT; if the build lacks it,
    # fall back to vector-only ranking.
    vec_params = [qvec, incident_description] + params + [qvec, RERANK_RETRIEVE_TOP_N]
    try:
        rows = query(sql, tuple(vec_params))
    except Exception:
        # Fallback: vector-only (older TiDB without fts_match_word scoring fn)
        sql_v = f"""
            SELECT ticket_id, subcategory, priority, summary, resolution, region, created_at,
                   (1 - VEC_COSINE_DISTANCE(embedding, %s)) AS vec_sim, 0 AS ft_score
            FROM tickets {filt}
            ORDER BY VEC_COSINE_DISTANCE(embedding, %s) ASC LIMIT %s
        """
        vp = [qvec] + params + [qvec, RERANK_RETRIEVE_TOP_N]
        try:
            rows = query(sql_v, tuple(vp))
        except Exception as e:
            return f"❌ SQL Error: {e}"

    # Normalise full-text scores to 0..1 and blend.
    max_ft = max((float(r.get("ft_score") or 0) for r in rows), default=0) or 1.0
    ranked = []
    for r in rows:
        vec = float(r.get("vec_sim") or 0)
        ft = float(r.get("ft_score") or 0) / max_ft
        r["hybrid_score"] = round(alpha * vec + (1 - alpha) * ft, 4)
        r["vec_sim"] = round(vec, 4)
        ranked.append(r)
    ranked.sort(key=lambda x: x["hybrid_score"], reverse=True)
    top = ranked[:OUTPUT_N_TICKETS]
    tool_context.state["ranked_tickets"] = [t["ticket_id"] for t in top]
    return json.dumps(top, default=str)
