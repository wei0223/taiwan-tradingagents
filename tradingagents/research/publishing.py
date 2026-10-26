"""Independent publication gates; schema validity never certifies investment value."""

import json
from datetime import datetime, timedelta

from tradingagents.dataflows.taiwan.calendar import month_sessions, verify_calendar
from tradingagents.dataflows.taiwan.publications import TAIPEI

from .scenarios import finite, timestamp


def market_gate(snapshot, cutoff):
    """Require an official calendar through the last completed settlement window.

    A report before 16:30 Taipei uses the previous calendar day. No weekday-only
    fallback, inferred emergency closure, or individual stock row defines sessions.
    """
    instant = timestamp(cutoff)
    local = instant.astimezone(TAIPEI)
    end = local.date() - timedelta(days=int((local.hour, local.minute) < (16, 30)))
    cal = snapshot.get("market_calendar", {})
    reasons, expected = [], None
    try:
        exchange = snapshot["instrument"]["exchange"]
        sessions = verify_calendar(cal, exchange)
        if cal["status"] != "ok":
            raise ValueError("calendar_not_current")
        if any(timestamp(a["retrieved_at"]) > instant for a in cal["archives"]):
            raise ValueError("calendar_captured_after_cutoff")
        if cal["end"] < end.isoformat():
            # prepare bounds selected sessions to yesterday. A freshly captured
            # official current-month response can establish today's boundary too;
            # never extend a stale archive or infer a session from a stock row.
            month = end.strftime("%Y%m01")
            query_month = month if exchange == "twse" else end.strftime("%Y/%m/01")
            fresh = [
                a
                for a in cal["archives"]
                if a["query"]["date"] == query_month
                and timestamp(a["retrieved_at"]).astimezone(TAIPEI).date() >= end
            ]
            if cal["end"][:7] != end.isoformat()[:7] or len(fresh) != 1:
                raise ValueError("calendar_not_current")
            recovered = month_sessions(
                json.loads(fresh[0]["raw_response_utf8"].lstrip("\ufeff")),
                exchange,
                datetime.strptime(month, "%Y%m%d").date(),
            )
            sessions = sorted(set(sessions) | {d.isoformat() for d in recovered if d <= end})
        sessions = [d for d in sessions if d <= end.isoformat()]
        expected = max(sessions)
    except (ValueError, KeyError, TypeError):
        reasons.append("official_calendar_coverage_unverified")
        sessions = []

    dates, groups = {}, {}
    all_rows = {}
    for name, payload in (
        ("ohlcv", snapshot.get("datasets", {}).get("TaiwanStockPrice", {})),
        ("adjusted", snapshot.get("extensions", {}).get("TaiwanStockPriceAdj", {})),
        (
            "institutions",
            snapshot.get("datasets", {}).get("TaiwanStockInstitutionalInvestorsBuySell", {}),
        ),
    ):
        rows = payload.get("records", [])
        all_rows[name] = rows
        dates[name] = max((r["data_date"] for r in rows), default=None)
        if expected is None or dates[name] != expected:
            reasons.append(name + "_not_at_expected_session")
        if any(timestamp(r["retrieved_at"]) > instant for r in rows):
            reasons.append(name + "_captured_after_cutoff")
    names = {
        "foreign": {"Foreign_Investor", "Foreign_Dealer_Self"},
        "investment_trust": {"Investment_Trust"},
        "dealer": {"Dealer_self", "Dealer_Hedging", "Dealer"},
    }
    for group, aliases in names.items():
        by_day = {}
        for r in all_rows["institutions"]:
            by_day.setdefault(r["data_date"], set()).add(r["values"].get("name"))

        def complete(day, categories, group=group, aliases=aliases):
            if group == "foreign":
                required = (
                    {"Foreign_Investor", "Foreign_Dealer_Self"}
                    if day >= "2017-12-18"
                    else {"Foreign_Investor"}
                )
                return required <= categories
            if group == "dealer":
                return (
                    "Dealer" in categories and not categories & {"Dealer_self", "Dealer_Hedging"}
                ) or (
                    "Dealer" not in categories and {"Dealer_self", "Dealer_Hedging"} <= categories
                )
            return bool(categories & aliases)

        seen = {day for day, categories in by_day.items() if complete(day, categories)}
        groups[group] = {
            "last_date": max(seen, default=None),
            "missing_sessions": sorted(set(sessions[-20:]) - seen),
        }
        if groups[group]["last_date"] != expected or groups[group]["missing_sessions"]:
            reasons.append(group + "_incomplete")
    for name in ("ohlcv", "adjusted"):
        seen = {r["data_date"] for r in all_rows[name]}
        if set(sessions[-20:]) - seen:
            reasons.append(name + "_missing_signal_sessions")
    technical_date = snapshot.get("technical", {}).get("data_date")
    if technical_date != dates["ohlcv"]:
        reasons.append("technical_date_mismatch")
    return {
        "status": "FAIL" if reasons else "PASS",
        "freshness": "STALE"
        if expected and any(d != expected for d in dates.values())
        else ("UNVERIFIED" if reasons else "CURRENT"),
        "cutoff_at": cutoff,
        "calendar_required_through": end.isoformat(),
        "expected_last_session": expected,
        "last_dates": dates,
        "institution_groups": groups,
        "technical_date": technical_date,
        "price_basis": {"ohlcv": "unadjusted", "adjusted": "vendor_adjusted_not_total_return"},
        "signal_basis": "vendor_adjusted_for_existing_signal_raw_technical_display_separate_never_combine",
        "current_trade_conclusion_allowed": not reasons,
        "reasons": sorted(set(reasons)),
    }


def parameter_register(forward, metadata, documents, cutoff):
    """Metadata is per scenario / quarter / driver, never inherited silently.

    Inferred future assumptions are permitted. They need an anchor, rationale,
    observation date, uncertainty and review; future actuals are not required.
    Legacy arithmetic without these fields remains a stress illustration.
    """
    from tradingagents.dataflows.taiwan.periods import superseded_documents

    stock = next((d.get("stock_id") for d in documents.values()), None)
    superseded = superseded_documents(documents, stock, cutoff)
    result = []
    for scenario, model in forward.get("scenarios", {}).items():
        for case in model["quarters"]:
            parameters, problems = [], []
            for key, item in case["inputs"].items():
                meta = metadata.get(scenario, {}).get(case["period"], {}).get(key, {})
                kind = meta.get("assumption_type", "stress_only")
                if (
                    key == "nonoperating_twd"
                    and item.get("value") == 0
                    and item.get("kind") != "fact"
                ):
                    kind = "stress_only"
                source = meta.get("source", [])
                if isinstance(source, str):
                    source = [source]
                valid = bool(
                    kind in {"official", "inferred"}
                    and item.get("value") is not None
                    and source
                    and all(s in documents for s in source)
                    and meta.get("observed_at")
                    and meta.get("uncertainty")
                    and meta.get("validity_status") == "reviewed"
                    and meta.get("rationale")
                    and meta.get("reviewed_by")
                )
                try:
                    if meta.get("observed_at") and timestamp(meta["observed_at"]) > timestamp(
                        cutoff
                    ):
                        valid = False
                    for s in source:
                        doc = documents[s]
                        if s in superseded:
                            valid = False
                        if timestamp(doc["captured_at"]) > timestamp(cutoff):
                            valid = False
                        if doc.get("published_at") and timestamp(doc["published_at"]) > timestamp(
                            cutoff
                        ):
                            valid = False
                except (KeyError, ValueError, TypeError):
                    valid = False
                if kind == "official" and item.get("kind") != "fact":
                    valid = False
                if item.get("kind") == "unknown":
                    valid = False
                if not valid:
                    problems.append(key)
                parameters.append(
                    {
                        "parameter_name": key,
                        "value": item.get("value"),
                        "source": source,
                        "evidence_ids": item.get("evidence_ids", []),
                        "observed_at": meta.get("observed_at"),
                        "assumption_type": kind,
                        "validity_status": "reviewed" if valid else "unverified",
                        "uncertainty": meta.get("uncertainty"),
                        "rationale": meta.get("rationale", item.get("rationale")),
                        "explicit_zero": item.get("value") == 0,
                    }
                )
            calc = case["calculation"]
            eps = calc.get("eps_twd")
            if any(item.get("value") is None for item in case["inputs"].values()):
                eps = None  # Never display a stale cached quotient after a driver is cleared.
            result.append(
                {
                    "scenario": scenario,
                    "period": case["period"],
                    "parameters": parameters,
                    "calculation": calc,
                    "unverified_parameters": problems,
                    "formal_eps_status": "BLOCKED" if problems or eps is None else "PASS",
                    "eps": {
                        "type": "conditional_eps" if eps is not None else "unknown",
                        "value": eps,
                        "denominator_type": "scenario_weighted_diluted_shares_not_verified_actual",
                    },
                    "official_actual_eps": {"type": "actual_diluted_eps", "value": None},
                    "presentation_tier": "stress_only"
                    if problems
                    else "reviewed_conditional_forecast",
                }
            )
    return result


def pending_events(plan, spec):
    missing = set(plan.get("completeness", {}).get("missing_material_events", []))
    inventory = {r.get("id"): r for r in spec.get("inventory", [])}
    inventory.update(
        {
            r["id"]: r
            for r in plan.get("completeness", {})
            .get("official_index_reconciliation", {})
            .get("unprocessed_events", [])
        }
    )
    out = []
    for key in sorted(missing):
        parts = key.split("|", 4)
        row = inventory.get(key, {})
        code = parts[3] if len(parts) == 5 else None
        out.append(
            {
                "source_id": key,
                "published_at": row.get("published_at"),
                "original_date": parts[1] if len(parts) == 5 else None,
                "original_time": parts[2] if len(parts) == 5 else None,
                "category": row.get("category", row.get("categories", code or "unknown")),
                "source_url": parts[4] if len(parts) == 5 else None,
                "affects_financial_conclusion": True,
                "impact_status": "unreviewed_assume_material",
                "disposition": "pending",
                "exclusion_reason": None,
            }
        )
    return out


def assess(snapshot, result, inputs=None, documents=None):
    inputs, documents = inputs or {}, documents or {}
    plan = result.get("extension", {}).get("research_plan", {})
    spec = inputs.get("research_plan", {})
    cutoff = (plan.get("context") or {}).get("cutoff_at") or snapshot["as_of"] + "T23:59:59+08:00"
    market = market_gate(snapshot, cutoff)
    completeness = plan.get("completeness", {})
    pending = pending_events(plan, spec)
    registry = result.get("official_source_registry", {})
    families = registry.get("families", {})
    coverage_ok = bool(completeness) and not completeness.get("coverage_gaps") and not pending
    # The source registry's declared scope must also be complete; schema pass is irrelevant.
    coverage_ok = coverage_ok and registry.get("current_research", {}).get(
        "full_current_research_ready", False
    )
    facts = registry.get("current_facts", {})
    original = facts.get("source_matched_previously_reviewed_financial_numbers", False)
    revisions = (
        coverage_ok and bool(families) and all(not f.get("pending_keys") for f in families.values())
    )
    capital = plan.get("forward", {}).get("capital_reconciled", False)
    rows = parameter_register(
        plan.get("forward", {}), inputs.get("forward_parameter_metadata", {}), documents, cutoff
    )
    # A known reviewed Q2 remains an available actual even when Q3 publication
    # history is unresolved. Index completeness is an independent gate.
    earnings = result.get("review", {}).get("axes", {}).get("accounting_earnings", {}).get("current", {}).get("eps_twd") is not None
    earnings = earnings or bool(completeness.get("announced_earnings_periods"))
    gates = {
        "vendor_schema_valid": "PASS" if result["source_audit"]["status"] == "passed" else "FAIL",
        "official_index_covered": "PASS" if coverage_ok else "FAIL",
        "original_document_matched": "PASS" if original else "FAIL",
        "revisions_resolved": "PASS" if revisions else "FAIL",
        "earnings_available": "PASS" if earnings else "FAIL",
        "capital_events_resolved": "PASS" if capital else "FAIL",
        "market_fresh": market["status"],
        "forward_parameters_reviewed": "PASS"
        if rows and all(r["formal_eps_status"] == "PASS" for r in rows)
        else "FAIL",
    }
    ready = all(v == "PASS" for v in gates.values())
    forward = plan.get("forward", {})
    base_eps = forward.get("scenarios", {}).get("base", {}).get("eps_sum_twd")
    raw = snapshot.get("datasets", {}).get("TaiwanStockPrice", {}).get("records", [])
    last = max(raw, key=lambda r: r["data_date"]) if raw else None
    price = last["values"]["close"] if last else None
    required = []
    for pe in spec.get("reverse_pe", []):
        pe = finite(pe)
        if price is not None and pe > 0:
            required.append(
                {
                    "assumed_pe": pe,
                    "required_four_quarter_eps_sum": price / pe,
                    "type": "profit_hurdle_equation_not_market_consensus",
                }
            )
    illustration = (
        plan.get("illustrative_profit_disclosure", {})
        .get("four_quarter_reference_totals", {})
        .get("base", {})
    )
    implied = illustration.get("conditional_market_cap_to_parent_profit_multiple")
    return {
        "schema_version": 1,
        "gates": gates,
        "publishing_status": "PASS"
        if ready
        else ("PARTIAL" if gates["vendor_schema_valid"] == "PASS" else "BLOCKED"),
        "publication_scope": "current_complete_research"
        if ready
        else "historical_or_partial_research_only",
        # Passing denominator gates can establish a PE ratio, not an exit PE or
        # fair price. No independent exit-multiple proof is supplied by this API.
        "full_valuation_status": "BLOCKED",
        "latest_market_gate": market,
        "pending_announcements": pending,
        "forward_parameter_register": rows,
        "valuation": {
            "price_date": last["data_date"] if last else None,
            "price_twd": price,
            "forward_four_quarter_eps_sum": {
                "type": "sum_quarterly_diluted_estimates_not_annual_eps",
                "value": base_eps if ready else None,
            },
            "forward_pe": price / base_eps
            if ready and price and base_eps and base_eps > 0
            else None,
            "scenario_implied_multiple": {
                "value": implied,
                "type": "market_cap_over_conditional_parent_profit_not_forward_pe",
                "presentation": "evidence_only_not_valuation_ranking",
            },
            "fair_value": None,
            "undervaluation_rank": None,
            "profit_hurdles": required,
            "reason": "No substitution of issued-share quotients or vendor PE for a reviewed diluted denominator; fair value additionally needs independently justified exit multiples",
        },
    }


def render(quality, table):
    market = quality["latest_market_gate"]
    out = (
        '<section id="publishing-quality"><h2>發布資格與資料時效</h2><p>發布狀態：'
        + quality["publishing_status"]
    )
    out += (
        "；完整估值："
        + quality["full_valuation_status"]
        + "。供應商結構通過不代表官方公告完整。</p>"
    )
    out += table(["驗收項目", "結果"], [("參數結構與具名審查（不代表校準或準確度）" if k == "forward_parameters_reviewed" else k, v)
                                       for k, v in quality["gates"].items()])
    out += table(
        ["行情檢查", "結果"],
        [
            ["應有最後交易日", market["expected_last_session"] or "官方日曆未涵蓋"],
            ["原始行情／還原價／法人日期", str(market["last_dates"])],
            ["行情狀態", market["freshness"]],
            [
                "當前交易結論",
                "可依同日規則檢查"
                if market["current_trade_conclusion_allowed"]
                else "BLOCKED：只供歷史觀察，不發布當前操作建議",
            ],
        ],
    )
    val = quality["valuation"]
    out += "<h3>前瞻 EPS 與估值分級</h3><p>條件 EPS、正式預測、實際稀釋 EPS 與現股商數分開。缺必要參數時仍可閱讀壓力試算；不能用來排名低估。</p>"
    out += table(
        ["情境", "季度", "條件 EPS（元）", "預測資格／缺項"],
        [
            [
                r["scenario"],
                r["period"],
                f"{r['eps']['value']:.2f}" if r["eps"]["value"] is not None else "未知",
                r["presentation_tier"]
                + f"；{len(r['unverified_parameters'])} 項參數待核對（附件逐項列示）",
            ]
            for r in quality["forward_parameter_register"]
        ],
    )
    out += '<p>逐參數依據、觀測時間、假設類別與不確定性：<a href="publishing-quality.json">驗收與參數明細</a>。完整估值／合理價／低估排名仍受來源完整性及獨立倍數驗證限制；另列的條件 Forward PE 僅依明示前瞻假設計算。</p>'
    out += table(
        ["明示假設 PE", "參考股價需達四季 EPS 和（元）"],
        [
            [r["assumed_pe"], f"{r['required_four_quarter_eps_sum']:.2f}"]
            for r in val["profit_hurdles"]
        ],
    )
    out += "<p>以上倒推僅是等式門檻，使用列示行情日價格，不代表市場共識、預測或合理價。未核對公告逐筆列於 publishing-quality.json。</p></section>"
    return out
