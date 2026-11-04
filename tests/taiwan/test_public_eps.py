"""Offline public-EPS parsing tests; all prices and publications below are synthetic."""

import base64
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from tradingagents.research.public_eps import build, parse


def source(eps="10.50(9.50)", year="2026年(前值)", published="2026-11-01T08:00:00+08:00"):
    raw = f'''<html><meta property="article:published_time" content="{published}">
    <article><h1>FactSet 最新調查：測試公司(3017-TW)EPS預估</h1>
    <p>共12位分析師，對測試公司做出2026年EPS預估：中位數為10.50元。</p>
    <p>市場預估EPS</p><table><tr><td>預估值</td><td>{year}</td><td>2027年</td></tr>
    <tr><td>最高值</td><td>99</td><td>999</td></tr>
    <tr><td>平均值</td><td>11</td><td>21</td></tr>
    <tr><td>中位數</td><td>{eps}</td><td>20.25</td></tr></table>
    <p>市場預估營收</p><table><tr><td>預估值</td><td>2026年</td></tr>
    <tr><td>中位數</td><td>987654</td></tr></table></article></html>'''.encode()
    return {
        "source_url": "https://news.cnyes.com/news/id/1234567",
        "captured_at": "2026-11-02T01:00:00+00:00",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "content_base64": base64.b64encode(raw).decode(),
    }


def replace(doc, old, new):
    raw = base64.b64decode(doc["content_base64"]).replace(old.encode(), new.encode())
    return {
        **doc,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "content_base64": base64.b64encode(raw).decode(),
    }


class PublicEpsTests(unittest.TestCase):
    def test_median_not_mean_target_revenue_or_prior(self):
        result = parse(source(), "3017")
        self.assertEqual([r["eps"] for r in result], ["10.50", "20.25"])
        self.assertEqual(result[0]["prior_eps"], "9.50")
        self.assertEqual(result[0]["analyst_count"], 12)
        self.assertIsNone(result[1]["analyst_count"])
        self.assertIsNone(result[0]["currency"])
        self.assertIsNone(result[0]["consensus_as_of"])
        self.assertEqual(result[0]["eps_basis"], "unknown")

    def test_wrong_company_or_adr_rejected(self):
        with self.assertRaises(ValueError):
            parse(source(), "2330")
        with self.assertRaises(ValueError):
            parse(replace(source(), "3017-TW", "TSM-US"), "3017")

    def test_no_median_no_estimate(self):
        with self.assertRaises(ValueError):
            parse(replace(source(), "中位數</td>", "最高價</td>"), "3017")

    def test_no_guessed_year_or_quarter(self):
        for year in ["明年", "2026Q4", "未來12月"]:
            with self.assertRaises(ValueError):
                parse(source(year=year), "3017")

    def test_missing_is_not_zero_and_negative_eps_supported(self):
        self.assertEqual(parse(source("--"), "3017")[0]["fiscal_year"], 2027)
        self.assertEqual(parse(source("-1.25"), "3017")[0]["eps"], "-1.25")

    def test_tampered_bytes_rejected(self):
        doc = source()
        doc["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            parse(doc, "3017")

    def test_no_unknown_host_private_api_or_query(self):
        for url in [
            "https://unknown.example/news/id/123",
            "https://news.cnyes.com/api/123",
            "https://news.cnyes.com/news/id/123?token=secret",
        ]:
            doc = source()
            doc["source_url"] = url
            with self.assertRaises(ValueError):
                parse(doc, "3017")

    def test_published_time_must_be_unambiguous(self):
        doc = replace(
            source(), "<article>", '<time datetime="2026-11-02T00:00:00Z"></time><article>'
        )
        # Outside-article time is ignored; a conflicting in-article time is not.
        self.assertTrue(parse(doc, "3017"))
        doc = replace(
            source(), "<article>", '<article><time datetime="2026-11-02T00:00:00Z"></time>'
        )
        with self.assertRaises(ValueError):
            parse(doc, "3017")

    def test_after_cutoff_excluded_even_when_article_is_old(self):
        packet = {"stock_id": "3017", "as_of": "2026-11-01", "sources": {"a": source()}}
        result = build(packet, "3017", "2026-11-01")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["excluded_sources"][0]["reason"], "after_research_cutoff")

    def test_latest_supplied_publication_and_same_time_conflicts(self):
        packet = {
            "stock_id": "3017",
            "as_of": "2026-11-03",
            "sources": {
                "old": source(),
                "new": source("12.50", published="2026-11-02T08:00:00+08:00"),
            },
        }
        result = build(packet, "3017", "2026-11-03")
        self.assertEqual(result["estimates"][0]["eps"], "12.50")
        self.assertEqual(len(result["vintages"]), 4)
        packet["sources"]["conflict"] = source("15.25", published="2026-11-02T08:00:00+08:00")
        with self.assertRaises(ValueError):
            build(packet, "3017", "2026-11-03")

    def test_no_data_and_identity_mismatch(self):
        self.assertEqual(build(None, "3017", "2026-11-03")["status"], "unavailable")
        with self.assertRaises(ValueError):
            build({"stock_id": "2330", "as_of": "2026-11-03"}, "3017", "2026-11-03")

    def test_report_and_evidence_archive(self):
        from tests.taiwan.test_report_reading import sample
        from tradingagents.dataflows.taiwan.source_store import SourceStore
        from tradingagents.research.reporting import package

        snap = sample()
        snap["as_of"] = "2026-11-03"
        packet = {"stock_id": "3017", "as_of": snap["as_of"], "sources": {"article": source()}}
        before = copy.deepcopy(packet)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            result = package(
                snap,
                root,
                extension_input={
                    "stock_id": "3017",
                    "as_of": snap["as_of"],
                    "one_shot": True,
                    "public_eps": packet,
                },
            )
            self.assertEqual(result["public_eps"]["estimates"][0]["eps"], "10.50")
            page = (root / "report.html").read_text("utf8")
            self.assertIn("FactSet 共識預估", page)
            self.assertIn("10.50", page)
            self.assertIn("鉅亨轉引", page)
            self.assertTrue((root / "public-eps.json").exists())
            store = SourceStore(root / "public-eps-sources")
            self.assertEqual(len(list((store.root / "blobs").glob("*.bin"))), 1)
            self.assertTrue((root / "evidence.zip").exists())
        self.assertEqual(before, packet)
