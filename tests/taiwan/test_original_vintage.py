"""Mock version registries are control tests, not real historical evidence."""

import base64
import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime

from tests.taiwan.test_originals import original, row
from tests.taiwan.test_v6_controls import vintage, work
from tradingagents.research import original_review, pit_validation, vintage as versions


def registry_proof(record, **changes):
    payload = {"semantics": "official_content_addressed_version_history",
               "history_complete_through": "2026-10-20T00:00:00Z", "versions": [{
                   "stock_id": record["stock_id"], "object_id": record["object_id"],
                   "revision_id": record["revision_id"], "content_sha256": record["source_sha256"],
                   "published_at": record["published_at"], "replaced_at": None, **changes}]}
    raw = json.dumps(payload).encode()
    return {"registry_receipt": {"source_url": "https://mopsov.twse.com.tw/mock-version-registry",
            "http_status": 200, "content_base64": base64.b64encode(raw).decode(),
            "sha256": hashlib.sha256(raw).hexdigest(), "captured_at": "2026-10-25T00:00:00Z"}}


def review_fixture():
    w, d = work(), original()
    entry = {"event_id": w["items"][0]["event_id"], "source_id": "official", "source_sha256": d["sha256"],
             "reviewed_by": "synthetic reviewer", "reviewed_at": "2026-10-25T00:00:00Z",
             "impact_conclusion": "same current revenue", "remaining_requirements": [], "observations": [{
                 "metric": "revenue_twd", "number_text": "21,018,128", "scale": 1000,
                 "value": 21018128000, "unit": "TWD", "source_quote": "21,018,128",
                 "qualification": "reported_current_month_not_initial_version"}]}
    return w, {"official": d}, entry


class VintageTests(unittest.TestCase):
    def record(self):
        r = vintage(2020, 1, "2026-10-20T00:00:00Z")
        r["object_id"] = "3017-revenue-2020-01"
        r["historical_version_proof"] = registry_proof(r)
        return r

    def test_late_capture_with_real_byte_binding_contract_can_reconstruct(self):
        r = self.record()
        k = versions.knowledge(r, "2020-03-01", "2026-10-26")
        self.assertTrue(k["eligible"])
        self.assertEqual(k["captured_at"], "2026-10-20T00:00:00Z")
        self.assertFalse(k["historically_issued_prediction"])
        self.assertEqual(len(pit_validation.known([r], "2020-03-01", "2026-10-26")), 1)

    def test_late_capture_without_proof_remains_excluded(self):
        r = self.record()
        del r["historical_version_proof"]
        self.assertFalse(versions.knowledge(r, "2020-03-01")["eligible"])

    def test_cannot_use_a_future_evidence_capture(self):
        with self.assertRaises(ValueError):
            versions.knowledge(self.record(), "2020-03-01", "2026-10-24")

    def test_replaced_version_excluded_after_replacement(self):
        r = self.record()
        r["historical_version_proof"] = registry_proof(r, replaced_at="2020-02-20T00:00:00Z")
        self.assertFalse(versions.knowledge(r, "2020-03-01", "2026-10-26")["eligible"])
        self.assertTrue(versions.knowledge(r, "2020-02-15", "2026-10-26")["eligible"])

    def test_target_hash_stock_and_revision_cannot_be_substituted(self):
        for field, value in [("content_sha256", "0"*64), ("stock_id", "2330"), ("revision_id", "other")]:
            r = self.record()
            r["historical_version_proof"] = registry_proof(r, **{field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                versions.knowledge(r, "2020-03-01", "2026-10-26")

    def test_original_registry_bytes_cannot_be_modified(self):
        r = self.record()
        r["historical_version_proof"]["registry_receipt"]["sha256"] = "0"*64
        with self.assertRaises(ValueError):
            versions.knowledge(r, "2020-03-01", "2026-10-26")

    def test_arbitrary_host_or_http_cannot_certify_history(self):
        for url in ["https://example.com/history", "http://mopsov.twse.com.tw/history", "https://mopsov.twse.com.tw.evil.org/history"]:
            r = self.record()
            r["historical_version_proof"]["registry_receipt"]["source_url"] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                versions.knowledge(r, "2020-03-01", "2026-10-26")

    def test_explicit_evaluation_clock_required_for_reconstruction(self):
        with self.assertRaises(ValueError):
            versions.knowledge(self.record(), "2020-03-01")

    def test_unproven_history_end_cannot_certify_later_cutoff(self):
        self.assertFalse(versions.knowledge(self.record(), "2026-10-21", "2026-10-26")["eligible"])

    def test_current_listing_date_or_reviewer_flag_is_not_proof(self):
        r = self.record()
        payload = {"filename": "old.pdf", "upload_date": "2020-02-10", "reviewed": True}
        raw = json.dumps(payload).encode()
        receipt = r["historical_version_proof"]["registry_receipt"]
        receipt.update(content_base64=base64.b64encode(raw).decode(), sha256=hashlib.sha256(raw).hexdigest())
        with self.assertRaises(ValueError):
            versions.knowledge(r, "2020-03-01", "2026-10-26")

    def test_date_only_publication_cannot_claim_intraday_knowledge(self):
        r = self.record()
        r["published_at"] = "2020-02-10"
        r["historical_version_proof"] = registry_proof(r)
        with self.assertRaises(ValueError):
            versions.knowledge(r, "2020-02-10T09:00:00+08:00", "2026-10-26")

    def attachment_record(self):
        r = self.record()
        raw = b"Synthetic exact version bytes, not issuer data"
        r["source_sha256"] = hashlib.sha256(raw).hexdigest()
        r["historical_version_proof"] = registry_proof(r)
        proof = r["historical_version_proof"]
        data = json.loads(base64.b64decode(proof["registry_receipt"]["content_base64"]))
        data["semantics"] = "official_immutable_attachment_version_history"
        url = "https://doc.twse.com.tw/archive/"+r["revision_id"]+".pdf"
        data["versions"][0]["immutable_attachment_url"] = url
        del data["versions"][0]["content_sha256"]
        original = json.dumps(data).encode()
        proof["registry_receipt"].update(content_base64=base64.b64encode(original).decode(), sha256=hashlib.sha256(original).hexdigest())
        proof["attachment_receipt"] = {"source_url": url, "http_status": 200,
            "captured_at": r["captured_at"], "sha256": r["source_sha256"], "content_base64": base64.b64encode(raw).decode()}
        return r

    def test_official_immutable_version_binding_does_not_require_published_hash(self):
        self.assertTrue(versions.knowledge(self.attachment_record(), "2020-03-01", "2026-10-26")["eligible"])

    def test_different_attachment_cannot_fill_same_filename(self):
        r = self.attachment_record()
        r["historical_version_proof"]["attachment_receipt"]["source_url"] += "?replacement=1"
        with self.assertRaises(ValueError):
            versions.knowledge(r, "2020-03-01", "2026-10-26")


class NumericReviewTests(unittest.TestCase):
    def run_review(self, change=None):
        w, docs, entry = review_fixture()
        if change:
            change(w, docs, entry)
        return original_review.audit(w, [row()], docs, [entry], "2026-10-26")

    def test_exact_original_amount_verified_not_initial_version(self):
        result = self.run_review()
        self.assertEqual(result["numeric_reviews_completed"], 1)
        self.assertFalse(result["reviewed"][0]["initial_version_proven"])
        self.assertFalse(result["model_or_forecast_changed"])

    def test_bad_value_or_scale_cannot_pass(self):
        for key, value in [("value", 1), ("scale", 1)]:
            def change(w, d, e, k=key, v=value):
                e["observations"][0][k] = v
            self.assertEqual(self.run_review(change)["numeric_reviews_completed"], 0)

    def test_forged_quote_or_original_hash_rejected(self):
        def change(w, d, e):
            e["observations"][0]["source_quote"] = "not found in original"
        self.assertTrue(self.run_review(change)["rejected"])
        def changed_original(w, d, e):
            d["official"]["sha256"] = "0"*64
        self.assertTrue(self.run_review(changed_original)["rejected"])

    def test_future_review_rejected(self):
        def change(w, d, e):
            e["reviewed_at"] = "2027-01-01"
        self.assertTrue(self.run_review(change)["rejected"])

    def test_shares_never_automatically_clear_future_dilution(self):
        def change(w, d, e):
            e["observations"][0]["unit"] = "shares"
        result = self.run_review(change)["reviewed"][0]
        self.assertEqual(result["resolution_state"], "NUMBERS_VERIFIED_SCOPE_PENDING")

    def test_commitment_never_automatically_becomes_cash_paid(self):
        def change(w, d, e):
            e["observations"][0]["qualification"] = "budget_not_cash_paid"
        self.assertTrue(self.run_review(change)["reviewed"][0]["remaining_requirements"])

    def test_unique_subject_second_aliases_can_join(self):
        w, _, _ = review_fixture()
        alias = copy.deepcopy(w["items"][0])
        alias.update(event_id="legacy", source_url=None, aliases=["pending-1"])
        w["items"].append(alias)
        self.assertEqual(original_review.bind_aliases(w, [row()])["canonical_count"], 1)

    def test_time_only_or_ambiguous_subject_does_not_join(self):
        w, _, _ = review_fixture()
        alias = copy.deepcopy(w["items"][0])
        alias.update(event_id="legacy", source_url=None, subject=None, aliases=["pending-1"])
        w["items"].append(alias)
        self.assertEqual(original_review.bind_aliases(w, [row()])["canonical_count"], 2)

    def test_reaudit_retains_legacy_identity_crosswalk(self):
        w, docs, entry = review_fixture()
        alias = copy.deepcopy(w["items"][0])
        alias.update(event_id="legacy", source_url=None, aliases=["pending-1"])
        w["items"].append(alias)
        first = original_review.bind_aliases(w, [row()])
        entry["event_id"] = "legacy"
        again = original_review.audit(first, [row()], docs, [entry], "2026-10-26")
        self.assertEqual(again["numeric_reviews_completed"], 1)

    def test_two_legacy_aliases_cannot_double_count_numeric_reviews(self):
        w, docs, entry = review_fixture()
        alias = copy.deepcopy(w["items"][0])
        alias.update(event_id="legacy", source_url=None, aliases=["pending-1"])
        w["items"].append(alias)
        with self.assertRaises(ValueError):
            original_review.audit(w, [row()], docs, [entry, {**entry, "event_id": "legacy"}], "2026-10-26")

    def test_wrong_company_cannot_join_or_review(self):
        def change(w, d, e):
            d["official"]["stock_id"] = "3231"
        self.assertTrue(self.run_review(change)["rejected"])


class PersistentRefusalTests(unittest.TestCase):
    def test_monthly_snapshot_exports_observed_vintages_without_backdating(self):
        from pathlib import Path

        from tests.taiwan.test_source_registry import PLAN, STAMP, Transport
        from tradingagents.research.source_registry import refresh

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            refresh(PLAN, folder/"ledger", folder/"run", Transport(), lambda: datetime.fromisoformat(STAMP))
            data = json.loads((folder/"run"/"3017-monthly-vintages.json").read_text("utf8"))
            self.assertEqual(len(data["rows"]), 1)
            row = data["rows"][0]
            self.assertIsNone(row["published_at"])
            self.assertEqual(row["value"], 1234000)
            self.assertFalse(row["initial_publication_verified"])
            self.assertEqual(pit_validation.known(data["rows"], "2026-10-19"), {})

    def test_restart_does_not_retry_refused_host_within_day(self):
        from tradingagents.dataflows.taiwan.official_sources import OfficialSources
        from tradingagents.dataflows.taiwan.source_store import SourceStore

        class NoNetwork:
            def get(self, *args, **kwargs):
                raise AssertionError("Refused host cannot be retried")
        with tempfile.TemporaryDirectory() as tmp:
            store = SourceStore(tmp)
            store.capture({"source_url": "https://doc.twse.com.tw/file", "captured_at": "2026-10-25T12:00:00Z",
                           "http_status": 403, "status": "source_rejected"})
            client = OfficialSources(store, NoNetwork(), clock=lambda: datetime.fromisoformat("2026-10-26T03:00:00+00:00"))
            receipt, _ = client.request("get", "https://doc.twse.com.tw/new-file")
            self.assertEqual(client.requests, 0)
            self.assertIn("skipped", receipt["status"])

    def test_expired_or_future_refusals_do_not_block(self):
        from tradingagents.dataflows.taiwan.source_store import SourceStore

        with tempfile.TemporaryDirectory() as tmp:
            store = SourceStore(tmp)
            for stamp in ["2026-10-20T00:00:00Z", "2026-11-20T00:00:00Z"]:
                store.capture({"source_url": "https://doc.twse.com.tw/file", "captured_at": stamp, "http_status": 403})
            self.assertEqual(store.recent_refusals(datetime.fromisoformat("2026-10-26T03:00:00+00:00")), {})
