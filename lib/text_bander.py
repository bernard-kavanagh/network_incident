"""Text banding — convert a DB row into the text we embed.

Single source of truth (the EV repo's lesson): the embedding pipeline and the
agent's context assembly MUST band text identically, or vector neighbours drift
apart from what the agent reasons over. Every table that has a vector column
gets one builder here, registered in TEXT_BUILDERS.
"""
import json


def _j(val):
    """Coerce a JSON column (str or already-parsed) to a compact display string."""
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        return json.dumps(val, default=str)
    return str(val)


def build_incident_text(row: dict) -> str:
    return (
        f"incident severity={row.get('severity')} "
        f"category={row.get('category')}/{row.get('subcategory')} "
        f"element={row.get('element_id')} site={row.get('site_id')} "
        f"alarms={row.get('alarm_count')} anomaly={row.get('anomaly_score')} "
        f"breakdown={_j(row.get('anomaly_breakdown'))} "
        f"{row.get('title') or ''}. {row.get('description') or ''}"
    ).strip()


def build_catalog_text(row: dict) -> str:
    return (
        f"pattern {row.get('pattern_name')} category={row.get('category')} "
        f"severity={row.get('severity')}. "
        f"root_cause: {row.get('root_cause')}. "
        f"symptoms: {_j(row.get('symptoms'))}. "
        f"resolution: {row.get('resolution')}"
    ).strip()


def build_ticket_text(row: dict) -> str:
    return (
        f"ticket category={row.get('category')}/{row.get('subcategory')} "
        f"priority={row.get('priority')} region={row.get('region')}. "
        f"{row.get('summary') or ''}. {row.get('description') or ''}. "
        f"resolution: {row.get('resolution') or ''}"
    ).strip()


def build_reasoning_text(row: dict) -> str:
    return (
        f"observation: {row.get('observation') or ''}. "
        f"hypothesis: {row.get('hypothesis') or ''}. "
        f"resolution: {row.get('resolution') or ''}"
    ).strip()


def build_memory_text(row: dict) -> str:
    return f"[{row.get('category')}] {row.get('content') or ''}".strip()


def build_runbook_text(row: dict) -> str:
    return (
        f"runbook {row.get('title')} :: trigger: {row.get('trigger_condition') or ''}. "
        f"steps: {_j(row.get('steps'))}"
    ).strip()


# table name -> (builder, vector column)
TEXT_BUILDERS = {
    "incidents":        build_incident_text,
    "incident_catalog": build_catalog_text,
    "tickets":          build_ticket_text,
    "agent_reasoning":  build_reasoning_text,
    "incident_memory":  build_memory_text,
    "runbook_memory":   build_runbook_text,
}

VECTOR_COLUMNS = {
    "incidents":        "signature_vec",
    "incident_catalog": "signature_vec",
    "tickets":          "embedding",
    "agent_reasoning":  "reasoning_vec",
    "incident_memory":  "memory_vec",
    "runbook_memory":   "runbook_vec",
}
