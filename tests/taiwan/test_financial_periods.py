"""Real transcribed amounts, mock documents, and strict period/version regressions."""

import base64
import copy
import hashlib
import json
import unittest
from pathlib import Path

from tradingagents.dataflows.taiwan.calculations import financial_metrics
from tradingagents.dataflows.taiwan.periods import cash_metrics, resolve

ACTUALS = json.loads((Path(__file__).parent / "fixtures/financial_period_actuals.json").read_text(encoding="utf8"))
CUTOFF = "2026-10-25T15:00:00+00:00"


def ledger(stock="3017"):
    values = ACTUALS[stock]
    raw = json.dumps(values, sort_keys=True).encode()
    doc = {"stock_id": stock, "revision_id": "mock-reviewed-transcription-v1",
           "source_url": "https://example.org/mock-archived-transcription",
           "content_base64": base64.b64encode(raw).decode(), "sha256": hashlib.sha256(raw).hexdigest(),
           "captured_at": "2026-10-25T14:00:00+00:00", "published_at": "2026-08-20"}
    rows, bridges = [], []
    for field, name in [("Revenue", "revenue"), ("CashFlowsFromOperatingActivities", "cfo")]:
        for prefix, end in [("q1", "2026-03-31"), ("h1", "2026-06-30")]:
            rows.append({"id": prefix + "." + field, "stock_id": stock, "field": field,
                         "value": values[f"{prefix}_{name}_twd"], "period_start": "2026-01-01",
                         "period_end": end, "basis": "ytd", "scope": "consolidated", "unit": "TWD",
                         "statement_type": "cash_flow" if name == "cfo" else "income",
                         "version_set": "mock-reviewed-current", "document_ids": ["doc"],
                         "locator": values["review_locator"], "values_reviewed": True,
                         "reviewed_by": "explicit real-value transcription test fixture"})
        bridges.append({"current_id": "h1." + field, "previous_id": "q1." + field, "field": field,
                        "bridge_verified": True, "version_set": "mock-reviewed-current",
                        "document_sha256s": [doc["sha256"]], "locator": "mock bridge review",
                        "reviewed_by": "fixture"})
    return {"stock_id": stock, "documents": {"doc": doc}, "observations": rows, "bridges": bridges}


class FinancialPeriodTests(unittest.TestCase):
    def test_older_verified_quarter_does_not_make_unknown_latest_quarter_ready(self):
        from tests.taiwan.test_extensions import make_provider
        from tradingagents.research.bundle import build_bundle

        provider, _ = make_provider()
        snapshot = provider.snapshot("2330", "2024-01-01", "2024-05-20")
        snapshot["as_of"] = "2024-07-20"
        review = snapshot["financial_period_review"]
        doc = copy.deepcopy(review["documents"]["synthetic"])
        doc.update(published_at="2024-07-10", captured_at="2024-07-20T12:00:00+00:00")
        review["documents"]["q2"] = doc
        obs = copy.deepcopy(review["observations"][0])
        obs.update(id="q2-gross-only", field="GrossProfit", value=100, unit="TWD",
                   period_start="2024-04-01", period_end="2024-06-30", document_ids=["q2"])
        review["observations"].append(obs)
        metrics = financial_metrics([], review, snapshot["as_of"], "2330")
        self.assertIsNotNone(metrics[0]["eps_twd"])
        self.assertIsNone(metrics[-1]["eps_twd"])
        bundle = build_bundle(snapshot)
        self.assertEqual(bundle["coverage"]["TaiwanStockFinancialStatements"]["calculation_status"],
                         "unavailable_unverified_field_periods")

    def test_reviewed_cash_capex_sign_and_free_cash_flow(self):
        from tests.taiwan.test_extensions import make_provider
        from tradingagents.research.financial_periods import prepare

        provider, _ = make_provider()
        snapshot = provider.snapshot("2330", "2024-01-01", "2024-05-20")
        proof = snapshot["financial_period_review"]["documents"]
        packet = {"stock_id": "2330", "basis": "single_quarter", "period_start": "2024-01-01",
                  "period_end": "2024-03-31", "scope": "consolidated", "unit": "TWD_and_shares",
                  "version_set": "synthetic-v1", "document_ids": ["synthetic"], "locator": "mock cash table",
                  "values_reviewed": True, "reviewed_by": "fixture", "values": {"cfo_twd": 1000, "capex_ppe_twd": 200},
                  "statement_kind": "cash_flow_statement"}
        corrected = prepare(snapshot, {"documents": proof}, {"financial_packets": {"cash": packet}})
        cash = corrected["extended_research"]["cashflow"][0]
        self.assertEqual(cash["ppe_cash_outflow_twd_as_reported"], 200)
        self.assertEqual(cash["operating_less_ppe_twd_as_reported"], 800)

    def test_pre_period_forecast_cannot_be_relabeled_actual(self):
        review = ledger()
        review["documents"]["doc"]["published_at"] = "2026-01-01"
        self.assertEqual(resolve(review, "3017", CUTOFF)["rows"], [])

    def test_monthly_revenue_packet_is_not_an_accounting_quarter(self):
        from tests.taiwan.test_extensions import make_provider
        from tradingagents.research.financial_periods import prepare

        provider, _ = make_provider()
        snapshot = provider.snapshot("2330", "2024-01-01", "2024-05-20")
        corrected = prepare(snapshot, {"financial_period_packet_selection": {"monthly": "monthly_revenue_sum"}},
                            {"financial_packets": {"monthly": {"values": {"revenue_twd": 999999}}}})
        self.assertTrue(any("accounting_statement_type" in row["reason"] for row in
                            corrected["financial_period_adapter_audit"]["rejected_packets"]))
        self.assertFalse(any(row["value"] == 999999 for row in corrected["financial_period_audit"]["rows"]))

    def test_qoq_uses_diluted_disclosures_and_keeps_basic_separate(self):
        review = ledger()
        template = copy.deepcopy(review["observations"][0])
        for index, (field, kind, start, end, value) in enumerate([
            ("DilutedEPS", "diluted", "2026-01-01", "2026-03-31", 19.81),
            ("DilutedEPS", "diluted", "2026-04-01", "2026-06-30", 23.92),
            ("BasicEPS", "basic", "2026-01-01", "2026-03-31", 20.17),
            ("BasicEPS", "basic", "2026-04-01", "2026-06-30", 24.37),
        ]):
            review["observations"].append({**template, "id": f"eps-{index}", "field": field,
                                           "eps_kind": kind, "unit": "TWD/share", "value": value,
                                           "basis": "single_quarter", "period_start": start, "period_end": end})
        metric = financial_metrics([], review, CUTOFF, "3017")[-1]
        self.assertEqual(round(metric["eps_qoq_pct"], 3), 20.747)
        self.assertEqual(metric["basic_eps_twd"], 24.37)
        self.assertEqual(metric["eps_twd"], 23.92)

    def test_unknown_publication_can_only_be_known_from_verified_capture(self):
        review = ledger()
        doc = review["documents"]["doc"]
        doc.update(published_at=None, public_capture_verified=True, publication_time_status="unknown_current_revision")
        self.assertTrue(resolve(review, "3017", CUTOFF)["rows"])
        self.assertEqual(resolve(review, "3017", "2026-10-25T13:00:00+00:00")["rows"], [])

    def test_three_real_companies_adjacent_ytd_amounts_and_cash_ratios(self):
        for stock, expected in ACTUALS.items():
            with self.subTest(stock=stock):
                review = ledger(stock)
                rows = resolve(review, stock, CUTOFF)["rows"]
                q2 = {r["field"]: r["value"] for r in rows if r["period_end"] == "2026-06-30"}
                self.assertEqual(q2["Revenue"], expected["q2_revenue_twd"])
                self.assertEqual(q2["CashFlowsFromOperatingActivities"], expected["q2_cfo_twd"])
                cash = cash_metrics([], review, CUTOFF, stock)[-1]
                self.assertAlmostEqual(cash["cfo_to_revenue"], expected["q2_cfo_twd"] / expected["q2_revenue_twd"])
                self.assertIsNone(cash["operating_less_ppe_twd_as_reported"])

    def test_unknown_vendor_date_and_forged_cached_metadata_never_approve_quarter(self):
        for stock, expected in ACTUALS.items():
            records = [{"stock_id": stock, "period": "2026-06-30", "retrieved_at": CUTOFF,
                        "values": {"Revenue": expected["h1_revenue_twd"], "EPS": 999},
                        "field_periods": {"Revenue": {"reviewed": True, "basis": "single_quarter"}}}]
            metric = financial_metrics(records, cutoff=CUTOFF, stock_id=stock)[-1]
            self.assertIsNone(metric["eps_twd"])
            self.assertIsNone(metric["revenue_twd"])
            self.assertIsNone(metric["gross_margin_pct"])

    def test_scope_unit_version_year_and_statement_mismatch_stop_differencing(self):
        for key, bad in [("scope", "parent_only"), ("unit", "TWD_thousands"),
                         ("version_set", "other_revision"), ("period_end", "2025-03-31"),
                         ("statement_type", "balance")]:
            with self.subTest(field=key):
                review = ledger()
                review["observations"][0][key] = bad
                rows = resolve(review, "3017", CUTOFF)["rows"]
                self.assertFalse(any(r["field"] == "Revenue" and r["period_end"] == "2026-06-30" for r in rows))

    def test_missing_bridge_or_different_hash_is_not_a_version_match(self):
        for bridge in [[], [{"current_id": "h1.Revenue", "previous_id": "q1.Revenue",
                              "field": "Revenue", "bridge_verified": True, "version_set": "mock-reviewed-current",
                              "reviewed_by": "fixture", "locator": "p1", "document_sha256s": ["0" * 64]}]]:
            review = ledger()
            review["bridges"] = bridge
            self.assertFalse(any(r["period_end"] == "2026-06-30" for r in resolve(review, "3017", CUTOFF)["rows"]))

    def test_eps_and_weighted_shares_are_never_ytd_differences(self):
        for field, unit in [("DilutedEPS", "TWD/share"), ("DilutedWeightedShares", "shares")]:
            review = ledger()
            for row in review["observations"]:
                row.update(field=field, unit=unit, statement_type="eps_note", eps_kind="diluted")
            self.assertEqual(resolve(review, "3017", CUTOFF)["rows"], [])

    def test_point_balances_never_ytd_differences(self):
        review = ledger()
        for row in review["observations"]:
            row.update(field="TotalAssets", statement_type="balance", basis="instant", period_start=None)
        self.assertEqual(resolve(review, "3017", CUTOFF)["rows"], [])

    def test_basic_eps_cannot_fill_diluted_slot(self):
        review = ledger()
        obs = copy.deepcopy(review["observations"][0])
        obs.update(id="eps", field="BasicEPS", eps_kind="diluted", unit="TWD/share",
                   statement_type="income", basis="single_quarter", value=24.37)
        review["observations"] = [obs]
        self.assertEqual(resolve(review, "3017", CUTOFF)["rows"], [])
        obs["eps_kind"] = "basic"
        metric = financial_metrics([], review, CUTOFF, "3017")[-1]
        self.assertIsNone(metric["eps_twd"])
        self.assertEqual(metric["basic_eps_twd"], 24.37)

    def test_future_capture_publication_or_changed_original_excluded(self):
        for key, value in [("captured_at", "2026-10-26T00:00:00+00:00"),
                           ("published_at", "2026-10-26"), ("sha256", "0" * 64)]:
            review = ledger()
            review["documents"]["doc"][key] = value
            self.assertEqual(resolve(review, "3017", CUTOFF)["rows"], [])

    def test_margins_require_matched_quarters_and_diluted_qoq_separate(self):
        review = ledger()
        for field, value in [("GrossProfit", ACTUALS["3017"]["q2_gross_profit_twd"]),
                             ("OperatingIncome", ACTUALS["3017"]["q2_operating_profit_twd"]),
                             ("DilutedEPS", 23.92)]:
            obs = copy.deepcopy(review["observations"][1])
            obs.update(id=field, field=field, value=value, basis="single_quarter", period_start="2026-04-01",
                       unit="TWD/share" if field == "DilutedEPS" else "TWD", eps_kind="diluted")
            review["observations"].append(obs)
        metric = financial_metrics([], review, CUTOFF, "3017")[-1]
        self.assertEqual(round(metric["operating_margin_pct"], 2), 27.44)
        self.assertEqual(metric["eps_twd"], 23.92)
        self.assertIsNone(metric["eps_qoq_pct"])
        review["observations"][-2]["version_set"] = "unmatched"
        self.assertIsNone(financial_metrics([], review, CUTOFF, "3017")[-1]["operating_margin_pct"])

    def test_conflicting_direct_and_derived_values_fail_closed(self):
        review = ledger()
        obs = copy.deepcopy(review["observations"][1])
        obs.update(id="direct", basis="single_quarter", period_start="2026-04-01", value=1)
        review["observations"].append(obs)
        self.assertFalse(any(r["field"] == "Revenue" and r["period_end"] == "2026-06-30" for r in resolve(review, "3017", CUTOFF)["rows"]))
