"""Prospective forecast errors and cost-aware, next-session long-only signal evaluation."""

from datetime import UTC, date, datetime
from statistics import mean

from .scenarios import finite, known_date, timestamp


def forecast_errors(predictions, actuals, today=None):
    today = today or datetime.now(UTC).date().isoformat()
    errors, pending, seen = [], [], set()
    index = {}
    for actual in actuals:
        key = (actual["stock_id"], actual["period"], actual["metric"], actual["basis"])
        if key in index:
            raise ValueError("Ambiguous actual vintage; select a revision explicitly")
        index[key] = actual
    for predicted in predictions:
        key = (predicted["stock_id"], predicted["period"], predicted["metric"], predicted["basis"])
        identity = (*key, predicted["model_version"], predicted["issued_at"])
        if identity in seen:
            raise ValueError("Duplicate forecast")
        seen.add(identity)
        if not predicted.get("input_snapshot_sha256"):
            raise ValueError("Prediction input vintage required")
        actual = index.get(key)
        if not actual or max(
            known_date(actual["published_at"]), known_date(actual["captured_at"])
        ) > date.fromisoformat(today):
            pending.append(predicted)
            continue
        if timestamp(predicted["issued_at"]) >= timestamp(actual["published_at"]):
            raise ValueError("Prediction issued after actual result became public")
        error = finite(predicted["value"]) - finite(actual["value"])
        errors.append(
            {
                "stock_id": key[0],
                "period": key[1],
                "metric": key[2],
                "basis": key[3],
                "model_version": predicted["model_version"],
                "error": error,
                "absolute_error": abs(error),
            }
        )
    groups = {}
    for row in errors:
        key = f"{row['model_version']}:{row['metric']}:{row['basis']}"
        groups.setdefault(key, []).append(row["absolute_error"])
    return {
        "resolved": errors,
        "pending": pending,
        "mae_by_model_metric_basis": {k: mean(v) for k, v in groups.items()},
        "status": "available" if errors else "pending_future_actuals",
    }


def signal_performance(
    signals, prices, sessions, horizon=20, fee_bps=14.25, tax_bps=30, slippage_bps=5
):
    """Signals must be sealed before next-session open. No overlapping portfolio simulation."""
    if (
        isinstance(horizon, bool)
        or not isinstance(horizon, int)
        or horizon < 1
        or sessions != sorted(set(sessions))
    ):
        raise ValueError("Ordered unique calendar and positive horizon required")
    if not signals:
        return {"status": "unavailable", "reason": "No sealed prospective signals", "trades": []}
    if min(finite(fee_bps), finite(tax_bps), finite(slippage_bps)) < 0:
        raise ValueError("Costs cannot be negative")
    for day in sessions:
        date.fromisoformat(day)
    cost_buy = (fee_bps + slippage_bps) / 10000
    cost_sell = (fee_bps + tax_bps + slippage_bps) / 10000
    if max(cost_buy, cost_sell) >= 1:
        raise ValueError("Cost assumptions exceed price")
    index, seen = {}, set()
    for row in prices:
        key = (row["stock_id"], row["date"])
        if key in index or row["basis"] != "vendor_adjusted":
            raise ValueError("Unique adjusted outcome observations required")
        if (
            row["date"] > datetime.now(UTC).date().isoformat()
            or min(finite(row["open"]), finite(row["close"])) <= 0
        ):
            raise ValueError("Invalid/future outcome price")
        index[key] = row
    trades, pending, observed = [], [], []
    for signal in signals:
        identity = (signal["stock_id"], signal["as_of"], signal["model_version"])
        if identity in seen:
            raise ValueError("Duplicate signal")
        seen.add(identity)
        if known_date(signal["sealed_at"]) > date.fromisoformat(signal["as_of"]) or not signal.get(
            "input_snapshot_sha256"
        ):
            raise ValueError("Signal not sealed by cutoff or input vintage missing")
        if signal["direction"] not in {0, 1}:
            raise ValueError("Long/observe only; no unmodeled short borrow")
        if signal["direction"] == 0:
            observed.append(signal)
            continue
        future = [d for d in sessions if d > signal["as_of"]]
        if len(future) < horizon or any(
            (signal["stock_id"], d) not in index for d in future[:horizon]
        ):
            pending.append(signal)
            continue
        rows = [index[(signal["stock_id"], d)] for d in future[:horizon]]
        entry, exit_price = rows[0]["open"], rows[-1]["close"]
        net = exit_price * (1 - cost_sell) / (entry * (1 + cost_buy)) - 1
        # Drawdown from close marks, including entry; cannot capture intraday lows.
        marks = [1.0] + [r["close"] * (1 - cost_sell) / (entry * (1 + cost_buy)) for r in rows]
        peak, drawdown = 1.0, 0.0
        for mark in marks:
            peak = max(peak, mark)
            drawdown = min(drawdown, mark / peak - 1)
        trades.append(
            {
                "stock_id": signal["stock_id"],
                "model_version": signal["model_version"],
                "as_of": signal["as_of"],
                "entry_date": future[0],
                "exit_date": future[horizon - 1],
                "gross_return": exit_price / entry - 1,
                "net_return": net,
                "marked_close_max_drawdown": drawdown,
            }
        )
    wins = [r["net_return"] for r in trades if r["net_return"] > 0]
    losses = [r["net_return"] for r in trades if r["net_return"] < 0]
    return {
        "status": "available" if trades else "pending_or_observe",
        "trades": trades,
        "pending": pending,
        "observations": observed,
        "net_win_rate": len(wins) / len(trades) if trades else None,
        "average_net_return": mean(r["net_return"] for r in trades) if trades else None,
        "average_win_loss_ratio": mean(wins) / abs(mean(losses)) if wins and losses else None,
        "worst_trade_marked_drawdown": min(
            (r["marked_close_max_drawdown"] for r in trades), default=None
        ),
        "cost_assumptions_bps": {
            "one_way_fee": fee_bps,
            "sell_tax": tax_bps,
            "one_way_slippage": slippage_bps,
        },
        "limitations": [
            "Independent event returns, not an overlapping cash-ledger portfolio",
            "Costs are configurable assumptions, not a verification of current broker charges",
            "Vendor adjustment is retrospective, not dividend-reinvestment total return",
        ],
    }


def paired_comparison(multi, single, baseline):
    """Compare only identical stock/cutoff cells; never call missing ablations zero."""

    def keyed(rows):
        result = {}
        for row in rows:
            key = (row["stock_id"], row["as_of"])
            if key in result:
                raise ValueError("Duplicate comparison cell")
            result[key] = finite(row["net_return"])
        return result

    indexes = [keyed(rows) for rows in (multi, single, baseline)]
    keys = set(indexes[0]) & set(indexes[1]) & set(indexes[2])
    return {
        "status": "available" if keys else "unavailable",
        "paired_cells": len(keys),
        "mean_returns": [mean(index[k] for k in sorted(keys)) for index in indexes]
        if keys
        else None,
        "labels": ["multi_agent", "single_agent", "simple_baseline"],
        "causal_superiority_proven": False,
    }
