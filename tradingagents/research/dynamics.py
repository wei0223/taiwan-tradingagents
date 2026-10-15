"""Rolling price/institution metrics. Individual missing sessions stay unavailable."""

from __future__ import annotations

from statistics import mean

from tradingagents.dataflows.taiwan.calculations import institutional_metrics
from tradingagents.dataflows.taiwan.calendar import verify_calendar
from tradingagents.dataflows.taiwan.extensions import ADJUSTED
from tradingagents.dataflows.taiwan.provider import INSTITUTIONS

from .bundle import build_bundle


def rolling(snapshot, windows=(5, 10, 20, 60)):
    bundle = build_bundle(snapshot)
    calendar = snapshot.get("market_calendar")
    if not calendar or calendar["status"] != "ok":
        return {"status": "unavailable", "reason": "complete official calendar required"}
    sessions = [d for d in verify_calendar(calendar, bundle["exchange"]) if d < bundle["as_of"]]
    prices = snapshot.get("extensions", {}).get(ADJUSTED, {}).get("records", [])
    by_date = {r["data_date"]: r for r in prices}
    if not sessions or len(by_date) != len(prices):
        raise ValueError("Empty calendar or duplicate adjusted prices")
    result = {
        "status": "available",
        "basis": "vendor_adjusted_capture_not_total_return",
        "latest_session": sessions[-1],
        "windows": {},
    }
    institutional = snapshot["datasets"][INSTITUTIONS]["records"]
    for window in windows:
        if isinstance(window, bool) or not isinstance(window, int) or window < 2:
            raise ValueError("Integer rolling window >=2 required")
        dates = sessions[-(2 * window + 1) :]
        if len(dates) < 2 * window + 1 or any(d not in by_date for d in dates):
            result["windows"][str(window)] = {
                "status": "unavailable",
                "reason": "insufficient history or missing stock session; not zero",
            }
            continue
        rows = [by_date[d] for d in dates]
        values = [r["values"] for r in rows]
        tail = values[-window:]
        closes = [r["close"] for r in values]
        ma = mean(closes[-window:])
        prior_ma = mean(closes[-window - 1 : -1])
        tr = [
            max(
                values[i]["high"] - values[i]["low"],
                abs(values[i]["high"] - closes[i - 1]),
                abs(values[i]["low"] - closes[i - 1]),
            )
            for i in range(len(values) - window, len(values))
        ]
        previous_high = max(r["high"] for r in values[-window - 1 : -1])
        prior_block_high = max(r["high"] for r in values[-2 * window : -window])
        recent_high = max(r["high"] for r in tail)
        breakout = closes[-1] > previous_high
        crossed_previous = closes[-2] > max(r["high"] for r in values[-window - 2 : -2])
        groups = institutional_metrics(institutional, rows[-window:], bundle["exchange"])["groups"]
        result["windows"][str(window)] = {
            "status": "available",
            "start": dates[-window],
            "end": dates[-1],
            "return_pct": (closes[-1] / closes[-window - 1] - 1) * 100,
            "ma": ma,
            "ma_one_session_change": ma - prior_ma,
            "volume_mean_shares": mean(r["volume"] for r in tail),
            "atr_simple_mean": mean(tr),
            "atr_method": "arithmetic mean of true ranges; not Wilder smoothing",
            "breakout_previous_window_high": breakout,
            "false_breakout_one_session_rule": crossed_previous
            and closes[-1] <= max(r["high"] for r in values[-window - 2 : -2]),
            "recent_high": recent_high,
            "prior_nonoverlap_high": prior_block_high,
            "higher_high_observed": recent_high > prior_block_high,
            "institutions": groups,
            "limitations": [
                "Pattern definitions are mechanical observations, not tested success probabilities",
                "Institutional sums require each price session's categories; missing categories remain null",
            ],
        }
    return result


def conditional_decision(model, market, chip_complete=False):
    """Observation is a first-class result; no automatic fills or forced signals."""
    ready = model.get("status") == "ready"
    windows = market.get("windows", {})
    recent = windows.get("20", {})
    confirmations = (
        recent.get("status") == "available"
        and recent.get("ma_one_session_change", 0) > 0
        and recent.get("breakout_previous_window_high") is True
    )
    return {
        "state": "research_candidate" if ready and confirmations and chip_complete else "observe",
        "horizon": "next 5-20 market sessions; review at each new capture or official filing",
        "checks": {
            "reviewed_valuation_model": ready,
            "price_confirmation": bool(confirmations),
            "complete_institutional_window": chip_complete,
        },
        "invalidation": [
            "New filing invalidates earnings assumptions",
            "Observed breakout fails or data coverage degrades",
        ],
        "execution": False,
        "position_size": None,
        "risk_reward": None,
        "risk_reward_reason": "Requires explicit entry, scenario target and loss boundary; never infer stops from MA",
    }


def reward_risk(entry, target, invalidation):
    if not 0 < invalidation < entry < target:
        raise ValueError("Long scenario requires positive loss boundary < entry < target")
    return {
        "reward_pct": (target / entry - 1) * 100,
        "loss_pct": (1 - invalidation / entry) * 100,
        "reward_risk": (target - entry) / (entry - invalidation),
        "kind": "conditional_not_fill_guarantee",
    }
