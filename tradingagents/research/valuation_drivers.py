"""Conditional valuation and industry transmission; missing drivers never imply earnings."""

from statistics import median

from .scenarios import finite, timestamp


def matrix(eps_values, pe_values):
    if not eps_values or not pe_values:
        raise ValueError("EPS and PE axes required")
    if any(finite(x) <= 0 for x in eps_values + pe_values):
        raise ValueError("Positive EPS/PE required")
    return {
        "label": "conditional_prices_not_fair_value",
        "eps": eps_values,
        "pe": pe_values,
        "prices": [[finite(eps) * finite(pe) for pe in pe_values] for eps in eps_values],
    }


def forward_history(rows, cutoff, min_samples=12):
    selected = []
    for row in rows:
        if row["basis"] != "forward_four_quarter_diluted" or row["horizon_months"] != 12:
            raise ValueError("Same forward diluted horizon required")
        if not row.get("source_sha256"):
            raise ValueError("Contemporaneous EPS forecast source required")
        if max(timestamp(row["eps_published_at"]), timestamp(row["eps_captured_at"])) > timestamp(
            row["price_at"]
        ):
            raise ValueError("Later forecast substituted for historical forward denominator")
        if timestamp(row["price_at"]) <= timestamp(cutoff) and finite(row["eps"]) > 0:
            selected.append(finite(row["price"]) / finite(row["eps"]))
    return {
        "status": "available" if len(selected) >= min_samples else "unavailable",
        "samples": len(selected),
        "median_forward_pe": median(selected) if len(selected) >= min_samples else None,
    }


def growth_adjusted(peers, cutoff, target_growth_pct):
    pegs = []
    for p in peers:
        if p["basis"] != "forward_four_quarter_diluted" or p["horizon_months"] != 12:
            raise ValueError("Peer forward horizon/basis mismatch")
        if not p.get("independent_business_peer") or not p.get("source_sha256"):
            raise ValueError("Independent peer and source required")
        if max(timestamp(p["published_at"]), timestamp(p["captured_at"])) > timestamp(cutoff):
            continue
        if finite(p["growth_pct"]) > 0 and finite(p["pe"]) > 0:
            pegs.append(p["pe"] / p["growth_pct"])
    if len(pegs) < 2 or finite(target_growth_pct) <= 0:
        return {
            "status": "unavailable",
            "reason": "Two comparable sourced forward EPS-growth peers required",
        }
    peg = median(pegs)
    return {
        "status": "conditional_reference",
        "median_peg": peg,
        "growth_adjusted_pe": peg * target_growth_pct,
        "limitation": "PEG assumes proportional growth valuation; ignores growth duration, risk and cash conversion",
    }


def event_effect(event, cutoff):
    proof = event.get("evidence", [])
    if not proof:
        return {"grade": "insufficient_evidence", "eps_delta": None}
    for x in proof:
        if not x.get("source_sha256") or not x.get("source_url"):
            raise ValueError("Event source proof required")
        if max(timestamp(x["published_at"]), timestamp(x["captured_at"])) > timestamp(cutoff):
            return {
                "grade": "insufficient_evidence",
                "eps_delta": None,
                "reason": "Future event evidence",
            }
    required = {
        "market_units",
        "penetration_delta",
        "company_share",
        "content_asp_twd",
        "cannibalized_revenue_twd",
        "incremental_operating_margin",
        "incremental_fixed_cost_twd",
        "tax_rate",
        "parent_profit_fraction",
        "diluted_shares",
    }
    inputs = event.get("inputs", {})
    if set(inputs) != required:
        return {
            "grade": "directional_only",
            "eps_delta": None,
            "missing": sorted(required - set(inputs)),
            "reason": "Do not equate industry benefit to incremental EPS",
        }
    for item in inputs.values():
        if not item.get("evidence_ids") or not set(item["evidence_ids"]) <= {
            x["id"] for x in proof
        }:
            raise ValueError("Each quantitative driver needs event evidence linkage")
        if item.get("kind") not in {"fact", "assumption"}:
            raise ValueError("Driver epistemic status required")
    v = {k: finite(x["value"]) for k, x in inputs.items()}
    if (
        min(v["market_units"], v["content_asp_twd"], v["cannibalized_revenue_twd"]) < 0
        or not -1 <= v["penetration_delta"] <= 1
    ):
        raise ValueError("Invalid industry volumes/prices/penetration")
    if v["diluted_shares"] <= 0 or any(
        not 0 <= v[k] <= 1 for k in ["company_share", "tax_rate", "parent_profit_fraction"]
    ):
        raise ValueError("Invalid industry transmission ratio/shares")
    revenue = (
        v["market_units"] * v["penetration_delta"] * v["company_share"] * v["content_asp_twd"]
        - v["cannibalized_revenue_twd"]
    )
    op = revenue * v["incremental_operating_margin"] - v["incremental_fixed_cost_twd"]
    return {
        "grade": "quantifiable_conditional",
        "revenue_delta_twd": revenue,
        "eps_delta": op * (1 - v["tax_rate"]) * v["parent_profit_fraction"] / v["diluted_shares"],
        "contains_assumptions": any(x["kind"] == "assumption" for x in inputs.values()),
        "limitation": "Fixed tax/ownership/shares and no non-operating changes; not an established causal effect",
    }
