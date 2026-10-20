"""Optional daily research integration; unavailable inputs stay explicit gaps."""

from datetime import timedelta

from tradingagents.dataflows.taiwan.calendar import verify_calendar

from .bundle import fingerprint
from .cash_quality import analyze, normalize_flow
from .financial_model import break_even_gross_margin, conditional_case, profile
from .historical_validation import short_horizon
from .market_expectations import quantile, valuation
from .review import _end_of_day, _packet, _period
from .scenarios import timestamp


def build(snapshot, reviewed, review_input, dynamics, inputs=None):
    inputs = inputs or {}
    stock, as_of = snapshot["instrument"]["stock_id"], snapshot["as_of"]
    if inputs and (inputs.get("stock_id") != stock or inputs.get("as_of") != as_of):
        raise ValueError("Extension company/cutoff mismatch")
    plan_input = inputs.get("research_plan")
    if plan_input:
        from .knowledge import context

        ctx = context(plan_input["context"], stock, as_of)
        cutoff = timestamp(ctx["cutoff_at"])
    else:
        cutoff = _end_of_day(as_of)
    docs = (review_input or {}).get("documents", {})
    evidence = {}
    for name, packet in inputs.get("financial_packets", {}).items():
        if packet.get("basis") == "balance":
            if (
                packet.get("period_start")
                or packet.get("period_end")
                or packet["as_of_date"] > as_of
            ):
                raise ValueError("Balance evidence must use a known point-in-time date")
        else:
            _period(packet, packet["basis"])
        scopes = ("issuer",) if packet.get("source_kind") == "issuer_capital" else ("consolidated",)
        if scopes == ("issuer",) and (
            packet.get("basis") != "balance"
            or set(packet.get("values", {})) - {"issued_shares", "treasury_shares"}
        ):
            raise ValueError("Issuer capital evidence must not be treated as consolidated earnings")
        values = _packet(
            packet, stock, docs, cutoff, tuple(packet.get("values", {})), allowed_scopes=scopes
        )
        for key, value in values.items():
            evidence[f"{name}.{key}"] = value
    cases = [conditional_case(case, evidence) for case in inputs.get("cases", [])]
    reference_eps = reviewed["axes"]["accounting_earnings"].get("current", {}).get("eps_twd")
    if reference_eps is not None:
        for case in cases:
            case["gross_margin_to_match_reference_eps"] = break_even_gross_margin(
                {k: v["value"] for k, v in case["inputs"].items()}, reference_eps
            )
    cash = analyze(stock, docs, cutoff, inputs.get("cash_quality"), reviewed)
    extensions = snapshot.get("extensions", {})
    records = snapshot["datasets"].get("TaiwanStockPrice", {}).get("records", [])
    prices = {r["data_date"]: r for r in records if r["data_date"] < as_of}
    reported = []
    for row in extensions.get("TaiwanStockPER", {}).get("records", []):
        if row["data_date"] >= as_of:
            continue
        reported.append(
            {
                "date": row["data_date"],
                "captured_at": row["retrieved_at"],
                "source_sha256": row.get("raw_sha256") or fingerprint(row.get("raw", {})),
                "reported_pe": row["values"]["reported_pe"],
            }
        )
    val_inputs = dict(inputs.get("valuation", {}))
    for key in ("forward", "trailing"):
        denominator = val_inputs.get(key)
        if denominator:
            proof = denominator.get("source_packet")
            if not proof or (key == "forward" and denominator.get("horizon_months") != 12):
                raise ValueError(
                    "A source-reviewed denominator packet and explicit horizon are required"
                )
            values = _packet(proof, stock, docs, cutoff, ("eps_twd",))
            if values["eps_twd"] != denominator["eps_twd"] or denominator["source_sha256"] not in {
                docs[k]["sha256"] for k in proof["document_ids"]
            }:
                raise ValueError("Valuation denominator differs from reviewed source")
    val = (
        valuation(
            prices[max(prices)]["values"]["close"], reported, cutoff.isoformat(), **val_inputs
        )
        if prices
        else {"status": "unavailable"}
    )
    float_packet = inputs.get("free_float")
    free_float = None
    if float_packet:
        # Actual issued/float shares are not the weighted diluted EPS denominator.
        if (
            float_packet.get("basis") != "verified_free_float"
            or float_packet.get("as_of_date", "9999") > as_of
        ):
            raise ValueError("Verified contemporaneous free float required")
        free_float = _packet(float_packet, stock, docs, cutoff, ("free_float_shares",))[
            "free_float_shares"
        ]
    flows = {}
    for key, window in dynamics.get("windows", {}).items():
        if window["status"] != "available":
            continue
        window_prices = [r for day, r in prices.items() if window["start"] <= day <= window["end"]]
        expected = int(key)
        volume = sum(r["values"]["volume"] for r in window_prices)
        flows[key] = {
            name: normalize_flow(group["window_net_shares"], volume, free_float)
            if group["window_net_shares"] is not None and len(window_prices) == expected
            else {"status": "unavailable_missing_sessions_or_categories"}
            for name, group in window["institutions"].items()
        }
    from .research_plan import build as build_plan

    plan = build_plan(plan_input, stock, as_of, docs, evidence, snapshot)
    if plan.get("completeness"):
        announced = plan["completeness"]["announced_earnings_periods"]
        for case in cases:
            if case["period"] in announced:
                case["label"] = "post_announcement_retrospective_calculation_not_forecast"
    return {
        "schema_version": 1,
        "stock_id": stock,
        "as_of": as_of,
        "company_profile": profile(stock, inputs.get("profile")),
        "financial_cases": cases,
        "financial_evidence": evidence,
        "forecast_status": "conditional_cases_only"
        if cases
        else "unavailable_missing_reviewed_drivers",
        "confidence_interval": None,
        "statistically_calibrated": False,
        "cash_quality": cash,
        "valuation": val,
        "normalized_institutions": flows,
        "trade_measurement_label": "情境測量，非預期報酬或已觸發訊號",
        "backtest": backtest(snapshot, inputs.get("backtest")),
        "industry_transmission": industry_events(
            inputs.get("industry_events", []), cutoff.isoformat()
        ),
        "forecasts_reissued": False,
        "research_plan": plan,
    }


def industry_events(events, cutoff):
    from .valuation_drivers import event_effect

    return [{"event": e.get("name", ""), "calculation": event_effect(e, cutoff)} for e in events]


def backtest(snapshot, config):
    if not config:
        return {
            "status": "unavailable",
            "reason": "Explicit frozen research/test split required",
            "empirical_edge_proven": False,
        }
    from .trade_rules import setup

    calendar = snapshot.get("market_calendar", {})
    if calendar.get("status") != "ok":
        raise ValueError("Official complete calendar required")
    sessions = [
        d
        for d in verify_calendar(calendar, snapshot["instrument"]["exchange"])
        if d < snapshot["as_of"]
    ]
    raw = snapshot.get("datasets", {}).get("TaiwanStockPrice", {}).get("records", [])
    raw_index = {r["data_date"]: r for r in raw}
    if len(raw_index) != len(raw) or any(day not in raw_index for day in sessions):
        return {
            "status": "unavailable",
            "reason": "Missing or duplicate unadjusted stock sessions; adjusted prices alone cannot verify tradability",
            "empirical_edge_proven": False,
        }
    for day in sessions:
        values = raw_index[day]["values"]
        ohlc = [values.get(key) for key in ("open", "high", "low", "close")]
        if (
            any(not isinstance(value, (int, float)) or value <= 0 for value in ohlc)
            or values.get("volume", 0) <= 0
            or not (ohlc[2] <= min(ohlc[0], ohlc[3]) <= max(ohlc[0], ohlc[3]) <= ohlc[1])
        ):
            return {
                "status": "unavailable",
                "reason": "Invalid unadjusted OHLCV; no zero-price fill or assumed execution",
                "empirical_edge_proven": False,
            }
    records = snapshot.get("extensions", {}).get("TaiwanStockPriceAdj", {}).get("records", [])
    index = {r["data_date"]: r for r in records}
    if len(index) != len(records) or any(day not in index for day in sessions):
        return {
            "status": "unavailable",
            "reason": "Missing or duplicate adjusted stock sessions",
            "empirical_edge_proven": False,
        }
    bars = [{"date": day, "basis": "vendor_adjusted", **index[day]["values"]} for day in sessions]
    split = config["test_start"]
    # Freeze metadata cannot convert a retrospectively chosen holdout into a pristine OOS experiment.
    if config.get("rule_version") != "breakout-v1" or not config.get("selected_at"):
        raise ValueError("Frozen rule version and actual selection timestamp required")
    timestamp(config["selected_at"])
    if not sessions[0] < split <= sessions[-1]:
        raise ValueError("Split outside price history")
    results = {}
    for label, start, end in (
        ("exploration", sessions[0], (timestamp(split) - timedelta(days=1)).date().isoformat()),
        ("holdout", split, sessions[-1]),
    ):
        results[label] = {}
        for horizon in (5, 10, 20):
            result = short_horizon(bars, sessions, start, end, horizon, **config.get("costs", {}))
            returns = [r["net_return"] for r in result["events"]]
            result["return_distribution"] = {
                f"p{int(p * 100)}": quantile(returns, p) for p in (0.1, 0.25, 0.5, 0.75, 0.9)
            }
            result["market_regimes"] = {}
            for event in result["events"]:
                i = sessions.index(event["signal_date"])
                history = bars[max(0, i - 59) : i + 1]
                regime = (
                    "insufficient_ma60"
                    if len(history) < 60
                    else (
                        "above_ma60"
                        if bars[i]["close"] > sum(b["close"] for b in history) / 60
                        else "below_ma60"
                    )
                )
                result["market_regimes"].setdefault(regime, []).append(event["net_return"])
            result["small_sample_warning"] = len(returns) < 30
            from .walk_forward import sample_summary

            result.update(sample_summary(result["events"]))
            if result["aggregate_statistics_suppressed"]:
                result["return_distribution"] = dict.fromkeys(result["return_distribution"])
            results[label][str(horizon)] = result
    plan = setup(bars[-22:], sessions[-22:]) if len(bars) >= 22 else None
    walk = {"status": "unavailable_frozen_walk_forward_plan_required"}
    if config.get("walk_forward"):
        from .walk_forward import run

        frozen = config["walk_forward"]
        walk = run(
            bars,
            sessions,
            frozen["folds"],
            frozen,
            {
                "sha256": fingerprint(records),
                "bars_sha256": fingerprint(bars),
                "sessions_sha256": fingerprint(sessions),
                "captured_at": max(r["retrieved_at"] for r in records),
                "point_in_time_adjustments_verified": False,
            },
            snapshot["instrument"]["stock_id"],
        )
    return {
        "status": "retrospective_diagnostic",
        "config": config,
        "splits": results,
        "current_setup": plan,
        "walk_forward": walk,
        "strict_pit": False,
        "empirical_edge_proven": False,
        "holdout_selection": "retrospectively_declared_not_untouched_prospective_test",
        "limitation": "Current adjusted price vintage and existing study exposure prevent pristine OOS claims; horizons overlap and are not independent samples",
    }
