"""Execution uncertainty and paired, clustered research diagnostics."""

import random
from datetime import date
from statistics import mean

from .bundle import fingerprint
from .market_expectations import quantile
from .scenarios import finite, timestamp


def execution_check(bar, side):
    if side not in {"buy", "sell"}:
        raise ValueError("Explicit execution side required")
    values = [finite(bar[k]) for k in ("open", "high", "low", "close")]
    if min(values) <= 0 or finite(bar["volume"]) <= 0:
        return {"executable": False, "reason": "no_positive_price_or_volume"}
    if len(set(values)) == 1:
        return {"executable": False, "reason": "one_price_bar_no_queue_or_fill_proof"}
    limits = bar.get("official_limits")
    if not limits:
        return {"executable": None, "reason": "official_daily_limits_and_exemptions_unavailable"}
    if not limits.get("source_sha256") or limits.get("date") != bar["date"]:
        raise ValueError("Daily limit source/date mismatch")
    if limits.get("exempt"):
        if not limits.get("exemption_reason"):
            raise ValueError("Listing/ex-right limit exemption needs evidence")
    else:
        lo, hi = finite(limits["down"]), finite(limits["up"])
        if not lo <= min(values) <= max(values) <= hi:
            raise ValueError("Raw prices outside official limits")
        if (side == "buy" and bar["open"] == hi) or (side == "sell" and bar["open"] == lo):
            return {"executable": None, "reason": "limit_price_open_queue_unobserved"}
    return {"executable": True, "reason": "daily_bar_not_proof_of_intraday_queue_or_liquidity"}


def clustered_summary(events, minimum_clusters=30, seed=20261021):
    """Calendar-week blocks retain co-moving companies and overlapping horizons."""
    if minimum_clusters < 30:
        raise ValueError("Cannot lower disclosure threshold")
    blocks = {}
    for event in events:
        d = date.fromisoformat(event["signal_date"])
        key = f"{d.isocalendar().year}-{d.isocalendar().week:02d}"
        blocks.setdefault(key, []).append(finite(event["net_return"]))
    result = {
        "event_count": len(events),
        "calendar_week_clusters": len(blocks),
        "dependence_unit": "calendar_week_all_companies_jointly",
        "mean_net_return": None,
        "block_bootstrap_interval": None,
        "empirical_edge_proven": False,
    }
    if len(blocks) < minimum_clusters:
        result["status"] = "insufficient_clusters_individual_events_only"
        return result
    result["mean_net_return"] = mean(e["net_return"] for e in events)
    rng, values = random.Random(seed), list(blocks.values())
    samples = [
        mean(x for block in rng.choices(values, k=len(values)) for x in block) for _ in range(2000)
    ]
    result.update(
        status="descriptive_block_resampling_not_prospective_edge",
        block_bootstrap_interval=[quantile(samples, 0.025), quantile(samples, 0.975)],
        bootstrap_seed=seed,
        limitation="Week blocks do not prove all serial dependence removed; rule selection and PIT remain separate gates",
    )
    return result


def nonoverlapping(events):
    """Do not count different horizons or concurrent holdings as independent trades."""
    result, through = [], {}
    for event in sorted(events, key=lambda e: (e["signal_date"], e["stock_id"], e["exit_date"])):
        stock = event["stock_id"]
        if event["entry_date"] <= through.get(stock, ""):
            continue
        result.append(event)
        through[stock] = event["exit_date"]
    return result


def paired_filter(events, fundamentals, rule, data_as_of, benchmark=None):
    """Filter only immutable releases known before entry; never use today's backfill."""
    if not rule.get("selected_at") or timestamp(rule["selected_at"]) > timestamp(data_as_of):
        raise ValueError("Rule must predate evaluation")
    spec = {k: v for k, v in rule.items() if k != "sha256"}
    if fingerprint(spec) != rule.get("sha256"):
        raise ValueError("Fundamental filter rule identity mismatch")
    rows, unavailable = [], []
    for event in nonoverlapping(events):
        known = [
            f
            for f in fundamentals
            if f["stock_id"] == event["stock_id"]
            and f.get("release_sha256")
            and f.get("input_source_sha256")
            and max(timestamp(f["issued_at"]), timestamp(f["captured_at"]))
            < timestamp(event["entry_date"])
            and timestamp(f["issued_at"]) <= timestamp(data_as_of)
        ]
        if not known or timestamp(rule["selected_at"]) >= timestamp(event["entry_date"]):
            unavailable.append(
                {
                    "stock_id": event["stock_id"],
                    "signal_date": event["signal_date"],
                    "reason": "no_frozen_rule_and_contemporaneous_fundamental_release",
                }
            )
            continue
        f = max(known, key=lambda x: timestamp(x["issued_at"]))
        key = rule["metric"]
        if key not in f.get("values", {}):
            unavailable.append(
                {"signal_date": event["signal_date"], "reason": "fundamental_metric_unavailable"}
            )
            continue
        passed = finite(f["values"][key]) >= finite(rule["minimum"])
        index = benchmark or {}
        start, end = index.get(event["entry_date"]), index.get(event["exit_date"])
        market_return = None
        if start and end and start.get("source_sha256") and end.get("source_sha256"):
            if start.get("basis") != end.get("basis"):
                raise ValueError("Benchmark price basis differs")
            market_return = finite(end["close"]) / finite(start["open"]) - 1
        rows.append(
            {
                **event,
                "fundamental_release_sha256": f["release_sha256"],
                "fundamental_pass": passed,
                "benchmark_gross_return": market_return,
                "net_strategy_minus_gross_benchmark": event["net_return"] - market_return
                if market_return is not None
                else None,
            }
        )
    return {
        "status": "paired_diagnostic" if rows else "unavailable_contemporaneous_fundamentals",
        "technical_only": clustered_summary(rows),
        "combined": clustered_summary([r for r in rows if r["fundamental_pass"]]),
        "fundamentals_only": {
            "status": "requires_separately_frozen_rebalance_universe_and_cost_ledger"
        },
        "paired_events": rows,
        "excluded": unavailable,
        "alpha_established": False,
        "limitation": "Combined filtering is selection-conditioned; no causal incremental-alpha claim or fundamental-only strategy is inferred",
    }
