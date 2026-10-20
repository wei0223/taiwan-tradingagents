"""Adversarial earnings, ownership, cash-period, valuation and evidence delivery checks."""

import base64
import copy
import hashlib
import json
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from tests.taiwan.test_report_reading import notes, packet, sample
from tests.taiwan.test_review_rules import inputs, run
from tradingagents.research.cash_quality import analyze, normalize_flow
from tradingagents.research.delivery import seal
from tradingagents.research.financial_model import (
    break_even_gross_margin,
    conditional_case,
    profile,
    stress,
    waterfall,
)
from tradingagents.research.market_expectations import valuation
from tradingagents.research.report_view import normalize_notes
from tradingagents.research.reporting import package
from tradingagents.research.research_debate import evidence_path, validate
from tradingagents.research.research_extension import backtest, build
from tradingagents.research.review import _end_of_day


def case():
    return {
        "revenue_twd": 1000,
        "variable_cogs_ratio": 0.5,
        "fixed_cogs_ex_depreciation_twd": 100,
        "cogs_depreciation_twd": 20,
        "variable_opex_ratio": 0.1,
        "fixed_opex_ex_depreciation_twd": 10,
        "opex_depreciation_twd": 5,
        "other_operating_twd": 3,
        "nonoperating_twd": -8,
        "tax_rate": 0.2,
        "noncontrolling_profit_twd": 10,
        "diluted_adjustment_twd": -2,
        "diluted_weighted_shares": 20,
    }


def cash_packets():
    data = inputs()
    flow = copy.deepcopy(data["cash_ytd"])
    flow["values"] = {
        "revenue_twd": 300,
        "cogs_twd": 200,
        "net_profit_twd": 60,
        "cfo_twd": -40,
        "depreciation_twd": 10,
        "capex_ppe_twd": 30,
        "capex_intangibles_twd": 5,
        "ar_cash_effect_twd": -100,
        "inventory_cash_effect_twd": -60,
        "ap_cash_effect_twd": 80,
        "financing_cashflow_twd": 100,
    }
    opening = {
        **flow,
        "basis": "balance",
        "as_of_date": "2025-12-31",
        "values": {"receivables_twd": 50, "inventory_twd": 100, "payables_twd": 80},
    }
    opening.pop("period_start")
    opening.pop("period_end")
    ending = {
        **opening,
        "as_of_date": "2026-06-30",
        "values": {"receivables_twd": 70, "inventory_twd": 140, "payables_twd": 100},
    }
    return data, {"flow": flow, "opening": opening, "ending": ending}


class EarningsModelTests(unittest.TestCase):
    def test_break_even_is_recomputed_not_inferred_from_revenue_growth(self):
        r = break_even_gross_margin(case(), 9.8)
        self.assertAlmostEqual(r["required_gross_margin"], 0.38)
        expanded = {**case(), "revenue_twd": 1200}
        self.assertLess(break_even_gross_margin(expanded, 9.8)["required_gross_margin"], 0.38)

    def test_noncontrolling_fraction_moves_with_taxed_profit(self):
        c = case()
        c.pop("noncontrolling_profit_twd")
        c["noncontrolling_profit_fraction"] = 0.2
        r = stress(c, {"variable_cogs_ratio": 0.6})
        self.assertAlmostEqual(r["eps_delta"], -100 * 0.8 * 0.8 / 20)

    def test_complete_waterfall_preserves_negative_numerator_adjustment(self):
        r = waterfall(case())
        self.assertEqual(r["gross_profit_twd"], 380)
        self.assertEqual(r["operating_profit_twd"], 268)
        self.assertEqual(r["parent_profit_twd"], 198)
        self.assertEqual(r["eps_twd"], 9.8)

    def test_capex_cost_or_dilution_change_reaches_eps(self):
        base = case()
        r = stress(base, {"cogs_depreciation_twd": 40, "diluted_weighted_shares": 25})
        self.assertLess(r["revised"]["eps_twd"], r["baseline"]["eps_twd"])
        self.assertEqual(base, case())
        self.assertFalse(r["causal_effect_established"])

    def test_no_missing_cost_silently_assumed_zero(self):
        d = case()
        d.pop("cogs_depreciation_twd")
        with self.assertRaises(ValueError):
            waterfall(d)

    def test_nan_boolean_and_zero_shares_rejected(self):
        for key, value in (
            ("tax_rate", float("nan")),
            ("revenue_twd", True),
            ("diluted_weighted_shares", 0),
        ):
            with self.assertRaises(ValueError):
                waterfall({**case(), key: value})

    def test_facts_cannot_hide_changed_assumptions(self):
        d = {
            "period": "2026-Q3",
            "inputs": {
                key: {"value": value, "kind": "fact", "evidence_ids": [key]}
                for key, value in case().items()
            },
        }
        self.assertEqual(conditional_case(d, case())["calculation"]["eps_twd"], 9.8)
        d["inputs"]["revenue_twd"]["value"] = 1200
        with self.assertRaises(ValueError):
            conditional_case(d, case())
        d["inputs"]["revenue_twd"].update(kind="assumption", rationale="Synthetic volume scenario")
        self.assertIsNone(conditional_case(d, case())["confidence_interval"])

    def test_company_profiles_do_not_inherit_numeric_margins(self):
        self.assertEqual(profile("2330")["key"], "foundry")
        self.assertEqual(profile("3017")["key"], "thermal")
        self.assertEqual(profile("3231")["key"], "odm")
        self.assertFalse(profile("3231")["numeric_parameters_inherited"])


class CashQualityTests(unittest.TestCase):
    def test_balance_cannot_inherit_a_quarterly_flow_period(self):
        d, p = cash_packets()
        p["opening"]["period_start"] = "2026-04-01"
        with self.assertRaises(ValueError):
            analyze("3231", d["documents"], _end_of_day("2026-10-15"), p, run(d))

    def test_matched_cumulative_fcf_not_single_quarter(self):
        d, p = cash_packets()
        r = analyze("3231", d["documents"], _end_of_day("2026-10-15"), p, run(d))
        self.assertEqual(r["details"]["fcf_ppe_and_intangibles_twd"], -75)
        self.assertEqual(r["details"]["basis"], "ytd")
        self.assertAlmostEqual(r["details"]["days"]["dso"], 60 / 300 * 181)
        self.assertFalse(r["details"]["cash_bridge_complete"])
        self.assertIn("unavailable", r["collection_evidence"])

    def test_version_mismatch_blocks_days(self):
        d, p = cash_packets()
        p["opening"]["version_set"] = "different"
        r = analyze("3231", d["documents"], _end_of_day("2026-10-15"), p, run(d))
        self.assertEqual(r["details"]["status"], "unavailable")

    def test_unmatched_balance_dates_rejected(self):
        d, p = cash_packets()
        p["opening"]["as_of_date"] = "2026-03-31"
        with self.assertRaises(ValueError):
            analyze("3231", d["documents"], _end_of_day("2026-10-15"), p, run(d))

    def test_future_source_cannot_be_cash_quality_evidence(self):
        d, p = cash_packets()
        d["documents"]["doc"]["captured_at"] = "2026-10-16T00:00:00+00:00"
        r = analyze("3231", d["documents"], _end_of_day("2026-10-15"), p, run(d))
        self.assertEqual(r["details"]["status"], "unavailable")

    def test_volume_ratio_available_without_invented_free_float(self):
        r = normalize_flow(-100, 2000)
        self.assertEqual(r["net_to_volume_pct"], -5)
        self.assertIsNone(r["net_to_free_float_pct"])
        with self.assertRaises(ValueError):
            normalize_flow(100, 2000, 0)


class ValuationTests(unittest.TestCase):
    def rows(self):
        return [
            {
                "date": "2026-10-14",
                "captured_at": "2026-10-15T00:00:00+00:00",
                "reported_pe": 20,
                "source_sha256": "a" * 64,
            }
        ]

    def test_reverse_equation_does_not_create_growth_denominator(self):
        r = valuation(100, self.rows(), "2026-10-15T12:00:00+00:00")
        self.assertEqual(r["reverse"][0]["required_eps_twd"], 5)
        self.assertIsNone(r["reverse"][0]["required_growth_vs_reference_pct"])
        self.assertFalse(r["market_consensus_verified"])
        self.assertFalse(r["fair_value_established"])

    def test_future_capture_excluded_from_historical_pe(self):
        rows = self.rows()
        rows[0]["captured_at"] = "2026-10-16T00:00:00+00:00"
        self.assertEqual(valuation(100, rows, "2026-10-15")["reported_history"]["count"], 0)

    def test_quarter_times_four_cannot_be_forward_denominator(self):
        with self.assertRaises(ValueError):
            valuation(100, [], "2026-10-15", forward={"basis": "quarter_times_four"})

    def test_forward_reference_needs_original_timing_and_review(self):
        p = {
            "basis": "next_12_month_diluted_estimate",
            "basis_reviewed": True,
            "source_sha256": "a" * 64,
            "published_at": "2026-10-14",
            "captured_at": "2026-10-14T12:00:00+00:00",
            "eps_twd": 4,
        }
        r = valuation(100, [], "2026-10-15", forward=p, reverse_pe=[20, 25])
        self.assertEqual(r["forward"]["pe"], 25)
        self.assertEqual(r["reverse"][0]["required_growth_vs_reference_pct"], 25)
        p["captured_at"] = "2026-10-16T00:00:00+00:00"
        self.assertEqual(
            valuation(100, [], "2026-10-15", forward=p)["forward"]["status"], "unavailable"
        )


class EvidenceAndIntegrationTests(unittest.TestCase):
    def test_formal_entry_does_not_accept_a_forward_eps_flag_only(self):
        source = sample()
        p = packet()
        with self.assertRaises(ValueError):
            build(
                source,
                run(p),
                p,
                {},
                {
                    "stock_id": "3017",
                    "as_of": "2026-10-15",
                    "valuation": {"forward": {"eps_twd": 99, "basis_reviewed": True}},
                },
            )

    def test_replay_builds_adjusted_basis_and_all_three_horizons(self):
        sessions = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(60)]
        records = [
            {
                "data_date": day,
                "values": {"open": 100, "close": 100, "high": 101, "low": 99, "volume": 1000},
            }
            for day in sessions
        ]
        source = {
            "as_of": "2026-10-15",
            "instrument": {"exchange": "twse"},
            "market_calendar": {"status": "ok"},
            "datasets": {"TaiwanStockPrice": {"records": records}},
            "extensions": {"TaiwanStockPriceAdj": {"records": records}},
        }
        with patch(
            "tradingagents.research.research_extension.verify_calendar", return_value=sessions
        ):
            r = backtest(
                source,
                {
                    "rule_version": "breakout-v1",
                    "selected_at": "2026-10-20T00:00:00+00:00",
                    "test_start": "2024-02-01",
                },
            )
        self.assertEqual(set(r["splits"]["holdout"]), {"5", "10", "20"})
        self.assertFalse(r["strict_pit"])
        self.assertTrue(r["splits"]["holdout"]["10"]["small_sample_warning"])

    def debate(self):
        packet = {"opening_file_sha256": {}, "opening_file_bytes_base64": {}}
        for role in ("bull", "bear"):
            claim = {
                "id": role + "_c1",
                "claim": "Synthetic argument",
                "strongest_counterevidence": "Synthetic counterevidence",
                "confidence": {"kind": "uncalibrated_qualitative"},
                "revision_condition": "Synthetic condition",
                "evidence_paths": ["work/3017/snapshot.json#$.as_of"],
            }
            opening = {
                "role": role,
                "stage": "opening",
                "context_mode": "fresh_no_baseline",
                "agent_id": role,
                "companies": [
                    {
                        "stock_id": "3017",
                        "position": "before",
                        "model": {"status": "unavailable"},
                        "claims": [claim],
                    }
                ],
            }
            packet[role + "_opening"] = opening
            raw = json.dumps(opening).encode()
            packet["opening_file_sha256"][role] = hashlib.sha256(raw).hexdigest()
            packet["opening_file_bytes_base64"][role] = base64.b64encode(raw).decode()
        for role, opponent in (("bull", "bear"), ("bear", "bull")):
            packet[role + "_reply"] = {
                "role": role,
                "stage": "reply",
                "agent_id": role,
                "opponent_opening_sha256": packet["opening_file_sha256"][opponent],
                "companies": [
                    {
                        "stock_id": "3017",
                        "position_before": "before",
                        "position_after": "after",
                        "changes": [
                            {
                                "claim_id": opponent + "_c1",
                                "assessment": "accept",
                                "reason": "Synthetic reason",
                                "changed_my_position": True,
                                "evidence_paths": ["work/3017/snapshot.json#$.as_of"],
                            }
                        ],
                    }
                ],
            }
        packet["risk"] = {"agent_id": "risk", "companies": [{"stock_id": "3017"}]}
        packet["judgment"] = {
            "companies": [
                {
                    "stock_id": "3017",
                    **dict.fromkeys(
                        (
                            "before",
                            "after",
                            "evidence_that_changed_judgment",
                            "stronger_case",
                            "unresolved",
                            "revision_conditions",
                        ),
                        "Synthetic ruling",
                    ),
                }
            ]
        }
        return packet

    def test_real_stage_contract_retains_positions_and_binds_openings(self):
        d = self.debate()
        result = validate(d, "3017", sample(), {})
        self.assertEqual(result["replies"]["bull"]["position_after"], "after")
        d["bull_opening"]["companies"][0]["position"] = "rewritten"
        with self.assertRaises(ValueError):
            validate(d, "3017", sample(), {})

    def test_missing_opponent_response_is_not_a_completed_debate(self):
        d = self.debate()
        d["bear_reply"]["companies"][0]["changes"] = []
        with self.assertRaises(ValueError):
            validate(d, "3017", sample(), {})

    def test_debate_never_claimed_when_absent(self):
        self.assertFalse(validate(None, "3017", {}, {})["independent_agents_executed"])

    def test_invalid_company_or_json_locator_rejected(self):
        for path in ("work/2330/snapshot.json#$.as_of", "work/3017/snapshot.json#$.missing"):
            with self.assertRaises(ValueError):
                evidence_path(path, "3017", sample(), {})

    def test_source_selectors_bind_actual_values(self):
        source = {
            "as_of": "2026-10-15",
            "monthly_revenue": [{"period": "2026-07", "revenue_twd": 5}],
        }
        evidence_path(
            "work/3017/snapshot.json#/monthly_revenue[period=2026-07..2026-09]", "3017", source, {}
        )
        with self.assertRaises(ValueError):
            evidence_path(
                "work/3017/snapshot.json#/monthly_revenue[period=2027-01]", "3017", source, {}
            )

    def test_excluded_raw_rows_cannot_be_resurrected(self):
        source = {
            "as_of": "2026-10-15",
            "datasets": {"x": {"raw_rows": [{"date": "2027-01-01"}], "records": []}},
        }
        with self.assertRaises(ValueError):
            evidence_path(
                "work/3017/snapshot.json#/datasets/x/raw_rows[date=2027-01-01]", "3017", source, {}
            )

    def test_schema_three_requires_counterevidence_and_confidence(self):
        d = notes()
        d["schema_version"] = 3
        _, missing = normalize_notes(d, "3017", "2026-10-15")
        self.assertIn("strongest_counterevidence", missing["fundamental"])

    def test_formal_package_contains_all_inputs_and_dashboard(self):
        with tempfile.TemporaryDirectory() as directory:
            r = package(sample(), directory, analyst_prose=notes(), review_input=packet())
            self.assertFalse(r["independent_agents_executed"])
            with zipfile.ZipFile(Path(directory) / "evidence.zip") as archive:
                for key in (
                    "snapshot.json",
                    "bundle.json",
                    "research.json",
                    "review-input.json",
                    "extension-input.json",
                    "debate-input.json",
                    "analysts.json",
                    "delivery-manifest.json",
                ):
                    self.assertIn(key, archive.namelist())
            page = (Path(directory) / "report.html").read_text(encoding="utf-8")
            self.assertIn("研究摘要儀表板", page)
            self.assertIn("情境測量", page)
            self.assertIsNone(r["extension"]["valuation"]["forward"].get("eps_twd"))

    def test_delivery_hashes_cover_nested_data_and_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "research.json").write_text("{}")
            (root / "report.pdf").write_bytes(b"synthetic pdf fixture")
            result = seal(root)
            self.assertIn("report.pdf", result["files"])
            self.assertNotIn("evidence.zip", result["files"])
            with zipfile.ZipFile(root / "evidence.zip") as archive:
                self.assertIsNone(archive.testzip())
