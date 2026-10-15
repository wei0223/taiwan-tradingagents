"""Financial semantics, time leakage, rolling gaps and portable exports; all offline."""

import base64
import copy
import hashlib
import tempfile
import unittest
from unittest.mock import patch

from tests.taiwan.test_research import snapshot
from tradingagents.research.bundle import build_bundle, fingerprint
from tradingagents.research.dynamics import conditional_decision, reward_risk, rolling
from tradingagents.research.quality import audit_html, compare_peers, industry_review, source_audit
from tradingagents.research.reporting import package
from tradingagents.research.scenarios import challenge, forecast, next_quarters, normalize_quarter
from tradingagents.research.validation import forecast_errors, paired_comparison, signal_performance


def model_spec(bundle):
    evidence = next(k for k, v in bundle["evidence"].items() if v["availability"] == "available")

    def assumption(value):
        return {
            "value": value,
            "kind": "assumption",
            "rationale": "Synthetic explicit test assumption",
            "evidence_ids": [evidence],
        }

    content = b"synthetic reviewed document"
    output = {
        "bundle_sha256": fingerprint(bundle),
        "as_of": bundle["as_of"],
        "basis_review": {
            "status": "verified",
            "reviewer": "synthetic test reviewer",
            "quarter_basis": True,
            "parent_ownership": True,
            "weighted_diluted_shares": True,
            "reviewed_facts": {
                "unit": "TWD_and_shares",
                "stock_id": bundle["stock_id"],
                "weighted_diluted_shares": 100,
                "diluted_numerator_twd": 990,
                "diluted_eps_twd": 9.9,
            },
            "documents": [
                {
                    "source_url": "https://example.org/report",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "content_base64": base64.b64encode(content).decode(),
                    "published_at": "2024-01-01",
                    "captured_at": "2024-01-02",
                }
            ],
        },
        "scenarios": {},
    }
    count = 8 - (int(bundle["as_of"][5:7]) - 1) // 3
    for name, revenue in [("bear", 100), ("base", 200), ("bull", 300)]:
        rows = []
        for period in next_quarters(bundle["as_of"], count):
            rows.append(
                {
                    "period": period,
                    **{
                        k: assumption(v)
                        for k, v in {
                            "revenue_twd": revenue,
                            "gross_margin": 0.4,
                            "operating_margin": 0.2,
                            "nonoperating_twd": 0,
                            "tax_rate": 0.2,
                            "noncontrolling_profit_twd": 1,
                            "diluted_numerator_adjustment_twd": -1,
                            "weighted_diluted_shares": 10,
                        }.items()
                    },
                }
            )
        output["scenarios"][name] = {"quarters": rows, "valuation_pe": assumption(10)}
    return output


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.source = snapshot()
        self.bundle = build_bundle(self.source)

    def test_unknown_basis_and_ytd_eps_refused(self):
        base = {
            "basis": "ytd",
            "period": "2024-Q2",
            "kind": "amount",
            "value": 30,
            "unit": "TWD",
            "stock_id": "2330",
            "scope": "consolidated",
            "field": "income",
            "version_set": "v1",
        }
        prior = {**base, "period": "2024-Q1", "value": 12}
        self.assertEqual(normalize_quarter(base, prior), 18)
        for changes in [{"kind": "eps"}, {"basis": "unknown"}]:
            with self.assertRaises(ValueError):
                normalize_quarter({**base, **changes}, prior)
        with self.assertRaises(ValueError):
            normalize_quarter(base, {**prior, "unit": "TWD thousands"})
        with self.assertRaises(ValueError):
            normalize_quarter(base)

    def test_forecast_numeric_identity_dilution_and_calendar(self):
        spec = model_spec(self.bundle)
        r = forecast(self.bundle, spec)
        q = r["scenarios"]["base"]["quarters"][0]
        self.assertAlmostEqual(q["eps_twd"], (200 * 0.2 * 0.8 - 1 - 1) / 10)
        self.assertAlmostEqual(
            r["scenarios"]["base"]["next_four_quarter_eps_sum"], q["eps_twd"] * 4
        )
        self.assertEqual(r["scenarios"]["base"]["annual"]["2025"]["quarters"], 4)
        self.assertEqual(forecast(self.bundle)["status"], "unavailable")
        self.assertNotIn("future", str(r["ttm_pe"]))

    def test_reject_future_basis_tampering_unknown_references_and_nan(self):
        mutations = [
            lambda s: s["basis_review"]["documents"][0].update(captured_at="2099-01-01"),
            lambda s: s["basis_review"]["documents"][0].update(content_base64="AAAA"),
            lambda s: s["basis_review"].update(quarter_basis=False),
            lambda s: s["scenarios"]["base"]["quarters"][0]["revenue_twd"].update(
                value=float("nan")
            ),
            lambda s: s["scenarios"]["base"]["quarters"][0]["tax_rate"].update(value=1.5),
        ]
        for mutate in mutations:
            spec = model_spec(self.bundle)
            mutate(spec)
            with self.assertRaises((ValueError, KeyError)):
                forecast(self.bundle, spec)
        bundle = copy.deepcopy(self.bundle)
        key = next(k for k, v in bundle["evidence"].items() if v["availability"] == "available")
        spec = model_spec(bundle)
        bundle["evidence"][key]["value_available_dates"] = ["2099-01-01"]
        spec["bundle_sha256"] = fingerprint(bundle)
        with self.assertRaises(ValueError):
            forecast(bundle, spec)

    def test_numeric_challenge_targets_opponent_input_without_claiming_agent_run(self):
        r = forecast(self.bundle, model_spec(self.bundle))
        period = r["scenarios"]["bear"]["quarters"][0]["period"]
        c = challenge(
            r,
            "bull",
            "bear",
            period,
            "operating_margin",
            0.22,
            "Counterfactual test",
            r["assumptions"]["scenarios"]["bear"]["quarters"][0]["operating_margin"][
                "evidence_ids"
            ],
        )
        rerun = forecast(self.bundle, c["counterfactual_specification"])
        self.assertGreater(
            rerun["scenarios"]["bear"]["next_four_quarter_eps_sum"],
            r["scenarios"]["bear"]["next_four_quarter_eps_sum"],
        )
        self.assertFalse(c["independent_agent_execution"])

    def test_html_portability_escaping_and_source_audit(self):
        self.assertEqual(audit_html('<a href="#E1">x</a><p id="E1">ok</p>')["status"], "passed")
        for text in [
            '<a href="file:///C:/secret">x</a>',
            '<a href="#missing">x</a>',
            "<script>alert(1)</script>",
            '<a href="../secret">x</a>',
            '<p id="x"></p><p id="x"></p>',
        ]:
            self.assertEqual(audit_html(text)["status"], "failed")
        self.assertEqual(source_audit(self.source)["status"], "passed")
        with tempfile.TemporaryDirectory() as path:
            result = package(
                self.source, path, analyst_prose={"fundamental": ["<script>malicious</script>"]}
            )
            self.assertEqual(result["forecast"]["status"], "unavailable")
            self.assertEqual(result["decision"]["state"], "observe")

    def test_rolling_missing_session_never_zero_and_atr(self):
        from datetime import date, timedelta

        days = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(12)]
        rows = [
            {
                "data_date": d,
                "values": {
                    "close": 100 + i,
                    "open": 100 + i,
                    "high": 101 + i,
                    "low": 99 + i,
                    "volume": 10,
                },
            }
            for i, d in enumerate(days)
        ]
        source = {
            "market_calendar": {"status": "ok"},
            "extensions": {"TaiwanStockPriceAdj": {"records": rows}},
            "datasets": {"TaiwanStockInstitutionalInvestorsBuySell": {"records": []}},
        }
        with (
            patch(
                "tradingagents.research.dynamics.build_bundle",
                return_value={"exchange": "twse", "as_of": "2024-02-01"},
            ),
            patch("tradingagents.research.dynamics.verify_calendar", return_value=days),
        ):
            r = rolling(source, (5,))["windows"]["5"]
            self.assertEqual(r["atr_simple_mean"], 2)
            self.assertIsNone(r["institutions"]["foreign"]["window_net_shares"])
            source["extensions"]["TaiwanStockPriceAdj"]["records"].pop(-2)
            self.assertEqual(rolling(source, (5,))["windows"]["5"]["status"], "unavailable")

    def test_forecast_mae_future_actual_pending_and_leakage_rejected(self):
        p = {
            "stock_id": "2330",
            "period": "2024-Q1",
            "metric": "eps",
            "basis": "diluted_quarter",
            "value": 2,
            "model_version": "v1",
            "issued_at": "2024-03-01",
            "input_snapshot_sha256": "test",
        }
        a = {**p, "value": 3, "published_at": "2024-05-01", "captured_at": "2024-05-02"}
        r = forecast_errors([p], [a], today="2024-05-03")
        self.assertEqual(next(iter(r["mae_by_model_metric_basis"].values())), 1)
        self.assertEqual(
            forecast_errors([p], [a], today="2024-04-01")["status"], "pending_future_actuals"
        )
        with self.assertRaises(ValueError):
            forecast_errors([{**p, "issued_at": "2024-05-03"}], [a], today="2024-05-04")

    def test_next_session_costs_pending_and_ablation(self):
        s = {
            "stock_id": "2330",
            "as_of": "2024-01-01",
            "sealed_at": "2024-01-01",
            "direction": 1,
            "input_snapshot_sha256": "test",
            "model_version": "v1",
        }
        rows = [
            {
                "stock_id": "2330",
                "date": "2024-01-02",
                "open": 100,
                "close": 110,
                "basis": "vendor_adjusted",
            }
        ]
        r = signal_performance([s], rows, ["2024-01-01", "2024-01-02"], 1, 0, 0, 0)
        self.assertAlmostEqual(r["average_net_return"], 0.1)
        cost = signal_performance([s], rows, ["2024-01-01", "2024-01-02"], 1)
        self.assertLess(cost["average_net_return"], r["average_net_return"])
        self.assertEqual(
            signal_performance([s], rows, ["2024-01-02"], 20)["status"], "pending_or_observe"
        )
        with self.assertRaises(ValueError):
            signal_performance([{**s, "sealed_at": "2024-01-02"}], rows, ["2024-01-02"], 1)
        self.assertEqual(paired_comparison([], [], [])["status"], "unavailable")
        self.assertEqual(
            paired_comparison(r["trades"], r["trades"], r["trades"])["paired_cells"], 1
        )
        self.assertEqual(reward_risk(100, 120, 90)["reward_risk"], 2)
        self.assertEqual(conditional_decision({"status": "unavailable"}, {})["state"], "observe")

    def test_financial_share_units_and_company_mismatch_stop_forecast(self):
        for changes in [
            {"stock_id": "3231"},
            {"weighted_diluted_shares": 100000},
            {"unit": "TWD_thousands"},
        ]:
            spec = model_spec(self.bundle)
            spec["basis_review"]["reviewed_facts"].update(changes)
            with self.assertRaises(ValueError):
                forecast(self.bundle, spec)

    def test_industry_date_exclusion_and_peer_alignment(self):
        source = {
            "classification": "management_statement",
            "dimension": "capacity",
            "claim": "Synthetic source",
            "transmission": "Capacity may affect revenue",
            "invalidation": "No production conversion",
            "source_url": "https://example.org/company",
            "content_sha256": "a" * 64,
            "published_at": "2024-01-01",
            "captured_at": "2024-02-01",
        }
        self.assertEqual(industry_review(self.bundle, [source])["status"], "available")
        self.assertEqual(
            industry_review(self.bundle, [{**source, "captured_at": "2099-01-01"}])["status"],
            "unavailable",
        )
        peer = {
            "stock_id": "2330",
            "metric": "pe",
            "period": "2024-01-31",
            "basis": "reported_unknown_denominator",
            "unit": "ratio",
            "published_at": "2024-02-01",
            "captured_at": "2024-02-02",
            "value": 10,
            "comparability_rationale": "Synthetic example, not real comparable selection",
            "source_url": "https://example.org",
        }
        self.assertEqual(
            compare_peers(self.bundle, [peer, {**peer, "stock_id": "3017"}])["status"], "available"
        )
        self.assertEqual(
            compare_peers(self.bundle, [peer, {**peer, "stock_id": "3017", "unit": "percent"}])[
                "status"
            ],
            "unavailable",
        )

    def test_three_codes_and_upgrade_cli(self):
        import json
        from pathlib import Path

        from tradingagents.research.__main__ import main

        for code in ["2330", "3017", "3231"]:
            with tempfile.TemporaryDirectory() as temp:
                p = Path(temp)
                (p / "source.json").write_text(json.dumps(snapshot(code)), encoding="utf-8")
                self.assertEqual(
                    main(
                        [
                            "upgrade",
                            "--snapshot",
                            str(p / "source.json"),
                            "--output-dir",
                            str(p / "report"),
                        ]
                    ),
                    0,
                )
                r = json.loads((p / "report/research.json").read_text(encoding="utf-8"))
                self.assertEqual(r["stock_id"], code)
                self.assertEqual(r["forecast"]["status"], "unavailable")
                self.assertFalse(r["orders"])
                manifest = json.loads((p / "report/manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(
                    hashlib.sha256((p / "report/report.html").read_bytes()).hexdigest(),
                    manifest["html_sha256"],
                )

    def test_timezone_boundary_does_not_backdate_capture(self):
        spec = model_spec(self.bundle)
        spec["basis_review"]["documents"][0]["captured_at"] = "2024-02-10T20:00:00+00:00"
        with self.assertRaises(ValueError):
            forecast(self.bundle, spec)


if __name__ == "__main__":
    unittest.main()
