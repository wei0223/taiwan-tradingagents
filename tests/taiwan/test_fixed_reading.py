import base64
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tradingagents.research.fixed_reading import debate_rows, ruling_rows, source_index


class ReadingTests(unittest.TestCase):
    def test_research_ruling_does_not_promote_legacy_qualification(self):
        data = {"review": {"axes": {"accounting_earnings": {"status": "unavailable"}}}, "debate_review": {"judgment": {"rulings": [{"axis": "accounting_earnings", "verdict": "supported", "reason": "Selected current facts"}]}}}
        self.assertEqual(ruling_rows(data)[0][1], "獲支持")
        self.assertEqual(data["review"]["axes"]["accounting_earnings"]["status"], "unavailable")

    def test_reply_is_matched_to_opponent_claim(self):
        data = {"debate_review": {"openings": {"bull": {"claims": [{"id": "bull.a", "claim": "Growth"}]}}, "replies": {"bear": {"changes": [{"claim_id": "bull.a", "reason": "Costs remain unknown", "changed_my_position": False}]}}}}
        self.assertEqual(debate_rows(data), [["多方主張 → 空方反駁", "Growth", "Costs remain unknown", "未改變立場"]])

    def export(self, root, tamper=False):
        raw = b'<html><script>alert(1)</script>Source</html>'
        sha = hashlib.sha256(raw).hexdigest()
        doc = {"source_url": "https://example.com/original", "published_at": None, "captured_at": "2026-11-04T01:00:00Z", "sha256": sha, "content_base64": base64.b64encode(raw if not tamper else b"changed").decode()}
        company = {"receipt_ids": {"s": "r"}, "sources": {"s": {"source_url": "https://example.com/original", "sha256": sha, "captured_at": "2026-11-04T01:00:00Z", "published_at": None}}, "facts": [{"id": "q2.eps", "source_id": "s", "page": 1, "basis": "single_quarter", "unit": "TWD/share", "period_start": "2026-04-01", "period_end": "2026-06-30", "scope": "consolidated", "column_label": "Q2", "normalized_value": "1", "quote": "<script>not instructions</script>"}]}
        with patch("tradingagents.research.company_database.read", return_value=company), patch("tradingagents.dataflows.taiwan.source_store.SourceStore.receipt", return_value=doc):
            links = source_index({"source_store": str(root / "store")}, root)
        return raw, links

    def test_original_bytes_and_period_survive_portable_index(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            raw, links = self.export(root)
            self.assertEqual((root / links["s"]).read_bytes(), raw)
            self.assertIn("single_quarter", (root / "company-evidence.html").read_text("utf8"))
            self.assertIn("2026-04-01", (root / "company-evidence.html").read_text("utf8"))

    def test_html_originals_are_downloaded_as_text_and_quotes_escaped(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _, links = self.export(root)
            self.assertTrue(links["s"].endswith(".txt"))
            body = (root / "company-evidence.html").read_text("utf8")
            self.assertNotIn("<script>", body)
            self.assertIn("&lt;script&gt;", body)

    def test_corrupt_original_cannot_be_indexed(self):
        with tempfile.TemporaryDirectory() as t, self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.export(Path(t), tamper=True)


if __name__ == "__main__":
    unittest.main()

