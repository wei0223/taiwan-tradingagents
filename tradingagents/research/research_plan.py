"""Source-bound research context, capital, industry and next-four-quarter integration."""

import base64
import calendar
import hashlib

from . import capital_model, forward_model, industry_models, knowledge
from .market_observation import latest
from .revenue_projection import project
from .review import _packet


def build(spec, stock, as_of, documents, evidence, snapshot=None):
    if not spec:
        return {
            "status": "unavailable_explicit_plan_required",
            "context": None,
            "completion_claim_allowed": False,
        }
    ctx = knowledge.context(spec["context"], stock, as_of)
    # New research sources must bind the supplied raw bytes, not just a hash label.
    for key, doc in documents.items():
        raw = base64.b64decode(doc["content_base64"], validate=True)
        if doc.get("stock_id") != stock or hashlib.sha256(raw).hexdigest() != doc["sha256"]:
            raise ValueError("Research source company/hash mismatch: " + key)
    audit = knowledge.completeness(
        ctx, documents, spec.get("inventory", []), spec.get("coverage", [])
    )
    eligible = set(audit["eligible_sources"])
    for event in (spec.get("capital") or {}).get("events", []):
        if event["source_id"] not in eligible:
            raise ValueError("Capital event source unavailable at cutoff")
    capital = (
        capital_model.build(**spec["capital"])
        if spec.get("capital")
        else {"status": "unavailable_reviewed_capital_model_required"}
    )
    operating = industry_models.reviewed(
        spec["industry_kind"], spec.get("operating_inputs", {}), evidence
    )
    forward = forward_model.build(
        as_of, spec.get("forward_scenarios"), evidence, audit["announced_earnings_periods"]
    )
    capital_unresolved = capital["status"] in {
        "unavailable_recognition",
        "unavailable_reviewed_capital_model_required",
    }
    if (
        capital_unresolved
        and spec.get("forward_scenarios")
        and any(
            c["inputs"]["diluted_weighted_shares"]["kind"] not in {"assumption", "unknown"}
            for rows in spec["forward_scenarios"].values()
            for c in rows
        )
    ):
        raise ValueError("Unresolved future shares cannot be labelled fact")
    capital_models = {}
    for period, capital_spec in spec.get("quarterly_capital", {}).items():
        capital_spec = dict(capital_spec)
        opening_proof = capital_spec.pop("opening_source_packet", None)
        opening_verified = False
        if opening_proof:
            from datetime import date, timedelta

            amounts = _packet(
                opening_proof,
                stock,
                documents,
                knowledge.exact(ctx["cutoff_at"]),
                ("issued_shares", "treasury_shares"),
                allowed_scopes=("issuer",),
            )
            opening_verified = bool(
                opening_proof.get("basis") == "balance"
                and opening_proof.get("as_of_date")
                == (date.fromisoformat(capital_spec["start"]) - timedelta(days=1)).isoformat()
                and amounts["issued_shares"] == capital_spec["opening_issued"]
                and amounts["treasury_shares"] == capital_spec["opening_treasury"]
            )
        for event in capital_spec.get("events", []):
            if event["source_id"] not in eligible:
                raise ValueError("Quarterly capital source unavailable")
        capital_models[period] = capital_model.build(**capital_spec)
        capital_models[period]["opening_verified"] = opening_verified
    matches = []
    for rows in forward["scenarios"].values():
        for case in rows["quarters"]:
            cap = capital_models.get(case["period"], {})
            quarter_end = int(case["period"][-1]) * 3
            period_end = cap.get("period_end", "")
            year = int(case["period"][:4])
            expected_start = f"{year}-{quarter_end - 2:02d}-01"
            expected_end = (
                f"{year}-{quarter_end:02d}-{calendar.monthrange(year, quarter_end)[1]:02d}"
            )
            inputs = case["inputs"]
            matches.append(
                bool(
                    cap.get("diluted_weighted_shares") is not None
                    and cap.get("opening_verified")
                    and inputs["diluted_weighted_shares"]["value"] is not None
                    and inputs["diluted_adjustment_twd"]["value"] is not None
                    and period_end == expected_end
                    and cap.get("period_start") == expected_start
                    and abs(
                        cap["diluted_weighted_shares"] - inputs["diluted_weighted_shares"]["value"]
                    )
                    < 0.01
                    and abs(
                        cap["diluted_numerator_adjustment_twd"]
                        - inputs["diluted_adjustment_twd"]["value"]
                    )
                    < 0.01
                )
            )
    forward["capital_reconciled"] = bool(matches) and all(matches)
    forward["quarterly_capital"] = capital_models
    forward["usable_as_verified_valuation_denominator"] = False
    operating_quarters = {}
    for scenario, inputs_list in spec.get("operating_scenarios", {}).items():
        quarters = forward.get("scenarios", {}).get(scenario, {}).get("quarters")
        if quarters is None or [r["period"] for r in inputs_list] != forward["target_quarters"]:
            raise ValueError("Operating model needs matching four-quarter financial scenario")
        results = []
        for operating_input, case in zip(inputs_list, quarters, strict=True):
            result = industry_models.reviewed(
                spec["industry_kind"], operating_input["inputs"], evidence
            )
            if result["status"] != "conditional_operating_equation":
                raise ValueError("Incomplete operating model cannot establish industry-linked EPS")
            financial = case["calculation"]
            if any(financial[k] is None for k in ("revenue_twd", "cogs_twd")):
                raise ValueError("Incomplete financial bridge cannot reconcile operating model")
            if any(
                abs(result[k] - financial[k]) > max(1, abs(financial[k]) * 1e-10)
                for k in ("revenue_twd", "cogs_twd")
            ):
                raise ValueError("Operating revenue/COGS differs from earnings waterfall")
            if spec["industry_kind"] == "odm":
                bridge = operating_input.get("nonoperating_before_model_financing_twd")
                if (
                    not bridge
                    or not bridge.get("rationale")
                    or bridge.get("kind") != "assumption"
                    or any(r not in evidence for r in bridge.get("evidence_ids", []))
                    or not bridge.get("evidence_ids")
                ):
                    raise ValueError("Separate source-anchored pre-financing assumption required")
                if (
                    abs(
                        bridge["value"]
                        - result["detail"]["financing_cost_twd"]
                        - case["inputs"]["nonoperating_twd"]["value"]
                    )
                    > 1
                ):
                    raise ValueError("ODM financing cost must reach nonoperating income once")
            results.append(result)
        operating_quarters[scenario] = results
    forward["industry_linked_arithmetic"] = bool(operating_quarters) and set(
        operating_quarters
    ) == set(forward["scenarios"])
    forward["operating_quarters"] = operating_quarters
    forward["model_kind"] = (
        "industry_linked_conditional"
        if forward["industry_linked_arithmetic"]
        else "top_down_revenue_and_accounting_sensitivity_not_industry_causal_forecast"
    )
    comparisons = spec.get("expectations", [])
    if any(p.get("source_id") not in eligible for p in comparisons):
        raise ValueError("Expectation source unavailable at cutoff")
    comparison = forward_model.expectations(forward, comparisons, stock)
    reverse = []
    if forward["status"] == "conditional_four_quarter_models":
        for pe in spec.get("reverse_pe", []):
            reverse.append(
                forward_model.reverse(
                    spec["price_twd"], pe, forward["scenarios"]["base"]["quarters"]
                )
            )
    actuals = []
    for row in spec.get("reported_actuals", []):
        if row.get("source_id") not in eligible or any(
            ref not in evidence for ref in row["values"].values()
        ):
            raise ValueError("Reported actual needs eligible source-bound numeric evidence")
        actuals.append(
            {
                **row,
                "values": {k: evidence[ref] for k, ref in row["values"].items()},
                "ex_ante_performance": False,
            }
        )
    from .model_disclosure import describe, parameters, sensitivity

    reference = spec.get("per_share_reference")
    if reference and reference.get("source_id") not in eligible:
        raise ValueError("Per-share reference source unavailable")
    if reference:
        packet = reference.get("source_packet")
        if not packet or reference["source_id"] not in packet["document_ids"]:
            raise ValueError("Per-share reference needs the reviewed issuer capital packet")
        amounts = _packet(
            packet,
            stock,
            documents,
            knowledge.exact(ctx["cutoff_at"]),
            ("issued_shares",),
            allowed_scopes=("issuer",),
        )
        if amounts["issued_shares"] != evidence.get(reference["evidence_id"]):
            raise ValueError("Per-share reference differs from reviewed capital original")
    disclosures = describe(forward, reference, evidence)
    parameter_table = parameters(
        spec["industry_kind"],
        spec.get("parameter_register", {}),
        documents,
        eligible,
        industry_models.FIELDS.get(spec["industry_kind"], ()),
    )
    for row in parameter_table["parameters"]:
        if row.get("kind") == "fact" and (
            row["numeric_evidence_id"] not in evidence
            or row["value"] != evidence[row["numeric_evidence_id"]]
        ):
            raise ValueError("Parameter fact differs from bound evidence")
    illustrative = forward_model.build(
        as_of,
        spec.get("illustrative_profit_scenarios"),
        evidence,
        audit["announced_earnings_periods"],
    )
    illustrative_disclosure = describe(illustrative, reference, evidence)
    illustrative_sensitivities = {
        name: [
            {
                "period": c["period"],
                **sensitivity(c, spec.get("sensitivity", {}).get(c["period"], {})),
            }
            for c in scenario["quarters"]
        ]
        for name, scenario in illustrative.get("scenarios", {}).items()
    }
    if snapshot:
        price_rows = snapshot.get("datasets", {}).get("TaiwanStockPrice", {}).get("records", [])
        if price_rows:
            priced = max(price_rows, key=lambda r: r["data_date"])
            for total in illustrative_disclosure["four_quarter_reference_totals"].values():
                quotient = total["four_quarter_parent_profit_per_current_issued_share_twd"]
                total["price_reference"] = {
                    "date": priced["data_date"],
                    "close_twd": priced["values"]["close"],
                    "raw_sha256": priced.get("raw_sha256"),
                }
                total["conditional_market_cap_to_parent_profit_multiple"] = (
                    priced["values"]["close"] / quotient
                    if quotient is not None and quotient > 0
                    else None
                )
                total["not_forward_pe"] = True
    illustrative_disclosure["label"] = (
        "historical_ratio_held_constant_illustration_not_expected_forecast"
    )
    sensitivities = {}
    for name, scenario in forward.get("scenarios", {}).items():
        sensitivities[name] = [
            {
                "period": c["period"],
                **sensitivity(c, spec.get("sensitivity", {}).get(c["period"], {})),
            }
            for c in scenario["quarters"]
        ]
    return {
        "status": "reviewed_with_gaps"
        if audit["status"] == "incomplete"
        else "reviewed_declared_coverage",
        "context": ctx,
        "completeness": audit,
        "capital": capital,
        "financial_output_disclosure": disclosures,
        "illustrative_profit_disclosure": illustrative_disclosure,
        "illustrative_sensitivity": illustrative_sensitivities,
        "parameter_register": parameter_table,
        "sensitivity_analysis": sensitivities,
        "formal_valuation_allowed": audit.get("formal_valuation_allowed", False)
        and forward["capital_reconciled"],
        "operating_model": operating,
        "forward": forward,
        "expectations": comparison,
        "reverse": reverse,
        "revenue_projection": project(snapshot) if snapshot else {"status": "unavailable"},
        "latest_market_observation": latest(snapshot, ctx)
        if snapshot
        else {"status": "unavailable"},
        "reported_actuals": actuals,
        "completion_claim_allowed": False,
        "prospective_performance": False,
        "reason": "Conditional models do not establish data completeness, calibrated probabilities or investment value",
    }
