"""Research protocol and billing guards, with synthetic data and mocked Codex processes."""

import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests.taiwan.test_provider import price_rows, provider
from tradingagents.dataflows.taiwan.provider import FINANCIAL, PRICE
from tradingagents.research.__main__ import main
from tradingagents.research.bundle import build_bundle, fingerprint
from tradingagents.research.debate import (
    STAGES,
    load_run,
    register_agent,
    start_run,
    stop,
    submit,
)
from tradingagents.research.runner import launch


def snapshot(code="2330"):
    p, _ = provider({PRICE: price_rows()})
    return {**p.snapshot(code, "2024-01-01", "2024-02-10"), "data_mode": "synthetic"}


def result(stage, bundle, opening_count=1):
    role = stage.split("_")[0] if "_" in stage else stage
    agent = "coordinator" if stage == "decision" else role + "-test"
    evidence = next(key for key, item in bundle["evidence"].items() if item["availability"] == "available")
    targets = []
    if stage in {"risk", "decision"}:
        targets = [f"{name}_c{i}" for name in ("bull", "bear") for i in range(1, opening_count + 1)]
    elif stage.endswith("reply"):
        targets = [f"{'bear' if role == 'bull' else 'bull'}_c{i}" for i in range(1, opening_count + 1)]
    return {
        "stage": stage, "bundle_sha256": fingerprint(bundle), "agent_id": agent,
        "summary": "資料支援有限的價格觀察，仍須區分事實與研究推論。",
        "claims": [{"id": f"{role}_c{i}", "text": "價格可供研究，但不能證明獲利成長。",
                    "evidence_ids": [evidence], "invalidated_by": "若資料有修訂，須重新檢查論點。"}
                   for i in range(1, opening_count + 1)] if stage.endswith("opening") else [],
        "responses": [{"claim_id": target, "assessment": "uncertain",
                       "reason": "缺乏完整基本面資料，價格證據不能替代獲利證明。",
                       "evidence_ids": [evidence]} for target in targets],
        "verdict": "insufficient_data" if stage in {"risk", "decision"} else "not_applicable",
        "limitations": ["未核實公布日的基本面資料不可引用。"],
    }


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.directory = Path(self.folder.name) / "run"
        self.bundle = build_bundle(snapshot())
        start_run(self.bundle, self.directory)
        for role in ("bull", "bear", "risk"):
            register_agent(self.directory, role, role + "-test")
        guard = patch("socket.socket.connect", side_effect=AssertionError("Network forbidden"))
        guard.start()
        self.addCleanup(guard.stop)

    def finish(self, count=1):
        for stage in STAGES:
            submit(self.directory, stage, result(stage, self.bundle, count))

    def test_three_target_codes_and_critical_missing(self):
        for code in ("2330", "3017", "3231"):
            with self.subTest(code=code):
                bundle = build_bundle(snapshot(code))
                self.assertEqual(bundle["stock_id"], code)
                self.assertTrue(bundle["requires_insufficient_data"])
                self.assertFalse(any(item["dataset"] == FINANCIAL for item in bundle["evidence"].values()))

    def test_future_raw_rows_not_exposed_and_metrics_recomputed(self):
        source = snapshot()
        source["technical"]["rsi_14"] = 99999
        source["datasets"][PRICE]["raw_rows"].append({"date": "2099-01-01", "close": "SECRET_FUTURE"})
        bundle = build_bundle(source)
        self.assertNotIn("SECRET_FUTURE", json.dumps(bundle))
        self.assertEqual(next(v["value"] for v in bundle["evidence"].values()
                              if v["path"] == "technical.rsi_14"), 100)

    def test_future_eligible_and_unknown_publication_rejected(self):
        source = snapshot()
        source["datasets"][PRICE]["records"][0]["data_date"] = "2099-01-01"
        with self.assertRaises(ValueError):
            build_bundle(source)
        source = snapshot()
        record = copy.deepcopy(source["datasets"][PRICE]["records"][0])
        record["dataset"] = FINANCIAL
        source["datasets"][FINANCIAL].update(status="ok", records=[record])
        with self.assertRaises(ValueError):
            build_bundle(source)

    def test_stale_price_coverage_and_html_escape(self):
        source = snapshot()
        source["as_of"] = "2024-03-01"
        bundle = build_bundle(source)
        self.assertEqual(bundle["coverage"][PRICE]["status"], "partial")
        self.assertTrue(bundle["requires_insufficient_data"])
        value = result("bull_opening", self.bundle)
        value["summary"] = "<script>文字</script>"
        submit(self.directory, "bull_opening", value)
        for stage in STAGES[1:]:
            submit(self.directory, stage, result(stage, self.bundle))
        report = (self.directory / "report.html").read_text(encoding="utf-8")
        self.assertIn("&lt;script&gt;", report)
        self.assertNotIn("<script>", report)

    def test_complete_protocol_and_reports(self):
        self.finish()
        state, _ = load_run(self.directory)
        self.assertEqual(state["status"], "complete")
        markdown = (self.directory / "report.md").read_text(encoding="utf-8")
        self.assertIn("資料覆蓋與限制", markdown)
        self.assertIn("資料不足", markdown)
        self.assertIn("130.0", markdown)
        self.assertTrue((self.directory / "report.html").exists())

    def test_stage_order_and_independent_agents(self):
        with self.assertRaises(ValueError):
            submit(self.directory, "risk", result("risk", self.bundle))
        with self.assertRaises(ValueError):
            register_agent(self.directory, "bear", "bull-test")
        with self.assertRaises(ValueError):
            submit(self.directory, "bull_opening", {**result("bull_opening", self.bundle), "agent_id": "fake"})

    def test_missing_cross_reply_duplicate_and_unknown_evidence(self):
        for stage in ("bull_opening", "bear_opening"):
            submit(self.directory, stage, result(stage, self.bundle, 2))
        reply = result("bull_reply", self.bundle, 2)
        for modified in ({**reply, "responses": reply["responses"][:1]},
                         {**reply, "responses": [reply["responses"][0]] * 2}):
            with self.assertRaises(ValueError):
                submit(self.directory, "bull_reply", modified)
        reply["responses"][0]["evidence_ids"] = ["E9999"]
        with self.assertRaises(ValueError):
            submit(self.directory, "bull_reply", reply)

    def test_missing_evidence_and_numeric_hallucination_rejected(self):
        value = result("bull_opening", self.bundle)
        for text in ("營收成長 25%", "股價為１２３元", ""):
            with self.subTest(text=text), self.assertRaises(ValueError):
                submit(self.directory, "bull_opening", {**value, "summary": text})
        value["claims"][0]["evidence_ids"] = []
        with self.assertRaises(ValueError):
            submit(self.directory, "bull_opening", value)

    def test_missing_data_veto_cannot_be_overridden(self):
        for stage in STAGES[:-1]:
            submit(self.directory, stage, result(stage, self.bundle))
        with self.assertRaises(ValueError):
            submit(self.directory, "decision", {**result("decision", self.bundle), "verdict": "supports_bull"})

    def test_idempotent_import_and_hash_tampering(self):
        self.finish()
        self.assertEqual(submit(self.directory, "decision", result("decision", self.bundle))["status"], "complete")
        path = self.directory / "bull_opening.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["summary"] = "修改已接受的論點。"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_run(self.directory)

    def test_input_change_resume_refused(self):
        bundle = copy.deepcopy(self.bundle)
        bundle["as_of"] = "2024-02-11"
        with self.assertRaises(ValueError):
            start_run(bundle, self.directory)

    def test_stop_and_continue_pending_stage(self):
        submit(self.directory, "bull_opening", result("bull_opening", self.bundle))
        stop(self.directory, "quota")
        state, _ = load_run(self.directory)
        self.assertEqual(state["stop_reason"], "quota")
        submit(self.directory, "bear_opening", result("bear_opening", self.bundle))
        state, _ = load_run(self.directory)
        self.assertEqual(state["status"], "running")
        self.assertEqual(len(state["completed"]), 2)

    def test_prepare_requires_secret_and_no_secret_export(self):
        p, _ = provider({PRICE: price_rows()})
        args = ["prepare", "--symbols", "2330,3017,3231", "--start", "2024-01-01",
                "--as-of", "2024-02-10", "--output-dir", str(Path(self.folder.name) / "prepared"),
                "--require-token"]
        with patch.dict(os.environ, {"FINMIND_TOKEN": ""}), redirect_stdout(io.StringIO()):
            self.assertEqual(main(args, provider=p), 2)
        with patch.dict(os.environ, {"FINMIND_TOKEN": "test-sponsor-secret"}), redirect_stdout(io.StringIO()):
            self.assertEqual(main(args, provider=p), 0)
        exported = Path(self.folder.name) / "prepared" / "2330-2024-02-10" / "bundle.json"
        self.assertNotIn("test-sponsor-secret", exported.read_text(encoding="utf-8"))
        p.snapshot = lambda *args: {**snapshot(), "secret": "test-sponsor-secret"}
        with patch.dict(os.environ, {"FINMIND_TOKEN": "test-sponsor-secret"}), redirect_stdout(io.StringIO()):
            self.assertEqual(main(args, provider=p), 1)

    def test_cli_billing_guard_and_env_scrubbing(self):
        calls = []

        def execute(command, **kwargs):
            calls.append((command, kwargs))
            if "login" in command:
                return subprocess.CompletedProcess(command, 0, "", "Logged in using ChatGPT")
            self.finish()
            return subprocess.CompletedProcess(command, 0, "Done", "")

        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret-api", "CODEX_API_KEY": "secret-codex",
                                     "FINMIND_TOKEN": "secret-finmind", "GITHUB_TOKEN": "secret-gh"}):
            self.assertEqual(launch(self.directory, execute=execute), 0)
        command, kwargs = calls[-1]
        self.assertIn('forced_login_method="chatgpt"', command)
        self.assertIn('model_provider="openai"', command)
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "FINMIND_TOKEN", "GITHUB_TOKEN"):
            self.assertNotIn(key, kwargs["env"])
        self.assertEqual(command[-1], "-")
        self.assertNotIn("secret", kwargs["input"])

    def test_api_auth_refused_and_quota_stop_no_fallback(self):
        calls = []

        def api(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "", "Logged in using an API key")

        self.assertEqual(launch(self.directory, execute=api), 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(load_run(self.directory)[0]["stop_reason"], "auth")

        def quota(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0 if "login" in command else 1,
                                               "", "ChatGPT" if "login" in command else "usage limit reached")

        self.assertEqual(launch(self.directory, execute=quota), 2)
        self.assertEqual(len(calls), 3)
        self.assertEqual(load_run(self.directory)[0]["stop_reason"], "quota")

    def test_cli_timeout_saved_and_never_retried(self):
        def execute(command, **kwargs):
            if "login" in command:
                return subprocess.CompletedProcess(command, 0, "", "ChatGPT")
            raise subprocess.TimeoutExpired(command, 900)

        self.assertEqual(launch(self.directory, execute=execute), 2)
        self.assertEqual(load_run(self.directory)[0]["stop_reason"], "timeout")


if __name__ == "__main__":
    unittest.main()
