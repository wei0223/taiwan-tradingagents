"""Deterministic calculations on already eligible records; null means unavailable."""

from __future__ import annotations

import calendar
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


def financial_metrics(records, review=None, cutoff=None, stock_id=None):
    """Only reviewed per-field quarters feed ratios; dates alone certify nothing."""
    from .periods import resolve

    stock_id = stock_id or (review or {}).get("stock_id")
    cutoff = cutoff or max((r.get("retrieved_at", r["period"]) for r in records), default="1900-01-01")
    resolved = resolve(review, stock_id, cutoff)
    quarters = {}
    for record in records:
        parsed = date.fromisoformat(record["period"])
        if {3: 31, 6: 30, 9: 30, 12: 31}.get(parsed.month) == parsed.day:
            quarters.setdefault(record["period"], {})
    for observation in resolved["rows"]:
        if observation["field"] not in {"CashFlowsFromOperatingActivities", "PropertyAndPlantAndEquipment"}:
            quarters.setdefault(observation["period_end"], {})[observation["field"]] = observation

    def same_basis(a, b):
        return bool(a and b) and all(a[k] == b[k] for k in
                                    ("period_start", "period_end", "scope", "version_set"))

    def ratio(values, numerator):
        a, b = values.get(numerator), values.get("Revenue")
        return a["value"] / b["value"] * 100 if same_basis(a, b) and b["value"] > 0 else None

    output = []
    for end, values in sorted(quarters.items()):
        parsed = date.fromisoformat(end)
        key = parsed.year * 4 + parsed.month // 3 - 1
        def past_eps(offset, key=key, values=values):
            index = key - offset
            month = (index % 4 + 1) * 3
            day = date(index // 4, month, calendar.monthrange(index // 4, month)[1]).isoformat()
            previous = quarters.get(day, {}).get("DilutedEPS")
            current = values.get("DilutedEPS")
            return previous["value"] if current and previous and all(
                current[k] == previous[k] for k in ("scope", "unit", "version_set")) else None
        eps = values.get("DilutedEPS", {}).get("value")
        output.append({
            "period": end, "period_start": date(parsed.year, parsed.month - 2, 1).isoformat(),
            "period_end": end, "scope": "consolidated", "eps_twd": eps,
            "eps_basis": "single_quarter_diluted" if eps is not None else "unavailable",
            "basic_eps_twd": values.get("BasicEPS", {}).get("value"),
            "eps_qoq_pct": growth(eps, past_eps(1)), "eps_yoy_pct": growth(eps, past_eps(4)),
            "revenue_twd": values.get("Revenue", {}).get("value"),
            "gross_margin_pct": ratio(values, "GrossProfit"),
            "operating_margin_pct": ratio(values, "OperatingIncome"),
            "net_margin_pct": ratio(values, "IncomeAfterTaxes"),
            "parent_net_margin_pct": ratio(values, "EquityAttributableToOwnersOfParent"),
            "input_periods": sorted({r["period_end"] for r in resolved["rows"]}),
            "field_periods": values,
            "status": "partial" if values else "unavailable_unverified_field_periods",
            "basis": "source_reviewed_single_quarter_fields_only; unknown vendor periods excluded",
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
