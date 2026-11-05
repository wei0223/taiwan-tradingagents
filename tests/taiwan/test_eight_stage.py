"""Mock-only eight-stage gate tests. No real Agent, network or forecast performance."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.taiwan.test_fixed_pipeline import NOW, company_input
from tests.taiwan.test_public_eps import source
from tests.taiwan.test_report_reading import sample
from tradingagents.research import eight_stage as w
from tradingagents.research.company_database import append


def fixture(directory, mode="mock", bound=True):
    root = Path(directory)
    snapshot = sample()
    snapshot["as_of"] = "2026-11-03"
    run = root / "agents"
    w.start(snapshot, run, mode=mode)
    if not bound:
        return run, snapshot, None, None
    accept(run, "planner")
    ref = append(company_input(), root / "db", clock=lambda: NOW)
    path = root / "company-reference.json"
    path.write_text(json.dumps(ref), encoding="utf8")
    eps = {"stock_id": "3017", "as_of": snapshot["as_of"], "sources": {"article": source()}}
    w.bind(run, path, eps)
    return run, snapshot, ref, eps


def result_for(run, job, queries=None):
    w.prepare_prompt(run, job)
    value = json.loads((run / "jobs" / job / "contract.json").read_text("utf8"))
    root, state = w.load(run)
    d = w.dimension(job)
    kind = w.role(job)
    agent = "mock-" + (w.pair(job)[0] if kind == "reply" else job)
    value.update(agent_id=agent, status="limited", stance="uncertain", summary="合成契約驗證，不是真實研究。",
                 gaps=["合成資料不完整"], conditions=["取得合格原件再研究"])
    ref = {"input_id": "metadata", "pointer": "/instrument/stock_id", "quote": "3017"} if job == "planner" else {"input_id": "company", "pointer": "/facts/0/normalized_value", "quote": str(w.load_json(w.artifact(root, state["revisions"][state["active_revision"]]["inputs"]["company"]))["facts"][0]["normalized_value"])}
    blocked = d in {"technical", "institutional"}
    value["findings"] = [] if blocked or kind in {"planner", "core", "supplement", "reply"} else [{
        "id": job + ".claim", "claim": "合成原件欄位存在", "horizon": "long_term", "evidence": [ref],
        "counterargument": "合成數字不能支持公司結論", "revision_condition": "版本變更需重看", "confidence": "uncalibrated_low"}]
    if blocked:
        value.update(status="unavailable", stance="unavailable")
    if job == "planner":
        value["payload"] = {"questions": [{"id": "q1", "question": "利潤如何傳導", "financial_transmission": "營收到現金", "required_sources": ["financial_statement"]}],
                            "business_hypotheses": ["待查假說"], "entities": [], "peers": []}
    if kind == "analyst":
        for check in value["payload"]["checks"]:
            check.update(status="unavailable", explanation="合成缺證", evidence=[])
        if d == "fundamental":
            value["payload"]["answers"] = [{"question_id": "q1", "status": "unavailable", "answer": "缺合格利潤橋接", "evidence": []}]
    if kind == "reply":
        own, other = (w.result(root, state, j) for j in w.pair(job))
        value["payload"] = {"position_before": own["stance"], "responses": [{"claim_id": f["id"], "assessment": "uncertain", "reason": "合成無法證實", "evidence": [] if blocked else [ref], "changed_my_position": False} for f in other["findings"]]}
    if kind == "judge":
        value["payload"] = {"primary_claim_id": None if blocked else d + ".claim", "reason": "合成無法作真實裁決", "key_disagreement": "缺外部實證"}
    if kind == "core":
        value["payload"] = {"faces": [{"dimension": dim, "claim_id": w.current(state, dim) + ".claim" if dim == "fundamental" else None,
            "reason": "合成核心選擇", "items": [{"role": "primary", "claim_id": w.current(state, dim) + ".claim", "reference": ref, "correlation_group": "同一合成財報"}] if dim == "fundamental" else [],
            "unavailable_reason": "缺資料" if dim != "fundamental" else ""} for dim in w.DIMENSIONS]}
    if kind == "cross":
        value["payload"] = {"relations": [{"id": "pair" + str(i), "dimensions": list(pair), "claim_ids": [], "relationship": "insufficient", "materiality": "unknown",
            "time_alignment": "長短期不可直接比較", "explanation": "另一面向缺資料", "missing_data": ["核對下一份官方原件"], "impact": "暫不升格結論"}
            for i, pair in enumerate((("fundamental", "technical"), ("fundamental", "institutional"), ("technical", "institutional")))]}
    if job == "supplement":
        value["payload"] = {"queries": queries or [], "skip_reason": "合成不需要真實補查"}
    if job in {"risk", "decision"}:
        for ruling in value["payload"]["rulings"]:
            ruling.update(verdict="uncertain", reason="合成不足作真實裁決", evidence=[ref])
    if job == "decision":
        value["payload"].update(position_before="uncertain", stronger_case="缺實證無法選邊", key_disagreement="缺資料", evidence_change="沒有真實新證據", changed_claim_ids=[])
    execution = {"agent_id": agent, "prompt_sha256": value["prompt_sha256"], "kind": "followup_task" if kind == "reply" else "spawn_agent" if w.fresh(job) else "coordinator",
                 "context_mode": "fresh", "tool_call_id": "mock-only-" + job, "evidence_kind": "mock" if state["mode"] == "mock" else "observed_tool_receipt"}
    return value, execution


def accept(run, job, **kwargs):
    value, execution = result_for(run, job, **kwargs)
    return w.submit(run, job, value, execution)


def through_cross(run):
    for job in (*w.DIMENSIONS, *w.LOCAL, "core", "cross"):
        accept(run, job)


def finish(run):
    through_cross(run)
    accept(run, "supplement")
    w.fetch(run, run.parent / "ledger")
    w.resolve(run, {})
    for job in w.FINAL:
        accept(run, job)


class EightStageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_mock_runs_all_eight_phases_without_claiming_real_execution(self):
        run, *_ = fixture(self.root)
        finish(run)
        audit = w.assemble(run)
        self.assertEqual(len(audit["workflow"]["phases"]), 8)
        self.assertEqual(len(audit["results"]), 28)
        self.assertEqual(audit["status"], "limited")
        self.assertFalse(audit["debate"]["independent_agents_executed"])
        self.assertFalse(audit["semantic_truth_verified"])
        self.assertFalse(audit["workflow"]["phases"][-1]["report_file_verified"])
        w.export(run, self.root / "portable")
        self.assertEqual(w.assemble(self.root / "portable")["workflow"], audit["workflow"])

    def test_analysis_requires_accepted_plan_and_bound_sources(self):
        run, *_ = fixture(self.root, bound=False)
        with self.assertRaises(ValueError):
            w.prepare_prompt(run, "fundamental")

    def test_final_cannot_skip_core_cross_and_supplement(self):
        run, *_ = fixture(self.root)
        with self.assertRaises(ValueError):
            w.prepare_prompt(run, "bull_opening")
        with self.assertRaises(ValueError):
            w.prepare_prompt(run, "core")

    def test_three_analysts_require_fresh_dispatch(self):
        run, *_ = fixture(self.root)
        value, execution = result_for(run, "fundamental")
        execution["kind"] = "coordinator"
        with self.assertRaisesRegex(ValueError, "fresh dispatch"):
            w.submit(run, "fundamental", value, execution)

    def test_distinct_analysis_agents_are_enforced(self):
        run, *_ = fixture(self.root)
        accept(run, "fundamental")
        value, execution = result_for(run, "technical")
        execution["agent_id"] = value["agent_id"] = "mock-fundamental"
        with self.assertRaisesRegex(ValueError, "reuse"):
            w.submit(run, "technical", value, execution)

    def test_missing_mandatory_analysis_check_is_rejected(self):
        run, *_ = fixture(self.root)
        value, execution = result_for(run, "fundamental")
        value["payload"]["checks"].pop()
        with self.assertRaisesRegex(ValueError, "every fixed"):
            w.submit(run, "fundamental", value, execution)

    def test_dimension_cannot_reference_other_dimension(self):
        run, *_ = fixture(self.root)
        value, execution = result_for(run, "technical")
        value.update(status="limited", stance="uncertain", findings=[{"id": "technical.fake", "claim": "fake", "horizon": "short_term", "evidence": [{"input_id": "company", "pointer": "/facts/0/normalized_value", "quote": "1234000"}], "counterargument": "fake", "revision_condition": "fake", "confidence": "uncalibrated_low"}])
        with self.assertRaisesRegex(ValueError, "scope"):
            w.submit(run, "technical", value, execution)

    def test_rebuttal_must_address_each_opposing_claim(self):
        run, *_ = fixture(self.root)
        for job in ("fundamental", "fundamental_bull", "fundamental_bear"):
            accept(run, job)
        value, execution = result_for(run, "fundamental_bull_reply")
        value["payload"]["responses"] = []
        with self.assertRaisesRegex(ValueError, "every opposing"):
            w.submit(run, "fundamental_bull_reply", value, execution)

    def test_reply_cannot_switch_agent(self):
        run, *_ = fixture(self.root)
        for job in ("fundamental", "fundamental_bull", "fundamental_bear"):
            accept(run, job)
        value, execution = result_for(run, "fundamental_bull_reply")
        value["agent_id"] = execution["agent_id"] = "mock-other"
        with self.assertRaisesRegex(ValueError, "original agent"):
            w.submit(run, "fundamental_bull_reply", value, execution)

    def core_setup(self):
        run, *_ = fixture(self.root)
        for job in (*w.DIMENSIONS, *w.LOCAL):
            accept(run, job)
        return run

    def test_core_cannot_duplicate_evidence(self):
        run = self.core_setup()
        value, execution = result_for(run, "core")
        face = value["payload"]["faces"][0]
        second = copy.deepcopy(face["items"][0])
        second["role"] = "support"
        face["items"].append(second)
        with self.assertRaisesRegex(ValueError, "Repeated evidence"):
            w.submit(run, "core", value, execution)

    def test_core_cannot_invent_a_claim(self):
        run = self.core_setup()
        value, execution = result_for(run, "core")
        value["payload"]["faces"][0]["claim_id"] = "invented.claim"
        with self.assertRaises(ValueError):
            w.submit(run, "core", value, execution)

    def test_core_cannot_exceed_three_per_dimension(self):
        run = self.core_setup()
        value, execution = result_for(run, "core")
        value["payload"]["faces"][0]["items"] *= 4
        with self.assertRaises(ValueError):
            w.submit(run, "core", value, execution)

    def test_cross_must_compare_all_dimension_pairs(self):
        run = self.core_setup()
        accept(run, "core")
        value, execution = result_for(run, "cross")
        value["payload"]["relations"].pop()
        with self.assertRaisesRegex(ValueError, "every dimension pair"):
            w.submit(run, "cross", value, execution)

    def test_query_cannot_execute_arbitrary_url_or_command(self):
        run, *_ = fixture(self.root)
        through_cross(run)
        value, execution = result_for(run, "supplement", queries=[query(operation="shell")])
        with self.assertRaisesRegex(ValueError, "fixed official"):
            w.submit(run, "supplement", value, execution)

    def test_future_query_period_is_rejected(self):
        run, *_ = fixture(self.root)
        through_cross(run)
        value, execution = result_for(run, "supplement", queries=[query(period="2026-12")])
        with self.assertRaisesRegex(ValueError, "not completed"):
            w.submit(run, "supplement", value, execution)

    def test_mock_cannot_call_live_sources(self):
        run, *_ = fixture(self.root)
        through_cross(run)
        accept(run, "supplement", queries=[query()])
        with self.assertRaisesRegex(ValueError, "mocks cannot"):
            w.fetch(run, self.root / "ledger", live=True)

    def test_refusal_stops_host_and_query_cannot_be_retried(self):
        run, *_ = fixture(self.root)
        through_cross(run)
        accept(run, "supplement", queries=[query(), query(id="q2", period="2026-09")])
        transport = Transport(403)
        batch = w.fetch(run, self.root / "ledger", transport=transport, clock=lambda: NOW)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(batch["http_requests"], 1)
        self.assertTrue(batch["refused_hosts"])
        with self.assertRaises(ValueError):
            w.fetch(run, self.root / "ledger", transport=transport)
        with self.assertRaises(ValueError):
            w.resolve(run, {"q1": {"status": "reviewed", "reason": "checkbox only"}, "q2": {"status": "unavailable", "reason": "missing"}}, self.root / "company-reference.json")

    def test_missing_resolution_keeps_final_stage_blocked(self):
        run, *_ = fixture(self.root)
        through_cross(run)
        accept(run, "supplement")
        w.fetch(run, self.root / "ledger")
        with self.assertRaises(ValueError):
            w.prepare_prompt(run, "risk")

    def test_mock_receipt_is_rejected_in_research_mode(self):
        run, *_ = fixture(self.root, mode="research", bound=False)
        value, execution = result_for(run, "planner")
        execution["evidence_kind"] = "mock"
        with self.assertRaisesRegex(ValueError, "Mock/research"):
            w.submit(run, "planner", value, execution, raw_result=json.dumps(value).encode())

    def test_archive_detects_input_tampering(self):
        run, *_ = fixture(self.root)
        path = run / "revisions/0/company.json"
        path.write_text("{}", encoding="utf8")
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            w.load(run)

    def test_report_rejects_mock_without_explicit_label(self):
        run, snapshot, ref, eps = fixture(self.root)
        finish(run)
        with self.assertRaisesRegex(ValueError, "Mock workflow"):
            w.report_inputs(run, snapshot, {"company_snapshot": ref, "public_eps": eps}, {})

    def test_formal_template_delivers_workflow_and_all_agent_artifacts(self):
        from tradingagents.research.reporting import package
        run, snapshot, ref, eps = fixture(self.root)
        finish(run)
        output = self.root / "report"
        result = package(snapshot, output, extension_input={"stock_id": "3017", "as_of": snapshot["as_of"], "one_shot": True, "fixed_pipeline": True,
            "agent_protocol": w.PROTOCOL, "agent_run": str(run), "agent_report_mode": "mock", "company_snapshot": ref, "public_eps": eps})
        self.assertIn("workflow_review", result)
        self.assertIn("八階段研究推導", (output / "report.html").read_text("utf8"))
        self.assertEqual(json.loads((output / "workflow-delivery.json").read_text("utf8"))["status"], "passed")
        self.assertTrue((output / "agent-run/jobs/core/accepted.json").is_file())
        self.assertTrue((output / "evidence.zip").is_file())


    def test_status_exposes_next_job_and_keeps_data_gate_pending_until_bind(self):
        run, *_ = fixture(self.root, bound=False)
        self.assertEqual(w.status(run)["next_jobs"], ["planner"])
        accept(run, "planner")
        self.assertEqual(w.status(run)["phases"][0]["gate"], "pending")
        self.assertEqual(w.status(run)["next_jobs"], [])

    def test_query_budget_and_duplicate_operations_are_rejected(self):
        run, *_ = fixture(self.root)
        through_cross(run)
        for queries in ([query(id="q" + str(i)) for i in range(4)], [query(), query(id="q2")]):
            value, execution = result_for(run, "supplement", queries=queries)
            with self.assertRaises(ValueError):
                w.submit(run, "supplement", value, execution)

    def test_raw_capture_alone_is_not_a_review_or_reanalysis(self):
        run, *_ = fixture(self.root)
        through_cross(run)
        accept(run, "supplement", queries=[query()])
        batch = w.fetch(run, self.root / "ledger", transport=Transport(), clock=lambda: NOW)
        self.assertFalse(batch["numeric_review_completed"])
        resolution = w.resolve(run, {"q1": {"status": "captured_pending_review", "reason": "只取得原文，未複核"}})
        self.assertEqual(resolution["new_revision"], 0)
        self.assertEqual(resolution["reanalysis_required"], [])

    def test_review_checkbox_without_new_captured_original_is_rejected(self):
        run, *_ = fixture(self.root)
        through_cross(run)
        accept(run, "supplement", queries=[query()])
        w.fetch(run, self.root / "ledger", transport=Transport(), clock=lambda: NOW)
        with self.assertRaisesRegex(ValueError, "newly captured"):
            w.resolve(run, {"q1": {"status": "reviewed", "reason": "只有旗標"}}, self.root / "company-reference.json")

    def test_reviewed_supplement_requires_new_analysis_without_overwriting_old_output(self):
        import base64
        import hashlib
        run, *_ = fixture(self.root)
        through_cross(run)
        original = (run / "jobs/fundamental/accepted.json").read_bytes()
        accept(run, "supplement", queries=[query(operation="financial_listing", period="2026")])
        company = company_input()
        raw = base64.b64decode(company["sources"]["q2"]["content_base64"]).replace(b"1,234", b"1,235")
        batch = w.fetch(run, self.root / "ledger", transport=Transport(content=raw), clock=lambda: NOW)
        receipt = batch["queries"][0]["receipts"][0]
        company["sources"]["q2"].update(source_url=receipt["source_url"], content_base64=base64.b64encode(raw).decode(),
            sha256=hashlib.sha256(raw).hexdigest(), captured_at=NOW.isoformat(), revision_id="synthetic-reviewed-v2")
        company["facts"][0].update(raw_value="1,235", quote="營業收入 1,235", reviewed_at=NOW.isoformat())
        ref = append(company, self.root / "db-new", clock=lambda: NOW)
        path = self.root / "reviewed-reference.json"
        path.write_text(json.dumps(ref), encoding="utf8")
        resolution = w.resolve(run, {"q1": {"status": "reviewed", "reason": "已核原句與單位"}}, path)
        self.assertEqual(resolution["new_revision"], 1)
        with self.assertRaisesRegex(ValueError, "prerequisite"):
            w.prepare_prompt(run, "bull_opening")
        for job in w.UPDATES:
            accept(run, job)
        for job in w.FINAL:
            accept(run, job)
        self.assertEqual((run / "jobs/fundamental/accepted.json").read_bytes(), original)
        audit = w.assemble(run)
        self.assertEqual(audit["workflow"]["active_revision"], 1)
        self.assertEqual(len(audit["results"]), 33)
        self.assertIn("1235000", audit["analysts"]["fundamental"]["quantified_evidence"])
        w.export(run, self.root / "portable-updated")
        self.assertEqual(w.assemble(self.root / "portable-updated")["workflow"]["active_revision"], 1)

    def test_final_risk_cannot_claim_direction_with_blocked_technicals(self):
        run, *_ = fixture(self.root)
        through_cross(run)
        accept(run, "supplement")
        w.fetch(run, self.root / "ledger")
        w.resolve(run, {})
        for job in w.FINAL[:4]:
            accept(run, job)
        value, execution = result_for(run, "risk")
        value["payload"]["rulings"][-1]["verdict"] = "supported"
        with self.assertRaisesRegex(ValueError, "directional trade"):
            w.submit(run, "risk", value, execution)

    def test_research_cannot_submit_without_actual_original_response_bytes(self):
        run, *_ = fixture(self.root, mode="research", bound=False)
        value, execution = result_for(run, "planner")
        with self.assertRaisesRegex(ValueError, "Original model"):
            w.submit(run, "planner", value, execution)

    def test_accepted_output_and_prompt_tampering_are_detected(self):
        for relative in ("jobs/planner/accepted.json", "jobs/planner/prompt.md"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                run, *_ = fixture(directory)
                (run / relative).write_text("{}", encoding="utf8")
                with self.assertRaisesRegex(ValueError, "artifact changed"):
                    w.load(run)

    def test_no_unavailable_query_can_be_omitted_at_resolution(self):
        run, *_ = fixture(self.root)
        through_cross(run)
        accept(run, "supplement", queries=[query()])
        w.fetch(run, self.root / "ledger", transport=Transport(403), clock=lambda: NOW)
        with self.assertRaisesRegex(ValueError, "each query"):
            w.resolve(run, {})

    def test_core_does_not_expose_extra_raw_context_to_cross_research(self):
        run = self.core_setup()
        accept(run, "core")
        w.prepare_prompt(run, "cross")
        request = json.loads((run / "jobs/cross/request.json").read_text("utf8"))
        self.assertEqual(request["visible_inputs"], {})
        self.assertEqual(set(request["dependency_outputs"]), {"core"})



    def test_reserved_interrupted_query_is_not_redispatched_and_can_close_limited(self):
        run, *_ = fixture(self.root)
        through_cross(run)
        accept(run, "supplement", queries=[query()])
        with patch("tradingagents.dataflows.taiwan.official_sources.OfficialSources", side_effect=RuntimeError("simulated process interruption")), self.assertRaises(RuntimeError):
            w.fetch(run, self.root / "ledger", transport=Transport())
        with self.assertRaisesRegex(ValueError, "interrupted batch"):
            w.fetch(run, self.root / "ledger", transport=Transport())
        batch = w.abandon_fetch(run, "實際批次中斷，沒有重送")
        self.assertIsNone(batch["http_requests"])
        w.resolve(run, {"q1": {"status": "unavailable", "reason": "不能證明已完成查詢"}})
        w.prepare_prompt(run, "bull_opening")

    def test_selected_core_packet_includes_claim_and_source_period_context(self):
        run = self.core_setup()
        accept(run, "core")
        w.prepare_prompt(run, "cross")
        request = json.loads((run / "jobs/cross/request.json").read_text("utf8"))
        face = request["core_evidence"]["faces"][0]
        self.assertEqual(face["claim"], "合成原件欄位存在")
        self.assertEqual(face["horizon"], "long_term")
        context = face["items"][0]["source_context"]
        self.assertEqual(context["basis"], "single_quarter")
        self.assertEqual(context["unit"], "TWD")
        self.assertFalse(context["independence_statistically_verified"])

    def test_official_pdf_query_uses_verified_listing_form_and_bytes(self):
        import base64

        from tests.taiwan.test_filing_links import fixture as listing_fixture
        run, *_ = fixture(self.root)
        through_cross(run)
        accept(run, "supplement", queries=[query(operation="financial_original", period="2026-Q2", edition="AI1")])
        _, listing, document = listing_fixture()
        metadata = json.loads((run / "revisions/0/metadata.json").read_text("utf8"))
        issuer = metadata["instrument"]["name"]
        raw = base64.b64decode(listing["content_base64"]).replace("公司名稱：奇鋐".encode(), ("公司名稱：" + issuer).encode())
        listing = {**listing, "content_base64": base64.b64encode(raw).decode()}
        replies = [listing, document["download_response"], document["pdf_response"]]
        class Chain:
            def __init__(self):
                self.calls = []
            def get(self, url, **kwargs):
                self.calls.append(url)
                raw = base64.b64decode(replies[len(self.calls) - 1]["content_base64"])
                return type("Response", (), {"status_code": 200, "content": raw})()
            post = get
        transport = Chain()
        batch = w.fetch(run, self.root / "ledger", transport=transport, clock=lambda: NOW)
        self.assertEqual(batch["http_requests"], 3)
        self.assertEqual(batch["queries"][0]["status"], "captured_pending_review")
        self.assertFalse(batch["numeric_review_completed"])

    def test_undispatched_analyst_cannot_keep_completed_checks(self):
        run, *_ = fixture(self.root)
        value, execution = result_for(run, "fundamental")
        value.update(status="unavailable", stance="unavailable", findings=[])
        execution["kind"] = "unavailable"
        value["payload"]["checks"][0].update(status="completed", evidence=[{"input_id": "company", "pointer": "/facts/0/normalized_value", "quote": "1234000"}])
        with self.assertRaisesRegex(ValueError, "Undispatched"):
            w.submit(run, "fundamental", value, execution)


    def test_upgrade_cli_detects_saved_v2_instead_of_forcing_legacy_protocol(self):
        from tradingagents.research.__main__ import main
        run, snapshot, ref, eps = fixture(self.root)
        finish(run)
        snapshot_path = self.root / "snapshot.json"
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf8")
        extension_path = self.root / "extension.json"
        extension_path.write_text(json.dumps({"stock_id": "3017", "as_of": snapshot["as_of"], "agent_report_mode": "mock", "company_snapshot": ref, "public_eps": eps}), encoding="utf8")
        output = self.root / "cli-report"
        code = main(["upgrade", "--snapshot", str(snapshot_path), "--output-dir", str(output), "--fixed-pipeline", "--agent-run", str(run), "--extension-input", str(extension_path)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((output / "agent-protocol-review.json").read_text("utf8"))["protocol"], w.PROTOCOL)
        extension = json.loads(extension_path.read_text("utf8"))
        extension["agent_protocol"] = "fixed-research-agents-v1"
        extension_path.write_text(json.dumps(extension), encoding="utf8")
        self.assertEqual(main(["upgrade", "--snapshot", str(snapshot_path), "--output-dir", str(self.root / "wrong"), "--agent-run", str(run), "--extension-input", str(extension_path)]), 2)
        self.assertFalse((self.root / "wrong").exists())


def query(**kwargs):
    return {"id": "q1", "issue_id": "pair0", "operation": "monthly_revenue", "period": "2026-10", "edition": None,
            "rationale": "驗證會影響結論的原件", "affected_dimensions": ["fundamental"], **kwargs}


class Transport:
    def __init__(self, status=200, content=b"official mock"):
        self.status, self.content = status, content
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return type("Response", (), {"status_code": self.status, "content": self.content})()

    post = get


if __name__ == "__main__":
    unittest.main()
