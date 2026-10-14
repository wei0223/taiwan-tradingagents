"""Deterministic calculations on already eligible records; null means unavailable."""

from __future__ import annotations

from datetime import date


def growth(current, previous):
    if current is None or previous is None or previous <= 0:
        return None
    return (current / previous - 1) * 100


def sma(values, period):
    return sum(values[-period:]) / period if len(values) >= period else None


def ema(values, period):
    """SMA seed, then recursive EMA; insufficient warm-up is represented by None."""
    result = [None] * len(values)
    if len(values) < period:
        return result
    value = sum(values[:period]) / period
    result[period - 1] = value
    alpha = 2 / (period + 1)
    for index in range(period, len(values)):
        value += alpha * (values[index] - value)
        result[index] = value
    return result


def rsi(values, period=14):
    if len(values) <= period:
        return None
    changes = [b - a for a, b in zip(values, values[1:], strict=False)]
    up = sum(max(c, 0) for c in changes[:period]) / period
    down = sum(max(-c, 0) for c in changes[:period]) / period
    for change in changes[period:]:
        up = (up * (period - 1) + max(change, 0)) / period
        down = (down * (period - 1) + max(-change, 0)) / period
    if down == 0:
        return 100.0 if up else 50.0
    return 100 - 100 / (1 + up / down)


def technical(records):
    closes = [r["values"]["close"] for r in records]
    volumes = [r["values"]["volume"] for r in records]
    fast, slow = ema(closes, 12), ema(closes, 26)
    macd = [f - s for f, s in zip(fast, slow, strict=True) if f is not None and s is not None]
    signal = ema(macd, 9)
    line, sig = (macd[-1] if macd else None), (signal[-1] if signal else None)
    return {
        "data_date": records[-1]["data_date"] if records else None,
        "input_dates": [r["data_date"] for r in records],
        "ma": {str(n): sma(closes, n) for n in (5, 20, 50, 200)},
        "rsi_14": rsi(closes), "macd": line, "macd_signal": sig,
        "macd_histogram": line - sig if sig is not None else None,
        "volume_shares": volumes[-1] if volumes else None, "volume_ma20": sma(volumes, 20),
        "status": "ok" if records else "unavailable",
        "warnings": ["Null indicators require more observations; prices are unadjusted"],
    }


def revenue_metrics(records):
    by_period = {r["period"]: r for r in records}
    output = []
    for period, record in sorted(by_period.items()):
        previous = by_period.get(f"{int(period[:4]) - 1:04d}{period[4:]}")
        output.append({"period": period, "revenue_twd": record["values"]["revenue"],
                       "yoy_pct": growth(record["values"]["revenue"],
                                         previous["values"]["revenue"] if previous else None),
                       "input_dates": [record["data_date"]] + ([previous["data_date"]] if previous else [])})
    return output


def financial_metrics(records):
    quarters = {}
    for record in records:
        # Only quarter-end observations are accepted; avoid arbitrary monthly / YTD dates.
        parsed = date.fromisoformat(record["period"])
        ends = {3: 31, 6: 30, 9: 30, 12: 31}
        if ends.get(parsed.month) != parsed.day:
            continue
        key = parsed.year * 4 + (parsed.month // 3 - 1)
        quarter = quarters.setdefault(key, {"period": record["period"], "values": {}})
        quarter["values"].update(record["values"])
    output = []
    for key, quarter in sorted(quarters.items()):
        values = quarter["values"]
        revenue = values.get("Revenue")
        eps = values.get("EPS")
        previous, year_ago = quarters.get(key - 1), quarters.get(key - 4)
        output.append({
            "period": quarter["period"], "eps_twd": eps,
            "eps_qoq_pct": growth(eps, previous["values"].get("EPS") if previous else None),
            "eps_yoy_pct": growth(eps, year_ago["values"].get("EPS") if year_ago else None),
            "gross_margin_pct": values.get("GrossProfit") / revenue * 100
            if revenue and revenue > 0 and "GrossProfit" in values else None,
            "operating_margin_pct": values.get("OperatingIncome") / revenue * 100
            if revenue and revenue > 0 and "OperatingIncome" in values else None,
            "net_margin_pct": values.get("IncomeAfterTaxes") / revenue * 100
            if revenue and revenue > 0 and "IncomeAfterTaxes" in values else None,
            "input_periods": [quarter["period"]] + ([previous["period"]] if previous else [])
            + ([year_ago["period"]] if year_ago else []),
            "basis": "FinMind single-quarter fields; no YTD differencing or EPS aggregation",
        })
    return output


def institutional_metrics(records, prices, exchange):
    days = [r["data_date"] for r in prices]
    by_day = {}
    known = {"Foreign_Investor", "Foreign_Dealer_Self", "Investment_Trust",
             "Dealer", "Dealer_self", "Dealer_Hedging"}
    unknown = set()
    for record in records:
        values = record["values"]
        by_day.setdefault(record["data_date"], {})[values["name"]] = values["net"]
        if values["name"] not in known:
            unknown.add(values["name"])
    series = {"foreign": [], "investment_trust": [], "dealer": []}
    split_date = "2017-12-18" if exchange == "twse" else "2018-01-15"
    for trading_day in days:
        row = by_day.get(trading_day, {})
        foreign_keys = ["Foreign_Investor"] + (["Foreign_Dealer_Self"] if trading_day >= split_date else [])
        foreign = sum(row[k] for k in foreign_keys) if all(k in row for k in foreign_keys) else None
        if "Dealer" in row and not any(k in row for k in ("Dealer_self", "Dealer_Hedging")):
            dealer = row["Dealer"]
        elif "Dealer" not in row and all(k in row for k in ("Dealer_self", "Dealer_Hedging")):
            dealer = row["Dealer_self"] + row["Dealer_Hedging"]
        else:
            dealer = None
        for name, value in (("foreign", foreign), ("investment_trust", row.get("Investment_Trust")),
                            ("dealer", dealer)):
            series[name].append(value)
    output = {}
    for name, values in series.items():
        latest = values[-1] if values else None
        count, total = 0, 0
        if latest is not None and latest != 0:
            direction = 1 if latest > 0 else -1
            for value in reversed(values):
                if value is None or value * direction <= 0:
                    break
                count += direction
                total += value
        output[name] = {
            "latest_net_shares": latest,
            "signed_days": count if latest is not None else None,
            "streak_net_shares": total if latest is not None else None,
            "window_net_shares": sum(values) if values and all(v is not None for v in values) else None,
            "left_censored": bool(values) and abs(count) == len(values),
            "coverage_complete": bool(values) and all(v is not None for v in values),
        }
    return {"groups": output, "input_dates": days, "unknown_categories": sorted(unknown),
            "calendar_basis": "observed price rows; exchange calendar completeness unverified",
            "warnings": ["Missing categories/days are unavailable, never zero; streaks are observed only"]}
