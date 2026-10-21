"""Explicit revised-data diagnostics, separate from strict captured-vintage forecasts."""

from datetime import UTC, datetime
from statistics import mean

from .predictive import month_index
from .scenarios import finite, timestamp
from .trade_rules import next_open, setup


def nowcast_error(prediction, actual, issued_at, evaluated_at=None):
    """Resolve only source-reviewed, same-quarter diluted actuals published after issue."""
    from .earnings_bridge import bridge, eps

    if actual is None:
        return {"status": "pending_official_actuals", "errors": None}
    if actual["stock_id"] != prediction["stock_id"]:
        raise ValueError("Nowcast company mismatch")
    first_public = min(
        timestamp(actual["published_at"]),
        timestamp(actual.get("earliest_profit_public_at", actual["published_at"])),
    )
    if first_public <= timestamp(issued_at):
        return {"status": "ineligible_actual_already_public", "errors": None}
    if actual["basis"] != "single_quarter_diluted" or actual["period"] != prediction["period"]:
        raise ValueError("Nowcast actual quarter/basis mismatch")
    if not actual.get("source_sha256") or not actual.get("weighted_shares_note_verified"):
        return {"status": "pending_reviewed_actual_inputs", "errors": None}
    if not actual.get("profit_release_history_reviewed") or not actual.get(
        "earliest_profit_public_at"
    ):
        return {"status": "pending_publication_history_review", "errors": None}
    if not actual.get("captured_at") or max(
        timestamp(actual["published_at"]), timestamp(actual["captured_at"])
    ) > timestamp(evaluated_at or datetime.now(UTC).isoformat()):
        return {"status": "pending_official_actuals", "errors": None}
    value = eps(actual["inputs"])
    if abs(value - finite(actual["reported_eps"])) > 0.005001:
        raise ValueError("Reviewed EPS numerator/share reconciliation failed")
    return {
        "status": "available",
        "model_error": eps(prediction["inputs"]) - actual["reported_eps"],
        "actual_minus_model_attribution": bridge(prediction["inputs"], actual["inputs"]),
        "rounding_residual": actual["reported_eps"] - value,
        "basis": "same-quarter diluted; attribution is mathematical not causal",
    }


def retrospective_revenue(rows, folds):
    """Never rewrite capture times. These errors cannot calibrate a strict PIT model."""
    from math import exp, log

    values = {month_index(x["period"]): x for x in rows}
    if len(values) != len(rows):
        raise ValueError("Select a numeric revision explicitly")
    result = []
    for f in folds:
        cutoff = timestamp(f["cutoff"])
        known = {
            i: x
            for i, x in values.items()
            if timestamp(x["published_at"]) <= cutoff and i < cutoff.year * 12 + cutoff.month - 1
        }
        targets = [month_index(x) for x in f["targets"]]
        if not known or any(i not in values for i in targets):
            continue
        if any(timestamp(values[i]["published_at"]) <= cutoff for i in targets):
            raise ValueError("Target already public")
        end = max(known)
        try:
            ys = [log(finite(known[i]["value"])) for i in range(end - 11, end + 1)]
            avg = mean(ys)
            slope = sum((i - 5.5) * (y - avg) for i, y in enumerate(ys)) / 143
            yoy = mean(known[i]["value"] / known[i - 12]["value"] for i in range(end - 2, end + 1))
            forecasts = {
                "log_trend": sum(exp(avg + slope * (i - end + 5.5)) for i in targets),
                "monthly_momentum": sum(known[i - 12]["value"] * yoy for i in targets),
                "seasonal_naive": sum(known[i - 12]["value"] for i in targets),
            }
        except KeyError:
            continue
        actual = sum(values[i]["value"] for i in targets)
        result.append(
            {
                **f,
                "actual": actual,
                "forecasts": forecasts,
                "absolute_errors": {k: abs(v - actual) for k, v in forecasts.items()},
                "captured_at_preserved": True,
            }
        )
    return {
        "mode": "revised_data_retrospective_diagnostic",
        "strict_pit": False,
        "folds": result,
        "mae": {k: mean(x["absolute_errors"][k] for x in result) for k in result[0]["forecasts"]}
        if result
        else {},
        "production_weights": None,
        "limitation": "Contemporary capture of old values is not historical knowledge; no PIT superiority claim",
    }


def short_horizon(
    bars,
    sessions,
    start,
    end,
    horizon=20,
    fee_bps=14.25,
    tax_bps=30,
    slippage_bps=5,
    execution_bars=None,
):
    if (
        horizon not in [5, 10, 20]
        or not bars
        or [b["date"] for b in bars] != sessions
        or sessions != sorted(set(sessions))
    ):
        raise ValueError("Complete calendar and fixed 5/10/20 horizon required")
    if min(finite(x) for x in [fee_bps, tax_bps, slippage_bps]) < 0:
        raise ValueError("Nonnegative costs required")
    for bar in bars:
        o, h, low, c = (finite(bar[k]) for k in ("open", "high", "low", "close"))
        volume = finite(bar["volume"])
        if not (0 < low <= min(o, c) <= max(o, c) <= h) or volume < 0 or not volume.is_integer():
            raise ValueError("Missing/nontradeable OHLCV cannot enter a return simulation")
    from .strategy_evaluation import execution_check

    execution = {r["date"]: r for r in (execution_bars or bars)}
    if len(execution) != len(bars) or sorted(execution) != sessions:
        raise ValueError("Raw execution calendar must match adjusted diagnostic sessions")
    buy = (fee_bps + slippage_bps) / 10000
    sell = (fee_bps + tax_bps + slippage_bps) / 10000
    if max(buy, sell) >= 1:
        raise ValueError("Invalid costs")
    events = []
    pending = []
    skipped = []
    next_free = 0
    for i in range(21, len(bars)):
        if not start <= sessions[i] <= end or i < next_free:
            continue
        plan = setup(bars[i - 21 : i + 1], sessions[i - 21 : i + 1])
        if plan["status"] != "candidate_pending_next_open":
            continue
        entry = i + 1
        if entry >= len(bars):
            pending.append({"signal_date": sessions[i], "reason": "Next open unavailable"})
            continue
        entry_check = execution_check(execution[sessions[entry]], "buy")
        if entry_check["executable"] is False:
            skipped.append({"signal_date": sessions[i], "reason": entry_check["reason"]})
            continue
        price = bars[entry]["open"]
        if (
            next_open(plan, price)["status"] != "eligible_for_simulation"
            or bars[entry]["volume"] <= 0
        ):
            skipped.append(
                {"signal_date": sessions[i], "reason": "Open invalidates setup/RR or no volume"}
            )
            continue
        terminal = entry + horizon - 1
        exit_idx = None
        exit_price = None
        reason = None
        deferred_exit = False
        for j in range(entry, len(bars)):
            bar = bars[j]
            exit_check = execution_check(execution[sessions[j]], "sell")
            if exit_check["executable"] is False:
                if bar["low"] <= plan["stop_twd"] or j >= terminal:
                    deferred_exit = True
                continue
            if deferred_exit:
                exit_idx, exit_price, reason = j, bar["open"], "deferred_exit_next_observable_open"
                break
            if j > terminal:
                break
            if bar["open"] <= plan["stop_twd"]:
                exit_idx, exit_price, reason = j, bar["open"], "gap_through_stop"
                break
            if bar["open"] >= plan["target_twd"]:
                exit_idx, exit_price, reason = (
                    j,
                    plan["target_twd"],
                    "target_gap_conservative_limit",
                )
                break
            if bar["low"] <= plan["stop_twd"]:
                exit_idx, exit_price, reason = j, plan["stop_twd"], "stop_first_if_both_touched"
                break
            if bar["high"] >= plan["target_twd"]:
                exit_idx, exit_price, reason = j, plan["target_twd"], "target_touch"
                break
        if exit_idx is None:
            if deferred_exit:
                pending.append(
                    {
                        "signal_date": sessions[i],
                        "reason": "Unexecutable exit; no later observable fill",
                    }
                )
                next_free = len(bars)
                continue
            if terminal >= len(bars):
                pending.append({"signal_date": sessions[i], "reason": "Horizon incomplete"})
                continue
            exit_idx, exit_price, reason = terminal, bars[terminal]["close"], "time_exit"
        if sessions[exit_idx] > end:
            pending.append(
                {"signal_date": sessions[i], "reason": "Outcome crosses evaluation split boundary"}
            )
            continue
        net = exit_price * (1 - sell) / (price * (1 + buy)) - 1
        marks = (
            [1.0]
            + [bars[j]["close"] * (1 - sell) / (price * (1 + buy)) for j in range(entry, exit_idx)]
            + [1 + net]
        )
        peak, drawdown = 1.0, 0.0
        for mark in marks:
            peak = max(peak, mark)
            drawdown = min(drawdown, mark / peak - 1)
        baseline = (
            None
            if terminal >= len(bars) or sessions[terminal] > end
            else bars[terminal]["close"] * (1 - sell) / (price * (1 + buy)) - 1
        )
        events.append(
            {
                "signal_date": sessions[i],
                "entry_date": sessions[entry],
                "exit_date": sessions[exit_idx],
                "entry_open": price,
                "exit_price": exit_price,
                "exit_reason": reason,
                "net_return": net,
                "marked_close_max_drawdown": drawdown,
                "same_entry_horizon_hold_net_return": baseline,
                "entry_price_capture_is_contemporary_not_historical": True,
                "execution_verified": False,
                "entry_execution_check": entry_check,
                "raw_entry_open": execution[sessions[entry]]["open"],
                "raw_exit_observation": execution[sessions[exit_idx]][
                    "open" if "open" in reason or "gap" in reason else "close"
                ],
            }
        )
        next_free = exit_idx + 1
    paired = [x for x in events if x["same_entry_horizon_hold_net_return"] is not None]
    return {
        "mode": "revised_adjusted_price_retrospective_simulation",
        "strict_pit": False,
        "horizon": horizon,
        "start": start,
        "end": end,
        "events": events,
        "pending": pending,
        "skipped": skipped,
        "count": len(events),
        "net_win_rate": mean(x["net_return"] > 0 for x in events) if len(events) >= 30 else None,
        "mean_net_return": mean(x["net_return"] for x in events) if len(events) >= 30 else None,
        "aggregate_statistics_suppressed": len(events) < 30,
        "worst_trade_marked_close_drawdown": min(
            (x["marked_close_max_drawdown"] for x in events), default=None
        ),
        "paired_exit_rule_difference": mean(
            x["net_return"] - x["same_entry_horizon_hold_net_return"] for x in paired
        )
        if paired
        else None,
        "costs_bps": {
            "fee_one_way": fee_bps,
            "tax_sell": tax_bps,
            "slippage_one_way": slippage_bps,
        },
        "empirical_edge_proven": False,
        "orders": False,
        "limitations": [
            "Frozen rule replay on currently captured adjusted prices; not historical tradability proof",
            "Same-entry baseline tests exit logic only, not entry-selection alpha",
            "Nonoverlapping event simulation per horizon, no cash ledger or liquidity/lot/tick model",
            "Stop first when intraday order ambiguous; gap below stop exits at open with costs",
        ],
    }
