"""Versioned, deterministic technical package. No LLM, transport or inferred sessions."""

import copy
import hashlib
import math
from datetime import date, timedelta
from importlib.metadata import version
from pathlib import Path

from .bundle import fingerprint

ENGINE_VERSION = "taiwan-technical-v1"
MIN_SESSIONS = 1000
DEPENDENCIES = {"TA-Lib": "0.8.3", "numpy": "2.3.5"}
MA_PERIODS = (5, 10, 20, 50, 60, 120, 200, 240)


def catalog():
    specs = []

    def add(key, function, **parameters):
        specs.append({"key": key, "function": function, "parameters": parameters})

    for kind in ("SMA", "EMA", "WMA"):
        for period in MA_PERIODS:
            add(f"{kind}_{period}", kind, timeperiod=period)
    for kind in ("HMA", "KAMA"):
        for period in (20, 60):
            add(f"{kind}_{period}", kind, timeperiod=period)
    for period in (6, 12, 14, 24):
        add(f"RSI_{period}", "RSI", timeperiod=period)
    add("MACD", "MACD", fastperiod=12, slowperiod=26, signalperiod=9)
    add(
        "KD",
        "STOCH",
        fastk_period=9,
        slowk_period=3,
        slowk_matype=0,
        slowd_period=3,
        slowd_matype=0,
    )
    add(
        "KDJ",
        "KDJ",
        fastk_period=9,
        slowk_period=3,
        slowk_matype=13,
        slowd_period=3,
        slowd_matype=13,
    )
    add("STOCHRSI", "STOCHRSI", timeperiod=14, fastk_period=14, fastd_period=3, fastd_matype=0)
    for kind in ("ADX", "ADXR", "PLUS_DI", "MINUS_DI", "WILLR", "MFI", "ATR", "NATR"):
        add(kind + "_14", kind, timeperiod=14)
    for kind, period in [
        ("CCI", 20),
        ("ROC", 10),
        ("ROC", 20),
        ("MOM", 10),
        ("TRIX", 15),
        ("AROON", 25),
        ("STDDEV", 20),
        ("CMF", 20),
        ("VWMA", 20),
    ]:
        add(f"{kind}_{period}", kind, timeperiod=period)
    add("PPO", "PPO", fastperiod=12, slowperiod=26, matype=1)
    add("ULTOSC", "ULTOSC", timeperiod1=7, timeperiod2=14, timeperiod3=28)
    for kind in ("OBV", "AD", "PVT"):
        add(kind, kind)
    add("ADOSC", "ADOSC", fastperiod=3, slowperiod=10)
    add("BBANDS", "BBANDS", timeperiod=20, nbdevup=2, nbdevdn=2, matype=0)
    add("KELTNER", "KC", timeperiod=20, atrperiod=10, nbdev=2)
    for n in (20, 60):
        add(f"DONCHIAN_{n}", "DONCHIAN", timeperiod=n)
    add("SAR", "SAR", acceleration=0.02, maximum=0.2)
    add("SUPERTREND", "SUPERTREND", timeperiod=10, multiplier=3)
    return specs


def history_audit(snapshot, *, include_as_of=False):
    """Require 1000 consecutive OFFICIAL sessions; no weekday or stock-row inference."""
    from tradingagents.dataflows.taiwan.calendar import verify_calendar

    result = {
        "required_sessions": MIN_SESSIONS,
        "engine_version": ENGINE_VERSION,
        "status": "blocked",
        "reasons": [],
    }
    cutoff = snapshot["as_of"]
    date.fromisoformat(cutoff)
    dataset = snapshot.get("datasets", {}).get("TaiwanStockPrice", {})
    if dataset.get("status") not in {"ok", "partial"}:
        return {**result, "reasons": ["price_source_unavailable"]}
    calendar = snapshot.get("market_calendar", {})
    if calendar.get("status") != "ok":
        return {**result, "reasons": ["verified_official_calendar_required"]}
    if (
        calendar.get("end", "0001-01-01")
        < (date.fromisoformat(cutoff) - timedelta(days=1)).isoformat()
    ):
        return {**result, "reasons": ["official_calendar_does_not_cover_cutoff"]}
    def eligible_date(day):
        return day < cutoff or (include_as_of and day == cutoff)

    sessions = [
        d for d in verify_calendar(calendar, snapshot["instrument"]["exchange"]) if eligible_date(d)
    ]
    rows = dataset.get("records", [])
    identities = [r["data_date"] for r in rows]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate price dates")
    eligible = {}
    from .review import _end_of_day
    from .scenarios import timestamp

    for row in rows:
        if row.get("stock_id") != snapshot["instrument"]["stock_id"]:
            raise ValueError("Price company mismatch")
        if not eligible_date(row["data_date"]):
            continue
        available = row.get("available_at", row["data_date"])
        available_time = _end_of_day(available) if len(available) == 10 else timestamp(available)
        if available_time > _end_of_day(cutoff) or timestamp(row["retrieved_at"]) > _end_of_day(
            cutoff
        ):
            raise ValueError("Price unavailable at research cutoff")
        v = row["values"]
        if any(
            isinstance(v.get(k), bool)
            or not isinstance(v.get(k), (int, float))
            or not math.isfinite(v[k])
            for k in ("open", "high", "low", "close", "volume")
        ):
            raise ValueError("Nonfinite/missing OHLCV")
        if (
            not 0
            < v["low"]
            <= min(v["open"], v["close"])
            <= max(v["open"], v["close"])
            <= v["high"]
            or v["volume"] < 0
        ):
            raise ValueError("Invalid OHLCV")
        eligible[row["data_date"]] = row
    wanted = sessions[-MIN_SESSIONS:]
    missing = [d for d in wanted if d not in eligible]
    result.update(
        available_price_rows=len(eligible),
        official_sessions_in_range=len(sessions),
        missing_sessions=missing,
        expected_last_session=sessions[-1] if sessions else None,
        calculation_window="last_1000_official_sessions; earlier rows retained in original snapshot",
    )
    if len(sessions) < MIN_SESSIONS or len(eligible) < MIN_SESSIONS:
        return {
            **result,
            "status": "insufficient_history",
            "reasons": ["fewer_than_1000_complete_sessions"],
        }
    if missing:
        return {**result, "reasons": ["missing_or_suspended_sessions_not_filled"]}
    unexpected = sorted(d for d in set(eligible) - set(sessions) if d >= wanted[0])
    if unexpected:
        raise ValueError("Price date absent from official calendar")
    return {**result, "status": "passed", "reasons": [], "selected_dates": wanted}


def acquire_history(provider, symbol, start, as_of):
    """Bounded acquisition, dependency-injected for Mock tests. Never retry source refusal."""
    attempts = []
    for attempt in range(3):
        snapshot = provider.snapshot(symbol, start, as_of)
        check = history_audit(snapshot)
        attempts.append({"start": start, "status": check["status"], "reasons": check["reasons"]})
        if check["status"] != "insufficient_history" or attempt == 2:
            break
        begin = date.fromisoformat(start)
        start = date(max(1, begin.year - 2), 1, 1).isoformat()
    snapshot = copy.deepcopy(snapshot)
    snapshot["technical_history_acquisition"] = {
        "attempts": attempts,
        "max_attempts": 3,
        "history": check,
    }
    return snapshot


def _libraries():
    for distribution, expected in DEPENDENCIES.items():
        if version(distribution) != expected:
            raise RuntimeError("Technical dependency version mismatch: " + distribution)
    import numpy as np
    import talib
    from talib import abstract

    if talib.get_compatibility() != 0:
        raise RuntimeError("Unexpected TA-Lib compatibility mode")
    if talib.__ta_version__.decode().split()[0] != "0.8.3":
        raise RuntimeError("Unexpected TA-Lib C version")
    for function in ("EMA", "KAMA", "RSI", "ADX", "ATR", "NATR"):
        if talib.get_unstable_period(function) != 0:
            raise RuntimeError("Unexpected smoothing configuration")
    return np, talib, abstract


def _calculate(rows, basis, weekly=False):
    np, ta, abstract = _libraries()
    prices = {
        k: np.asarray([r["values"][k] for r in rows], dtype=np.float64)
        for k in ("open", "high", "low", "close", "volume")
    }
    count = len(rows)
    series = {}
    specifications = {}

    def save(key, values, definition, warmup=0):
        cleaned = [
            float(v) if i >= warmup and np.isfinite(v) else None for i, v in enumerate(values)
        ]
        if len(cleaned) != count:
            raise ValueError("Indicator length mismatch")
        series[key] = cleaned
        specifications[key] = {
            **definition,
            "warmup_bars": warmup,
            "latest_status": "available"
            if cleaned and cleaned[-1] is not None
            else "unavailable_warmup_or_undefined",
        }

    specs = (
        catalog()
        if not weekly
        else [s for s in catalog() if s["key"] in {"RSI_14", "MACD", "KD", "ATR_14"}]
        + [
            {"key": f"SMA_{n}", "function": "SMA", "parameters": {"timeperiod": n}}
            for n in (10, 20, 40)
        ]
    )
    for spec in specs:
        fn = abstract.Function(spec["function"])
        fn.set_parameters(spec["parameters"])
        output = fn(prices)
        outputs = output if isinstance(output, (list, tuple)) else [output]
        for label, values in zip(fn.output_names, outputs, strict=True):
            key = spec["key"] if len(outputs) == 1 else spec["key"] + "." + label
            if basis == "vendor_adjusted" and "volume" in str(fn.input_names):
                values = np.full(count, np.nan)
            save(
                key,
                values,
                {**spec, "parameters": dict(fn.parameters), "output": label, "price_basis": basis},
                fn.lookback,
            )

    def lag(v, n):
        return (
            np.concatenate((np.full(n, np.nan), np.asarray(v, dtype=float)[:-n]))
            if n < len(v)
            else np.full(count, np.nan)
        )

    def safe(a, b):
        return np.divide(a, b, out=np.full(count, np.nan), where=np.asarray(b) != 0)

    close, high, low, vol = (prices[k] for k in ("close", "high", "low", "volume"))

    def custom(key, v, definition, n=0):
        save(
            key,
            v,
            {"function": "project_formula_v1", "definition": definition, "price_basis": basis},
            n,
        )

    if not weekly:
        for n in (6, 12, 24):
            custom(
                f"BIAS_{n}", (safe(close, ta.SMA(close, n)) - 1) * 100, "(close/SMA-1)*100", n - 1
            )
        for n in (5, 20):
            custom(f"VOLUME_SMA_{n}", ta.SMA(vol, n), "SMA(reported volume)", n - 1)
        custom(
            "VOLUME_RATIO_20",
            safe(vol, lag(ta.SMA(vol, 20), 1)),
            "volume/mean(previous20), excludes today",
            20,
        )
        for n in (20, 60, 240):
            custom(
                f"SMA_{n}_SLOPE_5",
                (safe(ta.SMA(close, n), lag(ta.SMA(close, n), 5)) - 1) * 100,
                "MA/MA_5_sessions_ago-1",
                n + 4,
            )
        bb = ta.BBANDS(close, timeperiod=20, nbdevup=2, nbdevdn=2, matype=0)
        custom(
            "BB_PERCENT_B", safe(close - bb[2], bb[0] - bb[2]), "(close-lower)/(upper-lower)", 19
        )
        custom("BB_WIDTH_PCT", safe(bb[0] - bb[2], bb[1]) * 100, "(upper-lower)/middle*100", 19)
        delta = close - lag(close, 1)
        for n in (12, 24):
            custom(
                f"PSY_{n}",
                ta.SUM((delta > 0).astype(float), n) / n * 100,
                "positive close changes/n*100",
                n,
            )
        up = np.where(delta > 0, vol, 0.0)
        down = np.where(delta < 0, vol, 0.0)
        flat = np.where(delta == 0, vol, 0.0)
        custom(
            "VR_26",
            safe(ta.SUM(up, 26) + ta.SUM(flat, 26) / 2, ta.SUM(down, 26) + ta.SUM(flat, 26) / 2)
            * 100
            if basis == "raw"
            else np.full(count, np.nan),
            "raw price/reported volume only; (up+.5flat)/(down+.5flat)*100",
            26,
        )
        custom(
            "AR_26",
            safe(ta.SUM(high - prices["open"], 26), ta.SUM(prices["open"] - low, 26)) * 100,
            "sum(high-open)/sum(open-low)*100",
            25,
        )
        custom(
            "BR_26",
            safe(
                ta.SUM(np.maximum(high - lag(close, 1), 0), 26),
                ta.SUM(np.maximum(lag(close, 1) - low, 0), 26),
            )
            * 100,
            "positive high-prevclose/prevclose-low",
            26,
        )
        prev_mid = lag((high + low) / 2, 1)
        custom(
            "CR_26",
            safe(
                ta.SUM(np.maximum(high - prev_mid, 0), 26),
                ta.SUM(np.maximum(prev_mid - low, 0), 26),
            )
            * 100,
            "positive high-prev_mid/prev_mid-low",
            26,
        )
        custom(
            "BBI",
            sum(ta.SMA(close, n) for n in (3, 6, 12, 24)) / 4,
            "mean(SMA3,SMA6,SMA12,SMA24)",
            23,
        )
        tenkan = (ta.MAX(high, 9) + ta.MIN(low, 9)) / 2
        kijun = (ta.MAX(high, 26) + ta.MIN(low, 26)) / 2
        custom("ICHIMOKU.TENKAN", tenkan, "midrange9", 8)
        custom("ICHIMOKU.KIJUN", kijun, "midrange26", 25)
        custom(
            "ICHIMOKU.CLOUD_A",
            lag((tenkan + kijun) / 2, 26),
            "current visible cloud from 26 bars ago; no future close",
            51,
        )
        custom(
            "ICHIMOKU.CLOUD_B",
            lag((ta.MAX(high, 52) + ta.MIN(low, 52)) / 2, 26),
            "visible52-midrange displaced26; no backshifted future close",
            77,
        )
        for n in (5, 20, 60, 120):
            custom(
                f"RETURN_{n}",
                (safe(close, lag(close, n)) - 1) * 100,
                "close/close_n_sessions_ago-1",
                n,
            )
        # True period VWAP needs reviewed turnover units/scope; do not silently substitute HLC3.
        custom(
            "VWAP_TURNOVER",
            np.full(count, np.nan),
            "unavailable: reviewed turnover amount and scope required",
        )
    patterns = {}
    if not weekly:
        for name in sorted(ta.get_function_groups()["Pattern Recognition"]):
            fn = abstract.Function(name)
            values = fn(prices)
            patterns[name] = {
                "parameters": dict(fn.parameters),
                "warmup_bars": fn.lookback,
                "values": [int(v) if i >= fn.lookback else None for i, v in enumerate(values)],
                "score_is_not_probability": True,
            }
    dates = [r["data_date"] for r in rows]
    events = []
    for a, b, label in [
        ("SMA_5", "SMA_20", "SMA5_20"),
        ("MACD.macd", "MACD.macdsignal", "MACD_SIGNAL"),
    ]:
        if a in series and b in series:
            x, y = series[a], series[b]
            for i in range(1, count):
                if any(v is None for v in (x[i - 1], y[i - 1], x[i], y[i])):
                    continue
                direction = (
                    "cross_up"
                    if x[i - 1] <= y[i - 1] and x[i] > y[i]
                    else "cross_down"
                    if x[i - 1] >= y[i - 1] and x[i] < y[i]
                    else None
                )
                if direction:
                    events.append(
                        {
                            "date": dates[i],
                            "rule": label,
                            "event": direction,
                            "is_trade_signal": False,
                        }
                    )
    return {
        "dates": dates,
        "bars": [r["values"] for r in rows],
        "indicators": series,
        "definitions": specifications,
        "latest": {key: values[-1] for key, values in series.items()},
        "patterns": patterns,
        "events": events,
        "indicator_columns": len(series),
        "pattern_functions": len(patterns),
        "price_basis": basis,
    }


def _weekly(rows, calendar, cutoff):
    groups = {}
    official = {}
    for d in calendar:
        key = date.fromisoformat(d).isocalendar()[:2]
        official.setdefault(key, []).append(d)
    for r in rows:
        groups.setdefault(date.fromisoformat(r["data_date"]).isocalendar()[:2], []).append(r)
    complete = []
    incomplete = []
    for key, items in sorted(groups.items()):
        monday = date.fromisocalendar(*key, 1)
        if monday + timedelta(days=5) > date.fromisoformat(cutoff) or [
            r["data_date"] for r in items
        ] != official.get(key):
            incomplete.extend(r["data_date"] for r in items)
            continue
        values = [r["values"] for r in items]
        complete.append(
            {
                "data_date": items[-1]["data_date"],
                "values": {
                    "open": values[0]["open"],
                    "high": max(v["high"] for v in values),
                    "low": min(v["low"] for v in values),
                    "close": values[-1]["close"],
                    "volume": sum(v["volume"] for v in values),
                },
            }
        )
    return complete, incomplete


def build(snapshot):
    from .market_observation import post_close_inputs

    snapshot, captures = post_close_inputs(snapshot)
    check = history_audit(snapshot, include_as_of=captures is not None)
    package = {
        "schema_version": 1,
        "engine_version": ENGINE_VERSION,
        "stock_id": snapshot["instrument"]["stock_id"],
        "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "as_of": snapshot["as_of"],
        "input_sha256": fingerprint(
            {
                "instrument": snapshot["instrument"],
                "as_of": snapshot["as_of"],
                "prices": snapshot["datasets"].get("TaiwanStockPrice"),
                "adjusted": snapshot.get("extensions", {}).get("TaiwanStockPriceAdj"),
                "calendar": snapshot.get("market_calendar"),
                **({"post_close_captures": captures} if captures else {}),
            }
        ),
        "history": check,
        "status": "blocked_history",
        "price_series": {},
        "dependency_lock": DEPENDENCIES,
        "legacy_indicators_replaced": False,
        "paid_llm_api": False,
        "strategy_edge_established": False,
    }
    if check["status"] != "passed":
        return package
    try:
        _libraries()
    except (ImportError, RuntimeError, LookupError) as exc:
        return {**package, "status": "blocked_dependency", "dependency_error": type(exc).__name__}
    selected = check["selected_dates"]
    raw = {r["data_date"]: r for r in snapshot["datasets"]["TaiwanStockPrice"]["records"]}
    rows = [raw[d] for d in selected]
    package["price_series"]["raw"] = _calculate(rows, "raw")
    adjusted = snapshot.get("extensions", {}).get("TaiwanStockPriceAdj", {}).get("records", [])
    adj = {r["data_date"]: r for r in adjusted}
    if len(adj) != len(adjusted):
        raise ValueError("Duplicate adjusted price dates")
    if all(d in adj for d in selected):
        # Validate adjusted OHLC/date/time too, before calculating a second independent basis.
        proxy = copy.deepcopy(snapshot)
        proxy["datasets"]["TaiwanStockPrice"]["records"] = adjusted
        if history_audit(proxy, include_as_of=captures is not None)["status"] == "passed":
            package["price_series"]["vendor_adjusted"] = _calculate(
                [adj[d] for d in selected], "vendor_adjusted"
            )
    if captures:
        from tradingagents.dataflows.taiwan.calculations import institutional_metrics

        flow = institutional_metrics(
            snapshot["datasets"]["TaiwanStockInstitutionalInvestorsBuySell"]["records"], rows[-20:],
            snapshot["instrument"]["exchange"],
        )
        package["post_close_observation"] = {
            "data_date": snapshot["as_of"], "captures": captures, "institutions": flow,
            "trade_signal_issued": False,
        }
    weeks, incomplete = _weekly(rows, snapshot["market_calendar"]["sessions"], snapshot["as_of"])
    package["weekly"] = {
        "complete": _calculate(weeks, "raw", True) if weeks else None,
        "excluded_partial_dates": incomplete,
        "rule": "ISO week complete from Saturday with all official sessions present; no partial first/last week",
    }
    package.update(
        status="ready",
        catalog_sha256=fingerprint(catalog()),
        library_note="TA-Lib default compatibility/zero unstable periods; warmup and undefined denominators remain null",
    )
    return package
