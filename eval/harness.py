"""Run the labelled eval set through the substrate for ONE tuning config and
return a metrics record. No LLM — pure substrate (retrieval, routing, gates),
which is where the latency/quality/cost claims and the tunability live.

    python3.12 eval/harness.py            # one record at default tuning
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.tidb import hybrid_rerank, vector_search           # noqa: E402
from lib.memory import route_investigation                  # noqa: E402
from lib.tuning import TUNING, set_tuning, reset_tuning, snapshot  # noqa: E402
from eval.dataset import build_eval_set                      # noqa: E402
from eval.metrics import (Timer, precision_at_k, reciprocal_rank,  # noqa: E402
                          mode_label, rate)

_ACTIVE = "status = 'active' AND superseded_by IS NULL"


def run_one(eval_set=None, k: int = 10, alpha: float = 0.7,
            tuning_overrides: dict = None) -> dict:
    """Evaluate the substrate at the given tuning. Returns a metrics dict."""
    if tuning_overrides:
        set_tuning(**tuning_overrides)
    if eval_set is None:
        eval_set = build_eval_set()

    rerank_t, recall_t = Timer(), Timer()
    top1_hits, precisions, rrs = [], [], []
    shortcuts, escalations = [], []
    queries = 0

    for item in eval_set:
        text, true = item["text"], item["true_subcat"]
        self_id = item.get("ticket_id")

        # --- retrieval quality: hybrid rerank over tickets (exclude self) ---
        with rerank_t.measure():
            hits = hybrid_rerank(text, subcategory=None, top_n=k + 6, retrieve_n=k + 6, alpha=alpha)
        queries += 1
        hits = [h for h in hits if h.get("ticket_id") != self_id][:k]
        labels = [h["subcategory"] for h in hits]
        precisions.append(precision_at_k(labels, true))
        rrs.append(reciprocal_rank(labels, true))
        top1_hits.append(mode_label(labels) == true)   # kNN classification

        # --- routing outcome: semantic-memory match -> gate decision ---
        with recall_t.measure():
            mem = vector_search(text, "incident_memory", k=3, where=_ACTIVE)
        queries += 1
        route = route_investigation(mem)
        shortcuts.append(route["path"] == "SHORTCUT")

        # --- remediation outcome: runbook relevance floor -> apply vs escalate ---
        rb = vector_search(text, "runbook_memory", k=1, where=_ACTIVE)
        queries += 1
        top_rb_sim = float(rb[0]["similarity"]) if rb else 0.0
        escalations.append(top_rb_sim < TUNING.runbook_relevance_floor)

    n = len(eval_set)
    return {
        "n_items": n,
        "k": k,
        "alpha": alpha,
        "tuning": snapshot(),
        "quality": {
            "classification_top1_accuracy": round(rate(top1_hits), 3),
            f"rerank_precision_at_{k}": round(sum(precisions) / n, 3),
            "rerank_mrr": round(sum(rrs) / n, 3),
        },
        "outcomes": {
            "shortcut_rate": rate(shortcuts),
            "escalation_rate": rate(escalations),
        },
        "latency": {
            "rerank": rerank_t.stats(),
            "memory_recall": recall_t.stats(),
        },
        "cost_proxy": {
            "total_queries": queries,
            "queries_per_item": round(queries / n, 1),
            "local_embeddings": queries,   # each retrieval embeds locally (no API $)
            "console_request_units": None, # paste from TiDB Cloud console for the run window
        },
    }


if __name__ == "__main__":
    reset_tuning()
    rec = run_one()
    print(json.dumps(rec, indent=2, default=str))
