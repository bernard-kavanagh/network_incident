"""Scoring + timing primitives for the eval harness. No external deps."""
from contextlib import contextmanager
from time import perf_counter


class Timer:
    """Collect wall-clock samples (ms) across many measured blocks."""

    def __init__(self):
        self.samples = []

    @contextmanager
    def measure(self):
        t = perf_counter()
        try:
            yield
        finally:
            self.samples.append((perf_counter() - t) * 1000.0)

    def stats(self) -> dict:
        s = sorted(self.samples)
        if not s:
            return {"n": 0}
        return {"n": len(s), "mean_ms": round(sum(s) / len(s), 1),
                "p50_ms": round(_pct(s, 50), 1), "p95_ms": round(_pct(s, 95), 1)}


def _pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def precision_at_k(retrieved_labels, true_label) -> float:
    """Fraction of retrieved items whose label matches the true label."""
    if not retrieved_labels:
        return 0.0
    return sum(1 for l in retrieved_labels if l == true_label) / len(retrieved_labels)


def reciprocal_rank(retrieved_labels, true_label) -> float:
    """1 / rank of the first matching label (0 if none match)."""
    for i, l in enumerate(retrieved_labels, start=1):
        if l == true_label:
            return 1.0 / i
    return 0.0


def mode_label(labels):
    """Most common label (ties broken by first-seen). None if empty."""
    counts = {}
    for l in labels:
        counts[l] = counts.get(l, 0) + 1
    return max(counts, key=counts.get) if counts else None


def rate(flags) -> float:
    """Fraction of truthy flags."""
    return round(sum(1 for f in flags if f) / len(flags), 3) if flags else 0.0
