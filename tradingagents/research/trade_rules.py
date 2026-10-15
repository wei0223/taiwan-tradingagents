"""Separate short-horizon mechanical setup from long-horizon earnings value. No orders."""

from statistics import mean

from .scenarios import finite


def setup(bars, sessions, min_volume_ratio=1.2, min_rr=2):
    if len(bars) < 22:
        return {"status": "unavailable", "reason": "22 complete sessions required"}
    dates = [b["date"] for b in bars]
    if dates != sorted(set(dates)) or dates != sessions:
        raise ValueError("Complete, unique, aligned session calendar required")
    if any(b["basis"] != "vendor_adjusted" for b in bars):
        raise ValueError("One adjusted-price basis required")
    for b in bars:
        if (
            min(finite(b[k]) for k in ["open", "high", "low", "close"]) <= 0
            or finite(b["volume"]) < 0
        ):
            raise ValueError("Invalid OHLCV")
        if not b["low"] <= min(b["open"], b["close"]) <= max(b["open"], b["close"]) <= b["high"]:
            raise ValueError("Inconsistent OHLC")
    if min_volume_ratio <= 0 or min_rr <= 0:
        raise ValueError("Positive rule thresholds required")
    prior = bars[-22:-2]
    high = max(b["high"] for b in prior)
    low = min(b["low"] for b in prior)
    recovery = all(
        bars[i]["close"] > mean(b["close"] for b in bars[i - 19 : i + 1])
        for i in [len(bars) - 2, len(bars) - 1]
    )
    volume = (
        bars[-1]["volume"] / mean(b["volume"] for b in bars[-21:-1])
        if mean(b["volume"] for b in bars[-21:-1]) > 0
        else None
    )
    tr = [
        max(
            bars[i]["high"] - bars[i]["low"],
            abs(bars[i]["high"] - bars[i - 1]["close"]),
            abs(bars[i]["low"] - bars[i - 1]["close"]),
        )
        for i in range(len(bars) - 14, len(bars))
    ]
    atr = mean(tr)
    # Prevent an extremely tight recent low from manufacturing an attractive RR.
    stop = min(min(b["low"] for b in bars[-10:]) * 0.995, bars[-1]["close"] - 1.5 * atr)
    target = high + (
        high - low
    )  # explicit measured-move hypothesis, unrelated to four-quarter valuation
    entry = bars[-1]["close"]  # provisional only; next open must be rechecked
    rr = (target - entry) / (entry - stop) if entry > stop else None
    checks = {
        "two_closes_above_ma20": recovery,
        "close_breaks_prior20_high": bars[-1]["close"] > high,
        "volume_confirmation": volume is not None and volume >= min_volume_ratio,
        "provisional_rr": rr is not None and rr >= min_rr,
    }
    return {
        "status": "candidate_pending_next_open" if all(checks.values()) else "observe",
        "as_of": bars[-1]["date"],
        "checks": checks,
        "volume_ratio": volume,
        "stop_twd": stop,
        "target_twd": target,
        "provisional_entry": entry,
        "provisional_rr": rr,
        "atr14_simple_mean": atr,
        "horizons_sessions": [5, 20],
        "thresholds": {
            "volume_ratio": min_volume_ratio,
            "min_rr": min_rr,
            "stop_buffer": 0.005,
            "minimum_risk_atr": 1.5,
        },
        "next_open_rule": "Recalculate RR at next observed open; skip if open<=stop, open>=target or RR<threshold; no assumed fills",
        "empirical_edge": "unvalidated",
        "orders": False,
    }


def next_open(plan, price):
    price = finite(price)
    if (
        plan["status"] != "candidate_pending_next_open"
        or not plan["stop_twd"] < price < plan["target_twd"]
    ):
        return {"status": "skip", "orders": False}
    rr = (plan["target_twd"] - price) / (price - plan["stop_twd"])
    return {
        "status": "eligible_for_simulation" if rr >= plan["thresholds"]["min_rr"] else "skip",
        "rr_at_open": rr,
        "orders": False,
    }
