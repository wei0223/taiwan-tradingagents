import base64
import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from tradingagents.research.company_intake import collect


class Refusing:
    def __init__(self):
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return type("Response", (), {"status_code": 429, "content": b"rate limit"})()


class CompanyIntakeTests(unittest.TestCase):
    def test_refusal_preserved_and_no_retry_or_invented_facts(self):
        transport = Refusing()
        snapshot = {"instrument": {"stock_id": "1216", "exchange": "twse", "name": "統一"},
                    "as_of": "2026-11-07"}
        with tempfile.TemporaryDirectory() as tmp:
            result = collect(snapshot, Path(tmp) / "out", Path(tmp) / "archive",
                             transport=transport, clock=lambda: datetime(2026, 11, 7, tzinfo=UTC))
            self.assertEqual(transport.calls, 1)
            self.assertEqual(result["facts_approved"], 0)
            packet = json.loads((Path(tmp) / "out/company-input-template.json").read_text("utf8"))
            self.assertEqual(packet["facts"], [])
            self.assertEqual(len(packet["gaps"]), 8)
            queue = json.loads((Path(tmp) / "out/review-queue.json").read_text("utf8"))
            self.assertEqual(len(queue), 8)
            # A subsequent invocation with the shared archive also honors cooldown.
            collect(snapshot, Path(tmp) / "out2", Path(tmp) / "archive",
                    transport=transport, clock=lambda: datetime(2026, 11, 7, 1, tzinfo=UTC))
            self.assertEqual(transport.calls, 1)

    def test_reuse_preserves_bytes_time_and_requires_new_review(self):
        raw = b"<p>official original</p>"
        sha = hashlib.sha256(raw).hexdigest()
        source = {"stock_id": "1216", "category": "annual_report", "revision_id": sha,
                  "source_url": "https://example.com/official", "published_at": None,
                  "captured_at": "2026-11-06T01:00:00+00:00", "sha256": sha,
                  "content_base64": base64.b64encode(raw).decode()}
        packet = {"stock_id": "1216", "as_of": "2026-11-06", "sources": {"old": source},
                  "facts": [], "gaps": []}
        snap = {"instrument": {"stock_id": "1216", "exchange": "twse"}, "as_of": "2026-11-07"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "out"
            collect(snap, root, Path(tmp) / "archive", saved_packets=[packet],
                    transport=Refusing(), clock=lambda: datetime(2026, 11, 7, tzinfo=UTC))
            saved = json.loads((root / "company-input-template.json").read_text("utf8"))
            self.assertEqual(saved["sources"][sha], source)
            self.assertEqual(saved["facts"], [])
            self.assertEqual((root / (sha + ".html")).read_bytes(), raw)
            with self.assertRaises(ValueError):
                collect(snap, root, Path(tmp) / "archive", transport=Refusing(),
                        clock=lambda: datetime(2026, 11, 7, tzinfo=UTC))

    def test_wrong_company_and_late_capture_rejected_before_request(self):
        transport = Refusing()
        snap = {"instrument": {"stock_id": "1216", "exchange": "twse"}, "as_of": "2026-11-07"}
        with tempfile.TemporaryDirectory() as tmp:
            for packets, clock in [
                ([{"stock_id": "2330"}], lambda: datetime(2026, 11, 7, tzinfo=UTC)),
                ([], lambda: datetime(2026, 11, 8, tzinfo=UTC)),
            ]:
                with self.assertRaises(ValueError):
                    collect(snap, Path(tmp) / "out", Path(tmp) / "archive",
                            saved_packets=packets, transport=transport, clock=clock)
            self.assertEqual(transport.calls, 0)

