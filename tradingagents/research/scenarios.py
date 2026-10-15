"""Explicit, source-linked quarterly assumptions; never infer share count from rounded EPS."""

from __future__ import annotations

import base64
import copy
import hashlib
import math
import re
from datetime import date, datetime

from tradingagents.dataflows.taiwan.publications import TAIPEI

from .bundle import fingerprint


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Finite numeric input required")
    return float(value)


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if len(value) == 10:
        return parsed.replace(tzinfo=TAIPEI)
    if parsed.tzinfo is None:
        raise ValueError("Timestamp timezone required")
    return parsed


def known_date(value):
    return timestamp(value).astimezone(TAIPEI).date()


def next_quarters(as_of, count=4):
    parsed = date.fromisoformat(as_of)
    index = parsed.year * 4 + (parsed.month - 1) // 3
    return [f"{(index + i) // 4}-Q{(index + i) % 4 + 1}" for i in range(count)]


def normalize_quarter(current, previous=None):
    """Amounts only: EPS/ratios/share counts cannot be differenced as YTD amounts."""
    if current["basis"] == "single_quarter":
        return finite(current["value"])
    if current["basis"] != "ytd" or current["kind"] != "amount":
        raise ValueError("Unverified/unsupported financial period basis")
    year, quarter = current["period"].split("-Q")
    if quarter == "1":
        return finite(current["value"])
    if previous is None or previous["period"] != f"{year}-Q{int(quarter) - 1}":
        raise ValueError("Prior cumulative period required")
    for key in ("basis", "kind", "unit", "stock_id", "scope", "field", "version_set"):
        if current[key] != previous[key]:
            raise ValueError("Cumulative period scope/unit/version mismatch")
    return finite(current["value"]) - finite(previous["value"])


def check_assumption(item, bundle, low=None, high=None):
    if item.get("kind") != "assumption" or not item.get("rationale", "").strip():
        raise ValueError("Explicit assumption and rationale required")
    refs = item.get("evidence_ids", [])
    if not refs or len(refs) != len(set(refs)):
        raise ValueError("Distinct supporting evidence required")
    for key in refs:
        evidence = bundle["evidence"][key]
        if evidence["availability"] != "available" or evidence["value"] is None:
            raise ValueError("Unavailable assumption evidence")
        if (
            max(
                [
                    evidence["data_end"],
                    *evidence["publication_dates"],
                    *evidence["value_available_dates"],
                ]
            )
            > bundle["as_of"]
        ):
            raise ValueError("Future assumption evidence")
    value = finite(item["value"])
    if (low is not None and value < low) or (high is not None and value > high):
        raise ValueError("Assumption outside supported range")
    return value


def forecast(bundle, specification=None):
    """Current calendar quarter + next three, not the next four announced results."""
    if specification is None:
        return {
            "status": "unavailable",
            "reason": "Missing reviewed quarter basis, ownership, weighted shares and explicit scenarios",
            "quarters": [],
            "price_range": None,
        }
    spec = copy.deepcopy(specification)
    if spec["bundle_sha256"] != fingerprint(bundle) or spec["as_of"] != bundle["as_of"]:
        raise ValueError("Forecast bundle/date mismatch")
    basis = spec["basis_review"]
    if basis["status"] != "verified" or not basis.get("reviewer") or not basis.get("documents"):
        raise ValueError("Financial basis must be reviewed against original documents")
    for source in basis["documents"]:
        for field in ("published_at", "captured_at"):
            if known_date(source[field]) > date.fromisoformat(bundle["as_of"]):
                raise ValueError("Source was not available by analysis date")
        if not source.get("source_url", "").startswith("https://") or not re.fullmatch(
            r"[0-9a-f]{64}", source.get("sha256", "")
        ):
            raise ValueError("Archived original document reference required")
        content = base64.b64decode(source["content_base64"], validate=True)
        if hashlib.sha256(content).hexdigest() != source["sha256"]:
            raise ValueError("Original document hash mismatch")
    if not all(
        basis.get(k) for k in ("quarter_basis", "parent_ownership", "weighted_diluted_shares")
    ):
        raise ValueError("Quarter basis, ownership and weighted shares must be verified")
    facts = basis["reviewed_facts"]
    if facts["unit"] != "TWD_and_shares" or facts["stock_id"] != bundle["stock_id"]:
        raise ValueError("Reviewed financial units/company mismatch")
    denominator = finite(facts["weighted_diluted_shares"])
    if (
        denominator <= 0
        or abs(
            finite(facts["diluted_numerator_twd"]) / denominator - finite(facts["diluted_eps_twd"])
        )
        > 0.0051
    ):
        raise ValueError("Original diluted EPS numerator/denominator identity failed")
    if set(spec["scenarios"]) != {"bear", "base", "bull"}:
        raise ValueError("Exactly bear/base/bull scenarios required")
    # Include the whole next calendar year as well as the next four forecast quarters.
    expected = next_quarters(
        bundle["as_of"], 8 - (date.fromisoformat(bundle["as_of"]).month - 1) // 3
    )
    output = {}
    for name, scenario in spec["scenarios"].items():
        rows = scenario["quarters"]
        if [row["period"] for row in rows] != expected:
            raise ValueError("Four consecutive forecast quarters required")
        estimates = []
        for row in rows:
            revenue = check_assumption(row["revenue_twd"], bundle, 0)
            gross = check_assumption(row["gross_margin"], bundle, 0, 1)
            operating = check_assumption(row["operating_margin"], bundle, -1, 1)
            nonoperating = check_assumption(row["nonoperating_twd"], bundle)
            tax = check_assumption(row["tax_rate"], bundle, 0, 1)
            minority = check_assumption(row["noncontrolling_profit_twd"], bundle)
            diluted_adjustment = check_assumption(row["diluted_numerator_adjustment_twd"], bundle)
            shares = check_assumption(row["weighted_diluted_shares"], bundle, 1)
            if operating > gross:
                raise ValueError("Operating margin exceeds gross margin")
            pretax = revenue * operating + nonoperating
            net_parent = pretax * (1 - tax) - minority
            estimates.append(
                {
                    "period": row["period"],
                    "revenue_twd": revenue,
                    "gross_profit_twd": revenue * gross,
                    "operating_profit_twd": revenue * operating,
                    "pretax_profit_twd": pretax,
                    "parent_net_profit_twd": net_parent,
                    "diluted_numerator_adjustment_twd": diluted_adjustment,
                    "weighted_diluted_shares": shares,
                    "eps_twd": (net_parent + diluted_adjustment) / shares,
                }
            )
        eps = sum(r["eps_twd"] for r in estimates[:4])
        pe = check_assumption(scenario["valuation_pe"], bundle, 0)
        # Annual weighted shares may differ; sum of quarterly diluted EPS is an approximation.
        annual = {}
        for year in {r["period"][:4] for r in estimates}:
            group = [r for r in estimates if r["period"].startswith(year)]
            annual[year] = {
                "status": "four_quarter_eps_sum_approximation"
                if len(group) == 4
                else "partial_year_only",
                "eps_sum": sum(r["eps_twd"] for r in group),
                "quarters": len(group),
            }
        output[name] = {
            "quarters": estimates,
            "next_four_quarter_eps_sum": eps,
            "annual": annual,
            "assumed_pe": pe,
            "scenario_price_twd": eps * pe if eps > 0 else None,
            "sensitivity_per_one_pe_turn_twd": eps if eps > 0 else None,
            "sensitivity_operating_margin_plus_one_pp_eps": sum(
                r["revenue_twd"]
                * 0.01
                * (1 - check_assumption(row["tax_rate"], bundle, 0, 1))
                / r["weighted_diluted_shares"]
                for r, row in zip(estimates[:4], rows[:4], strict=True)
            ),
        }
    if not (
        output["bear"]["next_four_quarter_eps_sum"]
        <= output["base"]["next_four_quarter_eps_sum"]
        <= output["bull"]["next_four_quarter_eps_sum"]
    ):
        raise ValueError("Scenario EPS ordering is inconsistent")
    result = {
        "status": "ready",
        "as_of": bundle["as_of"],
        "bundle_sha256": fingerprint(bundle),
        "scenarios": output,
        "assumptions": spec,
        "kind": "conditional_model_not_verified_forecast",
        "ttm_pe": None,
        "limitations": [
            "Scenario PE is an assumption, not independently established fair value",
            "Quarterly EPS sums are not audited annual diluted EPS; partial-year estimates are never full-year EPS",
            "Loss tax benefits require explicit reviewed assumptions, not an inferred cash refund",
        ],
    }
    result["forecast_sha256"] = fingerprint(result)
    return result


def challenge(forecast_result, challenger, opponent, period, field, proposed, rationale, refs):
    """Numeric counterfactual tied to one specific opponent input; no fake agent execution."""
    if (
        challenger not in {"bull", "bear"}
        or opponent not in {"bull", "bear"}
        or challenger == opponent
    ):
        raise ValueError("Opposing roles required")
    spec = copy.deepcopy(forecast_result["assumptions"])
    rows = spec["scenarios"][opponent]["quarters"]
    row = next(r for r in rows if r["period"] == period)
    original = row[field]
    row[field] = {
        "value": finite(proposed),
        "kind": "assumption",
        "rationale": rationale,
        "evidence_ids": refs,
    }
    return {
        "status": "pending_recalculation_and_reply",
        "challenger": challenger,
        "opponent": opponent,
        "period": period,
        "field": field,
        "original_assumption": original,
        "proposed_assumption": row[field],
        "counterfactual_specification": spec,
        "required_reply": "accept/dispute/uncertain with input evidence and recalculated EPS impact",
        "independent_agent_execution": False,
    }
