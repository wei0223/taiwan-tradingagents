import base64
import copy
import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime

from tradingagents.research import capital_model, forward_model, industry_models, knowledge
from tradingagents.research.bundle import fingerprint
from tradingagents.research.walk_forward import run, sample_summary


class DailyIntegrationTests(unittest.TestCase):
    def test_material_query_and_daily_report_keep_missing_original_visible(self):
        from pathlib import Path

        from tests.taiwan.test_extensions import make_provider
        from tradingagents.research.acceptance import accept

        provider, _ = make_provider()
        snapshot = provider.snapshot("2330", "2024-01-01", "2024-05-20")
        row = {
            "COMPANY_ID": "2330",
            "AN_CODE": "M99",
            "CDATE": "113/05/15",
            "CTIME": "15:21:38",
            "SUBJECT": "台積公司2024年第一季每股盈餘",
            "HYPERLINK": "https://mopsov.twse.com.tw/mops/web/test",
        }
        raw = json.dumps({"status": "success", "data": [row]}).encode()
        snapshot["material_event_audit"] = {
            "stock_id": "2330",
            "rows": [row],
            "source_url": "https://mopsov.twse.com.tw/mops/web/ezsearch_query",
            "captured_at": "2024-05-20T06:00:00+00:00",
            "published_at": None,
            "public_capture_verified": True,
            "http_status": 200,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "content_base64": base64.b64encode(raw).decode(),
        }
        with tempfile.TemporaryDirectory() as folder:
            result = accept(snapshot, folder)
            self.assertFalse(result["research_ready"])
            self.assertEqual(
                result["research_completeness"]["missing_material_events"], ["material-0"]
            )
            report = json.loads((Path(folder) / "extension.json").read_text())
            self.assertIn(
                "2024-Q1", report["research_plan"]["completeness"]["announced_earnings_periods"]
            )

    def test_material_transport_is_mockable_and_has_no_finmind_headers(self):
        from unittest.mock import Mock

        from tradingagents.dataflows.taiwan.material_events import MaterialEventClient

        transport = Mock()
        transport.post.return_value.status_code = 200
        transport.post.return_value.content = b'{"status":"success","data":[]}'
        result = MaterialEventClient(transport).query("2330", "2026-10-01", "2026-10-20")
        self.assertEqual(result["status"], "captured_unclassified")
        self.assertNotIn("headers", transport.post.call_args.kwargs)
        self.assertFalse(result["coverage_verified"])

    def test_material_transport_failure_keeps_receipt(self):
        from unittest.mock import Mock

        from tradingagents.dataflows.errors import VendorUnavailableError
        from tradingagents.dataflows.taiwan.material_events import MaterialEventClient

        transport = Mock()
        transport.post.side_effect = ConnectionError("unavailable")
        with self.assertRaises(VendorUnavailableError) as caught:
            MaterialEventClient(transport).query("2330", "2026-10-01", "2026-10-20")
        self.assertFalse(caught.exception.failure_receipt["public_capture_verified"])


def values():
    return {
        "revenue_twd": 1000,
        "variable_cogs_ratio": 0.4,
        "fixed_cogs_ex_depreciation_twd": 10,
        "cogs_depreciation_twd": 10,
        "variable_opex_ratio": 0.1,
        "fixed_opex_ex_depreciation_twd": 5,
        "opex_depreciation_twd": 5,
        "other_operating_twd": 0,
        "nonoperating_twd": 0,
        "tax_rate": 0.2,
        "noncontrolling_profit_twd": 0,
        "diluted_adjustment_twd": 0,
        "diluted_weighted_shares": 100,
    }


def cases():
    return [
        {
            "period": q,
            "inputs": {
                k: {
                    "value": v,
                    "kind": "assumption",
                    "evidence_ids": ["anchor"],
                    "rationale": "Conditional stress anchored on reviewed history, not guidance",
                }
                for k, v in values().items()
            },
        }
        for q in ("2026-Q4", "2027-Q1", "2027-Q2", "2027-Q3")
    ]


class KnowledgeTests(unittest.TestCase):
    def test_http_error_page_is_not_an_announcement(self):
        self.doc.update(http_status=403, status="source_rejected")
        self.assertFalse(knowledge.source_eligibility(self.doc, self.ctx)["eligible"])

    def setUp(self):
        self.ctx = {"mode": "historical_replay", "cutoff_at": "2026-10-15T15:00:00+08:00"}
        self.doc = {
            "source_url": "https://example.test",
            "sha256": "a" * 64,
            "captured_at": "2026-10-15T06:00:00+00:00",
            "published_at": "2026-10-15T13:00:00+08:00",
        }

    def test_exact_timezone(self):
        for value in ("2026-10-15", "2026-10-15T15:00:00"):
            with self.assertRaises(ValueError):
                knowledge.exact(value)

    def test_same_day_later_publication_excluded(self):
        self.doc["published_at"] = "2026-10-15T16:00:00+08:00"
        self.assertEqual(
            knowledge.source_eligibility(self.doc, self.ctx)["reason"], "published_after_cutoff"
        )

    def test_capture_after_cutoff_is_leakage(self):
        self.doc["captured_at"] = "2026-10-20T00:00:00+00:00"
        self.assertFalse(knowledge.source_eligibility(self.doc, self.ctx)["eligible"])
        self.ctx["mode"] = "retrospective_diagnostic"
        self.assertFalse(
            knowledge.source_eligibility(self.doc, self.ctx)["historical_vintage_proven"]
        )

    def test_date_only_not_known_at_midday(self):
        self.doc["published_at"] = "2026-10-15"
        self.assertFalse(knowledge.source_eligibility(self.doc, self.ctx)["eligible"])

    def test_unknown_publication_requires_capture_proof(self):
        self.doc["published_at"] = None
        self.assertFalse(knowledge.source_eligibility(self.doc, self.ctx)["eligible"])
        self.doc["public_capture_verified"] = True
        self.assertTrue(knowledge.source_eligibility(self.doc, self.ctx)["eligible"])

    def test_missing_earnings_blocks_complete(self):
        r = knowledge.completeness(
            self.ctx,
            {},
            [
                {
                    "id": "Q3",
                    "kind": "earnings",
                    "period": "2026-Q3",
                    "published_at": self.doc["published_at"],
                    "document_id": "missing",
                }
            ],
            [],
        )
        self.assertEqual(r["status"], "incomplete")
        self.assertIn("Q3", r["missing_material_events"])
        self.assertIn("2026-Q3", r["announced_earnings_periods"])

    def test_no_inventory_does_not_mean_complete(self):
        self.assertEqual(len(knowledge.completeness(self.ctx, {}, [], [])["coverage_gaps"]), 4)

    def test_stale_latest_rejected(self):
        with self.assertRaises(ValueError):
            knowledge.context(
                {"mode": "latest_research", "stock_id": "2330", "cutoff_at": self.ctx["cutoff_at"]},
                "2330",
                "2026-10-15",
                datetime(2026, 10, 20, tzinfo=UTC),
            )


class CapitalTests(unittest.TestCase):
    def test_treasury_cancellation_does_not_reduce_outstanding_twice(self):
        event = self.event(
            kind="cancellation", shares=100, recognition_date="2026-01-01", from_treasury=True
        )
        self.assertEqual(
            capital_model.build("2026-01-01", "2026-01-01", 1000, 100, [event])[
                "basic_weighted_shares"
            ],
            900,
        )

    def test_conversion_not_also_potential(self):
        p = {
            "eps_note_reviewed": True,
            "anti_dilution_test_reviewed": True,
            "components": [
                {
                    "id": "cb",
                    "security_id": "cb",
                    "dilutive": True,
                    "weighted_incremental_shares": 100,
                    "numerator_adjustment_twd": 0,
                }
            ],
        }
        event = self.event(id="conversion", security_id="cb")
        with self.assertRaises(ValueError):
            capital_model.build("2026-07-01", "2026-09-30", 3180, 0, [event], p)

    def event(self, **extra):
        return {
            "id": "gdr",
            "kind": "issue",
            "shares": 250,
            "source_id": "doc",
            "recognition_date": "2026-09-16",
            "recognition_reviewed": True,
            **extra,
        }

    def test_day_weighting_not_listing_day(self):
        r = capital_model.build(
            "2026-07-01",
            "2026-09-30",
            3180,
            0,
            [
                self.event(),
                self.event(id="listing", kind="stock_listing", recognition_date="2026-10-13"),
            ],
        )
        self.assertAlmostEqual(r["basic_weighted_shares"], 3180 + 250 * 15 / 92)
        self.assertEqual(r["ending_issued_shares"], 3430)
        self.assertIsNone(r["diluted_weighted_shares"])

    def test_missing_recognition_not_replaced_by_listing(self):
        r = capital_model.build(
            "2026-07-01", "2026-09-30", 3180, 0, [self.event(recognition_date=None)]
        )
        self.assertIsNone(r["basic_weighted_shares"])

    def test_duplicate_events_rejected(self):
        with self.assertRaises(ValueError):
            capital_model.build("2026-07-01", "2026-09-30", 3180, 0, [self.event(), self.event()])

    def test_prior_event_must_be_in_opening(self):
        with self.assertRaises(ValueError):
            capital_model.build("2026-10-01", "2026-12-31", 3180, 0, [self.event()])

    def test_existing_potential_added_once(self):
        p = {
            "eps_note_reviewed": True,
            "anti_dilution_test_reviewed": True,
            "components": [
                {
                    "id": "cb",
                    "dilutive": True,
                    "weighted_incremental_shares": 200,
                    "numerator_adjustment_twd": -10,
                }
            ],
        }
        r = capital_model.build("2026-10-01", "2026-12-31", 3430, 0, [], p)
        self.assertEqual(r["diluted_weighted_shares"], 3630)
        self.assertEqual(r["diluted_numerator_adjustment_twd"], -10)


class ForwardTests(unittest.TestCase):
    def test_foreign_company_consensus_does_not_pass(self):
        model = forward_model.build(
            "2026-10-20", {n: cases() for n in ("bear", "base", "bull")}, {"anchor": 1}
        )
        p = {
            "kind": "consensus",
            "stock_id": "3231",
            "currency": "TWD",
            "source_id": "doc",
            "values_reviewed": True,
            "reviewed_by": "reviewer",
            "locator": "p1",
            "basis_reviewed": True,
            "metric": "eps_twd",
            "basis": "sum_quarterly_diluted_eps",
            "periods": model["target_quarters"],
            "low": 1,
            "high": 2,
        }
        self.assertFalse(forward_model.expectations(model, [p], "2330")["consensus_verified"])

    def test_unknown_shares_preserve_profit_but_never_eps(self):
        spec = {name: cases() for name in ("bear", "base", "bull")}
        for rows in spec.values():
            for row in rows:
                row["inputs"]["diluted_weighted_shares"].update(
                    kind="unknown", value=None, rationale="No prospective weighted share note"
                )
        r = forward_model.build("2026-10-20", spec, {"anchor": 1000})
        q = r["scenarios"]["base"]["quarters"][0]["calculation"]
        self.assertGreater(q["parent_profit_twd"], 0)
        self.assertIsNone(q["eps_twd"])
        self.assertIsNone(r["scenarios"]["base"]["eps_sum_twd"])

    def test_four_individual_quarters(self):
        spec = {name: cases() for name in ("bear", "base", "bull")}
        spec["base"][1]["inputs"]["revenue_twd"]["value"] = 2000
        r = forward_model.build("2026-10-20", spec, {"anchor": 1000})
        self.assertEqual(r["scenarios"]["base"]["revenue_twd"], 5000)
        self.assertIsNone(r["scenarios"]["base"]["annual_weighted_eps"])

    def test_announced_target_rejected(self):
        with self.assertRaises(ValueError):
            forward_model.build(
                "2026-10-20",
                {n: cases() for n in ("bear", "base", "bull")},
                {"anchor": 1},
                ["2026-Q4"],
            )

    def test_duplicate_quarter_rejected(self):
        c = cases()
        c[1] = copy.deepcopy(c[0])
        with self.assertRaises(ValueError):
            forward_model.build(
                "2026-10-20", dict.fromkeys(("bear", "base", "bull"), c), {"anchor": 1}
            )

    def test_reverse_revenue_equation(self):
        r = forward_model.reverse(300, 20, cases())
        v = values()
        v["revenue_twd"] *= r["required_revenue_multiplier"]
        from tradingagents.research.financial_model import waterfall

        self.assertAlmostEqual(4 * waterfall(v)["eps_twd"], 15)

    def test_no_false_consensus_comparison(self):
        model = forward_model.build("2026-10-20", None, {})
        r = forward_model.expectations(
            model,
            [
                {
                    "kind": "management_guidance",
                    "source_id": "doc",
                    "metric": "revenue_usd",
                    "basis_reviewed": True,
                    "periods": ["2026-Q4"],
                }
            ],
        )
        self.assertEqual(r["comparisons"][0]["status"], "not_comparable")


class IndustryTests(unittest.TestCase):
    def test_missing_is_unknown_not_zero(self):
        for kind in industry_models.FIELDS:
            self.assertEqual(
                industry_models.calculate(kind, {})["status"], "unavailable_missing_drivers"
            )

    def test_foundry_mix_changes_revenue(self):
        v = dict.fromkeys(industry_models.FIELDS["foundry"], 1)
        v.update(capacity_wafers=100, advanced_asp_twd=20, mature_asp_twd=10, advanced_mix=0.5)
        a = industry_models.calculate("foundry", v)
        v["advanced_mix"] = 0.6
        self.assertEqual(
            industry_models.calculate("foundry", v)["revenue_twd"] - a["revenue_twd"], 100
        )

    def test_thermal_capacity_and_yield(self):
        v = dict.fromkeys(industry_models.FIELDS["thermal"], 1)
        v.update(server_units=100, capacity_units=50)
        self.assertEqual(industry_models.calculate("thermal", v)["status"], "infeasible_capacity")

    def test_odm_growth_can_need_financing(self):
        v = dict.fromkeys(industry_models.FIELDS["odm"], 1)
        v.update(
            ai_units=100,
            ai_asp_twd=100,
            ai_unit_cost_twd=90,
            dso_days=90,
            dio_days=60,
            dpo_days=30,
            period_days=90,
            annual_financing_rate=0.04,
        )
        r = industry_models.calculate("odm", v)
        self.assertGreater(r["detail"]["financing_cost_twd"], 0)
        self.assertFalse(r["detail"]["collection_verified"])

    def test_unsourced_fact_rejected(self):
        with self.assertRaises(ValueError):
            industry_models.reviewed(
                "thermal", {"yield": {"kind": "fact", "value": 1, "evidence_ids": ["missing"]}}, {}
            )


class PerformanceTests(unittest.TestCase):
    def test_self_attested_pit_batch_stays_retrospective(self):
        from datetime import date, timedelta

        sessions = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(90)]
        bars = [
            {
                "date": d,
                "open": 10,
                "close": 10,
                "high": 11,
                "low": 9,
                "volume": 100,
                "basis": "vendor_adjusted",
            }
            for d in sessions
        ]
        folds = [{"test_start": sessions[60], "test_end": sessions[-1]}]
        cfg = {
            "selected_at": "2023-01-01T00:00:00+00:00",
            "rule_version": "breakout-v1",
            "universe": ["2330"],
            "folds": folds,
        }
        cfg["plan_sha256"] = fingerprint(cfg)
        receipt = {
            "sha256": "source",
            "captured_at": cfg["selected_at"],
            "bars_sha256": fingerprint(bars),
            "sessions_sha256": fingerprint(sessions),
            "point_in_time_adjustments_verified": True,
        }
        result = run(bars, sessions, folds, cfg, receipt, "2330")
        self.assertEqual(result["folds"][0]["mode"], "retrospective_diagnostic")

    def test_zero_ohlc_cannot_enter_return_simulation(self):
        from tradingagents.research.historical_validation import short_horizon

        bars = [{"date": "2024-01-01", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0}]
        with self.assertRaises(ValueError):
            short_horizon(bars, ["2024-01-01"], "2024-01-01", "2024-01-01")


class ProjectionTests(unittest.TestCase):
    def test_horizon_errors_are_not_historical_pit(self):
        from tradingagents.research.predictive import month_name
        from tradingagents.research.revenue_projection import project

        snapshot = {
            "monthly_revenue": [
                {"period": month_name(2020 * 12 + i), "revenue_twd": 100 + i} for i in range(70)
            ]
        }
        result = project(snapshot)
        self.assertEqual(len(result["forecasts"]), 12)
        self.assertFalse(result["strict_pit"])
        self.assertFalse(result["independent_origins"])
        self.assertAlmostEqual(sum(result["forecasts"][0]["weights"].values()), 1)

    def test_future_revenue_not_used(self):
        from tradingagents.research.revenue_projection import project

        with self.assertRaises(ValueError):
            project(
                {
                    "as_of": "2026-10-20",
                    "monthly_revenue": [{"period": "2026-10", "revenue_twd": 100}],
                }
            )

    def test_two_winners_do_not_display_win_rate(self):
        r = sample_summary([{"net_return": 0.1}] * 2)
        self.assertIsNone(r["net_win_rate"])
        self.assertEqual(r["display_policy"], "individual_events_only")

    def test_thirty_is_descriptive_not_edge(self):
        r = sample_summary([{"net_return": 0.1}] * 30)
        self.assertEqual(r["net_win_rate"], 1)
        self.assertFalse(r["empirical_edge_proven"])

    def test_tampered_plan_rejected(self):
        cfg = {
            "selected_at": "2026-10-20T00:00:00+00:00",
            "rule_version": "breakout-v1",
            "folds": [],
            "universe": ["2330"],
        }
        cfg["plan_sha256"] = fingerprint(cfg)
        cfg["costs"] = {}
        with self.assertRaises(ValueError):
            run([], [], [], cfg, {"sha256": "a", "captured_at": cfg["selected_at"]}, "2330")


class TradabilityCoverageTests(unittest.TestCase):
    def test_adjusted_prices_cannot_replace_missing_raw_sessions(self):
        from unittest.mock import patch

        from tradingagents.research.research_extension import backtest

        snapshot = {
            "as_of": "2026-10-20",
            "instrument": {"exchange": "TWSE"},
            "market_calendar": {"status": "ok"},
            "datasets": {},
            "extensions": {},
        }
        with patch(
            "tradingagents.research.research_extension.verify_calendar", return_value=["2026-10-19"]
        ):
            result = backtest(snapshot, {"test_start": "2025-01-01"})
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("unadjusted", result["reason"])
        self.assertFalse(result["empirical_edge_proven"])
