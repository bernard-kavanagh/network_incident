"""Build (or load) a labelled evaluation set.

Each item is an incoming incident with known ground truth:
    {ticket_id, text, true_subcat}

Now: sampled from the seeded `tickets` (subcategory is the label), stratified
across subcategories and deterministic. Later: `load(path)` reads MasOrange's
real labelled incidents from JSON — same shape, no code change.
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.tidb import query  # noqa: E402


def build_eval_set(per_subcategory: int = 4, seed: int = 7) -> list:
    """Stratified sample of tickets across subcategories (deterministic)."""
    rng = random.Random(seed)
    subs = [r["subcategory"] for r in query(
        "SELECT DISTINCT subcategory FROM tickets WHERE subcategory IS NOT NULL")]
    items = []
    for sub in sorted(subs):
        rows = query(
            """SELECT ticket_id, summary, description, subcategory
               FROM tickets WHERE subcategory = %s ORDER BY ticket_id""", (sub,))
        for r in rng.sample(rows, min(per_subcategory, len(rows))):
            items.append({
                "ticket_id": r["ticket_id"],
                "text": f"{r['summary']}. {r['description'] or ''}".strip(),
                "true_subcat": r["subcategory"],
            })
    rng.shuffle(items)
    return items


def load(path: str) -> list:
    """Load a labelled set from JSON (list of {ticket_id?, text, true_subcat})."""
    return json.loads(Path(path).read_text())


def save(items: list, path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(items, indent=2, default=str))


if __name__ == "__main__":
    s = build_eval_set()
    print(f"Built {len(s)} labelled items across "
          f"{len(set(i['true_subcat'] for i in s))} subcategories")
    for i in s[:3]:
        print(" ", i["true_subcat"], "::", i["text"][:70])
