"""Manual outcome diagnostics for immutable one-shot research records."""

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from decimal import Decimal, DecimalException
from pathlib import Path
from urllib.parse import urlsplit

from tradingagents.dataflows.taiwan.calculations import institutional_metrics
from tradingagents.dataflows.taiwan.calendar import verify_calendar
from tradingagents.dataflows.taiwan.extensions import ADJUSTED, normalize
from tradingagents.dataflows.taiwan.provider import INSTITUTIONS, PRICE, TaiwanMarketDataProvider

from .bundle import build_bundle, canonical, fingerprint
from .delivery import read_bytes
from .forecast_journal import read_research
from .publication import quarter_end
from .scenarios import known_date, timestamp
from .tracking import initial_vintage
from .validation import forecast_errors


def _bars(snapshot, dataset, now):
    section = snapshot["extensions"] if dataset == ADJUSTED else snapshot["datasets"]
    rows = {}
    for row in section.get(dataset, {}).get("records", []):
        if (timestamp(row["retrieved_at"]) > now
                or row["published_at"] and timestamp(row["published_at"]) > now):
            continue
        raw = row["raw"]
        convert = normalize if dataset == ADJUSTED else TaiwanMarketDataProvider._normalize
        if (raw.get("stock_id") != snapshot["instrument"]["stock_id"] or raw.get("date") != row["data_date"]
                or convert(dataset, raw) != (row["period"], row["values"], row["units"])
                or row["data_date"] in rows):
            raise ValueError("Market bar differs from captured original or has duplicate date")
        rows[row["data_date"]] = row
    return rows


def market_metrics(adjusted, benchmark, raw, setup, invalidation):
    entry = adjusted[0]["open"]
    terminal = adjusted[-1]["close"]
    gross = terminal / entry
    benchmark_return = benchmark[-1]["close"] / benchmark[0]["open"] - 1
    net_return = gross * (1 - setup["exit_cost_bps"] / 10000) / (1 + setup["entry_cost_bps"] / 10000) - 1
    # Absolute raw price thresholds are not comparable across a detected adjustment.
    ratios = [r["close"] / a["close"] for r, a in zip(raw, adjusted, strict=True)] if raw else []
    levels_comparable = bool(ratios) and all(math.isclose(x, ratios[0], rel_tol=0.001) for x in ratios)
    stop = max(setup["invalidation_twd"], invalidation)
    return {
        "stock_return_pct": (gross - 1) * 100, "net_cost_sensitivity_pct": net_return * 100,
        "benchmark_return_pct": benchmark_return * 100,
        "excess_return_pct": (gross - 1 - benchmark_return) * 100,
        "mfe_pct": max(0, max(b["high"] for b in adjusted) / entry - 1) * 100,
        "mae_pct": min(0, min(b["low"] for b in adjusted) / entry - 1) * 100,
        "net_excess_sensitivity_pct": (net_return - benchmark_return) * 100,
        "benchmark_regime": "rising" if benchmark_return > 0 else "falling" if benchmark_return < 0 else "flat",
        "raw_level_check": "observed" if levels_comparable else "unavailable_adjustment_or_missing_raw",
        "invalidation_close_observed": any(b["close"] < stop for b in raw) if levels_comparable else None,
        "target_close_observed": any(b["close"] >= setup["target_twd"] for b in raw) if levels_comparable else None,
        "basis": "vendor_adjusted_next_open_to_horizon_close_no_fill_inference",
    }


def market_results(record, snapshots, benchmark, now):
    conditions = record["decision_conditions"]
    if record["scoring_status"] != "pending" or conditions["status"] != "conditional_candidate":
        return [{"sessions": h, "status": "unavailable", "reason": record["scoring_status"]
                 if record["scoring_status"] != "pending" else "no_sealed_trade_candidate"} for h in (5, 20)]
    stock = snapshots.get(record["stock_id"])
    index = snapshots.get(benchmark)
    if not stock or not index:
        return [{"sessions": h, "status": "pending", "reason": "stock_or_benchmark_capture_missing"} for h in (5, 20)]
    calendar = stock.get("market_calendar")
    anchor = conditions["market_session"]
    if (not calendar or calendar["status"] != "ok"
            or any(timestamp(a["retrieved_at"]) > now for a in calendar["archives"])):
        return [{"sessions": h, "status": "pending", "reason": "verified_calendar_unavailable"} for h in (5, 20)]
    exchange = stock["instrument"]["exchange"]
    sessions = verify_calendar(calendar, exchange)
    if anchor not in sessions:
        return [{"sessions": h, "status": "unavailable", "reason": "calendar_does_not_cover_sealed_anchor"} for h in (5, 20)]
    dates = [d for d in sessions if anchor < d < min(stock["as_of"], index["as_of"])
             and d < known_date(now.isoformat()).isoformat()]
    adj, bench = _bars(stock, ADJUSTED, now), _bars(index, ADJUSTED, now)
    raw = _bars(stock, PRICE, now)
    results = []
    for horizon in (5, 20):
        window = dates[:horizon]
        row = {"sessions": horizon, "status": "pending", "reason": "window_not_observed"}
        if window and timestamp(record["recorded_at"]) >= timestamp(window[0] + "T09:00:00+08:00"):
            row.update(status="unavailable", reason="archive_after_first_session_open")
        elif len(window) == horizon:
            if any(d not in adj or d not in bench for d in window):
                row.update(status="unavailable", reason="missing_aligned_adjusted_bars")
            else:
                raw_values = []
                # The signal-to-entry step is also relevant to fixed raw levels.
                if all(d in raw and d in adj for d in [anchor, *window]):
                    anchor_ratio = raw[anchor]["values"]["close"] / adj[anchor]["values"]["close"]
                    if all(math.isclose(raw[d]["values"]["close"] / adj[d]["values"]["close"],
                                        anchor_ratio, rel_tol=0.001) for d in window):
                        raw_values = [raw[d]["values"] for d in window]
                values = market_metrics([adj[d]["values"] for d in window],
                                        [bench[d]["values"] for d in window], raw_values,
                                        conditions["hypothetical"], conditions["technical"]["invalidation_level"])
                flows = [r for r in stock["datasets"].get(INSTITUTIONS, {}).get("records", [])
                         if r["data_date"] in window[:5] and timestamp(r["retrieved_at"]) <= now
                         and (not r["published_at"] or timestamp(r["published_at"]) <= now)]
                for r in flows:
                    original = r["raw"]
                    if (original.get("stock_id") != record["stock_id"] or original.get("date") != r["data_date"]
                            or TaiwanMarketDataProvider._normalize(INSTITUTIONS, original)
                            != (r["period"], r["values"], r["units"])):
                        raise ValueError("Institutional observation differs from captured original")
                foreign = institutional_metrics(flows, [adj[d] for d in window[:5]], exchange)["groups"].get("foreign", {})
                row.update(status="observed", reason=None, entry_date=window[0], exit_date=window[-1],
                           foreign_next_five_net_shares=foreign.get("window_net_shares") if foreign.get("coverage_complete") else None,
                           **values)
        results.append(row)
    return results


def verify_actual_quote(row):
    from .company_database import _normal, _text

    raw = read_bytes(row["source_file"])
    if hashlib.sha256(raw).hexdigest() != row.get("source_sha256"):
        raise ValueError("Actual archived source bytes mismatch")
    if not row.get("reviewer") or not row.get("source_quote") or not row.get("raw_value"):
        raise ValueError("Actual needs a named review and a numeric original quote")
    quote = _normal(row["source_quote"])
    literal = _normal(row["raw_value"])
    if (quote not in _normal(_text(raw, row["source_page"], row.get("encoding", "utf-8-sig")))
            or not re.search(r"(?<![\d.,-])" + re.escape(literal) + r"(?![\d.,])", quote)):
        raise ValueError("Actual numeric quote absent from archived source")
    multiplier = row.get("multiplier")
    allowed = {1} if row["metric"] == "eps" else {1, 1000, 1000000, 100000000}
    if type(multiplier) is not int or multiplier not in allowed:
        raise ValueError("Unverified actual unit multiplier")
    try:
        expected = Decimal(literal.replace(",", "")) * multiplier
        if not expected.is_finite() or expected != Decimal(str(row["value"])):
            raise ValueError("Actual normalized value differs from original quote")
    except DecimalException as error:
        raise ValueError("Invalid actual numeric quote") from error


def financial_results(directory, record, actuals, now):
    periods = record["conditional_valuation"]["periods"]
    if record["scoring_status"] != "pending" or not periods:
        return {"status": "unavailable", "reason": "no_prospective_conditional_model"}
    model = json.loads(read_bytes(Path(directory) / "forward-estimate.json"))
    predictions = []
    issued = record["recorded_at"]
    for scenario, case in model["scenarios"].items():
        for row in case["quarters"]:
            if row["period"] not in periods or quarter_end(row["period"]) <= known_date(issued):
                continue
            for metric, key, basis in (("eps", "eps_twd", "diluted_single_quarter"),
                                       ("revenue", "revenue_twd", "single_quarter_twd")):
                if row.get(key) is not None:
                    predictions.append({"stock_id": record["stock_id"], "period": row["period"],
                                        "metric": metric, "basis": basis, "value": row[key],
                                        "model_version": f"{record['research_id']}:{scenario}",
                                        "issued_at": issued, "input_snapshot_sha256": record["snapshot_sha256"]})
    keys = {(r["period"], r["metric"], r["basis"]) for r in predictions}
    eligible = []
    for row in actuals:
        if (row.get("stock_id") != record["stock_id"] or not initial_vintage(row)
                or row.get("evidence_kind") == "mock" or not row.get("revision_id")
                or row.get("unit") != ("TWD/share" if row.get("metric") == "eps" else "TWD")
                or (row.get("period"), row.get("metric"), row.get("basis")) not in keys):
            continue
        source = urlsplit(row.get("source_url", ""))
        if (source.scheme != "https" or source.username or source.password or source.hostname not in {"mops.twse.com.tw", "doc.twse.com.tw",
                                                               "www.twse.com.tw", "www.tpex.org.tw"}):
            continue
        published, captured = row.get("published_at"), row.get("captured_at")
        if not (published and captured and len(published) > 10 and len(captured) > 10
                and timestamp(issued) < timestamp(published) <= timestamp(captured) <= now
                and known_date(published) >= quarter_end(row["period"])):
            continue
        verify_actual_quote(row)
        eligible.append(row)
    result = forecast_errors(predictions, eligible, known_date(now.isoformat()).isoformat())
    result["actual_lineage"] = [{k: row[k] for k in ("period", "metric", "basis", "source_url", "source_sha256",
                                                     "published_at", "captured_at", "revision_id", "source_locator")}
                                for row in eligible]
    result["scope"] = "conditional_scenario_diagnostics_not_formal_prediction_score"
    return result


def score_reports(directories, market_snapshots, actuals, benchmark, output, clock=None):
    now = (clock or (lambda: datetime.now(UTC)))()
    if now.tzinfo is None:
        raise ValueError("Scoring requires a timezone")
    directories = [Path(directory).resolve() for directory in directories]
    target = Path(output).resolve()
    if any(target.is_relative_to(directory) for directory in directories):
        raise ValueError("Save evaluation outside the immutable research archive")
    if any(row.get("evidence_kind") == "mock" for row in actuals):
        raise ValueError("Mock actuals cannot enter manual research scoring")
    snapshots = {}
    for snapshot in market_snapshots:
        build_bundle(snapshot)
        code = snapshot["instrument"]["stock_id"]
        if code in snapshots or snapshot["as_of"] > known_date(now.isoformat()).isoformat():
            raise ValueError("Duplicate issuer or future market capture")
        snapshots[code] = snapshot
    scores = []
    for directory in directories:
        record, manifest = read_research(directory)
        if timestamp(record["recorded_at"]) > now or benchmark == record["stock_id"]:
            raise ValueError("Future research archive or same-issuer benchmark")
        market = market_results(record, snapshots, benchmark, now)
        financial = financial_results(directory, record, actuals, now)
        score = {"research_id": record["research_id"], "research_manifest_sha256": manifest["manifest_sha256"],
                 "stock_id": record["stock_id"], "checked_at": now.isoformat(), "benchmark": benchmark,
                 "market": market, "fundamentals": financial,
                 "attribution": {
                     "data_gaps": sorted({r["reason"] for r in market if r.get("reason")}),
                     "observed_price_invalidation_windows": [r["sessions"] for r in market if r.get("invalidation_close_observed") is True],
                     "earnings_below_sealed_minimum_periods": sorted({
                         r["period"] for r in financial.get("resolved", []) if r["metric"] == "eps"
                         and record["decision_conditions"]["fundamental"].get("minimum_eps_twd") is not None
                         and r["actual_value"] < record["decision_conditions"]["fundamental"]["minimum_eps_twd"]
                     }),
                     "causal_attribution": "unavailable",
                 },
                 "outcome_snapshot_sha256": {k: fingerprint(snapshots[k]) for k in (benchmark, record["stock_id"]) if k in snapshots}}
        score["evaluation_sha256"] = fingerprint(score)
        scores.append(score)
    if not scores or len({r["research_id"] for r in scores}) != len(scores):
        raise ValueError("Provide distinct archived research editions")
    market = [row for score in scores for row in score["market"]]
    summary = {"research_count": len(scores), "pending_market_windows": sum(r["status"] == "pending" for r in market),
               "observed_market_windows": sum(r["status"] == "observed" for r in market),
               "unavailable_market_windows": sum(r["status"] == "unavailable" for r in market),
               "observed_by_horizon_and_regime": {
                   str(h): {regime: sum(r["sessions"] == h and r.get("benchmark_regime") == regime for r in market)
                            for regime in ("rising", "falling", "flat")} for h in (5, 20)},
               "resolved_financial_rows": sum(len(r["fundamentals"].get("resolved", [])) for r in scores),
               "overlapping_windows_are_independent": False, "empirical_edge_proven": False}
    directory = target
    directory.mkdir(parents=True)
    with (directory / "forecast-evaluation.json").open("x", encoding="utf8") as handle:
        handle.write(canonical(scores) + "\n")
    with (directory / "research-performance-summary.json").open("x", encoding="utf8") as handle:
        handle.write(canonical(summary) + "\n")
    return summary
