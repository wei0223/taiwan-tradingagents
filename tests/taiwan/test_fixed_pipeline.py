"""Synthetic offline source/price tests; never Sponsor or external LLM calls."""

import base64
import copy
import hashlib
import importlib.util
import json
import math
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from tradingagents.research.company_database import append, export, plan, read, validate
from tradingagents.research.fixed_pipeline import prepare, report_inputs
from tradingagents.research.technical_engine import (
    _calculate,
    acquire_history,
    build,
    history_audit,
)

HAS_TALIB = importlib.util.find_spec("talib") is not None
NOW = datetime(2026, 11, 3, 9, tzinfo=UTC)


def market_snapshot(n=1100, exchange="twse", flat=False):
    cursor = date(2026, 11, 2)
    days = []
    while len(days) < n:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    days.reverse()
    rows = []
    for i, d in enumerate(days):
        close = 100 if flat else 100 + i * 0.03 + 2 * math.sin(i / 9)
        opened = close if flat else close + 0.3 * math.cos(i / 7)
        rows.append(
            {
                "data_date": d,
                "stock_id": "3017",
                "available_at": d,
                "retrieved_at": NOW.isoformat(),
                "values": {
                    "open": opened,
                    "high": max(close, opened) + 1,
                    "low": min(close, opened) - 1,
                    "close": close,
                    "volume": 1000 + (i % 30) * 100,
                },
            }
        )
    monthly = {}
    for d in days:
        monthly.setdefault(d[:7], []).append(d)
    archives = []
    for month, values in monthly.items():
        year, mm = map(int, month.split("-"))
        payload = {
            "date": f"{year}{mm:02d}01",
            "stat": "OK",
            "fields": ["日期"],
            "data": [[f"{int(d[:4]) - 1911}/{d[5:7]}/{d[8:]}"] for d in values],
        }
        url = "https://www.twse.com.tw/exchangeReport/FMTQIK"
        query = {"date": f"{year}{mm:02d}01", "response": "json"}
        if exchange == "tpex":
            payload = {
                "date": f"{year}{mm:02d}01",
                "stat": "ok",
                "tables": [
                    {
                        "date": month.replace("-", "/"),
                        "fields": ["Date"],
                        "totalCount": len(values),
                        "data": [[d.replace("-", "/")] for d in values],
                    }
                ],
            }
            url = "https://www.tpex.org.tw/www/en-us/indexInfo/inx"
            query["date"] = f"{year}/{mm:02d}/01"
        raw = json.dumps(payload, ensure_ascii=False)
        archives.append(
            {
                "source_url": url,
                "query": query,
                "raw_response_utf8": raw,
                "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "retrieved_at": NOW.isoformat(),
            }
        )
    return {
        "instrument": {"stock_id": "3017", "exchange": exchange},
        "as_of": "2026-11-03",
        "datasets": {"TaiwanStockPrice": {"status": "ok", "records": rows}},
        "market_calendar": {
            "status": "ok",
            "exchange": exchange,
            "start": days[0],
            "end": "2026-11-02",
            "sessions": days,
            "archives": archives,
        },
    }


def company_input():
    raw = "奇鋐 3017 2026年4月1日至6月30日 單季 合併 TWD仟元 營業收入 1,234 基本EPS 2.50 稀釋EPS 2.40 水冷產品".encode()
    source = {
        "stock_id": "3017",
        "category": "financial_statement",
        "revision_id": "synthetic-fixture-v1",
        "source_url": "https://example.test/official-fixture",
        "published_at": "2026-08-12",
        "captured_at": "2026-11-01T01:00:00+00:00",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "content_base64": base64.b64encode(raw).decode(),
    }
    fact = {
        "id": "revenue.2026Q2",
        "kind": "numeric",
        "metric": "revenue",
        "raw_value": "1,234",
        "multiplier": 1000,
        "unit": "TWD",
        "period_start": "2026-04-01",
        "period_end": "2026-06-30",
        "basis": "single_quarter",
        "scope": "consolidated",
        "source_id": "q2",
        "page": 1,
        "quote": "營業收入 1,234",
        "column_label": "2026年4月1日至6月30日",
        "context_quotes": ["單季", "合併", "TWD仟元"],
        "reviewer": "synthetic offline fixture",
        "reviewed_at": "2026-11-02T00:00:00+00:00",
    }
    return {
        "stock_id": "3017",
        "as_of": "2026-11-03",
        "exchange": "twse",
        "sources": {"q2": source},
        "facts": [fact],
        "assumptions": [],
        "gaps": [
            {"category": "annual_report", "status": "pending", "reason": "synthetic absent annual"}
        ],
    }


class FixedHistoryTests(unittest.TestCase):
    def test_1000_official_sessions_pass_both_markets(self):
        for market in ("twse", "tpex"):
            self.assertEqual(history_audit(market_snapshot(1000, market))["status"], "passed")

    def test_999_sessions_do_not_pass(self):
        s = market_snapshot(1000)
        s["datasets"]["TaiwanStockPrice"]["records"].pop(0)
        self.assertEqual(history_audit(s)["status"], "insufficient_history")

    def test_gap_with_more_than_1000_rows_still_blocks(self):
        s = market_snapshot()
        s["datasets"]["TaiwanStockPrice"]["records"].pop(-5)
        self.assertEqual(history_audit(s)["reasons"], ["missing_or_suspended_sessions_not_filled"])

    def test_wrong_calendar_source_hash_and_stale_coverage_fail(self):
        s = market_snapshot()
        s["market_calendar"]["archives"][0]["raw_response_utf8"] += " "
        with self.assertRaises(ValueError):
            history_audit(s)
        s = market_snapshot()
        s["market_calendar"]["end"] = "2026-11-01"
        self.assertEqual(history_audit(s)["reasons"], ["official_calendar_does_not_cover_cutoff"])

    def test_duplicate_wrong_company_nonfinite_and_invalid_ohlcv(self):
        for mutation in ("duplicate", "company", "nan", "range"):
            s = market_snapshot()
            rows = s["datasets"]["TaiwanStockPrice"]["records"]
            if mutation == "duplicate":
                rows.append(copy.deepcopy(rows[-1]))
            if mutation == "company":
                rows[-1]["stock_id"] = "2330"
            if mutation == "nan":
                rows[-1]["values"]["close"] = float("nan")
            if mutation == "range":
                rows[-1]["values"]["low"] = 99999
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                history_audit(s)

    def test_future_capture_rejected(self):
        s = market_snapshot()
        s["datasets"]["TaiwanStockPrice"]["records"][-1]["retrieved_at"] = (
            "2026-11-04T00:00:00+00:00"
        )
        with self.assertRaises(ValueError):
            history_audit(s)

    def test_source_unavailable_does_not_retry(self):
        s = market_snapshot()
        s["datasets"]["TaiwanStockPrice"]["status"] = "unavailable"

        class Fake:
            calls = 0

            def snapshot(self, *args):
                self.calls += 1
                return copy.deepcopy(s)

        provider = Fake()
        result = acquire_history(provider, "3017", "2021-01-01", "2026-11-03")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(
            result["technical_history_acquisition"]["attempts"][0]["status"], "blocked"
        )

    def test_bounded_backfill_stops_after_three_attempts(self):
        s = market_snapshot(999)

        class Fake:
            calls = 0

            def snapshot(self, *args):
                self.calls += 1
                return copy.deepcopy(s)

        provider = Fake()
        result = acquire_history(provider, "3017", "2021-01-01", "2026-11-03")
        self.assertEqual(provider.calls, 3)
        self.assertEqual(len(result["technical_history_acquisition"]["attempts"]), 3)


@unittest.skipUnless(HAS_TALIB, "fixed-research optional dependencies not installed")
class FixedIndicatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = market_snapshot()
        cls.package = build(cls.source)

    def test_catalog_and_talib_oracle(self):
        import numpy as np
        import talib

        raw = self.package["price_series"]["raw"]
        close = np.asarray([b["close"] for b in raw["bars"]])
        hi = np.asarray([b["high"] for b in raw["bars"]])
        lo = np.asarray([b["low"] for b in raw["bars"]])
        self.assertEqual(raw["indicator_columns"], 108)
        self.assertEqual(raw["pattern_functions"], 61)
        for key, oracle in [
            ("SMA_20", talib.SMA(close, 20)),
            ("RSI_14", talib.RSI(close, 14)),
            ("ATR_14", talib.ATR(hi, lo, close, 14)),
        ]:
            self.assertAlmostEqual(raw["latest"][key], float(oracle[-1]), places=12)

    def test_every_series_aligned_and_json_safe(self):
        raw = self.package["price_series"]["raw"]
        self.assertEqual(len(raw["dates"]), 1000)
        self.assertTrue(all(len(v) == 1000 for v in raw["indicators"].values()))
        self.assertIsNone(raw["indicators"]["SMA_240"][238])
        self.assertIsNotNone(raw["indicators"]["SMA_240"][239])
        json.dumps(self.package, allow_nan=False)

    def test_volume_ratio_excludes_today(self):
        raw = self.package["price_series"]["raw"]
        vol = [b["volume"] for b in raw["bars"]]
        self.assertAlmostEqual(raw["latest"]["VOLUME_RATIO_20"], vol[-1] / (sum(vol[-21:-1]) / 20))

    def test_prefix_invariance_no_future_data(self):
        rows = self.source["datasets"]["TaiwanStockPrice"]["records"][:300]
        prefix = _calculate(rows[:200], "raw")
        full = _calculate(rows, "raw")
        for key, values in prefix["indicators"].items():
            self.assertEqual(values, full["indicators"][key][:200], key)
        for key, p in prefix["patterns"].items():
            self.assertEqual(p["values"], full["patterns"][key]["values"][:200], key)

    def test_weekly_excludes_current_partial_week(self):
        self.assertIn("2026-11-02", self.package["weekly"]["excluded_partial_dates"])
        self.assertLess(self.package["weekly"]["complete"]["dates"][-1], "2026-11-02")

    def test_zero_volume_and_flat_price_have_no_infinity(self):
        s = market_snapshot(flat=True)
        for r in s["datasets"]["TaiwanStockPrice"]["records"]:
            r["values"]["volume"] = 0
        p = build(s)
        self.assertEqual(p["status"], "ready")
        self.assertIsNone(p["price_series"]["raw"]["latest"]["VOLUME_RATIO_20"])
        json.dumps(p, allow_nan=False)

    def test_repeated_run_and_input_not_mutated(self):
        before = copy.deepcopy(self.source)
        self.assertEqual(build(self.source), self.package)
        self.assertEqual(before, self.source)

    def test_all_preparation_outputs_and_no_fake_report(self):
        with tempfile.TemporaryDirectory() as d:
            state = prepare(self.source, Path(d) / "fixed")
            self.assertFalse(state["report_generated"])
            self.assertTrue((Path(d) / "fixed/candles.svg").exists())
            self.assertTrue((Path(d) / "fixed/company-instructions.md").exists())
            with self.assertRaises(ValueError):
                prepare(self.source, Path(d) / "fixed")


class CompanyDatabaseTests(unittest.TestCase):
    def test_plan_is_fixed_and_company_specific(self):
        p = plan("3017", "2026-11-03", "twse")
        self.assertEqual(len(p["financial_quarters"]), 8)
        self.assertEqual(len(p["revenue_months"]), 60)
        self.assertNotEqual(p["questions"], plan("2330", "2026-11-03", "twse")["questions"])
        self.assertFalse(p["create_monitor"])

    def test_quote_units_and_independent_qualifications(self):
        p = validate(company_input())
        self.assertEqual(p["facts"][0]["normalized_value"], "1234000")
        self.assertFalse(p["strict_pit_ready"])
        self.assertFalse(p["financial_qualification_automatically_promoted"])

    def test_hash_quote_company_unit_and_period_rejections(self):
        for kind in ("hash", "quote", "company", "unit", "period", "derived"):
            p = company_input()
            f = p["facts"][0]
            if kind == "hash":
                p["sources"]["q2"]["sha256"] = "0" * 64
            if kind == "quote":
                f["quote"] = "營業收入 9,999"
            if kind == "company":
                p["sources"]["q2"]["stock_id"] = "3231"
            if kind == "unit":
                f["unit"] = "shares"
            if kind == "period":
                f["period_start"] = "2026-01-01"
            if kind == "derived":
                f["derivation"] = "ytd_difference"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate(p)

    def test_eps_kind_and_thousand_multiplier_cannot_mix(self):
        p = company_input()
        f = p["facts"][0]
        f.update(
            metric="diluted_eps",
            raw_value="2.40",
            quote="稀釋EPS 2.40",
            unit="TWD/share",
            eps_kind="basic",
            multiplier=1,
        )
        with self.assertRaises(ValueError):
            validate(p)
        f["eps_kind"] = "diluted"
        f["context_quotes"] = ["稀釋EPS"]
        f["multiplier"] = 1000
        with self.assertRaises(ValueError):
            validate(p)
        f["multiplier"] = 1
        self.assertEqual(validate(p)["facts"][0]["normalized_value"], "2.40")

    def test_future_source_and_review_before_capture_rejected(self):
        p = company_input()
        p["sources"]["q2"]["captured_at"] = "2026-11-04T01:00:00+00:00"
        with self.assertRaises(ValueError):
            validate(p)
        p = company_input()
        p["facts"][0]["reviewed_at"] = "2026-10-01T00:00:00+00:00"
        with self.assertRaises(ValueError):
            validate(p)

    def test_conflicting_vintages_require_explicit_selection(self):
        p = company_input()
        p["facts"].append({**p["facts"][0], "id": "revenue.version2"})
        with self.assertRaises(ValueError):
            validate(p)
        p["selected_fact_ids"] = ["revenue.version2"]
        result = validate(p)
        self.assertEqual(len(result["facts"]), 1)
        self.assertEqual(len(result["fact_vintages"]), 2)

    def test_assumptions_cannot_be_facts_or_reference_excluded_vintage(self):
        p = company_input()
        p["facts"][0]["kind"] = "assumption"
        with self.assertRaises(ValueError):
            validate(p)
        p = company_input()
        p["assumptions"] = [
            {
                "rationale": "synthetic scenario",
                "uncertainty": "unverified",
                "evidence_fact_ids": ["missing"],
            }
        ]
        with self.assertRaises(ValueError):
            validate(p)

    def test_append_read_export_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as d:
            ref = append(company_input(), Path(d) / "db", clock=lambda: NOW)
            before = Path(ref["snapshot_path"]).read_bytes()
            self.assertEqual(read(ref)["facts"][0]["normalized_value"], "1234000")
            exported = export(ref, Path(d) / "portable/company-database")
            reference = Path(d) / "portable/company-reference.json"
            reference.write_text(json.dumps(exported), encoding="utf8")
            self.assertEqual(read(reference)["stock_id"], "3017")
            second = append(company_input(), Path(d) / "db", clock=lambda: NOW)
            self.assertEqual(ref["snapshot_sha256"], second["snapshot_sha256"])
            self.assertEqual(before, Path(ref["snapshot_path"]).read_bytes())
            Path(ref["snapshot_path"]).write_text("{}")
            with self.assertRaises(ValueError):
                read(ref)

    def test_missing_history_cannot_adopt_ai_technical_prose(self):
        s = market_snapshot(99)
        p, company, notes, state = report_inputs(
            s, {}, {"technical": {"paragraphs": ["unbound invented RSI"]}}
        )
        self.assertNotIn("unbound invented RSI", notes["technical"]["paragraphs"])
        self.assertEqual(p["status"], "blocked_history")
        self.assertIsNone(company)
        self.assertFalse(state["technical_prose_bound"])


if __name__ == "__main__":
    unittest.main()


class FixedEntrypointTests(unittest.TestCase):
    def test_company_cli_roundtrip_and_no_overwrite(self):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        from tradingagents.research.__main__ import main
        from tradingagents.research.company_database import latest

        with (
            tempfile.TemporaryDirectory() as d,
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()),
        ):
            root = Path(d)
            source = root / "input.json"
            source.write_text(json.dumps(company_input(), ensure_ascii=False), encoding="utf8")
            ref = root / "new/ref.json"
            args = [
                "company-import",
                "--input",
                str(source),
                "--database-dir",
                str(root / "db"),
                "--output",
                str(ref),
            ]
            self.assertEqual(main(args), 0)
            self.assertEqual(main(args), 2)
            self.assertEqual(
                main(
                    ["company-audit", "--reference", str(ref), "--output", str(root / "audit.json")]
                ),
                0,
            )
            self.assertFalse(
                json.loads((root / "audit.json").read_text("utf8"))["strict_pit_ready"]
            )
            self.assertEqual(
                latest(root / "db", "3017", "2026-11-03")["snapshot_sha256"],
                json.loads(ref.read_text("utf8"))["snapshot_sha256"],
            )
            self.assertEqual(latest(root / "db", "2330", "2026-11-03")["status"], "not_found")

    def test_cli_short_history_fails_without_faking_indicators(self):
        from contextlib import redirect_stdout
        from io import StringIO

        from tradingagents.research.__main__ import main

        with tempfile.TemporaryDirectory() as d, redirect_stdout(StringIO()):
            root = Path(d)
            src = root / "snapshot.json"
            src.write_text(json.dumps(market_snapshot(999)), encoding="utf8")
            self.assertEqual(
                main(["fixed-inputs", "--snapshot", str(src), "--output-dir", str(root / "fixed")]),
                1,
            )
            package = json.loads((root / "fixed/technical-package.json").read_text("utf8"))
            self.assertEqual(package["price_series"], {})
            self.assertFalse((root / "fixed/candles.svg").exists())

    def test_report_requires_selected_company_and_exports_portable_sources(self):
        from tests.taiwan.test_report_reading import sample
        from tradingagents.research.reporting import package

        snapshot = sample()
        snapshot["as_of"] = "2026-11-03"
        extension = {
            "stock_id": "3017",
            "as_of": "2026-11-03",
            "one_shot": True,
            "fixed_pipeline": True,
        }
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaisesRegex(ValueError, "selected company snapshot"):
                package(snapshot, root / "bad", extension_input=extension)
            ref = append(company_input(), root / "db", clock=lambda: NOW)
            extension["company_snapshot"] = ref
            result = package(snapshot, root / "report", extension_input=extension)
            self.assertEqual(result["fixed_technical"]["status"], "blocked_history")
            self.assertIn("停止", (root / "report/report.html").read_text("utf8"))
            saved = read(root / "report/company-reference.json")
            self.assertEqual(saved["facts"][0]["normalized_value"], "1234000")
            self.assertTrue((root / "report/technical-package.json").exists())

    @unittest.skipUnless(HAS_TALIB, "Install fixed-research dependencies for numerical integration")
    def test_bound_technical_notes_are_preserved(self):
        from tradingagents.research.bundle import fingerprint

        snapshot = market_snapshot()
        identity = fingerprint(build(snapshot))
        prose = {
            "technical": {
                "technical_package_sha256": identity,
                "position": "Bound calculation explanation",
            }
        }
        _, _, effective, state = report_inputs(snapshot, {}, prose)
        self.assertTrue(state["technical_prose_bound"])
        self.assertEqual(effective["technical"]["position"], "Bound calculation explanation")


class CompletedWeeklyTests(unittest.TestCase):
    def test_friday_is_a_completed_week_on_saturday(self):
        from tradingagents.research.technical_engine import _weekly

        snapshot = market_snapshot()
        rows = snapshot["datasets"]["TaiwanStockPrice"]["records"]
        calendar = snapshot["market_calendar"]["sessions"]
        complete, incomplete = _weekly(rows, calendar, "2026-10-31")
        self.assertEqual(complete[-1]["data_date"], "2026-10-30")
        complete, incomplete = _weekly(rows, calendar, "2026-10-30")
        self.assertLess(complete[-1]["data_date"], "2026-10-30")
