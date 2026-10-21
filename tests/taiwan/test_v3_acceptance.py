import base64
import copy
import hashlib
import json
import unittest

from tests.taiwan.test_research_completeness import cases
from tradingagents.dataflows.taiwan.publications import MopsAnnouncementClient
from tradingagents.research import (
    announcement_coverage as coverage,
    capital_model,
    forward_model,
    knowledge,
    model_disclosure,
    parameter_tracking,
    strategy_evaluation,
    valuation_basis,
)
from tradingagents.research.bundle import fingerprint

CTX = {
    "mode": "retrospective_diagnostic",
    "stock_id": "3017",
    "cutoff_at": "2026-10-20T23:00:00+08:00",
}


def index_doc(rows, total=None):
    payload = {"status": "success", "data": rows}
    if total is not None:
        payload["total_count"] = total
    raw = json.dumps(payload).encode()
    return {
        "source_url": MopsAnnouncementClient.endpoint,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "stock_id": "3017",
        "content_base64": base64.b64encode(raw).decode(),
        "captured_at": "2026-10-20T23:01:00+08:00",
        "published_at": None,
        "public_capture_verified": True,
        "query": {"CO_ID": "3017", "SDATE": "115/10/01", "EDATE": "115/10/20", "AN": ""},
    }


def windows():
    return [
        {
            "category": k,
            "from_at": "2026-10-01T00:00:00+08:00",
            "through_at": CTX["cutoff_at"],
            "document_id": "index",
            "reviewed": True,
        }
        for k in coverage.CATEGORIES
    ]


def event(code="F22", subject="月營收"):
    return {
        "COMPANY_ID": "3017",
        "CDATE": "115/10/10",
        "CTIME": "15:00:00",
        "AN_CODE": code,
        "SUBJECT": subject,
        "HYPERLINK": "https://mopsov.twse.com.tw/mops/web/original?company=3017&seq=1",
    }


class AnnouncementTests(unittest.TestCase):
    def test_identical_official_duplicates_preserved_but_counted_once(self):
        row = event()
        result = coverage.reconcile(
            CTX, {"index": index_doc([row, row], 2)}, [], windows(), {"index"}
        )
        self.assertEqual(result["mother_set_count"], 1)
        self.assertEqual(result["index_sources"][0]["identical_duplicate_rows"], 1)

    def test_index_captured_before_window_end_cannot_certify_absence(self):
        doc = index_doc([], 0)
        doc["captured_at"] = "2026-10-20T22:00:00+08:00"
        result = coverage.reconcile(CTX, {"index": doc}, [], windows(), {"index"})
        self.assertFalse(result["source_coverage_verified"])

    def test_unknown_material_cannot_be_silently_ignored(self):
        result = coverage.reconcile(
            CTX, {"index": index_doc([event("M99", "其他重大事項")], 1)}, [], windows(), {"index"}
        )
        self.assertFalse(result["source_coverage_verified"])
        self.assertIn("unclassified_material", result["unprocessed_events"][0]["categories"])

    def test_review_flag_cannot_bless_unexhausted_index(self):
        doc = index_doc([])
        r = coverage.reconcile(CTX, {"index": doc}, [], windows(), {"index"})
        self.assertFalse(r["latest_complete_research_allowed"])
        self.assertEqual(r["categories"]["capital"]["state"], "index_exhaustion_unverified")

    def test_bound_empty_total_proves_scoped_no_updates(self):
        r = coverage.reconcile(CTX, {"index": index_doc([], 0)}, [], windows(), {"index"})
        self.assertTrue(r["source_coverage_verified"])
        self.assertFalse(r["latest_complete_research_allowed"])
        self.assertEqual(r["categories"]["earnings"]["state"], "verified_no_updates")

    def test_official_mother_set_discovers_omitted_monthly_release(self):
        r = coverage.reconcile(CTX, {"index": index_doc([event()], 1)}, [], windows(), {"index"})
        self.assertEqual(r["categories"]["monthly_revenue"]["state"], "missing_document")
        self.assertEqual(len(r["unprocessed_events"]), 1)

    def test_hash_binding_rejects_changed_index(self):
        doc = index_doc([], 0)
        doc["sha256"] = "bad"
        with self.assertRaises(ValueError):
            coverage.reconcile(CTX, {"index": doc}, [], windows(), {"index"})

    def test_future_index_event_excluded(self):
        row = event()
        row["CTIME"] = "23:30:00"
        row["CDATE"] = "115/10/20"
        r = coverage.reconcile(CTX, {"index": index_doc([row], 1)}, [], windows(), {"index"})
        self.assertEqual(r["mother_set_count"], 0)

    def test_unknown_exhaustion_and_correction_link_remain_distinct(self):
        row = event(subject="更正月營收")
        doc = {"source_url": row["HYPERLINK"], "sha256": "a", "revision_id": "revision-2"}
        r = coverage.reconcile(
            CTX,
            {"index": index_doc([row], 1), "original": doc},
            [{"original_url": row["HYPERLINK"], "document_id": "original"}],
            windows(),
            {"index", "original"},
        )
        self.assertFalse(r["revision_links"][0]["revision_link_verified"])
        self.assertEqual(r["unprocessed_events"][0]["status"], "revision_link_pending")

    def test_legacy_coverage_without_index_never_passes(self):
        doc = {
            "source_url": "https://example.com",
            "sha256": "x",
            "captured_at": CTX["cutoff_at"],
            "published_at": "2026-10-01",
        }
        r = knowledge.completeness(CTX, {"index": doc}, [], windows())
        self.assertFalse(r["formal_valuation_allowed"])


def pe_rows(values):
    return [
        {
            "date": f"2026-10-{day:02d}",
            "reported_pe": pe,
            "captured_at": CTX["cutoff_at"],
            "source_sha256": str(day),
        }
        for day, pe in values
    ]


class ValuationTests(unittest.TestCase):
    def test_cached_median_cannot_be_changed_without_detection(self):
        original = valuation_basis.sample(pe_rows([(1, 10), (2, 20)]), CTX["cutoff_at"])
        changed = copy.deepcopy(original)
        changed["median"] = 99
        with self.assertRaises(ValueError):
            valuation_basis.compare(original, changed)

    def test_same_input_same_receipt(self):
        a = valuation_basis.sample(pe_rows([(1, 10), (2, 20)]), CTX["cutoff_at"])
        b = valuation_basis.sample(list(reversed(pe_rows([(1, 10), (2, 20)]))), CTX["cutoff_at"])
        self.assertEqual(a["sample_sha256"], b["sample_sha256"])
        self.assertFalse(a["usable_as_verified_pe"])

    def test_attribution_adds_to_total_change(self):
        a = valuation_basis.sample(pe_rows([(1, 10), (2, 20), (3, 30)]), CTX["cutoff_at"])
        b = valuation_basis.sample(pe_rows([(2, 22), (3, 31), (4, 40)]), CTX["cutoff_at"])
        r = valuation_basis.compare(a, b)
        x = r["attribution"]
        self.assertAlmostEqual(
            x["removed_dates_effect"] + x["common_date_revision_effect"] + x["added_dates_effect"],
            x["total_change"],
        )
        self.assertEqual(len(r["revised_common_dates"]), 2)

    def test_fixed_window_excludes_outside_and_null_explicitly(self):
        r = valuation_basis.sample(
            pe_rows([(1, 10), (2, None), (3, 30)]),
            CTX["cutoff_at"],
            {"start": "2026-10-02", "end": "2026-10-20"},
        )
        self.assertEqual(r["count"], 1)
        self.assertEqual(
            {e["reason"] for e in r["excluded"]}, {"outside_fixed_window", "missing_pe"}
        )

    def test_current_backfill_not_historical_ttm(self):
        price = [{"stock_id": "3017", "date": "2026-10-01", "close": 100, "source_sha256": "p"}]
        eps = [
            {
                "stock_id": "3017",
                "basis": "trailing_12_month_diluted_verified",
                "basis_reviewed": True,
                "source_sha256": "f",
                "locator": "EPS note",
                "currency": "TWD",
                "price_adjustment_basis": "unadjusted",
                "published_at": "2026-08-01",
                "captured_at": "2026-10-20",
                "period_end": "2026-06-30",
            }
        ]
        r = valuation_basis.historical_ttm(
            price,
            eps,
            {
                "price_basis": "unadjusted",
                "eps_basis": "verified_ttm_diluted",
                "start": "2026-10-01",
                "end": "2026-10-01",
            },
        )
        self.assertEqual(r["count"], 0)


class DisclosureTests(unittest.TestCase):
    def test_parent_sensitivity_does_not_publish_dummy_eps(self):
        case = self.model()["scenarios"]["base"]["quarters"][0]
        spec = {
            "axes": [
                {
                    "name": "revenue",
                    "endpoints": [
                        {"revenue_multiplier": {"value": 0.9, "rationale": "test endpoint"}},
                        {"revenue_multiplier": {"value": 1.1, "rationale": "test endpoint"}},
                    ],
                }
            ]
        }
        result = model_disclosure.sensitivity(case, spec)
        self.assertIsNone(result["baseline"]["eps_twd"])
        self.assertGreater(result["ranking"][0]["parent_profit_span_twd"], 0)
        self.assertEqual(result["ranking"][0]["conditional_eps_twd"], [None, None])

    def model(self):
        spec = {name: cases() for name in ("bear", "base", "bull")}
        for rows in spec.values():
            for row in rows:
                row["inputs"]["diluted_weighted_shares"].update(
                    kind="unknown", value=None, rationale="No EPS weighting note"
                )
        return forward_model.build("2026-10-20", spec, {"anchor": 1000})

    def test_issued_quotient_is_not_eps(self):
        model = self.model()
        r = model_disclosure.describe(
            model, {"source_id": "capital", "evidence_id": "issued"}, {"issued": 100}
        )
        row = r["rows"][0]
        self.assertGreater(row["parent_profit_per_current_issued_share_twd"], 0)
        self.assertIsNone(row["official_diluted_eps"])
        self.assertIsNone(row["conditional_eps_twd"])
        self.assertEqual(len(row["share_reference_sensitivity"]), 3)
        self.assertIn("EPS note", row["blockers"][0]["required_evidence"])

    def test_partial_bridge_exposes_tax_and_pretax(self):
        row = self.model()["scenarios"]["base"]["quarters"][0]
        c = row["calculation"]
        self.assertAlmostEqual(c["pretax_profit_twd"] - c["tax_twd"], c["consolidated_profit_twd"])
        self.assertGreater(c["diluted_numerator_twd"], 0)

    def test_sensitivity_requires_dependence_and_rationale(self):
        case = forward_model.build(
            "2026-10-20", {n: cases() for n in ("bear", "base", "bull")}, {"anchor": 1000}
        )["scenarios"]["base"]["quarters"][0]
        with self.assertRaises(ValueError):
            model_disclosure.sensitivity(case, {"joint": [{"name": "bad", "changes": {}}]})
        spec = {
            "axes": [
                {
                    "name": "revenue",
                    "endpoints": [
                        {"revenue_multiplier": {"value": 0.9, "rationale": "illustrative"}},
                        {"revenue_multiplier": {"value": 1.1, "rationale": "illustrative"}},
                    ],
                }
            ]
        }
        result = model_disclosure.sensitivity(case, spec)
        self.assertGreater(result["ranking"][0]["eps_span"], 0)
        self.assertIsNone(result["probabilities"])

    def test_missing_industry_parameters_are_not_calibrated(self):
        r = model_disclosure.parameters("thermal", {}, {}, set(), ["yield", "liquid_asp_twd"])
        self.assertEqual(r["missing_count"], 2)
        self.assertFalse(r["statistically_calibrated"])

    def test_anti_dilution_computed_not_attested(self):
        components = [
            {
                "id": "cb",
                "source_id": "note",
                "weighting_reviewed": True,
                "weighted_incremental_shares": 10,
                "numerator_adjustment_twd": 100,
            }
        ]
        r = capital_model.evaluate_dilution(100, 100, 100, components)
        self.assertFalse(r["component_decisions"][0]["included"])
        self.assertEqual(r["eps_twd"], 1)
        components[0]["numerator_adjustment_twd"] = 1
        r = capital_model.evaluate_dilution(100, 100, 100, components)
        self.assertTrue(r["component_decisions"][0]["included"])
        self.assertAlmostEqual(r["eps_twd"], 101 / 110)


class TradingTests(unittest.TestCase):
    def test_locked_stop_defers_exit_without_inventing_limit_fill(self):
        from datetime import date, timedelta
        from unittest.mock import patch

        from tradingagents.research.historical_validation import short_horizon

        sessions = [(date(2026, 10, 1) + timedelta(days=i)).isoformat() for i in range(30)]
        bars = [
            {"date": day, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}
            for day in sessions
        ]
        bars[23].update(open=90, high=90, low=90, close=90)
        bars[24].update(open=92, high=94, low=91, close=93)
        plan = {"status": "candidate_pending_next_open", "stop_twd": 95, "target_twd": 110}
        with (
            patch("tradingagents.research.historical_validation.setup", return_value=plan),
            patch(
                "tradingagents.research.historical_validation.next_open",
                return_value={"status": "eligible_for_simulation"},
            ),
        ):
            result = short_horizon(bars, sessions, sessions[21], sessions[-1], 5)
        event = result["events"][0]
        self.assertEqual(event["exit_reason"], "deferred_exit_next_observable_open")
        self.assertEqual(event["exit_price"], 92)
        self.assertFalse(event["execution_verified"])

    def test_one_price_bar_is_not_assumed_fill(self):
        b = {
            "date": "2026-10-20",
            "open": 100,
            "high": 100,
            "low": 100,
            "close": 100,
            "volume": 100,
        }
        self.assertFalse(strategy_evaluation.execution_check(b, "buy")["executable"])
        b["high"] = 101
        self.assertIsNone(strategy_evaluation.execution_check(b, "buy")["executable"])

    def test_same_week_thirty_stocks_are_one_cluster(self):
        events = [{"signal_date": "2026-10-19", "net_return": 0.1}] * 30
        result = strategy_evaluation.clustered_summary(events)
        self.assertEqual(result["calendar_week_clusters"], 1)
        self.assertIsNone(result["block_bootstrap_interval"])

    def test_overlapping_horizons_not_counted_twice(self):
        a = {
            "stock_id": "3017",
            "signal_date": "2026-10-01",
            "entry_date": "2026-10-02",
            "exit_date": "2026-10-08",
        }
        b = {**a, "exit_date": "2026-10-20"}
        self.assertEqual(len(strategy_evaluation.nonoverlapping([a, b])), 1)

    def test_backfilled_fundamentals_cannot_validate_old_entry(self):
        rule = {"selected_at": "2020-01-01T00:00:00+08:00", "metric": "growth", "minimum": 0.1}
        rule["sha256"] = fingerprint(rule)
        event = {
            "stock_id": "3017",
            "signal_date": "2026-10-01",
            "entry_date": "2026-10-02",
            "exit_date": "2026-10-08",
            "net_return": 0.1,
        }
        f = {
            "stock_id": "3017",
            "issued_at": "2026-08-01",
            "captured_at": "2026-10-20",
            "release_sha256": "r",
            "input_source_sha256": "f",
            "values": {"growth": 0.2},
        }
        result = strategy_evaluation.paired_filter([event], [f], rule, CTX["cutoff_at"])
        self.assertEqual(result["status"], "unavailable_contemporaneous_fundamentals")


class TrackingTests(unittest.TestCase):
    def test_parameter_cli_does_not_overwrite_original_or_existing_review(self):
        import tempfile
        from pathlib import Path

        from tradingagents.research.__main__ import main

        release = {
            "stock_id": "3017",
            "period": "2026-Q3",
            "metric": "margin",
            "unit": "fraction",
            "basis": "quarter",
            "issued_at": "2026-10-20",
            "value": 0.25,
        }
        release["release_sha256"] = fingerprint(release)
        actual = {k: release[k] for k in ("stock_id", "period", "metric", "unit", "basis")}
        actual.update(value=0.26)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            a, b, out = root / "release.json", root / "actual.json", root / "new-review.json"
            a.write_text(json.dumps(release), encoding="utf8")
            b.write_text(json.dumps(actual), encoding="utf8")
            original = a.read_bytes()
            args = [
                "score-parameter",
                "--release",
                str(a),
                "--actual",
                str(b),
                "--evaluated-at",
                "2026-10-21T23:00:00+08:00",
                "--output",
                str(out),
            ]
            self.assertEqual(main(args), 0)
            self.assertEqual(
                json.loads(out.read_text())["status"], "pending_official_version_review"
            )
            self.assertEqual(main(args), 2)
            self.assertEqual(main(args[:-1] + [str(a)]), 2)
            self.assertEqual(a.read_bytes(), original)

    def test_already_public_actual_not_prospective_score(self):
        release = {
            "stock_id": "3017",
            "period": "2026-Q3",
            "metric": "margin",
            "unit": "fraction",
            "basis": "quarter",
            "issued_at": "2026-10-20",
            "value": 0.25,
        }
        release["release_sha256"] = fingerprint(release)
        actual = {k: release[k] for k in ("stock_id", "period", "metric", "unit", "basis")}
        actual.update(
            value=0.26,
            source_sha256="f",
            locator="p1",
            revision_id="initial",
            vintage_kind="initial",
            initial_publication_verified=True,
            published_at="2026-10-15",
            captured_at="2026-10-20",
        )
        before = copy.deepcopy(release)
        result = parameter_tracking.score(release, actual, "2026-10-21")
        self.assertEqual(result["status"], "ineligible_already_public")
        self.assertEqual(release, before)
