"""Remediation Agent — Agent 3.

For an open incident, it exercises the full cognitive foundation:
  1. assemble_context()  — multi-tier context (element profile, recent activity,
                           prior investigations, semantic memory) under a budget
  2. route_investigation() — substrate decides shortcut vs explore from the
                           Tier-5 semantic matches (warm vs cold start)
  3. recall_runbooks() / recall_similar() — procedural + semantic recall
  4. proposes a remediation, writes an episodic checkpoint, and on a confirmed
     outcome promotes a learned pattern (write control) and records the runbook
     outcome (procedural write-back).

Substrate-driven by default (the duties are pure SQL). A `--synthesize` hook is
left for where a Gemini call would compose the operator-facing narrative.

    python agents/remediation.py --incident INC-NE-00007-202606141200
    python agents/remediation.py --latest
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.tidb import get_db, query                                          # noqa: E402
from lib.memory import (assemble_context, route_investigation,              # noqa: E402
                        recall_runbooks, recall_similar,
                        write_reasoning_checkpoint, remember_pattern,
                        record_runbook_outcome)

# Minimum runbook vector similarity to auto-apply; below this we escalate.
RUNBOOK_RELEVANCE_FLOOR = float(os.getenv("RUNBOOK_RELEVANCE_FLOOR", "0.45"))


def _pick_incident(incident_ref, latest):
    if incident_ref:
        rows = query("SELECT * FROM incidents WHERE incident_ref = %s", (incident_ref,))
    elif latest:
        rows = query("""SELECT * FROM incidents WHERE status='open'
                        ORDER BY anomaly_score DESC, window_start DESC LIMIT 1""")
    else:
        rows = query("""SELECT * FROM incidents WHERE status='open'
                        ORDER BY window_start DESC LIMIT 1""")
    return rows[0] if rows else None


def remediate(incident_ref=None, latest=False, auto_resolve=True) -> dict:
    inc = _pick_incident(incident_ref, latest)
    if not inc:
        return {"error": "no matching incident"}

    ref = inc["incident_ref"]
    elem = inc["element_id"]
    # Clean recall signal: subcategory + title discriminate far better than the
    # noisy full description (KPI dumps + breakdown JSON blur the vector match).
    recall_query = (f"{inc.get('subcategory') or ''} {inc.get('title') or ''}").strip() \
        or inc.get("description") or ref
    session = f"remediation-{ref}"

    # 1. assemble context from the substrate (memory tiers)
    ctx = assemble_context(entity_ref=elem, session_id=session, trigger_text=recall_query)
    # 2. substrate-driven routing
    route = route_investigation(ctx["vector_matches"])
    # 3. recall procedural + semantic memory
    runbooks = _safe(recall_runbooks, recall_query)
    similar = _safe(recall_similar, recall_query)

    # Relevance floor: only apply a runbook if the nearest one is actually close
    # enough. Otherwise escalate rather than run an unrelated runbook.
    top_runbook = runbooks[0] if isinstance(runbooks, list) and runbooks else None
    top_sim = float(top_runbook.get("similarity") or 0) if top_runbook else 0.0
    chosen_runbook = top_runbook if top_sim >= RUNBOOK_RELEVANCE_FLOOR else None

    # 4. write episodic checkpoint
    conf = float(inc["anomaly_score"]) if inc.get("anomaly_score") else 0.6
    obs = (f"Incident {ref} on {elem}: {inc.get('subcategory')} "
           f"sev={inc.get('severity')} anomaly={inc.get('anomaly_score')}")
    hyp = (f"Apply runbook '{chosen_runbook['title']}'" if chosen_runbook
           else "No matching runbook; escalate for manual investigation")
    write_reasoning_checkpoint(session_id=session, observation=obs, hypothesis=hyp,
                               resolution="confirmed" if chosen_runbook else "escalated",
                               confidence=min(0.95, 0.5 + conf / 2),
                               incident_ref=ref, element_id=elem)

    # write-back: record runbook outcome + promote a learned pattern
    actions = []
    if chosen_runbook and auto_resolve:
        record_runbook_outcome(chosen_runbook["id"], succeeded=True)
        actions.append(f"recorded success for runbook {chosen_runbook['id']}")
        pat = remember_pattern(
            content=(f"{inc.get('subcategory')} on {inc.get('vendor','element')} resolved by "
                     f"runbook '{chosen_runbook['title']}' (anomaly {inc.get('anomaly_score')})."),
            category="pattern", confidence=min(0.95, 0.5 + conf / 2), scope="global",
            source_refs=[ref])
        actions.append(pat)
        _set_status(ref, "resolved")
    else:
        reason = (f"nearest runbook similarity {top_sim:.2f} < floor "
                  f"{RUNBOOK_RELEVANCE_FLOOR} — no relevant runbook, escalating"
                  if top_runbook else "no runbooks in memory — escalating")
        actions.append(reason)
        _set_status(ref, "investigating")

    return {
        "incident_ref": ref, "element": elem, "subcategory": inc.get("subcategory"),
        "context_budget": f"{ctx['budget_used']}/{ctx['budget_total']}",
        "context_sources": ctx["sources"],
        "route": {"path": route["path"], "reason": route["reason"]},
        "chosen_runbook": chosen_runbook["title"] if chosen_runbook else None,
        "top_runbook_similarity": round(top_sim, 3),
        "runbook_relevance_floor": RUNBOOK_RELEVANCE_FLOOR,
        "runbook_steps": chosen_runbook["steps"] if chosen_runbook else None,
        "similar_patterns": similar if isinstance(similar, list) else [],
        "write_back_actions": actions,
    }


def _is_err(s):
    return isinstance(s, str) and s.startswith("❌")


def _safe(fn, arg):
    res = fn(arg)
    if _is_err(res) or (isinstance(res, str) and res.startswith("No ")):
        return []
    try:
        return json.loads(res)
    except Exception:
        return []


def _set_status(ref, status):
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("UPDATE incidents SET status=%s WHERE incident_ref=%s", (status, ref))
            db.commit()
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--incident", default=None)
    ap.add_argument("--latest", action="store_true", help="pick highest-anomaly open incident")
    a = ap.parse_args()
    result = remediate(incident_ref=a.incident, latest=a.latest)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
