"""Threshold sweep — the tunability demonstration.

Varies ONE knob at a time (others at default) over the SAME eval set, so the
causal link between a gate and an outcome is unambiguous:

  routing_similarity_gate ↑   → shortcut_rate ↓        (fewer cheap shortcuts)
  runbook_relevance_floor ↑   → escalation_rate ↑      (more human escalations)
  alpha (vector weight)       → rerank precision / MRR (retrieval quality)

This is the artefact that shows engineers *steering* the cognitive foundation.
Writes JSON + Markdown to eval/out/.

    python3.12 eval/sweep.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.tuning import reset_tuning                 # noqa: E402
from eval.dataset import build_eval_set             # noqa: E402
from eval.harness import run_one                    # noqa: E402

OUT = Path(__file__).resolve().parent / "out"

SWEEPS = [
    {"name": "routing_similarity_gate", "knob": "routing_similarity_gate",
     "values": [0.45, 0.55, 0.65], "watch": "shortcut_rate", "where": "outcomes"},
    {"name": "runbook_relevance_floor", "knob": "runbook_relevance_floor",
     "values": [0.35, 0.45, 0.55], "watch": "escalation_rate", "where": "outcomes"},
    {"name": "alpha (vector vs full-text)", "knob": "__alpha__",
     "values": [0.3, 0.7, 1.0], "watch": "rerank_mrr", "where": "quality"},
]


def _run(eval_set, knob, value, k=10):
    reset_tuning()
    if knob == "__alpha__":
        return run_one(eval_set=eval_set, k=k, alpha=value)
    return run_one(eval_set=eval_set, k=k, tuning_overrides={knob: value})


def main():
    eval_set = build_eval_set(per_subcategory=3)
    print(f"Eval set: {len(eval_set)} labelled items\n")
    report = {"n_items": len(eval_set), "sweeps": []}

    for sweep in SWEEPS:
        rows = []
        for v in sweep["values"]:
            rec = _run(eval_set, sweep["knob"], v)
            rows.append({
                "value": v,
                "shortcut_rate": rec["outcomes"]["shortcut_rate"],
                "escalation_rate": rec["outcomes"]["escalation_rate"],
                "top1_acc": rec["quality"]["classification_top1_accuracy"],
                "precision@k": rec["quality"][f"rerank_precision_at_{rec['k']}"],
                "mrr": rec["quality"]["rerank_mrr"],
                "rerank_p95_ms": rec["latency"]["rerank"].get("p95_ms"),
            })
        report["sweeps"].append({"name": sweep["name"], "watch": sweep["watch"], "rows": rows})
        _print_table(sweep, rows)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sweep.json").write_text(json.dumps(report, indent=2, default=str))
    (OUT / "sweep.md").write_text(_markdown(report))
    print(f"\nWrote {OUT/'sweep.json'} and {OUT/'sweep.md'}")


def _print_table(sweep, rows):
    print(f"── Sweep: {sweep['name']}  (watch → {sweep['watch']}) ──")
    print(f"  {'value':>7} | {'shortcut':>8} | {'escalate':>8} | {'top1':>5} | "
          f"{'prec@k':>6} | {'mrr':>5} | {'p95ms':>6}")
    for r in rows:
        print(f"  {r['value']:>7} | {r['shortcut_rate']:>8} | {r['escalation_rate']:>8} | "
              f"{r['top1_acc']:>5} | {r['precision@k']:>6} | {r['mrr']:>5} | {str(r['rerank_p95_ms']):>6}")
    print()


def _markdown(report) -> str:
    out = [f"# Tunability Sweep — {report['n_items']} labelled items\n"]
    for s in report["sweeps"]:
        out.append(f"## {s['name']}  (watch → **{s['watch']}**)\n")
        out.append("| value | shortcut_rate | escalation_rate | top1_acc | precision@k | mrr | rerank_p95_ms |")
        out.append("|---|---|---|---|---|---|---|")
        for r in s["rows"]:
            out.append(f"| {r['value']} | {r['shortcut_rate']} | {r['escalation_rate']} | "
                       f"{r['top1_acc']} | {r['precision@k']} | {r['mrr']} | {r['rerank_p95_ms']} |")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    main()
