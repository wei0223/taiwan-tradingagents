"""Adversarial fiscal semantics, time leakage, dilution and cross-company daily wiring."""

import base64
import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from tests.taiwan.test_research import snapshot
from tradingagents.research.__main__ import main
from tradingagents.research.reporting import package
from tradingagents.research.review import AXES, review

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)


def inputs(stock="3231"):
    content = b"Synthetic financial statement, not an actual issuer document"
    doc = {
        "stock_id": stock,
        "revision_id": "fixture-r1",
        "source_url": "https://example.invalid/synthetic",
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode(),
        "published_at": "2026-08-04",
        "captured_at": "2026-08-05T00:00:00+00:00",
    }
    shared = {
        "stock_id": stock,
        "reviewed_by": "Synthetic fixture reviewer",
        "values_reviewed": True,
        "locator": "Synthetic page 5, EPS note",
        "version_set": "reviewed-fixture-comparison-v1",
        "scope": "consolidated",
        "unit": "TWD_and_shares",
        "document_ids": ["doc"],
    }

    def earning(year, revenue, parent, shares, basic):
        return {
            **shared,
            "basis": "single_quarter_diluted",
            "period_start": f"{year}-04-01",
            "period_end": f"{year}-06-30",
            "diluted_note_verified": True,
            "dilution_components": [{"event_id": "cb-1", "weighted_shares": shares - basic}],
            "values": {
                "revenue_twd": revenue,
                "operating_profit_twd": revenue * 0.1,
                "parent_profit_twd": parent,
                "diluted_numerator_twd": parent,
                "numerator_adjustment_twd": 0,
                "basic_weighted_shares": basic,
                "diluted_weighted_shares": shares,
            },
        }

    cash = {
        **shared,
        "basis": "ytd",
        "period_start": "2026-01-01",
        "period_end": "2026-06-30",
        "bridge_verified": True,
        "bridge_locator": "Synthetic statement version alignment",
        "values": {"cfo_twd": -40, "revenue_twd": 300},
    }
    prior_cash = {
        **cash,
        "period_end": "2026-03-31",
        "values": {"cfo_twd": -70, "revenue_twd": 100},
    }
    return {
        "stock_id": stock,
        "cutoff_at": NOW.isoformat(),
        "documents": {"doc": doc},
        "earnings": earning(2026, 200, 40, 20, 18),
        "prior_earnings": earning(2025, 100, 10, 10, 9),
        "cash_ytd": cash,
        "previous_cash_ytd": prior_cash,
    }


def run(data):
    return review(data["stock_id"], "2026-10-15", data, clock=lambda: NOW)


class ReviewRulesTests(unittest.TestCase):
    def test_unknown_publication_requires_verified_public_capture_and_no_backfill(self):
        d = inputs()
        d["documents"]["doc"]["published_at"] = None
        self.assertEqual(run(d)["axes"]["accounting_earnings"]["status"], "unavailable")
        d["documents"]["doc"].update(
            publication_time_status="unknown_current_revision", public_capture_verified=True
        )
        self.assertEqual(run(d)["axes"]["accounting_earnings"]["status"], "both_increased")
        d["cutoff_at"] = "2026-08-04T12:00:00+00:00"
        r = review("3231", "2026-08-04", d, clock=lambda: NOW)
        self.assertEqual(r["axes"]["accounting_earnings"]["status"], "unavailable")

    def test_q1_cash_needs_no_prior_and_missing_q1_does_not_erase_H1(self):
        d = inputs()
        d.pop("previous_cash_ytd")
        c = run(d)["axes"]["cash_sustainability"]
        self.assertEqual(c["status"], "unavailable")
        self.assertEqual(c["ytd_observation"]["values"]["cfo_twd"], -40)
        d["cash_ytd"]["period_end"] = "2026-03-31"
        c = run(d)["axes"]["cash_sustainability"]
        self.assertEqual(c["single_quarter"]["cfo_twd"], -40)

    def test_company_specific_no_wistron_margin_or_share_threshold(self):
        for stock, parent, shares in [("2330", 4000, 200), ("3017", 400, 20), ("3231", 40, 20)]:
            d = inputs(stock)
            d["earnings"]["values"].update(
                parent_profit_twd=parent,
                diluted_numerator_twd=parent,
                basic_weighted_shares=shares - 2,
                diluted_weighted_shares=shares,
            )
            r = run(d)
            self.assertEqual(tuple(r["axes"]), AXES)
            self.assertEqual(r["axes"]["accounting_earnings"]["status"], "both_increased")
            self.assertFalse(r["axes"]["valuation"]["undervaluation_established"])

    def test_parent_growth_does_not_guarantee_per_share_growth(self):
        d = inputs()
        d["earnings"]["values"].update(basic_weighted_shares=98, diluted_weighted_shares=100)
        self.assertEqual(run(d)["axes"]["accounting_earnings"]["status"], "mixed")

    def test_cash_ytd_differenced_not_reported_as_single_quarter(self):
        cash = run(inputs())["axes"]["cash_sustainability"]
        self.assertEqual(cash["single_quarter"]["cfo_twd"], 30)
        self.assertEqual(cash["ytd"]["cfo_twd"], -40)
        self.assertEqual(cash["period_start"], "2026-04-01")
        self.assertFalse(cash["collection_confirmed"])
        self.assertEqual(cash["collection_evidence"]["status"], "unavailable")

    def test_version_mismatch_and_missing_alignment_not_silently_subtracted(self):
        for changes in ({"version_set": "later-restatement"}, {"period_end": "2025-03-31"}):
            d = inputs()
            d["previous_cash_ytd"].update(changes)
            self.assertEqual(run(d)["axes"]["cash_sustainability"]["status"], "unavailable")
        d = inputs()
        d["cash_ytd"]["bridge_verified"] = False
        self.assertEqual(run(d)["axes"]["cash_sustainability"]["status"], "unavailable")

    def test_no_eps_ytd_or_unit_mixing(self):
        for changes in ({"basis": "ytd"}, {"unit": "thousand_TWD"}, {"scope": "parent_only"}):
            d = inputs()
            d["earnings"].update(changes)
            self.assertEqual(run(d)["axes"]["accounting_earnings"]["status"], "unavailable")

    def test_publication_capture_and_date_precision_are_independent_gates(self):
        for field, value in [
            ("published_at", "2026-10-16"),
            ("captured_at", "2026-10-16T00:00:00+00:00"),
            ("captured_at", "2026-08-05"),
            ("published_at", "2026-10-15"),
        ]:
            d = inputs()
            d["documents"]["doc"][field] = value
            self.assertEqual(run(d)["axes"]["accounting_earnings"]["status"], "unavailable")

    def test_quarter_yoy_mismatch_rejected(self):
        d = inputs()
        d["prior_earnings"].update(period_start="2025-01-01", period_end="2025-03-31")
        self.assertEqual(run(d)["axes"]["accounting_earnings"]["status"], "unavailable")

    def test_missing_diluted_note_not_inferred_from_rounded_eps(self):
        d = inputs()
        d["earnings"]["diluted_note_verified"] = False
        self.assertEqual(run(d)["axes"]["accounting_earnings"]["status"], "unavailable")

    def test_denominator_numerator_and_double_dilution_fail(self):
        for change in ("denominator", "numerator", "duplicate", "incremental"):
            d = inputs()
            e = d["earnings"]
            if change == "denominator":
                e["values"]["diluted_weighted_shares"] += 1
            if change == "numerator":
                e["values"]["diluted_numerator_twd"] += 1
            if change == "duplicate":
                e["dilution_components"] *= 2
            if change == "incremental":
                e["incremental_event_ids"] = ["cb-1"]
            with self.subTest(change=change), self.assertRaises(ValueError):
                run(d)

    def test_source_hash_foreign_company_and_nonfinite_fail(self):
        for kind in ("hash", "company", "nan"):
            d = inputs()
            if kind == "hash":
                d["documents"]["doc"]["sha256"] = "0" * 64
            if kind == "company":
                d["documents"]["doc"]["stock_id"] = "3017"
            if kind == "nan":
                d["earnings"]["values"]["parent_profit_twd"] = float("nan")
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                run(d)

    def test_authorization_vs_completed_issue_and_following_note(self):
        d = inputs()
        event = {**d["earnings"], "event_id": "gdr-2026", "event_status": "authorized"}
        d["capital_events"] = [event]
        self.assertEqual(run(d)["capital_events"][0]["status"], "authorization_only")
        event.update(
            event_status="issued",
            actual_new_shares=2,
            issue_date="2026-05-01",
            completion_locator="Issue completion announcement",
        )
        self.assertEqual(run(d)["capital_events"][0]["status"], "unavailable")
        event.update(eps_note_reconciled=True, eps_note_locator="Synthetic weighted capital bridge")
        d["earnings"]["basic_share_event_ids"] = ["gdr-2026"]
        event["share_register_bridge"] = {
            "opening_shares": 16,
            "closing_shares": 18,
            "other_net_shares": 0,
            "locator": "Synthetic share register",
        }
        self.assertEqual(run(d)["capital_events"][0]["status"], "issued_and_note_reconciled")
        self.assertFalse(run(d)["capital_events"][0]["recompute_eps"])
        event["issue_date"] = "2026-09-01"
        self.assertEqual(run(d)["capital_events"][0]["reason"], "await_following_quarter_EPS_note")

    def test_capital_amount_must_match_register(self):
        d = inputs()
        d["earnings"]["basic_share_event_ids"] = ["gdr-2026"]
        d["capital_events"] = [
            {
                **d["earnings"],
                "event_id": "gdr-2026",
                "event_status": "issued",
                "actual_new_shares": 2,
                "issue_date": "2026-05-01",
                "completion_locator": "Issue announcement",
                "eps_note_reconciled": True,
                "eps_note_locator": "Basic shares note",
                "share_register_bridge": {
                    "opening_shares": 16,
                    "closing_shares": 19,
                    "other_net_shares": 0,
                    "locator": "Share register",
                },
            }
        ]
        with self.assertRaises(ValueError):
            run(d)

    def test_absolute_cash_deterioration_and_ratio_improvement_can_coexist(self):
        d = inputs()
        d["prior_year_cash_ytd"] = {
            **d["cash_ytd"],
            "period_start": "2025-01-01",
            "period_end": "2025-06-30",
            "values": {"cfo_twd": -30, "revenue_twd": 100},
        }
        r = run(d)["axes"]["cash_sustainability"]["same_YTD_comparison"]
        self.assertEqual(r["cfo_change_twd"], -10)
        self.assertGreater(r["ratio_change_pp"], 0)

    def test_new_reports_always_contain_four_axes_and_refuse_overwrite(self):
        for stock in ("2330", "3017"):
            s = snapshot(stock)
            with tempfile.TemporaryDirectory() as folder:
                r = package(s, folder)
                self.assertEqual(tuple(r["review"]["axes"]), AXES)
                page = (Path(folder) / "report.html").read_text(encoding="utf-8")
                self.assertIn("已實現會計獲利", page)
                self.assertIn("現金持續性", page)
                self.assertTrue((Path(folder) / "review-input.json").exists())
                original = (Path(folder) / "report.html").read_bytes()
                with self.assertRaises(ValueError):
                    package(s, folder)
                self.assertEqual((Path(folder) / "report.html").read_bytes(), original)

    def test_cli_review_binding_prevents_reusing_another_company(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "snapshot.json").write_text(json.dumps(snapshot("2330")), encoding="utf-8")
            (root / "review.json").write_text(json.dumps(inputs("3017")), encoding="utf-8")
            self.assertEqual(
                main(
                    [
                        "upgrade",
                        "--snapshot",
                        str(root / "snapshot.json"),
                        "--review-input",
                        str(root / "review.json"),
                        "--output-dir",
                        str(root / "new-report"),
                    ]
                ),
                2,
            )


if __name__ == "__main__":
    unittest.main()
