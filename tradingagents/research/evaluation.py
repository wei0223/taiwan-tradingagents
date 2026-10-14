"""Offline decision evaluation. No LLM calls, fills, cash ledger, or order execution."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tradingagents.dataflows.taiwan.provider import day, number
from tradingagents.dataflows.taiwan.publications import TAIPEI

from .bundle import build_bundle, check_dates, fingerprint
from .debate import STAGES, load_run, start_run, validate_result, write_json
from .vintages import VintageStore


def outcomes_from_snapshots(snapshots):
    """Rebuild scoring observations from raw-bound captured adjusted data, never LLM inputs."""
    from tradingagents.dataflows.taiwan.extensions import ADJUSTED

    prices, hashes, exchange = {}, [], None
    if not snapshots:
        raise ValueError("Captured outcome snapshots required")
    for snapshot in snapshots:
        build_bundle(snapshot)
        current = snapshot["instrument"]["exchange"]
        if exchange and exchange != current:
            raise ValueError("Use separate official-calendar evaluations for different exchanges")
        exchange = current
        code = snapshot["instrument"]["stock_id"]
        result = snapshot.get("extensions", {}).get(ADJUSTED, {})
        if result.get("status") != "ok" or not result.get("records") or code in prices:
            raise ValueError("One usable adjusted outcome capture per stock required")
        prices[code] = [{"date": row["data_date"], "stock_id": code,
                         "open": row["values"]["open"], "close": row["values"]["close"]}
                        for row in result["records"]]
        hashes.append(fingerprint(snapshot))
    return {"basis": "vendor_adjusted", "prices": prices, "exchange": exchange,
            "source_snapshot_sha256": hashes}


def prepare_sweep(store, symbols, dates, output_dir):
    """Prepare evidence-only native-Codex runs from vintages; unavailable cells are explicit."""
    store = VintageStore(store)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    cells = []
    for code in dict.fromkeys(symbols):
        for as_of in dict.fromkeys(dates):
            check_dates(as_of, as_of)
            try:
                snapshot = store.select(code, as_of)
                bundle = build_bundle(snapshot)
                run = output / f"{code}-{as_of}"
                start_run(bundle, run)
                # Evidence and original source lineage remain available for verification.
                write_json(run / "snapshot.json", snapshot)
                cells.append({"stock_id": code, "as_of": as_of, "status": "ready",
                              "run_dir": str(run), "bundle_sha256": fingerprint(bundle)})
            except ValueError:
                cells.append({"stock_id": code, "as_of": as_of, "status": "unavailable",
                              "reason": "No trustworthy vintage or invalid archive; never substitute current data"})
    write_json(output / "sweep.json", {"cells": cells, "llm_api_calls": False, "orders": False})
    return cells


def outcome_series(rows, stock_id):
    """Outcome observations can be obtained later, but are never analysis inputs."""
    result = {}
    today = datetime.now(TAIPEI).date()
    for row in rows:
        observed = day(row["date"])
        if row["stock_id"] != stock_id or observed > today:
            raise ValueError("Outcome instrument mismatch or future observation")
        opening, close = number(row["open"]), number(row["close"])
        if min(opening, close) <= 0 or row["date"] in result:
            raise ValueError("Invalid/duplicate outcome price")
        result[row["date"]] = (opening, close)
    return result


def evaluate_runs(run_dirs, outcomes, sessions, horizon=20, benchmark="0050"):
    """Next-session adjusted open to holding-horizon close; mixed is explicitly unscored."""
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise ValueError("Positive trading-session horizon required")
    if sessions != sorted(set(sessions)):
        raise ValueError("Sessions must be unique and ordered")
    for session in sessions:
        day(session)
    if outcomes.get("basis") != "vendor_adjusted":
        raise ValueError("Use an explicitly adjusted outcome archive; unadjusted returns refused")
    series = {code: outcome_series(rows, code) for code, rows in outcomes["prices"].items()}
    cells = []
    seen = set()
    for run_dir in run_dirs:
        state, bundle = load_run(run_dir)
        identity = (bundle["stock_id"], bundle["as_of"])
        if identity in seen:
            continue
        seen.add(identity)
        item = {"stock_id": identity[0], "as_of": identity[1], "status": "pending",
                "bundle_sha256": state["bundle_sha256"]}
        if outcomes.get("exchange") and outcomes["exchange"] != bundle["exchange"]:
            raise ValueError("Outcome/analysis exchange mismatch")
        if state["status"] != "complete":
            item["reason"] = "Native Codex debate not complete"
            cells.append(item)
            continue
        from .debate import load_json

        for stage in STAGES:
            validate_result(load_json(Path(run_dir) / f"{stage}.json"), stage, state, bundle, run_dir)
        verdict = load_json(Path(run_dir) / "decision.json")["verdict"]
        item["verdict"] = verdict
        future_sessions = [d for d in sessions if d > identity[1]]
        if len(future_sessions) < horizon:
            item["reason"] = "Holding horizon not yet observed"
            cells.append(item)
            continue
        entry, exit_date = future_sessions[0], future_sessions[horizon - 1]
        prices, market = series.get(identity[0], {}), series.get(benchmark, {})
        if any(d not in prices or d not in market for d in future_sessions[:horizon]):
            item["reason"] = "Missing stock/benchmark sessions; no interpolation through suspensions"
            cells.append(item)
            continue
        stock_return = prices[exit_date][1] / prices[entry][0] - 1
        benchmark_return = market[exit_date][1] / market[entry][0] - 1
        alpha = stock_return - benchmark_return
        direction = {"supports_bull": 1, "supports_bear": -1}.get(verdict)
        item.update(status="resolved" if direction else "unscored", entry_date=entry, exit_date=exit_date,
                    stock_return_pct=stock_return * 100, benchmark_return_pct=benchmark_return * 100,
                    alpha_pct=alpha * 100, direction_correct=(direction * alpha > 0) if direction else None)
        cells.append(item)
    scored = [item for item in cells if item["status"] == "resolved"]
    return {"cells": cells, "resolved": len(scored),
            "pending": sum(item["status"] == "pending" for item in cells),
            "unscored": sum(item["status"] == "unscored" for item in cells),
            "direction_hit_rate": sum(item["direction_correct"] for item in scored) / len(scored) if scored else None,
            "mean_alpha_pct": sum(item["alpha_pct"] for item in scored) / len(scored) if scored else None,
            "benchmark": benchmark, "horizon_trading_sessions": horizon,
            "direction_target": "relative return versus benchmark, not absolute price direction",
            "outcome_source_snapshot_sha256": outcomes.get("source_snapshot_sha256", []),
            "limitations": ["Independent decision evaluation; no transaction costs, filled orders or portfolio NAV",
                            "Adjusted outcomes serve scoring only; never substituted into earlier analysis bundles",
                            "Mixed/insufficient-data verdicts carry no directional claim and are not scored as wins"]}
