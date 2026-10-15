"""Program-calculated independent EPS cases and exact Shapley arithmetic attribution."""

from itertools import combinations
from math import factorial

from .scenarios import finite

FIELDS = (
    "revenue_twd",
    "op_margin",
    "nonoperating",
    "tax_rate",
    "minority_ratio",
    "diluted_shares",
    "dilution_adjustment",
)


def eps(case):
    v = {k: finite(case[k]) for k in FIELDS}
    if v["diluted_shares"] <= 0 or not all(0 <= v[k] <= 1 for k in ["tax_rate", "minority_ratio"]):
        raise ValueError("Invalid EPS case")
    return (
        (v["revenue_twd"] * v["op_margin"] + v["nonoperating"])
        * (1 - v["tax_rate"])
        * (1 - v["minority_ratio"])
        + v["dilution_adjustment"]
    ) / v["diluted_shares"]


def bridge(original, revised):
    n = len(FIELDS)
    contributions = {}
    for field in FIELDS:
        others = [k for k in FIELDS if k != field]
        total = 0
        for size in range(n):
            weight = factorial(size) * factorial(n - size - 1) / factorial(n)
            for keys in combinations(others, size):
                before = {**original, **{k: revised[k] for k in keys}}
                total += weight * (eps({**before, field: revised[field]}) - eps(before))
        contributions[field] = total
    return {
        "baseline_eps": eps(original),
        "revised_eps": eps(revised),
        "delta": eps(revised) - eps(original),
        "contributions": contributions,
        "interpretation": "Exact mathematical Shapley attribution, not causal evidence or probability",
    }
