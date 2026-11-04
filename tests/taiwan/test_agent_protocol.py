"""Synthetic mock-only agent protocol tests; never dispatch or call external APIs."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tests.taiwan.test_fixed_pipeline import NOW, company_input
from tests.taiwan.test_public_eps import source
from tests.taiwan.test_report_reading import sample
from tradingagents.research.agent_protocol import (
    PROTOCOL,
    STEPS,
    assemble,
    bind,
    load,
    prepare_prompt,
    report_inputs,
    start,
    submit,
)
from tradingagents.research.company_database import append


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")


def fixture(directory, mode="mock", bound=True):
    root = Path(directory)
    snapshot = sample()
    snapshot["as_of"] = "2026-11-03"
    run = root / "agents"
    start(snapshot, run, mode=mode)
    if not bound:
        return run, snapshot, None, None
    result, execution = result_for(run, "planner")
    submit(run, "planner", result, execution)
    ref = append(company_input(), root / "db", clock=lambda: NOW)
    ref_path = root / "company-reference.json"
    dump(ref_path, ref)
    eps = {"stock_id": "3017", "as_of": snapshot["as_of"], "sources": {"article": source()}}
    bind(run, ref_path, eps)
    return run, snapshot, ref, eps


def result_for(run, stage):
    prepare_prompt(run, stage)
    result = json.loads((run / "stages" / stage / "result-template.json").read_text("utf8"))
    root, state = load(run)
    agent = {
        "bull_opening": "mock-bull",
        "bull_reply": "mock-bull",
        "bear_opening": "mock-bear",
        "bear_reply": "mock-bear",
        "risk": "mock-risk",
    }.get(stage, "mock-coordinator")
    result.update(
        agent_id=agent,
        status="complete",
        position="合成測試立場 " + stage,
        paragraphs=["合成離線驗證，沒有真實Agent或研究績效。"],
        conditions=["新公告出現且期間/單位可核對後再研究。"],
    )
    ref = (
        {"input_id": "metadata", "pointer": "/instrument/stock_id", "quote": "3017"}
        if stage == "planner"
        else {"input_id": "company", "pointer": "/facts/0/normalized_value", "quote": "1234000"}
    )
    for step in result["steps"]:
        step.update(status="completed", explanation="合成契約驗證，不是公司判斷。", evidence=[ref])
    result["findings"] = [
        {
            "id": stage + ".claim",
            "claim": "合成來源欄位存在。",
            "evidence": [ref],
            "strongest_counterevidence": "此合成數值不代表真實公司。",
            "confidence": "uncalibrated_low",
            "revision_condition": "來源欄位或版本改變就重新研究。",
        }
    ]
    if stage == "planner":
        result["plan"] = {
            "business_hypotheses": ["先查業務組合，尚未證實。"],
            "entities": [],
            "peers": [],
            "questions": [
                {
                    "id": "q1",
                    "question": "營收如何轉成歸母獲利？",
                    "financial_transmission": "產品組合/成本到利潤及股數。",
                    "required_sources": ["financial_statement", "eps_note"],
                }
            ],
        }
    if stage == "fundamental":
        result["question_answers"] = [
            {
                "question_id": "q1",
                "status": "unavailable",
                "answer": "合成流程沒有獲利傳導證據，保留缺口。",
                "evidence": [],
            }
        ]
    if stage == "technical":
        result.update(status="unavailable", findings=[], gaps=["合成行情不足1000個官方交易日。"])
        for step in result["steps"]:
            step.update(status="unavailable", evidence=[])
    if stage.endswith("reply"):
        own = stage.split("_")[0] + "_opening"
        opponent = "bear_opening" if stage.startswith("bull") else "bull_opening"
        before = json.loads((run / "stages" / own / "accepted.json").read_text("utf8"))
        opposing = json.loads((run / "stages" / opponent / "accepted.json").read_text("utf8"))
        result["position_before"] = before["position"]
        result["responses"] = [
            {
                "claim_id": c["id"],
                "assessment": "uncertain",
                "reason": "只驗證合成契約，不能支持真實判斷。",
                "evidence": [ref],
                "changed_my_position": False,
            }
            for c in opposing["findings"]
        ]
    if stage in ("risk", "decision"):
        for ruling in result["rulings"]:
            ruling.update(verdict="uncertain", reason="合成測試不代表真實裁決。", evidence=[ref])
    if stage == "decision":
        result.update(
            position_before="待裁決",
            stronger_case="合成內容不足以支持多空。",
            key_disagreement="缺真正證據。",
            evidence_change="沒有真實新證據，沒有真實改判。",
            changed_claim_ids=[],
        )
    kind = (
        "spawn_agent"
        if stage in ("bull_opening", "bear_opening", "risk")
        else "followup_task"
        if stage.endswith("reply")
        else "coordinator"
    )
    execution = {
        "agent_id": agent,
        "prompt_sha256": result["prompt_sha256"],
        "kind": kind,
        "context_mode": "fresh",
        "tool_call_id": "mock-only-" + stage,
        "evidence_kind": "mock" if state["mode"] == "mock" else "observed_tool_receipt",
    }
    return result, execution


def finish(run):
    for stage in STEPS:
        _, state = load(run)
        if stage not in state["completed"]:
            result, execution = result_for(run, stage)
            submit(run, stage, result, execution)
    return assemble(run)


class AgentProtocolTests(unittest.TestCase):
    def test_fixed_prompts_and_blind_openings(self):
        with tempfile.TemporaryDirectory() as d:
            run, *_ = fixture(d)
            for stage in (
                "fundamental",
                "technical",
                "institutional",
                "bull_opening",
                "bear_opening",
            ):
                first = prepare_prompt(run, stage)
                second = prepare_prompt(run, stage)
                self.assertFalse(first["reused"])
                self.assertTrue(second["reused"])
                request = json.loads((run / "stages" / stage / "request.json").read_text("utf8"))
                if stage.endswith("opening"):
                    self.assertEqual(set(request["dependencies"]), {"planner"})
                    self.assertNotIn("source_snapshot", request["inputs"])
                    self.assertNotIn("review", request["inputs"])
                    self.assertNotIn("public_eps_input", request["inputs"])
                    self.assertTrue(request["fresh_context"])

    def test_dependency_and_source_seal_required(self):
        with tempfile.TemporaryDirectory() as d:
            run, *_ = fixture(d, bound=False)
            with self.assertRaises(ValueError):
                prepare_prompt(run, "bull_reply")
            with self.assertRaises(ValueError):
                prepare_prompt(run, "fundamental")
            with self.assertRaises(ValueError):
                bind(run, Path(d) / "absent.json")

    def test_wrong_prompt_company_and_missing_step_rejected(self):
        for mutation in ("prompt", "company", "step"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as d:
                run, *_ = fixture(d)
                r, e = result_for(run, "fundamental")
                if mutation == "prompt":
                    r["prompt_sha256"] = "0" * 64
                if mutation == "company":
                    r["stock_id"] = "2330"
                if mutation == "step":
                    r["steps"].pop()
                with self.assertRaises(ValueError):
                    submit(run, "fundamental", r, e)

    def test_source_quote_null_and_unselected_fact_rejected(self):
        for bad in [
            {"input_id": "company", "pointer": "/facts/0/normalized_value", "quote": "999"},
            {
                "input_id": "company",
                "pointer": "/fact_vintages/0/normalized_value",
                "quote": "1234000",
            },
            {"input_id": "source_snapshot", "pointer": "/as_of", "quote": "2026-11-03"},
            {"input_id": "public_eps", "pointer": "/estimates/0/currency", "quote": "None"},
            {"input_id": "company", "pointer": "/facts/-1/normalized_value", "quote": "1234000"},
        ]:
            with self.subTest(ref=bad), tempfile.TemporaryDirectory() as d:
                run, *_ = fixture(d)
                r, e = result_for(run, "fundamental")
                r["findings"][0]["evidence"] = [bad]
                with self.assertRaises(ValueError):
                    submit(run, "fundamental", r, e)

    def test_input_prompt_output_and_receipt_tampering(self):
        for target in ("input", "prompt", "output", "receipt", "contract"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as d:
                run, *_ = fixture(d)
                p = {
                    "input": run / "inputs/company.json",
                    "prompt": run / "stages/planner/prompt.md",
                    "output": run / "stages/planner/accepted.json",
                    "receipt": run / "stages/planner/execution.json",
                    "contract": run / "stages/planner/result-template.json",
                }[target]
                p.write_bytes(p.read_bytes() + b" ")
                with self.assertRaises(ValueError):
                    load(run)

    def test_no_overwrite_and_incomplete_export(self):
        with tempfile.TemporaryDirectory() as d:
            run, *_ = fixture(d)
            r = json.loads((run / "stages/planner/accepted.json").read_text("utf8"))
            e = json.loads((run / "stages/planner/execution.json").read_text("utf8"))
            with self.assertRaises(ValueError):
                submit(run, "planner", r, e)
            with self.assertRaises(ValueError):
                assemble(run)

    def test_separate_bull_bear_and_original_reply_agent(self):
        with tempfile.TemporaryDirectory() as d:
            run, *_ = fixture(d)
            r, e = result_for(run, "bull_opening")
            submit(run, "bull_opening", r, e)
            r, e = result_for(run, "bear_opening")
            r["agent_id"] = e["agent_id"] = "mock-bull"
            with self.assertRaises(ValueError):
                submit(run, "bear_opening", r, e)
            r, e = result_for(run, "bear_opening")
            submit(run, "bear_opening", r, e)
            r, e = result_for(run, "bull_reply")
            r["agent_id"] = e["agent_id"] = "new-agent"
            with self.assertRaises(ValueError):
                submit(run, "bull_reply", r, e)

    def test_rebut_every_claim_exactly_once(self):
        with tempfile.TemporaryDirectory() as d:
            run, *_ = fixture(d)
            for stage in ("bull_opening", "bear_opening"):
                r, e = result_for(run, stage)
                submit(run, stage, r, e)
            r, e = result_for(run, "bull_reply")
            r["responses"] = []
            with self.assertRaises(ValueError):
                submit(run, "bull_reply", r, e)

    def test_missing_four_axes_and_fake_probability_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            run, *_ = fixture(d)
            for stage in ("bull_opening", "bear_opening", "bull_reply", "bear_reply"):
                r, e = result_for(run, stage)
                submit(run, stage, r, e)
            r, e = result_for(run, "risk")
            r["rulings"].pop()
            with self.assertRaises(ValueError):
                submit(run, "risk", r, e)
            r, e = result_for(run, "risk")
            r["findings"][0]["confidence"] = "95%"
            with self.assertRaises(ValueError):
                submit(run, "risk", r, e)

    def test_mock_receipt_cannot_enter_real_run(self):
        with tempfile.TemporaryDirectory() as d:
            run, *_ = fixture(d, mode="research", bound=False)
            r, e = result_for(run, "planner")
            e["evidence_kind"] = "mock"
            with self.assertRaises(ValueError):
                submit(run, "planner", r, e)

    def test_full_mock_does_not_claim_independent_execution(self):
        with tempfile.TemporaryDirectory() as d:
            run, *_ = fixture(d)
            result = finish(run)
            self.assertEqual(result["mode"], "mock")
            self.assertFalse(result["debate"]["independent_agents_executed"])
            self.assertFalse(result["semantic_truth_verified"])
            self.assertIn("technical_package_sha256", result["analysts"]["technical"])

    def test_report_binding_and_mock_only_gate(self):
        with tempfile.TemporaryDirectory() as d:
            run, snap, ref, eps = fixture(d)
            finish(run)
            ext = {"company_snapshot": ref, "public_eps": eps}
            with self.assertRaises(ValueError):
                report_inputs(run, snap, ext, None)
            ext["agent_report_mode"] = "mock"
            self.assertEqual(report_inputs(run, snap, ext, None)["protocol"], PROTOCOL)
            changed = copy.deepcopy(snap)
            changed["as_of"] = "2026-11-04"
            with self.assertRaises(ValueError):
                report_inputs(run, changed, ext, None)
            ext["public_eps"] = None
            with self.assertRaises(ValueError):
                report_inputs(run, snap, ext, None)

    def test_report_archives_prompts_and_uses_accepted_outputs(self):
        from tradingagents.research.reporting import package

        with tempfile.TemporaryDirectory() as d:
            run, snap, ref, eps = fixture(d)
            finish(run)
            ext = {
                "stock_id": "3017",
                "as_of": snap["as_of"],
                "one_shot": True,
                "fixed_pipeline": True,
                "agent_protocol": PROTOCOL,
                "agent_run": str(run),
                "agent_report_mode": "mock",
                "company_snapshot": ref,
                "public_eps": eps,
            }
            root = Path(d) / "report"
            result = package(snap, root, extension_input=ext)
            self.assertIn("Mock流程驗收", (root / "report.html").read_text("utf8"))
            self.assertTrue((root / "agent-run/stages/bull_reply/prompt.md").exists())
            self.assertTrue((root / "agent-protocol-review.json").exists())
            self.assertFalse(result["independent_agents_executed"])
            with self.assertRaises(ValueError):
                package(
                    snap, Path(d) / "bad", extension_input=ext, analyst_prose={"fundamental": []}
                )


class AdditionalContractTests(unittest.TestCase):
    def test_planning_questions_cannot_be_silently_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            run, *_ = fixture(d)
            r, e = result_for(run, "fundamental")
            r["question_answers"] = []
            with self.assertRaises(ValueError):
                submit(run, "fundamental", r, e)

    def test_blocked_technical_data_cannot_be_completed_with_company_facts(self):
        with tempfile.TemporaryDirectory() as d:
            run, *_ = fixture(d)
            r, e = result_for(run, "technical")
            r["steps"][0].update(
                status="completed",
                evidence=[
                    {
                        "input_id": "company",
                        "pointer": "/facts/0/normalized_value",
                        "quote": "1234000",
                    }
                ],
            )
            with self.assertRaises(ValueError):
                submit(run, "technical", r, e)

    def test_unexecuted_rebuttal_does_not_fake_a_position_change(self):
        with tempfile.TemporaryDirectory() as d:
            run, *_ = fixture(d)
            for stage in ("bull_opening", "bear_opening"):
                r, e = result_for(run, stage)
                submit(run, stage, r, e)
            r, e = result_for(run, "bull_reply")
            r.update(status="unavailable", findings=[], gaps=["合成能力不足。"])
            for step in r["steps"]:
                step.update(status="unavailable", evidence=[])
            for response in r["responses"]:
                response.update(evidence=[], assessment="uncertain", changed_my_position=False)
            e["kind"] = "unavailable"
            bad = copy.deepcopy(r)
            bad["responses"][0]["changed_my_position"] = True
            with self.assertRaises(ValueError):
                submit(run, "bull_reply", bad, e)
            self.assertEqual(submit(run, "bull_reply", r, e)["research_status"], "unavailable")

    def test_fixed_protocol_cli(self):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        from tradingagents.research.__main__ import main

        with (
            tempfile.TemporaryDirectory() as d,
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()),
        ):
            run, *_ = fixture(d)
            self.assertEqual(
                main(["agent-prompt", "--run", str(run), "--stage", "bull_opening"]), 0
            )
            self.assertEqual(main(["agent-prompt", "--run", str(run), "--stage", "unknown"]), 2)
            self.assertEqual(main(["agent-audit", "--run", str(run)]), 2)

    def test_templates_cover_three_companies_and_generic_stock(self):
        from tests.taiwan.test_research import snapshot

        hashes = []
        with tempfile.TemporaryDirectory() as d:
            for stock, profile in [
                ("2330", "foundry"),
                ("3017", "thermal"),
                ("3231", "odm"),
                ("1101", "generic"),
            ]:
                data = (
                    snapshot(stock)
                    if stock != "1101"
                    else {
                        "instrument": {
                            "stock_id": stock,
                            "exchange": "twse",
                            "name": "Synthetic generic issuer",
                        },
                        "datasets": {},
                    }
                )
                data["as_of"] = "2026-11-03"
                run = Path(d) / stock
                start(data, run, mode="mock")
                request = json.loads((run / "stages/planner/request.json").read_text("utf8"))
                company_plan = json.loads((run / "inputs/company_plan.json").read_text("utf8"))
                self.assertEqual(company_plan["profile"], profile)
                self.assertEqual(request["stock_id"], stock)
                hashes.append(request["template_sha256"])
            self.assertEqual(len(set(hashes)), 1)
