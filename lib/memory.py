"""Cognitive foundation — three-tier agent memory + the five custodial duties.

This is the layer that replaces Vertex AI Agent Engine Memory Bank. Domain-
agnostic: the same module backs all 50 agents. Telecom specifics live in the
adapter (adapters/network_incident); this file only knows the substrate.

Memory tiers
  episodic   -> agent_reasoning   (outcomes-only investigation checkpoints)
  semantic   -> incident_memory   (learned network patterns, vector-indexed)
  procedural -> runbook_memory    (remediation runbooks, recalled + improved)

Five custodial duties (pure SQL, no LLM calls)
  1 write control      remember_pattern() rejects sub-threshold writes
  2 deduplication      consolidate_memory() merges cosine-near rows
  3 reconciliation     supersession chains via superseded_by
  4 confidence decay   decay_memory() fades unreinforced patterns
  5 compaction         compact_memory() archives dead rows
"""
import json
import math
import os

from lib.tidb import get_db, query
from lib.embeddings import embed_str

# ---- tuning (env-overridable) ----------------------------------------------
CONTEXT_BUDGET_TOKENS = int(os.getenv("CONTEXT_BUDGET_TOKENS", "3600"))
DEDUP_DISTANCE_THRESHOLD = float(os.getenv("DEDUP_DISTANCE_THRESHOLD", "0.15"))
WRITE_CONTROL_MIN_CONFIDENCE = float(os.getenv("WRITE_CONTROL_MIN_CONFIDENCE", "0.80"))
ROUTING_CONFIDENCE_GATE = float(os.getenv("ROUTING_CONFIDENCE_GATE", "0.85"))
ROUTING_SIMILARITY_GATE = float(os.getenv("ROUTING_SIMILARITY_GATE", "0.55"))

TIER_CAPS = {
    "t1_entity": 120, "t2_recent": 300, "t3_active": 200,
    "t4_prior": 500, "t5_semantic": 500,
}


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _truncate(text: str, max_tokens: int) -> str:
    max_chars = max_tokens * 4
    return text if len(text) <= max_chars else text[:max_chars] + "…"


# ============================================================================
# CONTEXT ASSEMBLY — what the model sees BEFORE its first token. Pure SQL.
# ============================================================================
def assemble_context(entity_ref: str = None, session_id: str = None,
                     trigger_text: str = None, adapter=None) -> dict:
    """Build the agent's prompt context from the substrate, under a hard budget.

    Tiers 1/2/4 are domain-specific (delegated to the adapter). Tiers 3/5 are
    substrate-generic. Returns system_context + provenance + the Tier-5 vector
    matches that feed the routing layer.
    """
    if adapter is None:
        from adapters import network_incident as adapter

    sources, blocks = {}, []
    budget_used = 0
    top_match, vector_matches = None, []

    db = get_db()
    try:
        cur = db.cursor()

        # T1 entity profile (adapter)
        t1_text, t1_status = adapter.tier_1_entity(cur, entity_ref)
        if t1_text:
            t1_text = _truncate(t1_text, TIER_CAPS["t1_entity"])
            cost = _approx_tokens(t1_text)
            if budget_used + cost <= CONTEXT_BUDGET_TOKENS:
                blocks.append(t1_text); budget_used += cost
                sources["t1_entity"] = {"tokens": cost, "status": t1_status}
        else:
            sources["t1_entity"] = {"tokens": 0, "status": t1_status}

        # T2 recent activity for this entity (adapter)
        t2_lines, t2_status = adapter.tier_2_recent(cur, entity_ref)
        if t2_lines:
            t2_text = _truncate("[T2 recent] " + " | ".join(t2_lines), TIER_CAPS["t2_recent"])
            cost = _approx_tokens(t2_text)
            if budget_used + cost <= CONTEXT_BUDGET_TOKENS:
                blocks.append(t2_text); budget_used += cost
                sources["t2_recent"] = {"tokens": cost, "count": len(t2_lines), "status": t2_status}
        else:
            sources["t2_recent"] = {"tokens": 0, "status": t2_status}

        # T3 active investigation checkpoint (substrate)
        if session_id:
            cur.execute(
                """SELECT observation, hypothesis, confidence FROM agent_reasoning
                   WHERE session_id = %s ORDER BY created_at DESC LIMIT 1""",
                (session_id,))
            row = cur.fetchone()
            if row:
                t3 = _truncate(
                    f"[T3 active] obs={row['observation']} | hyp={row['hypothesis']} "
                    f"| conf={row['confidence']}", TIER_CAPS["t3_active"])
                cost = _approx_tokens(t3)
                if budget_used + cost <= CONTEXT_BUDGET_TOKENS:
                    blocks.append(t3); budget_used += cost
                    sources["t3_active"] = {"tokens": cost, "status": "ok"}

        # T4 prior investigations for this entity (adapter)
        t4_lines, t4_status = adapter.tier_4_prior(cur, entity_ref)
        if t4_lines:
            t4_text = _truncate("[T4 prior] " + " | ".join(t4_lines), TIER_CAPS["t4_prior"])
            cost = _approx_tokens(t4_text)
            if budget_used + cost <= CONTEXT_BUDGET_TOKENS:
                blocks.append(t4_text); budget_used += cost
                sources["t4_prior"] = {"tokens": cost, "count": len(t4_lines), "status": t4_status}
        else:
            sources["t4_prior"] = {"tokens": 0, "status": t4_status}

        # T5 semantic memory via vector similarity (substrate, capped at 500)
        if trigger_text:
            try:
                qvec = embed_str(trigger_text)
                cur.execute(
                    """SELECT id, scope, category, content, confidence, evidence_count,
                              (1 - VEC_COSINE_DISTANCE(memory_vec, %s)) AS similarity
                       FROM incident_memory
                       WHERE status = 'active' AND superseded_by IS NULL
                       ORDER BY VEC_COSINE_DISTANCE(memory_vec, %s) ASC LIMIT 3""",
                    (qvec, qvec))
                vector_matches = cur.fetchall() or []
                if vector_matches:
                    top_match = max(vector_matches, key=lambda r: float(r["confidence"]))
                    lines = [
                        f"mem={r['id']} cat={r['category']} conf={r['confidence']} "
                        f"sim={float(r['similarity']):.2f} :: {r['content'][:80]}"
                        for r in vector_matches]
                    t5 = _truncate("[T5 semantic] " + " | ".join(lines), TIER_CAPS["t5_semantic"])
                    cost = _approx_tokens(t5)
                    if budget_used + cost <= CONTEXT_BUDGET_TOKENS:
                        blocks.append(t5); budget_used += cost
                        sources["t5_semantic"] = {"tokens": cost, "count": len(vector_matches), "status": "ok"}
            except Exception as e:
                sources["t5_semantic"] = {"tokens": 0, "status": f"degraded:{e}"}
    finally:
        db.close()

    return {
        "system_context": "\n".join(blocks),
        "sources": sources,
        "budget_used": budget_used,
        "budget_total": CONTEXT_BUDGET_TOKENS,
        "top_match": top_match,
        "vector_matches": vector_matches,
    }


# ============================================================================
# SEMANTIC MEMORY — recall + write (duty 1: write control)
# ============================================================================
def recall_similar(query_text: str, scope: str = None, k: int = 5) -> str:
    """Retrieve active learned patterns from incident_memory by vector similarity."""
    try:
        qvec = embed_str(query_text)
        sql = """SELECT id, scope, category, content, confidence, evidence_count,
                        (1 - VEC_COSINE_DISTANCE(memory_vec, %s)) AS similarity
                 FROM incident_memory
                 WHERE status = 'active' AND superseded_by IS NULL"""
        params = [qvec]
        if scope:
            sql += " AND scope = %s"; params.append(scope)
        sql += " ORDER BY VEC_COSINE_DISTANCE(memory_vec, %s) ASC LIMIT %s"
        params += [qvec, k]
        rows = query(sql, tuple(params))
        if not rows:
            return "No similar patterns in memory."
        return json.dumps([
            {"id": r["id"], "scope": r["scope"], "category": r["category"],
             "content": r["content"], "confidence": float(r["confidence"]),
             "similarity": float(r["similarity"]), "evidence_count": r["evidence_count"]}
            for r in rows], default=str)
    except Exception as e:
        return f"❌ Recall Error: {e}"


def remember_pattern(content: str, category: str = "pattern", confidence: float = 0.80,
                     scope: str = "global", source_refs: list = None) -> str:
    """Duty 1 — write control. Persist a learned pattern to incident_memory only
    if confidence clears the floor. Deterministic gate; misaligned models cannot
    pollute the store."""
    if confidence < WRITE_CONTROL_MIN_CONFIDENCE:
        return (f"❌ Write control: confidence={confidence:.2f} below floor "
                f"({WRITE_CONTROL_MIN_CONFIDENCE}). Not persisted.")
    try:
        vec = embed_str(content)
        n = _exec(
            """INSERT INTO incident_memory
                 (category, scope, content, source_refs, confidence, evidence_count,
                  last_reinforced_at, memory_vec)
               VALUES (%s, %s, %s, %s, %s, 1, NOW(), %s)""",
            (category, scope, content, json.dumps(source_refs or []), confidence, vec))
        return f"✅ incident_memory pattern stored (scope={scope}, conf={confidence:.2f}, rows={n})."
    except Exception as e:
        return f"❌ Write Error: {e}"


def write_reasoning_checkpoint(session_id: str, observation: str, hypothesis: str = None,
                               resolution: str = "confirmed", confidence: float = 0.5,
                               incident_ref: str = None, element_id: str = None,
                               evidence_refs: list = None) -> str:
    """Episodic memory write. Outcomes-only checkpoint, not a transcript."""
    try:
        vec = embed_str(f"{observation} {hypothesis or ''} {resolution}")
        n = _exec(
            """INSERT INTO agent_reasoning
                 (incident_ref, element_id, session_id, observation, hypothesis,
                  evidence_refs, confidence, resolution, reasoning_vec)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (incident_ref, element_id, session_id, observation, hypothesis,
             json.dumps(evidence_refs or []), confidence, resolution, vec))
        return f"✅ agent_reasoning checkpoint written (rows={n})."
    except Exception as e:
        return f"❌ Checkpoint Error: {e}"


# ============================================================================
# PROCEDURAL MEMORY — runbook recall + outcome write-back (Remediation Agent)
# ============================================================================
def recall_runbooks(query_text: str, k: int = 3) -> str:
    """Retrieve candidate remediation runbooks by vector similarity."""
    try:
        qvec = embed_str(query_text)
        rows = query(
            """SELECT id, title, trigger_condition, steps, confidence,
                      success_count, fail_count,
                      (1 - VEC_COSINE_DISTANCE(runbook_vec, %s)) AS similarity
               FROM runbook_memory
               WHERE status = 'active' AND superseded_by IS NULL
               ORDER BY VEC_COSINE_DISTANCE(runbook_vec, %s) ASC LIMIT %s""",
            (qvec, qvec, k))
        if not rows:
            return "No runbooks in memory."
        return json.dumps(rows, default=str)
    except Exception as e:
        return f"❌ Runbook Recall Error: {e}"


def record_runbook_outcome(runbook_id: int, succeeded: bool) -> str:
    """Procedural write-back: reinforce/penalise a runbook by observed outcome.
    Confidence = success_count / (success_count + fail_count)."""
    try:
        col = "success_count" if succeeded else "fail_count"
        _exec(f"UPDATE runbook_memory SET {col} = {col} + 1, last_used = NOW() WHERE id = %s",
              (runbook_id,))
        _exec(
            """UPDATE runbook_memory
               SET confidence = ROUND(success_count / GREATEST(success_count + fail_count, 1), 2)
               WHERE id = %s""", (runbook_id,))
        return f"✅ runbook {runbook_id} outcome recorded (succeeded={succeeded})."
    except Exception as e:
        return f"❌ Outcome Error: {e}"


# ============================================================================
# CUSTODIAL DUTIES 2 & 4 — deduplication and confidence decay (pure SQL)
# ============================================================================
def consolidate_memory() -> str:
    """Duty 2 — deduplication. Merge incident_memory rows within the cosine
    threshold: keep the highest-confidence row, sum evidence, supersede the rest."""
    db = get_db()
    merges = []
    try:
        cur = db.cursor()
        cur.execute("""SELECT id, memory_vec, confidence, evidence_count
                       FROM incident_memory
                       WHERE status='active' AND superseded_by IS NULL ORDER BY id ASC""")
        rows = cur.fetchall()
        if len(rows) < 2:
            return json.dumps({"status": "ok", "active_rows": len(rows), "merges": []})

        seen = set()
        for anchor in rows:
            if anchor["id"] in seen:
                continue
            cur.execute(
                """SELECT id, confidence, evidence_count,
                          VEC_COSINE_DISTANCE(memory_vec, %s) AS distance
                   FROM incident_memory
                   WHERE status='active' AND superseded_by IS NULL AND id != %s
                     AND VEC_COSINE_DISTANCE(memory_vec, %s) < %s
                   ORDER BY distance ASC""",
                (anchor["memory_vec"], anchor["id"], anchor["memory_vec"], DEDUP_DISTANCE_THRESHOLD))
            dupes = cur.fetchall()
            if not dupes:
                continue
            cluster = [anchor] + dupes
            keep = max(cluster, key=lambda r: (float(r["confidence"]), -r["id"]))
            total = sum(int(r["evidence_count"]) for r in cluster)
            losers = [r for r in cluster if r["id"] != keep["id"]]
            for loser in losers:
                cur.execute(
                    "UPDATE incident_memory SET superseded_by=%s, status='superseded' WHERE id=%s",
                    (keep["id"], loser["id"]))
                seen.add(loser["id"])
            cur.execute(
                "UPDATE incident_memory SET evidence_count=%s, last_reinforced_at=NOW() WHERE id=%s",
                (total, keep["id"]))
            seen.add(keep["id"])
            merges.append({"kept": keep["id"], "merged": [r["id"] for r in losers],
                           "evidence_consolidated": total})
        db.commit()
        return json.dumps({"status": "ok", "active_before": len(rows),
                           "active_after": len(rows) - sum(len(m["merged"]) for m in merges),
                           "merges": merges}, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})
    finally:
        db.close()


def decay_memory(half_life_days: int = 30, dry_run: bool = True,
                 grace_period_days: int = 7, floor: float = 0.30) -> str:
    """Duty 4 — confidence decay. new = old * exp(-ln2 * days_unreinforced / half_life).
    Always preview with dry_run=True first."""
    db = get_db()
    try:
        cur = db.cursor()
        cur.execute(
            """SELECT id, content, confidence, last_reinforced_at,
                      DATEDIFF(NOW(), COALESCE(last_reinforced_at, created_at)) AS days_unreinforced
               FROM incident_memory WHERE status='active' AND superseded_by IS NULL""")
        rows = cur.fetchall()
        decayed, skip_grace, skip_floor = [], 0, 0
        for r in rows:
            days = int(r["days_unreinforced"] or 0)
            old = float(r["confidence"])
            if days < grace_period_days:
                skip_grace += 1; continue
            if old < floor:
                skip_floor += 1; continue
            new = round(old * math.exp(-math.log(2) * days / half_life_days), 2)
            decayed.append({"id": r["id"], "days_unreinforced": days,
                            "old_confidence": old, "new_confidence": new,
                            "will_lose_routing": new < ROUTING_CONFIDENCE_GATE,
                            "snippet": r["content"][:60]})
        if not dry_run and decayed:
            for item in decayed:
                cur.execute("UPDATE incident_memory SET confidence=%s WHERE id=%s",
                            (item["new_confidence"], item["id"]))
            db.commit()
        return json.dumps({"dry_run": dry_run, "half_life_days": half_life_days,
                           "total_active": len(rows), "eligible_decayed": len(decayed),
                           "skipped_grace": skip_grace, "skipped_floor": skip_floor,
                           "patterns": decayed}, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})
    finally:
        db.close()


# ============================================================================
# ROUTING — substrate decides which model/budget runs (Gemini thinking budget)
# ============================================================================
ROUTE_SHORTCUT = {"path": "SHORTCUT", "thinking_budget": 0, "max_tool_rounds": 3}
ROUTE_EXPLORE = {"path": "EXPLORE", "thinking_budget": 512, "max_tool_rounds": 15}


def route_investigation(vector_matches, confidence_gate=None, similarity_gate=None) -> dict:
    """Scan Tier-5 matches; if any passes both gates, take the shortcut path
    (low thinking budget, few tool rounds). Otherwise explore."""
    cg = ROUTING_CONFIDENCE_GATE if confidence_gate is None else float(confidence_gate)
    sg = ROUTING_SIMILARITY_GATE if similarity_gate is None else float(similarity_gate)

    if not vector_matches:
        return {**ROUTE_EXPLORE, "reason": "no semantic-memory matches (cold start)",
                "matched_id": None}
    if isinstance(vector_matches, dict):
        vector_matches = [vector_matches]

    passing = [(float(m.get("confidence") or 0) * float(m.get("similarity") or 0),
                float(m.get("confidence") or 0), float(m.get("similarity") or 0), m)
               for m in vector_matches
               if float(m.get("confidence") or 0) >= cg and float(m.get("similarity") or 0) >= sg]
    if passing:
        passing.sort(key=lambda t: (t[0], t[2]), reverse=True)
        score, conf, sim, best = passing[0]
        return {**ROUTE_SHORTCUT, "matched_id": best.get("id"),
                "reason": (f"{len(passing)}/{len(vector_matches)} matches passed gates "
                           f"(conf≥{cg}, sim≥{sg}); chose mem {best.get('id')} "
                           f"(conf={conf:.2f}, sim={sim:.2f}) — substrate-driven shortcut")}
    return {**ROUTE_EXPLORE, "matched_id": None,
            "reason": f"scanned {len(vector_matches)} matches; none passed both gates"}


# ---- internal write helper (keeps imports lazy) ----------------------------
def _exec(sql: str, params: tuple) -> int:
    from lib.tidb import execute
    return execute(sql, params)
