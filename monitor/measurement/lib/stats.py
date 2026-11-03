"""Statistics for autocorrelated 1 Hz series: percentiles, moving-block bootstrap CIs.

CIs are 95 % percentile intervals from a moving-block bootstrap (Künsch 1989) with a fixed
seed, so the same raw files always give the same report. Differences between legs combine
half-widths in quadrature (independent legs). Replicate blocks combine with the larger of the
pooled within-leg half-width and the half-range between replicates, so two disagreeing
replicates widen the interval instead of averaging the disagreement away.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from typing import Any

DEFAULT_SEED = 20261102
DEFAULT_RESAMPLES = 1000


def mean(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def percentile(xs: Sequence[float], p: float) -> float | None:
    """Linear-interpolation percentile (numpy ``linear`` method)."""
    if not xs:
        return None
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p / 100.0
    lo = math.floor(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _auto_block(n: int) -> int:
    return max(2, int(round(n ** (1.0 / 3.0))))


def mean_ci(xs: Sequence[float], seed: int = DEFAULT_SEED, block: int | None = None,
            resamples: int = DEFAULT_RESAMPLES) -> dict[str, Any] | None:
    """Mean with a 95 % moving-block bootstrap CI; ``lo``/``hi`` None for n < 2."""
    n = len(xs)
    if n == 0:
        return None
    m = sum(xs) / n
    if n < 2:
        return {"mean": m, "lo": None, "hi": None, "n": n, "method": "single_value",
                "block": None}
    b = min(block or _auto_block(n), n)
    rng = random.Random(seed)
    starts = n - b + 1
    nblocks = math.ceil(n / b)
    means = []
    for _ in range(resamples):
        total = 0.0
        count = 0
        for _ in range(nblocks):
            s = rng.randrange(starts)
            seg = xs[s:s + b]
            total += sum(seg)
            count += len(seg)
        means.append(total / count)
    return {"mean": m, "lo": percentile(means, 2.5), "hi": percentile(means, 97.5), "n": n,
            "method": "moving_block_bootstrap", "block": b}


def half_width(ci: dict[str, Any]) -> float:
    if ci.get("lo") is None or ci.get("hi") is None:
        return 0.0
    return (ci["hi"] - ci["lo"]) / 2.0


def linear_combination(terms: Sequence[tuple[float, dict[str, Any] | None]]) -> dict[str, Any] | None:
    """Σ coef·mean with half-widths combined in quadrature; None if any term is missing."""
    if not terms or any(ci is None or ci.get("mean") is None for _, ci in terms):
        return None
    m = sum(c * ci["mean"] for c, ci in terms)
    hw = math.sqrt(sum((c * half_width(ci)) ** 2 for c, ci in terms))
    known = all(ci.get("lo") is not None for _, ci in terms)
    return {"mean": m, "lo": m - hw if known else None, "hi": m + hw if known else None}


def diff_ci(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any] | None:
    return linear_combination([(1.0, a), (-1.0, b)])


def ci_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return not (a["hi"] < b["lo"] or b["hi"] < a["lo"])


def combine_replicates(reps: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    reps = [r for r in reps if r is not None and r.get("mean") is not None]
    if not reps:
        return None
    n = len(reps)
    m = sum(r["mean"] for r in reps) / n
    within = math.sqrt(sum(half_width(r) ** 2 for r in reps)) / n
    spread = (max(r["mean"] for r in reps) - min(r["mean"] for r in reps)) / 2.0
    hw = max(within, spread)
    known = n > 1 or reps[0].get("lo") is not None
    return {"mean": m, "lo": m - hw if known else None, "hi": m + hw if known else None, "n": n,
            "method": "replicates:max(pooled_within,half_range)"}
