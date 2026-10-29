"""Data preparation in Actions; local Codex debate orchestration and evidence validation."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from tradingagents.dataflows.errors import VendorError
from tradingagents.dataflows.taiwan.provider import TaiwanMarketDataProvider

from .bundle import build_bundle, check_dates, fingerprint, reject_secret
from .debate import STAGES, load_json, load_run, register_agent, start_run, stop, submit, write_json


def main(argv=None, provider=None, material_client=None, resolution_client=None):
    parser = argparse.ArgumentParser(description="Taiwan research: ChatGPT-auth Codex only, no orders")
    commands = parser.add_subparsers(dest="command", required=True)
    publication = commands.add_parser("publish-forward", help="Seal explicitly approved forecasts at actual issue time; no valuation promotion")
    publication.add_argument("--bundle", type=Path, required=True)
    publication.add_argument("--model", type=Path, required=True)
    publication.add_argument("--debate", type=Path, required=True)
    publication.add_argument("--selections", type=Path, required=True)
    publication.add_argument("--review-input", type=Path, required=True)
    publication.add_argument("--release-dir", type=Path, required=True)
    gap_audit = commands.add_parser("audit-gap-closure", help="Close scoped investigations without upgrading data eligibility")
    gap_audit.add_argument("--ledger", type=Path, required=True)
    gap_audit.add_argument("--review-input", type=Path, required=True)
    gap_audit.add_argument("--output-dir", type=Path, required=True)
    numeric_audit = commands.add_parser("audit-original-values", help="Verify quoted official values and keep unresolved denominator scope")
    numeric_audit.add_argument("--research", type=Path, required=True)
    numeric_audit.add_argument("--review-input", type=Path, required=True)
    numeric_audit.add_argument("--transcriptions", type=Path, required=True)
    numeric_audit.add_argument("--output-dir", type=Path, required=True)
    resolve_events = commands.add_parser("resolve-events", help="Append bounded official-source repair attempts; never waive numeric review")
    resolve_events.add_argument("--research", type=Path, required=True)
    resolve_events.add_argument("--review-input", type=Path, required=True)
    resolve_events.add_argument("--output-dir", type=Path, required=True)
    resolve_events.add_argument("--live", action="store_true", help="Opt in to bounded public official captures, no token")
    resolve_events.add_argument("--max-documents", type=int, default=1)
    refresh_market = commands.add_parser("refresh-market", help="Bounded public TWSE raw prices/institutions supplement; adjusted prices remain separately gated")
    refresh_market.add_argument("--snapshot", type=Path, required=True)
    refresh_market.add_argument("--output-dir", type=Path, required=True)
    registry = commands.add_parser("refresh-sources", help="Capture official source families into a persistent local version ledger; no secrets")
    registry.add_argument("--plan", type=Path, required=True)
    registry.add_argument("--archive-dir", type=Path, required=True)
    registry.add_argument("--output-dir", type=Path, required=True)
    registry.add_argument("--review-input-dir", type=Path)
    filing_links = commands.add_parser("audit-filing-links", help="Offline listing/form/PDF chain audit; preserve historical event ambiguity")
    filing_links.add_argument("--input", type=Path, required=True)
    filing_links.add_argument("--output-dir", type=Path, required=True)
    capture_originals = commands.add_parser("capture-originals", help="Opt-in bounded public original capture from a saved official index")
    capture_originals.add_argument("--index", type=Path, required=True)
    capture_originals.add_argument("--output-dir", type=Path, required=True)
    capture_originals.add_argument("--max-documents", type=int, default=50)
    announcement_audit = commands.add_parser("audit-announcements", help="Offline original-content and coverage audit; never modify forecasts")
    announcement_audit.add_argument("--input", type=Path, required=True)
    announcement_audit.add_argument("--output-dir", type=Path, required=True)
    prepare = commands.add_parser("prepare", help="Fetch once and create evidence-only run folders")
    prepare.add_argument("--symbols", default=os.getenv("RESEARCH_SYMBOLS", "2330,3017,3231"))
    prepare.add_argument("--start", default=os.getenv("RESEARCH_START"), required=not os.getenv("RESEARCH_START"))
    prepare.add_argument("--as-of", default=os.getenv("RESEARCH_AS_OF"), required=not os.getenv("RESEARCH_AS_OF"))
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--require-token", action="store_true")
    prepare.add_argument("--mops-publications", action="store_true",
                         help="Archive MOPS dates; numeric vintages usable only from capture date")
    prepare.add_argument("--extended", action="store_true", help="Add valuation, statements, adjusted prices, news/social and official sessions")
    prepare.add_argument("--news-days", type=int, default=3)
    prepare.add_argument("--material-events", action="store_true", help="Capture recent all-category official announcements; originals still require review")
    prepare.add_argument("--archive-dir", type=Path, help="Persist immutable captured versions")
    prepare.add_argument("--financial-period-review-dir", type=Path,
                         help="Optional stock.json reviewed period ledgers; missing company reviews stay unavailable")
    imported = commands.add_parser("from-snapshot", help="Create a run from an already saved snapshot")
    imported.add_argument("--snapshot", type=Path, required=True)
    for name in ("from-snapshot", "status", "register-agent", "submit", "stop", "run"):
        item = imported if name == "from-snapshot" else commands.add_parser(name)
        item.add_argument("--run-dir", type=Path, required=True)
        if name == "register-agent":
            item.add_argument("--role", choices=("bull", "bear", "risk"), required=True)
            item.add_argument("--agent-id", required=True)
        elif name == "submit":
            item.add_argument("--stage", choices=STAGES, required=True)
            item.add_argument("--file", type=Path, required=True)
        elif name == "stop":
            item.add_argument("--reason", required=True)
    archive = commands.add_parser("archive", help="Save an immutable captured snapshot")
    archive.add_argument("--snapshot", type=Path, required=True)
    archive.add_argument("--archive-dir", type=Path, required=True)
    filings = commands.add_parser("filings", help="Archive current official original PDF bytes and future corrections")
    filings.add_argument("--symbol", required=True)
    filings.add_argument("--year", type=int, required=True)
    filings.add_argument("--as-of", required=True)
    filings.add_argument("--archive-dir", type=Path, required=True)
    replay = commands.add_parser("replay", help="Prepare research only from a version captured by the analysis date")
    replay.add_argument("--archive-dir", type=Path, required=True)
    replay.add_argument("--symbol", required=True)
    replay.add_argument("--as-of", required=True)
    replay.add_argument("--run-dir", type=Path, required=True)
    sweep = commands.add_parser("sweep", help="Prepare independent historical research cells without LLM calls")
    sweep.add_argument("--archive-dir", type=Path, required=True)
    sweep.add_argument("--symbols", required=True)
    sweep.add_argument("--dates", required=True, help="Comma-separated YYYY-MM-DD dates")
    sweep.add_argument("--output-dir", type=Path, required=True)
    outcomes = commands.add_parser("outcomes", help="Save captured adjusted snapshots for later scoring only")
    outcomes.add_argument("--snapshot", type=Path, action="append", required=True)
    outcomes.add_argument("--output", type=Path, required=True)
    audit = commands.add_parser("audit", help="Offline repeatable source-quality audit; no paid models")
    audit.add_argument("--snapshot", type=Path, action="append", required=True)
    audit.add_argument("--output", type=Path, required=True)
    audit.add_argument("--require-complete", action="store_true")
    evaluation = commands.add_parser("evaluate", help="Score completed decisions against subsequently observed adjusted outcomes")
    evaluation.add_argument("--run-dir", type=Path, action="append", required=True)
    evaluation.add_argument("--outcomes", type=Path, required=True)
    evaluation.add_argument("--calendar", type=Path, required=True)
    evaluation.add_argument("--horizon", type=int, default=20)
    evaluation.add_argument("--benchmark", default="0050")
    evaluation.add_argument("--output", type=Path, required=True)
    upgrade = commands.add_parser("upgrade", help="Offline quality, scenarios, rolling metrics and portable three-analyst report")
    upgrade.add_argument("--snapshot", type=Path, required=True)
    upgrade.add_argument("--output-dir", type=Path, required=True)
    upgrade.add_argument("--model", type=Path)
    upgrade.add_argument("--industry", type=Path)
    upgrade.add_argument("--analysts", type=Path)
    upgrade.add_argument("--review-input", type=Path, help="Named-reviewer source packets; never infer missing statement semantics")
    upgrade.add_argument("--release", type=Path, help="Original sealed release; preserve issue time and predictions")
    upgrade.add_argument("--actuals", type=Path, help="Explicit selected actual revision list; requires --release")
    upgrade.add_argument("--extension-input", type=Path, help="Company-specific source-reviewed drivers, cash detail, valuation and frozen replay configuration")
    upgrade.add_argument("--research-plan", type=Path, help="Exact mode/cutoff, material announcement coverage, capital and four-quarter models")
    upgrade.add_argument("--debate-input", type=Path, help="Actual independent openings, rebuttals, risk and evidence-led rulings")
    upgrade.add_argument("--pdf-font", type=Path, help="Optional PDF edition: installed CJK TrueType font; requires reportlab")
    upgrade.add_argument("--official-source-store", type=Path)
    upgrade.add_argument("--official-source-snapshot")
    validation = commands.add_parser("validate-forecasts", help="Score sealed forecasts only after actual publication")
    validation.add_argument("--input", type=Path, required=True)
    validation.add_argument("--output", type=Path, required=True)
    signals = commands.add_parser("validate-signals", help="Next-session, cost-aware adjusted outcome evaluation")
    signals.add_argument("--input", type=Path, required=True)
    signals.add_argument("--output", type=Path, required=True)
    signals.add_argument("--horizon", type=int, default=20)
    acceptance = commands.add_parser("acceptance", help="Offline integration audit and three-analyst report; no credentials")
    acceptance.add_argument("--snapshot", type=Path, required=True)
    acceptance.add_argument("--output-dir", type=Path, required=True)
    acceptance.add_argument("--review-input", type=Path)
    tracking = commands.add_parser("track-release", help="Append outcomes to an original release, without reissuing forecasts")
    tracking.add_argument("--release", type=Path, required=True)
    tracking.add_argument("--actuals", type=Path)
    tracking.add_argument("--output-dir", type=Path, required=True)
    tracking.add_argument("--report", action="store_true", help="Append a readable tracking report")
    tracking.add_argument("--mock-report", action="store_true", help="Label the report as mock rehearsal, never performance")
    drill = commands.add_parser("tracking-drill", help="Offline mock-only outcome rehearsal; never publishes")
    drill.add_argument("--release", type=Path, action="append", required=True)
    drill.add_argument("--output-dir", type=Path, required=True)
    parameter = commands.add_parser("score-parameter", help="Append a versioned operating-parameter forecast comparison")
    parameter.add_argument("--release", type=Path, required=True)
    parameter.add_argument("--actual", type=Path, required=True)
    parameter.add_argument("--evaluated-at", required=True)
    parameter.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "publish-forward":
            from .prospective import publish as publish_forward
            from .publication import read_release

            path = publish_forward(args.release_dir, load_json(args.bundle), load_json(args.model),
                                   load_json(args.debate), load_json(args.selections),
                                   load_json(args.review_input)["documents"])
            released = read_release(path)
            print(json.dumps({"release": str(path), "issued_at": released["issued_at"],
                              "release_sha256": released["release_sha256"], "predictions": len(released["predictions"])}))
            return 0
        if args.command == "audit-gap-closure":
            from datetime import UTC, datetime

            from .gap_closure import audit as audit_gaps

            if args.output_dir.exists():
                raise ValueError("New gap audit directory required; previous reviews are immutable")
            reviewed = load_json(args.review_input)
            result = audit_gaps(load_json(args.ledger), reviewed["documents"],
                                datetime.now(UTC).isoformat(), reviewed["stock_id"])
            args.output_dir.mkdir(parents=True)
            write_json(args.output_dir / "gap-closure-review.json", result)
            print(json.dumps(result["counts"]))
            return 0
        if args.command == "audit-original-values":
            from datetime import UTC, datetime

            from .original_review import audit as audit_values
            from .resolution import index_rows

            research, reviewed = load_json(args.research), load_json(args.review_input)
            work = research["v6_review"]["resolution_queue"]
            if work["stock_id"] != reviewed["stock_id"] or args.output_dir.exists():
                raise ValueError("Company identity and new audit directory required")
            docs = reviewed["documents"]
            result = audit_values(work, index_rows(docs), docs, load_json(args.transcriptions), datetime.now(UTC).isoformat())
            args.output_dir.mkdir(parents=True)
            write_json(args.output_dir / "original-numeric-review.json", result)
            print(json.dumps({"stock_id": work["stock_id"], "reviewed": result["numeric_reviews_completed"],
                              "rejected": len(result["rejected"]), "forecasts_modified": False}))
            return 1 if result["rejected"] else 0
        if args.command == "resolve-events":
            from datetime import UTC, datetime

            from tradingagents.dataflows.taiwan.originals import MopsOriginalClient

            from .resolution import execute, index_rows as original_index_rows

            if not 0 <= args.max_documents <= 10:
                raise ValueError("Bounded capture budget must be 0 through 10")
            research, reviewed = load_json(args.research), load_json(args.review_input)
            work = research["v6_review"]["resolution_queue"]
            if work["stock_id"] != reviewed["stock_id"]:
                raise ValueError("Resolution company mismatch")
            documents = reviewed.get("documents", {})
            index_rows = original_index_rows(documents)
            client = (resolution_client or MopsOriginalClient()) if args.live else None
            repaired = execute(work, index_rows, documents, args.output_dir / "attempts", client,
                               datetime.now(UTC).isoformat(), args.max_documents)
            write_json(args.output_dir / "resolution-run.json", repaired)
            print(json.dumps({"stock_id": work["stock_id"], "items": len(repaired["items"]),
                              "numeric_impact_verified": False, "warnings_removed": False}))
            return 0
        if args.command == "refresh-market":
            from tradingagents.dataflows.taiwan.current_market import CurrentMarketProvider

            if args.output_dir.exists():
                raise ValueError("Use a new capture directory")
            args.output_dir.mkdir(parents=True)
            client = CurrentMarketProvider()
            try:
                refreshed = client.refresh(load_json(args.snapshot))
                write_json(args.output_dir / "snapshot.json", refreshed)
            finally:
                write_json(args.output_dir / "capture-receipts.json", client.receipts)
            print(json.dumps({"status": "raw_and_institutions_refreshed", "adjusted_prices_modified": False}))
            return 0
        if args.command == "refresh-sources":
            from .source_registry import refresh

            plan = load_json(args.plan)
            reviews = ({c["stock_id"]: load_json(args.review_input_dir / (c["stock_id"] + ".json")) for c in plan["companies"]}
                       if args.review_input_dir else None)
            result = refresh(plan, args.archive_dir, args.output_dir, reviews=reviews)
            print(json.dumps({"companies": list(result["companies"]), "requests": result["request_count"], "pdfs": result["pdf_count"]}))
            return 0
        if args.command == "audit-filing-links":
            from .filing_link_audit import audit as audit_filing_links

            result = audit_filing_links(load_json(args.input), args.output_dir)
            print(json.dumps({"documents": result["unique_current_documents"], "verified_event_version_links": result["verified_event_version_links"]}))
            return 0
        if args.command in {"capture-originals", "audit-announcements"}:
            from .announcement_audit import audit as audit_announcements, capture

            result = (capture(load_json(args.index), args.output_dir, args.max_documents)
                      if args.command == "capture-originals" else audit_announcements(load_json(args.input), args.output_dir))
            print(json.dumps({"status": result.get("status", "captured"), "forecast_modified": False}))
            return 0
        if args.command == "score-parameter":
            from .parameter_tracking import score

            if args.output.exists() or args.output.resolve() in {args.release.resolve(), args.actual.resolve()}:
                raise ValueError("Use a new parameter evaluation output; originals cannot be overwritten")
            result = score(load_json(args.release), load_json(args.actual), args.evaluated_at)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.output, result)
            print(json.dumps({"status": result["status"]}))
            return 0
        if args.command == "tracking-drill":
            from .tracking_drill import rehearse

            result = rehearse(args.release, args.output_dir)
            print(json.dumps({"mode": result["evaluation_mode"], "passed": result["passed"]}))
            return 0
        if args.command == "track-release":
            from .tracking import read_tracking, track

            actuals = load_json(args.actuals) if args.actuals else []
            if any(r.get("evidence_kind") == "mock" for r in actuals) and not args.mock_report:
                raise ValueError("Mock actuals require --mock-report")
            path = track(args.release, actuals, args.output_dir, mock=args.mock_report)
            result = read_tracking(path)
            if args.report or args.mock_report:
                from .tracking_report import write_tracking_report

                write_tracking_report(args.release, path, mock=args.mock_report)
            print(json.dumps({"release_sha256": result["release_sha256"],
                              "status": result["primary_initial_vintage_results"]["status"]}))
            return 0
        if args.command == "acceptance":
            from .acceptance import accept

            result = accept(load_json(args.snapshot), args.output_dir,
                            load_json(args.review_input) if args.review_input else None)
            print(json.dumps({k: result[k] for k in ("stock_id", "status", "calendar_sessions", "annual_mops_verified")}))
            return 0 if result["status"] == "passed" else 1
        if args.command in {"validate-forecasts", "validate-signals"}:
            from .validation import forecast_errors, signal_performance

            data = load_json(args.input)
            result = (forecast_errors(data['predictions'], data['actuals']) if args.command == 'validate-forecasts'
                      else signal_performance(data['signals'], data['prices'], data['sessions'], args.horizon,
                                              **data.get('cost_assumptions', {})))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.output, result)
            print(json.dumps({'status': result['status']}))
            return 0
        if args.command == "upgrade":
            from .reporting import package

            snapshot = load_json(args.snapshot)
            extension = load_json(args.extension_input) if args.extension_input else None
            if args.research_plan:
                extension = extension or {"stock_id": snapshot["instrument"]["stock_id"], "as_of": snapshot["as_of"]}
                if extension.get("research_plan"):
                    raise ValueError("Supply one research plan only")
                extension["research_plan"] = load_json(args.research_plan)
            result = package(snapshot, args.output_dir,
                             load_json(args.model) if args.model else None,
                             load_json(args.industry) if args.industry else None,
                             load_json(args.analysts) if args.analysts else None,
                             load_json(args.review_input) if args.review_input else None,
                             args.release, load_json(args.actuals) if args.actuals else None,
                             extension,
                             load_json(args.debate_input) if args.debate_input else None, args.pdf_font,
                             args.official_source_store, args.official_source_snapshot)
            print(json.dumps({"stock_id": result["stock_id"], "decision": result["decision"]["state"],
                              "forecast": result["forecast"]["status"], "source_audit": result["source_audit"]["status"],
                              "review_axes": {k: v["status"] for k, v in result["review"]["axes"].items()}}))
            return 0
        if args.command == "prepare":
            token = os.getenv("FINMIND_TOKEN", "")
            if args.require_token and not token:
                raise ValueError("FINMIND_TOKEN Secret missing; Sponsor data preparation stopped")
            check_dates(args.start, args.as_of)
            symbols = list(dict.fromkeys(args.symbols.replace(" ", ",").split(",")))
            symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
            if not symbols or len(symbols) > 10 or any(not re.fullmatch(r"\d{4,6}(\.TW|\.TWO)?", s) for s in symbols):
                raise ValueError("Use at most ten Taiwan stock codes")
            if provider is None:
                resolver = None
                if args.mops_publications or args.extended:
                    from tradingagents.dataflows.taiwan.publications import MopsPublicationResolver

                    resolver = MopsPublicationResolver(args.start, args.as_of)
                if args.extended:
                    from tradingagents.dataflows.taiwan.calendar import OfficialCalendarProvider
                    from tradingagents.dataflows.taiwan.extensions import ExtendedTaiwanProvider

                    provider = ExtendedTaiwanProvider(publication_resolver=resolver,
                                                      calendar_provider=OfficialCalendarProvider(), news_days=args.news_days)
                else:
                    provider = TaiwanMarketDataProvider(publication_resolver=resolver)
            summary = []
            for symbol in symbols:
                try:
                    snapshot = provider.snapshot(symbol, args.start, args.as_of)
                    if args.financial_period_review_dir:
                        from tradingagents.dataflows.taiwan.periods import refresh_snapshot

                        reviewed_file = args.financial_period_review_dir / (snapshot["instrument"]["stock_id"] + ".json")
                        if reviewed_file.is_file():
                            snapshot = refresh_snapshot(snapshot, load_json(reviewed_file))
                    if args.material_events:
                        from datetime import date, timedelta

                        from tradingagents.dataflows.taiwan.material_events import (
                            MaterialEventClient,
                        )

                        client = material_client or MaterialEventClient()
                        beginning = max(args.start, (date.fromisoformat(args.as_of)-timedelta(days=90)).isoformat())
                        try:
                            snapshot["material_event_audit"] = client.query(snapshot["instrument"]["stock_id"], beginning, args.as_of)
                        except VendorError as exc:
                            snapshot["material_event_audit"] = {**getattr(exc, "failure_receipt", {}), "status": "unavailable", "rows": []}
                    reject_secret(snapshot, token)
                    bundle = build_bundle(snapshot)
                    if args.archive_dir:
                        from .vintages import VintageStore

                        VintageStore(args.archive_dir).put(snapshot)
                    folder = args.output_dir / f"{symbol}-{args.as_of}"
                    start_run(bundle, folder)
                    # Original dates, raw excluded rows, and full provenance are kept for audit.
                    write_json(folder / "snapshot.json", snapshot)
                    summary.append({"symbol": symbol, "status": "prepared", "path": folder.name,
                                    "bundle_sha256": fingerprint(bundle),
                                    "critical_data_missing": bundle["requires_insufficient_data"]})
                except (VendorError, ValueError, KeyError, TypeError):
                    summary.append({"symbol": symbol, "status": "unavailable"})
            args.output_dir.mkdir(parents=True, exist_ok=True)
            write_json(args.output_dir / "preparation.json", {"as_of": args.as_of, "runs": summary})
            print(json.dumps(summary, ensure_ascii=False))
            return 0 if all(item["status"] == "prepared" for item in summary) else 1
        if args.command == "filings":
            from tradingagents.dataflows.taiwan.filings import MopsFilingArchive

            check_dates(args.as_of, args.as_of)
            archive = MopsFilingArchive(args.archive_dir)
            path = archive.capture(args.symbol, args.year, args.as_of)
            receipt = archive.verify(path)
            print(json.dumps({"status": "archived", "documents": len(receipt["documents"]),
                              "receipt_sha256": receipt["receipt_sha256"]}))
            return 0
        if args.command == "archive":
            from .vintages import VintageStore

            path = VintageStore(args.archive_dir).put(load_json(args.snapshot))
            print(json.dumps({"status": "archived", "version": path.stem}))
            return 0
        if args.command == "sweep":
            from .evaluation import prepare_sweep

            cells = prepare_sweep(args.archive_dir, args.symbols.split(','), args.dates.split(','), args.output_dir)
            print(json.dumps(cells, ensure_ascii=False))
            return 0 if all(item["status"] == "ready" for item in cells) else 1
        if args.command == "audit":
            from .health import audit_snapshots

            result = audit_snapshots([load_json(path) for path in args.snapshot])
            args.output.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.output, result)
            print(json.dumps({key: result[key] for key in ("ready", "incomplete", "audit_sha256")}))
            return 1 if args.require_complete and result["incomplete"] else 0
        if args.command == "outcomes":
            from .evaluation import outcomes_from_snapshots

            snapshots = [load_json(path) for path in args.snapshot]
            result = outcomes_from_snapshots(snapshots)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.output, {"snapshots": snapshots, "summary": result})
            print(json.dumps({"status": "saved", "symbols": sorted(result["prices"]), "analysis_inputs": False}))
            return 0
        if args.command == "evaluate":
            from tradingagents.dataflows.taiwan.calendar import verify_calendar

            from .evaluation import evaluate_runs, outcomes_from_snapshots

            calendar = load_json(args.calendar)
            if calendar["status"] != "ok":
                raise ValueError("Full official calendar coverage required for evaluation")
            sessions = verify_calendar(calendar, calendar["exchange"])
            outcomes = outcomes_from_snapshots(load_json(args.outcomes)["snapshots"])
            if outcomes["exchange"] != calendar["exchange"]:
                raise ValueError("Outcome/calendar exchange mismatch")
            result = evaluate_runs(args.run_dir, outcomes, sessions, args.horizon, args.benchmark)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.output, result)
            print(json.dumps({key: result[key] for key in ("resolved", "pending", "unscored", "direction_hit_rate")}))
            return 0
        if args.command == "replay":
            from .vintages import VintageStore

            check_dates(args.as_of, args.as_of)
            snapshot = VintageStore(args.archive_dir).select(args.symbol, args.as_of)
            start_run(build_bundle(snapshot), args.run_dir)
            write_json(args.run_dir / "snapshot.json", snapshot)
        elif args.command == "from-snapshot":
            snapshot = load_json(args.snapshot)
            reject_secret(snapshot, os.getenv("FINMIND_TOKEN", ""))
            start_run(build_bundle(snapshot), args.run_dir)
        elif args.command == "register-agent":
            register_agent(args.run_dir, args.role, args.agent_id)
        elif args.command == "submit":
            submit(args.run_dir, args.stage, load_json(args.file))
        elif args.command == "stop":
            stop(args.run_dir, args.reason)
        elif args.command == "run":
            from .runner import launch

            return launch(args.run_dir)
        state, _ = load_run(args.run_dir)
        print(json.dumps({"status": state["status"], "completed": list(state["completed"]),
                          "stop_reason": state["stop_reason"],
                          "bundle_sha256": state["bundle_sha256"]}, ensure_ascii=False))
        return 0
    except (VendorError, ValueError, KeyError, TypeError, OSError):
        # Do not echo input JSON, subprocess logs, paths supplied by a model, or credentials.
        print("Research operation failed: check dates, required fields, evidence references, and stage prerequisites.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
