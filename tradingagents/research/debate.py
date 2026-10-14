"""Validate independent openings, rebuttals, risk review, and a final research decision."""

from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from .bundle import canonical, fingerprint

STAGES = ("bull_opening", "bear_opening", "bull_reply", "bear_reply", "risk", "decision")
ROLES = {"bull_opening": "bull", "bear_opening": "bear", "bull_reply": "bull",
         "bear_reply": "bear", "risk": "risk", "decision": "coordinator"}
PREREQUISITES = {
    "bull_opening": (), "bear_opening": (),
    "bull_reply": ("bull_opening", "bear_opening"),
    "bear_reply": ("bull_opening", "bear_opening"),
    "risk": ("bull_reply", "bear_reply"), "decision": ("risk",),
}


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(canonical(value) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def start_run(bundle, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "state.json").exists():
        state, saved = load_run(directory)
        if fingerprint(saved) != fingerprint(bundle):
            raise ValueError("Existing run uses different evidence")
        return state
    if any(directory.iterdir()):
        raise ValueError("New run directory must be empty")
    state = {"schema_version": 1, "bundle_sha256": fingerprint(bundle),
             "hash_algorithm": "sha256-canonical-json-utf8", "status": "ready",
             "agents": {}, "completed": {}, "stop_reason": None,
             "created_at": datetime.now(UTC).isoformat()}
    write_json(directory / "bundle.json", bundle)
    write_json(directory / "state.json", state)
    return state


def load_run(directory):
    directory = Path(directory)
    state = load_json(directory / "state.json")
    bundle = load_json(directory / "bundle.json")
    if fingerprint(bundle) != state["bundle_sha256"]:
        raise ValueError("Evidence changed; create a new run")
    for stage, digest in state["completed"].items():
        if stage not in STAGES or fingerprint(load_json(directory / f"{stage}.json")) != digest:
            raise ValueError("Accepted stage changed; resume refused")
    return state, bundle


def register_agent(directory, role, agent_id):
    state, _ = load_run(directory)
    if state["status"] == "complete":
        raise ValueError("Run is complete")
    if role not in {"bull", "bear", "risk"} or not re.fullmatch(r"[A-Za-z0-9_./-]{1,160}", agent_id):
        raise ValueError("Invalid role or agent identifier")
    if role in state["agents"] and state["agents"][role] != agent_id:
        raise ValueError("Role already registered; resume its original agent")
    if any(key != role and value == agent_id for key, value in state["agents"].items()):
        raise ValueError("Roles require distinct subagents")
    state["agents"][role] = agent_id
    write_json(Path(directory) / "state.json", state)


def prose(value):
    # Exact figures are injected from evidence by the renderer, never supplied by the LLM.
    if not isinstance(value, str) or not value.strip() or len(value) > 1800 or re.search(r"\d", value):
        raise ValueError("Narrative must be non-empty, bounded, and contain no numeric literals")


def refs(value, bundle):
    if not isinstance(value, list) or not value or len(value) > 8 or len(set(value)) != len(value):
        raise ValueError("Each claim/response needs bounded unique evidence references")
    for key in value:
        if key not in bundle["evidence"] or bundle["evidence"][key]["availability"] != "available":
            raise ValueError("Unknown or unavailable evidence reference")


def opening_claims(directory):
    return [claim for stage in ("bull_opening", "bear_opening")
            for claim in load_json(Path(directory) / f"{stage}.json")["claims"]]


def validate_result(result, stage, state, bundle, directory):
    required = {"stage", "bundle_sha256", "agent_id", "summary", "claims", "responses", "verdict", "limitations"}
    if not isinstance(result, dict) or set(result) != required:
        raise ValueError("Stage output has missing or unexpected fields")
    if result["stage"] != stage or result["bundle_sha256"] != state["bundle_sha256"]:
        raise ValueError("Stage or bundle hash mismatch")
    role = ROLES[stage]
    expected = "coordinator" if role == "coordinator" else state["agents"].get(role)
    if not expected or result["agent_id"] != expected:
        raise ValueError("Output must identify its registered agent")
    prose(result["summary"])
    if not isinstance(result["limitations"], list) or len(result["limitations"]) > 8:
        raise ValueError("Invalid limitations")
    for limitation in result["limitations"]:
        prose(limitation)
    claims, responses = result["claims"], result["responses"]
    if not isinstance(claims, list) or not isinstance(responses, list):
        raise ValueError("Claims and responses must be lists")
    if stage.endswith("opening"):
        if responses or len(claims) > 3:
            raise ValueError("Openings cannot respond or exceed three claims")
        if not claims and not bundle["requires_insufficient_data"]:
            raise ValueError("Opening needs evidence-based claims")
    elif claims:
        raise ValueError("Later stages respond to existing claims; no new claims")
    identifiers = set()
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {"id", "text", "evidence_ids", "invalidated_by"}:
            raise ValueError("Invalid claim fields")
        if not re.fullmatch(role + r"_c[1-3]", claim["id"]) or claim["id"] in identifiers:
            raise ValueError("Invalid or duplicate claim identifier")
        identifiers.add(claim["id"])
        prose(claim["text"])
        prose(claim["invalidated_by"])
        refs(claim["evidence_ids"], bundle)
    expected_targets = set()
    if stage in {"bull_reply", "bear_reply", "risk", "decision"}:
        openings = opening_claims(directory)
        targets = openings
        if stage.endswith("reply"):
            opponent = "bear" if role == "bull" else "bull"
            targets = [c for c in openings if c["id"].startswith(opponent + "_")]
        expected_targets = {c["id"] for c in targets}
    actual_targets = []
    for response in responses:
        if not isinstance(response, dict) or set(response) != {"claim_id", "assessment", "reason", "evidence_ids"}:
            raise ValueError("Invalid response fields")
        if response["assessment"] not in {"accept", "dispute", "uncertain"}:
            raise ValueError("Invalid assessment")
        prose(response["reason"])
        refs(response["evidence_ids"], bundle)
        actual_targets.append(response["claim_id"])
    if len(set(actual_targets)) != len(actual_targets) or set(actual_targets) != expected_targets:
        raise ValueError("Must address each required claim exactly once")
    if stage in {"risk", "decision"}:
        if result["verdict"] not in {"supports_bull", "supports_bear", "mixed", "insufficient_data"}:
            raise ValueError("Invalid research verdict")
        if bundle["requires_insufficient_data"] and result["verdict"] != "insufficient_data":
            raise ValueError("Missing critical data requires insufficient_data")
        if stage == "decision":
            risk = load_json(Path(directory) / "risk.json")
            if risk["verdict"] == "insufficient_data" and result["verdict"] != "insufficient_data":
                raise ValueError("Risk veto cannot be overridden")
    elif result["verdict"] != "not_applicable":
        raise ValueError("Only risk/decision stages may issue a verdict")


def submit(directory, stage, result):
    if stage not in STAGES:
        raise ValueError("Unknown stage")
    state, bundle = load_run(directory)
    if stage in state["completed"]:
        if fingerprint(result) == state["completed"][stage]:
            return state  # Idempotent import, including retry after a completed decision.
        raise ValueError("Stage already accepted; overwrite refused")
    if any(required not in state["completed"] for required in PREREQUISITES[stage]):
        raise ValueError("Prerequisite stages incomplete")
    validate_result(result, stage, state, bundle, directory)
    write_json(Path(directory) / f"{stage}.json", result)
    state["completed"][stage] = fingerprint(result)
    state["status"] = "complete" if stage == "decision" else "running"
    state["stop_reason"] = None
    write_json(Path(directory) / "state.json", state)
    if stage == "decision":
        render(directory)
    return state


def stop(directory, reason):
    if reason not in {"quota", "auth", "timeout", "agent_failure", "validation", "user_pause"}:
        raise ValueError("Unsupported stop reason")
    state, _ = load_run(directory)
    if state["status"] != "complete":
        state.update(status="stopped", stop_reason=reason)
        write_json(Path(directory) / "state.json", state)


def render(directory):
    state, bundle = load_run(directory)
    if state["status"] != "complete":
        raise ValueError("Cannot render an incomplete debate")
    labels = {"bull_opening": "多方獨立論證", "bear_opening": "空方獨立論證",
              "bull_reply": "多方交叉回應", "bear_reply": "空方交叉回應",
              "risk": "風險審查", "decision": "研究主管結論"}
    verdicts = {"not_applicable": "本階段提出論證，尚不作整體判斷", "supports_bull": "證據較支持多方",
                "supports_bear": "證據較支持空方", "mixed": "證據分歧",
                "insufficient_data": "資料不足，暫不形成完整方向判斷"}
    assessments = {"accept": "採納", "dispute": "爭議", "uncertain": "未定"}
    statuses = {"ok": "可用", "partial": "不完整", "unavailable": "缺資料"}
    lines = [f"# 台股研究：{bundle['stock_id']}", "",
             f"分析日期：{bundle['as_of']}｜資料雜湊：{state['bundle_sha256']}", "",
             f"資料模式：{bundle.get('data_mode', 'provider_snapshot')}", "",
             "數值由 Python 計算；此為研究觀點，不含下單或部位指令。", ""]
    for stage in STAGES:
        result = load_json(Path(directory) / f"{stage}.json")
        lines += [f"## {labels[stage]}", "", result["summary"], ""]
        for claim in result["claims"]:
            lines += [f"- {claim['id']}：{claim['text']}",
                      f"  證據：{', '.join(claim['evidence_ids'])}；失效條件：{claim['invalidated_by']}"]
        for response in result["responses"]:
            lines += [f"- {response['claim_id']}（{assessments[response['assessment']]}）：{response['reason']}",
                      f"  證據：{', '.join(response['evidence_ids'])}"]
        lines += ["", f"研究判定：{verdicts[result['verdict']]}", ""]
        lines += [f"- {item}" for item in result["limitations"]]
    lines += ["", "## 程式核實的證據數值", "", "| 證據 | 欄位 | 數值 | 單位 | 資料日期 |",
              "|---|---|---:|---|---|"]
    cited = {key for stage in STAGES for item in (
        load_json(Path(directory) / f"{stage}.json")["claims"]
        + load_json(Path(directory) / f"{stage}.json")["responses"]) for key in item["evidence_ids"]}
    for key in sorted(cited):
        item = bundle["evidence"][key]
        value = html.escape(str(item['value'])).replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| {key} | {item['path']} | {value} | {item['unit']} | {item['data_end']} |")
    lines += ["", "## 證據來源、公布與取得日期", ""]
    for key in sorted(cited):
        item = bundle["evidence"][key]
        lines += [f"- {key}：來源 {', '.join(item['sources'])}；公布日期 {', '.join(item['publication_dates']) or '未驗證'}；"
                  f"數值版本可用日 {', '.join(item['value_available_dates']) or '依每日資料政策'}；"
                  f"取得时间 {', '.join(item['retrieved_at'])}"]
        for name in ("article_urls", "publication_document_urls", "publication_evidence_sha256"):
            for value in item.get(name, []):
                lines.append(f"  {name}：{html.escape(value)}")
    lines += ["", "## 資料覆蓋與限制", ""]
    lines += [f"- {name}：{statuses[item['status']]}；可用列 {item['eligible_rows']}"
              for name, item in bundle["coverage"].items()]
    lines += [f"- {item}" for item in bundle["limitations"] if not item.startswith("No LLM analysis")]
    text = "\n".join(lines) + "\n"
    (Path(directory) / "report.md").write_text(text, encoding="utf-8")
    (Path(directory) / "report.html").write_text(
        '<!doctype html><html lang="zh-Hant"><meta charset="utf-8">'
        '<title>台股研究報告</title><style>body{max-width:1100px;margin:2rem auto;padding:1rem;'
        'font-family:system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
        f"<body><pre>{html.escape(text)}</pre></body></html>", encoding="utf-8")
    return text
