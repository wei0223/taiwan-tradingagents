"""Versioned prompts, dependency-bound submissions and portable on-demand research runs.

Execution receipts are coordinator attestations, not cryptographic proof of tool execution.
"""

import copy
import hashlib
import json
import re
import shutil
from pathlib import Path

from tradingagents.dataflows.taiwan.source_store import SourceStore

from .bundle import fingerprint

PROTOCOL = "fixed-research-agents-v1"
TEMPLATES = Path(__file__).with_name("prompts") / "v1"
STEPS = {
    "planner": (
        "business_map",
        "entity_and_peer_candidates",
        "financial_transmission",
        "source_plan",
    ),
    "fundamental": (
        "business_and_peers",
        "earnings_bridge",
        "cash_quality",
        "consensus_eps",
        "valuation_limits",
    ),
    "technical": (
        "history_and_basis",
        "trend",
        "momentum",
        "volatility_volume",
        "short_term_conditions",
    ),
    "institutional": (
        "coverage",
        "flows",
        "price_volume_alignment",
        "normalization_limits",
        "counterevidence",
    ),
    "bull_opening": ("independent_thesis", "consensus_challenge", "strongest_counterevidence"),
    "bear_opening": ("independent_thesis", "consensus_challenge", "strongest_counterevidence"),
    "bull_reply": ("opponent_claims", "position_update"),
    "bear_reply": ("opponent_claims", "position_update"),
    "risk": ("accounting", "cash", "valuation", "trading"),
    "decision": ("evidence_comparison", "four_axis_ruling", "revision_conditions"),
}
DEPENDENCIES = {
    "planner": (),
    **dict.fromkeys(
        ("fundamental", "technical", "institutional", "bull_opening", "bear_opening"), ("planner",)
    ),
    "bull_reply": ("bull_opening", "bear_opening"),
    "bear_reply": ("bull_opening", "bear_opening"),
    "risk": ("bull_opening", "bear_opening", "bull_reply", "bear_reply"),
    "decision": (
        "fundamental",
        "technical",
        "institutional",
        "bull_opening",
        "bear_opening",
        "risk",
        "bull_reply",
        "bear_reply",
    ),
}
AXES = ("accounting_earnings", "cash_sustainability", "valuation", "short_term")
COMMON_FIELDS = {
    "protocol",
    "stage",
    "stock_id",
    "as_of",
    "prompt_sha256",
    "agent_id",
    "status",
    "position",
    "paragraphs",
    "findings",
    "steps",
    "gaps",
    "conditions",
}


def write_new(path, value, text=False):
    path = SourceStore.path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        value.encode("utf8")
        if text
        else (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(
            "utf8"
        )
    )
    with path.open("xb") as f:
        f.write(raw)
    return hashlib.sha256(raw).hexdigest()


def load_json(path):
    return json.loads(SourceStore.path(path).read_text("utf8"))


def save_state(root, state):
    # Only the index advances. Accepted prompts, inputs, outputs and receipts never overwrite.
    tmp = root / "state.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    tmp.replace(root / "state.json")


def text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 12000:
        raise ValueError("Nonempty bounded narrative required")


def template(stage):
    if stage not in STEPS:
        raise ValueError("Unknown research stage")
    return (
        (TEMPLATES / "common.md").read_text("utf8")
        + "\n\n"
        + (TEMPLATES / (stage + ".md")).read_text("utf8")
    )


def load(run):
    root = SourceStore.path(run)
    state = load_json(root / "state.json")
    if state["protocol"] != PROTOCOL:
        raise ValueError("Unsupported agent protocol")
    for name, item in state["inputs"].items():
        p = root / item["path"]
        if hashlib.sha256(p.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Agent input changed: " + name)
    for stage, item in state["prompts"].items():
        request = load_json(root / item["request_path"])
        if request["template_sha256"] != state["template_versions"][stage] or any(
            state["inputs"].get(k) != v for k, v in request["inputs"].items()
        ):
            raise ValueError("Prompt input/template version changed")
        if any(
            state["completed"].get(k, {}).get("output_sha256") != v
            for k, v in request["dependencies"].items()
        ):
            raise ValueError("Prompt prerequisite changed")
        if (
            hashlib.sha256((root / item["result_template_path"]).read_bytes()).hexdigest()
            != item["result_template_sha256"]
        ):
            raise ValueError("Result contract changed")
        if fingerprint(request) != item["request_sha256"]:
            raise ValueError("Agent request changed")
        if hashlib.sha256((root / item["path"]).read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Rendered prompt changed")
    for _stage, item in state["completed"].items():
        for key in ("output", "execution", "original"):
            if (
                hashlib.sha256((root / item[key + "_path"]).read_bytes()).hexdigest()
                != item[key + "_sha256"]
            ):
                raise ValueError("Accepted agent artifact changed")
    if state["bound"]:
        from .company_database import read

        read(root / "inputs/company_reference.json")
    return root, state


def add_input(root, state, key, data):
    relative = "inputs/" + key + ".json"
    state["inputs"][key] = {"path": relative, "sha256": write_new(root / relative, data)}


def start(snapshot, directory, technical=None, mode="research"):
    from .company_database import plan
    from .technical_engine import build

    root = SourceStore.path(directory)
    if root.exists() and any(root.iterdir()):
        raise ValueError("Use a new agent run directory")
    root.mkdir(parents=True, exist_ok=True)
    if mode not in ("research", "mock"):
        raise ValueError("Unknown agent run mode")
    stock, cutoff = snapshot["instrument"]["stock_id"], snapshot["as_of"]
    state = {
        "protocol": PROTOCOL,
        "mode": mode,
        "stock_id": stock,
        "as_of": cutoff,
        "inputs": {},
        "prompts": {},
        "completed": {},
        "bound": False,
        "snapshot_sha256": fingerprint(snapshot),
        "template_versions": {
            stage: hashlib.sha256(template(stage).encode()).hexdigest() for stage in STEPS
        },
        "execution_proven_by_metadata": False,
    }
    add_input(root, state, "metadata", {"instrument": snapshot["instrument"], "as_of": cutoff})
    add_input(root, state, "company_plan", plan(stock, cutoff, snapshot["instrument"]["exchange"]))
    calculated = build(snapshot)
    if technical is not None and fingerprint(technical) != fingerprint(calculated):
        raise ValueError("Technical input must equal the fixed program calculation")
    add_input(root, state, "technical", calculated)
    # Snapshot is held for later program calculations, not exposed in the blind opening prompt.
    add_input(root, state, "source_snapshot", snapshot)
    save_state(root, state)
    prepare_prompt(root, "planner")
    return state


def bind(run, company_reference, public_eps=None, review_input=None):
    from .company_database import export, read
    from .dynamics import rolling
    from .public_eps import build

    root, state = load(run)
    if state["bound"] or any(s != "planner" for s in state["prompts"]):
        raise ValueError("Research inputs already sealed")
    if "planner" not in state["completed"]:
        raise ValueError(
            "Complete the fixed research plan before collecting/binding company inputs"
        )
    ref_path = Path(company_reference).resolve()
    reference = load_json(ref_path)
    store = Path(reference["source_store"])
    reference["source_store"] = str(store if store.is_absolute() else ref_path.parent / store)
    company = read(reference)
    if (company["stock_id"], company["as_of"]) != (state["stock_id"], state["as_of"]):
        raise ValueError("Company snapshot company/cutoff mismatch")
    portable = export(reference, root / "inputs/company-database")
    add_input(root, state, "company_reference", portable)
    # Never expose a coordinator's assumptions or unselected vintages to blind researchers.
    evidence = {k: v for k, v in company.items() if k not in ("assumptions", "fact_vintages")}
    add_input(root, state, "company", evidence)
    from .financial_core import build as financial_calculations

    add_input(root, state, "financial", financial_calculations(company))
    consensus = build(public_eps, state["stock_id"], state["as_of"])
    add_input(root, state, "public_eps", consensus)
    add_input(root, state, "public_eps_input", public_eps or {})
    add_input(root, state, "review", review_input or {})
    snapshot = load_json(root / state["inputs"]["source_snapshot"]["path"])
    add_input(root, state, "dynamics", rolling(snapshot))
    state["bound"] = True
    save_state(root, state)
    return {
        "protocol": PROTOCOL,
        "status": "inputs_sealed",
        "company_sha256": reference["snapshot_sha256"],
    }


def skeleton(state, stage):
    value = {
        "protocol": PROTOCOL,
        "stage": stage,
        "stock_id": state["stock_id"],
        "as_of": state["as_of"],
        "prompt_sha256": "",
        "agent_id": "",
        "status": "pending",
        "position": "",
        "paragraphs": [],
        "findings": [],
        "steps": [
            {"id": k, "status": "pending", "explanation": "", "evidence": []} for k in STEPS[stage]
        ],
        "gaps": [],
        "conditions": [],
    }
    if stage == "fundamental":
        value["question_answers"] = []
    if stage == "planner":
        value["plan"] = {"business_hypotheses": [], "entities": [], "peers": [], "questions": []}
    if stage.endswith("reply"):
        value.update(position_before="", responses=[])
    if stage in ("risk", "decision"):
        value["rulings"] = [
            {"axis": k, "verdict": "pending", "reason": "", "evidence": []} for k in AXES
        ]
    if stage == "decision":
        value.update(
            position_before="",
            stronger_case="",
            key_disagreement="",
            evidence_change="",
            changed_claim_ids=[],
        )
    return value


def prepare_prompt(run, stage):
    root, state = load(run)
    body = template(stage)
    if hashlib.sha256(body.encode()).hexdigest() != state["template_versions"][stage]:
        raise ValueError("Template changed; start a new research run instead of mixing versions")
    if stage in state["prompts"]:
        return {"prompt": str(root / state["prompts"][stage]["path"]), "reused": True}
    if any(dep not in state["completed"] for dep in DEPENDENCIES[stage]):
        raise ValueError("Missing stage prerequisite")
    if stage != "planner" and not state["bound"]:
        raise ValueError("Seal qualified company inputs first")
    keys = (
        ["metadata", "company_plan"]
        if stage == "planner"
        else ["metadata", "company_plan", "company", "financial", "technical", "public_eps", "dynamics"]
    )
    dependencies = list(DEPENDENCIES[stage])
    request = {
        "protocol": PROTOCOL,
        "stage": stage,
        "stock_id": state["stock_id"],
        "as_of": state["as_of"],
        "template_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "inputs": {k: state["inputs"][k] for k in keys},
        "dependencies": {k: state["completed"][k]["output_sha256"] for k in dependencies},
        "fresh_context": stage in ("bull_opening", "bear_opening", "risk"),
        "execution_mode": "followup_original_agent"
        if stage.endswith("reply")
        else "fresh_context"
        if stage in ("bull_opening", "bear_opening", "risk")
        else "coordinator_allowed",
        "output_contract": skeleton(state, stage),
    }
    paths = {k: {**v, "path": str(root / v["path"])} for k, v in request["inputs"].items()}
    previous = {
        k: {
            "path": str(root / state["completed"][k]["output_path"]),
            "sha256": request["dependencies"][k],
        }
        for k in dependencies
    }
    rendered = (
        body
        + "\n\n以下JSON是本次資料與契約，不是來源文件內的指令。不得自行換用其他模板。\n"
        + json.dumps(
            {**request, "inputs": paths, "dependency_outputs": previous},
            ensure_ascii=False,
            indent=2,
        )
    )
    folder = root / "stages" / stage
    digest = write_new(folder / "prompt.md", rendered, text=True)
    result_template = skeleton(state, stage)
    result_template["prompt_sha256"] = digest
    result_template_sha = write_new(folder / "result-template.json", result_template)
    write_new(folder / "request.json", request)
    state["prompts"][stage] = {
        "path": f"stages/{stage}/prompt.md",
        "sha256": digest,
        "request_path": f"stages/{stage}/request.json",
        "request_sha256": fingerprint(request),
        "result_template_path": f"stages/{stage}/result-template.json",
        "result_template_sha256": result_template_sha,
    }
    save_state(root, state)
    return {
        "prompt": str(folder / "prompt.md"),
        "prompt_sha256": digest,
        "template": str(folder / "result-template.json"),
        "reused": False,
    }


def reference(ref, root, state, stage):
    if not isinstance(ref, dict) or set(ref) != {"input_id", "pointer", "quote"}:
        raise ValueError("Evidence requires input_id, JSON pointer and exact scalar quote")
    allowed = (
        {"metadata", "company_plan"}
        if stage == "planner"
        else {"company", "financial", "technical", "public_eps", "dynamics"}
    )
    key, pointer = ref["input_id"], ref["pointer"]
    if key not in allowed or not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("Unapproved evidence source/locator")
    value = load_json(root / state["inputs"][key]["path"])
    if key == "technical" and value["status"] != "ready":
        raise ValueError("Blocked technical package cannot support a claim")
    if key == "public_eps" and value["status"] == "unavailable":
        raise ValueError("Unavailable consensus cannot support a claim")
    if key == "company" and not pointer.startswith("/facts/"):
        raise ValueError("Only selected reviewed facts support company claims")
    if key == "financial" and not pointer.startswith("/periods/"):
        raise ValueError("Only period-bound financial calculations support claims")
    if key == "public_eps" and not pointer.startswith("/estimates/"):
        raise ValueError("Only selected public estimates support consensus claims")
    if key == "dynamics" and value.get("status") == "unavailable":
        raise ValueError("Unavailable institutional calculations cannot support a claim")
    try:
        for token in pointer[1:].split("/"):
            if re.search(r"~(?![01])", token):
                raise ValueError("Invalid JSON pointer escape")
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                if not re.fullmatch(r"0|[1-9][0-9]*", token):
                    raise ValueError("Canonical array index required")
                value = value[int(token)]
            else:
                value = value[token]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Unresolved evidence pointer") from exc
    if value is None or isinstance(value, (dict, list)) or str(value) != ref["quote"]:
        raise ValueError("Evidence quote must exactly bind an available scalar")


def evidence(items, root, state, stage, required=True):
    if not isinstance(items, list) or len(items) > 20 or (required and not items):
        raise ValueError("Bounded evidence references required")
    for item in items:
        reference(item, root, state, stage)


def validate_result(root, state, stage, result, execution):
    expected = set(skeleton(state, stage))
    if set(result) != expected:
        raise ValueError("Stage output has missing or extra fields")
    for key, value in {
        "protocol": PROTOCOL,
        "stage": stage,
        "stock_id": state["stock_id"],
        "as_of": state["as_of"],
        "prompt_sha256": state["prompts"][stage]["sha256"],
    }.items():
        if result[key] != value:
            raise ValueError("Result company/date/prompt mismatch")
    text(result["agent_id"])
    if result["status"] not in ("complete", "limited", "unavailable"):
        raise ValueError("Explicit stage completion/limitation status required")
    text(result["position"])
    for key in ("paragraphs", "conditions", "gaps"):
        if not isinstance(result[key], list) or len(result[key]) > 20:
            raise ValueError("Bounded narrative arrays required")
        for item in result[key]:
            text(item)
    if (
        not result["paragraphs"]
        or not result["conditions"]
        or (result["status"] != "complete" and not result["gaps"])
    ):
        raise ValueError("Narrative, revision conditions and explicit gaps required")
    steps = result["steps"]
    if len(steps) != len(STEPS[stage]) or {s["id"] for s in steps} != set(STEPS[stage]):
        raise ValueError("Every fixed research step must be addressed exactly once")
    for step in steps:
        if set(step) != {"id", "status", "explanation", "evidence"} or step["status"] not in (
            "completed",
            "unavailable",
        ):
            raise ValueError("Invalid research step")
        text(step["explanation"])
        evidence(step["evidence"], root, state, stage, step["status"] == "completed")
        if (
            stage == "technical"
            and step["status"] == "completed"
            and any(e["input_id"] != "technical" for e in step["evidence"])
        ):
            raise ValueError("Technical research steps must use the fixed technical package")
    if result["status"] == "complete" and any(s["status"] != "completed" for s in steps):
        raise ValueError("Unavailable mandatory steps cannot be marked complete")
    findings = result["findings"]
    if (
        not isinstance(findings, list)
        or len(findings) > 6
        or len({f["id"] for f in findings}) != len(findings)
    ):
        raise ValueError("Bounded unique claims required")
    if result["status"] == "unavailable" and findings:
        raise ValueError("Unavailable stage cannot contain asserted findings")
    if not findings and result["status"] != "unavailable":
        raise ValueError("Available research requires evidence-based claims")
    for finding in findings:
        if set(finding) != {
            "id",
            "claim",
            "evidence",
            "strongest_counterevidence",
            "confidence",
            "revision_condition",
        }:
            raise ValueError("Incomplete claim contract")
        if not re.fullmatch(stage + r"\.[A-Za-z0-9_-]{1,40}", finding["id"]):
            raise ValueError("Stage-scoped claim ID required")
        for k in ("claim", "strongest_counterevidence", "revision_condition"):
            text(finding[k])
        if finding["confidence"] not in (
            "uncalibrated_low",
            "uncalibrated_medium",
            "uncalibrated_high",
        ):
            raise ValueError("No calibrated probability or confidence interval is available")
        evidence(finding["evidence"], root, state, stage)
    if (
        execution.get("agent_id") != result["agent_id"]
        or execution.get("prompt_sha256") != result["prompt_sha256"]
    ):
        raise ValueError("Execution receipt must bind agent and rendered prompt")
    expected_kind = "mock" if state["mode"] == "mock" else "observed_tool_receipt"
    if execution.get("evidence_kind") != expected_kind:
        raise ValueError("Mock receipts cannot enter a research run")
    kind = execution.get("kind")
    if kind not in (
        "spawn_agent",
        "followup_task",
        "coordinator",
        "unavailable",
    ) or not execution.get("tool_call_id"):
        raise ValueError("Actual dispatch/coordinator receipt required; never invent an identifier")
    if kind == "unavailable" and (
        result["status"] != "unavailable"
        or findings
        or any(s["status"] != "unavailable" for s in steps)
    ):
        raise ValueError("Undispatched stage cannot contain claimed research")
    if stage in ("bull_opening", "bear_opening", "risk") and kind != "unavailable":
        if kind != "spawn_agent" or execution.get("context_mode") != "fresh":
            raise ValueError("Independent opening/risk requires actual fresh-context dispatch")
        other_ids = [
            load_json(root / c["output_path"])["agent_id"]
            for s, c in state["completed"].items()
            if s in ("bull_opening", "bear_opening", "risk")
            and load_json(root / c["execution_path"])["kind"] != "unavailable"
        ]
        if result["agent_id"] in other_ids:
            raise ValueError("Bull, bear and risk must use distinct agents")
    if stage.endswith("reply"):
        role = stage.split("_")[0]
        own = load_json(root / state["completed"][role + "_opening"]["output_path"])
        opponent = "bear_opening" if role == "bull" else "bull_opening"
        opposing = load_json(root / state["completed"][opponent]["output_path"])
        if result["agent_id"] != own["agent_id"] or result["position_before"] != own["position"]:
            raise ValueError("Rebuttal must preserve original agent and opening position")
        if kind != "unavailable" and kind != "followup_task":
            raise ValueError("Follow up the original independent agent")
        expected_ids = {f["id"] for f in opposing["findings"]}
        responses = result["responses"]
        if (
            len(responses) != len(expected_ids)
            or {r["claim_id"] for r in responses} != expected_ids
        ):
            raise ValueError("Rebut every opposing claim exactly once")
        for response in responses:
            if (
                set(response)
                != {"claim_id", "assessment", "reason", "evidence", "changed_my_position"}
                or response["assessment"] not in ("accept", "dispute", "uncertain")
                or not isinstance(response["changed_my_position"], bool)
            ):
                raise ValueError("Incomplete rebuttal")
            text(response["reason"])
            evidence(response["evidence"], root, state, stage, kind != "unavailable")
            if kind == "unavailable" and (
                response["assessment"] != "uncertain" or response["changed_my_position"]
            ):
                raise ValueError(
                    "Unexecuted rebuttal can only declare uncertainty, not a researched revision"
                )
    if stage in ("risk", "decision"):
        rulings = result["rulings"]
        if len(rulings) != 4 or {r["axis"] for r in rulings} != set(AXES):
            raise ValueError("Accounting/cash/valuation/short-term rulings must be separate")
        for ruling in rulings:
            if set(ruling) != {"axis", "verdict", "reason", "evidence"} or ruling[
                "verdict"
            ] not in ("supported", "challenged", "uncertain", "unavailable"):
                raise ValueError("Explicit ruling required")
            text(ruling["reason"])
            evidence(ruling["evidence"], root, state, stage, ruling["verdict"] != "unavailable")
    if stage == "fundamental":
        plan_result = load_json(root / state["completed"]["planner"]["output_path"])
        question_ids = {q["id"] for q in plan_result["plan"]["questions"]}
        answers = result["question_answers"]
        if (
            not isinstance(answers, list)
            or len(answers) != len(question_ids)
            or {a["question_id"] for a in answers} != question_ids
        ):
            raise ValueError("Address every company-specific planning question exactly once")
        for answer in answers:
            if set(answer) != {"question_id", "status", "answer", "evidence"} or answer[
                "status"
            ] not in ("answered", "unavailable"):
                raise ValueError(
                    "Explicit question answer or missing-evidence explanation required"
                )
            text(answer["answer"])
            evidence(answer["evidence"], root, state, stage, answer["status"] == "answered")
    if stage == "planner":
        plan = result["plan"]
        if (
            set(plan) != {"business_hypotheses", "entities", "peers", "questions"}
            or not plan["questions"]
        ):
            raise ValueError("Structured research plan required")
        if not isinstance(plan["business_hypotheses"], list):
            raise ValueError("Planning hypotheses must be a list")
        for hypothesis in plan["business_hypotheses"]:
            text(hypothesis)
        if len(plan["questions"]) > 30 or len({q["id"] for q in plan["questions"]}) != len(
            plan["questions"]
        ):
            raise ValueError("Bounded unique company research questions required")
        for q in plan["questions"]:
            if (
                set(q) != {"id", "question", "financial_transmission", "required_sources"}
                or not q["required_sources"]
            ):
                raise ValueError("Question requires financial transmission and source plan")
            for category in q["required_sources"]:
                text(category)
            for k in ("id", "question", "financial_transmission"):
                text(q[k])
        for row in [*plan["entities"], *plan["peers"]]:
            if (
                set(row) != {"name", "stock_id", "relationship", "reason", "status"}
                or row["status"] != "candidate_requires_verification"
            ):
                raise ValueError(
                    "Planning candidates are not verified relationships or comparable peers"
                )
            if row["stock_id"] is not None and not re.fullmatch(r"\d{4,6}", row["stock_id"]):
                raise ValueError("Candidate code or explicit null required")
            for k in ("name", "relationship", "reason"):
                text(row[k])
    if stage == "decision":
        for k in ("position_before", "stronger_case", "key_disagreement", "evidence_change"):
            text(result[k])
        all_ids = {
            f["id"]
            for s, c in state["completed"].items()
            for f in load_json(root / c["output_path"])["findings"]
        }
        if not isinstance(result["changed_claim_ids"], list) or any(
            c not in all_ids for c in result["changed_claim_ids"]
        ):
            raise ValueError("Changed judgment must identify accepted claim IDs")


def submit(run, stage, result, execution, raw_result=None):
    root, state = load(run)
    if stage not in state["prompts"] or stage in state["completed"]:
        raise ValueError(
            "Prepare the stage once before submission; never overwrite accepted output"
        )
    validate_result(root, state, stage, result, execution)
    if raw_result is None:
        if state["mode"] != "mock":
            raise ValueError("Archive the original UTF-8 result bytes in a research run")
        raw_result = (json.dumps(result, ensure_ascii=False) + "\n").encode("utf8")
    if not isinstance(raw_result, bytes) or json.loads(raw_result) != result:
        raise ValueError("Original result bytes do not match parsed output")
    original = f"stages/{stage}/original.json"
    original_sha = write_new(root / original, raw_result.decode("utf8"), text=True)
    output = f"stages/{stage}/accepted.json"
    receipt = f"stages/{stage}/execution.json"
    state["completed"][stage] = {
        "output_path": output,
        "output_sha256": write_new(root / output, result),
        "execution_path": receipt,
        "execution_sha256": write_new(root / receipt, execution),
        "original_path": original,
        "original_sha256": original_sha,
    }
    save_state(root, state)
    return {"stage": stage, "status": "accepted", "research_status": result["status"]}


def assemble(run):
    root, state = load(run)
    if set(state["completed"]) != set(STEPS) or not state["bound"]:
        raise ValueError("Every fixed stage must finish or explicitly declare unavailable")
    results = {s: load_json(root / c["output_path"]) for s, c in state["completed"].items()}
    for stage, result in results.items():
        # Revalidate immutable results against the same protocol, excluding the stage itself.
        prior = copy.deepcopy(state)
        prior["completed"].pop(stage)
        validate_result(
            root,
            prior,
            stage,
            result,
            load_json(root / state["completed"][stage]["execution_path"]),
        )
    notes = {"schema_version": 3, "stock_id": state["stock_id"], "as_of": state["as_of"]}
    for role in ("fundamental", "technical", "institutional"):
        result = results[role]
        findings = result["findings"]
        notes[role] = {
            "position": result["position"],
            "paragraphs": result["paragraphs"],
            "conditions": result["conditions"],
            "primary_claim": "；".join(f["claim"] for f in findings) or "本階段無合格證據。",
            "quantified_evidence": "；".join(r["quote"] for f in findings for r in f["evidence"])
            or "不可用",
            "strongest_counterevidence": "；".join(f["strongest_counterevidence"] for f in findings)
            or "證據不足。",
            "confidence": "未校準質性判斷",
            "stronger_case": result["position"],
            "disagreement": results["decision"]["key_disagreement"],
            "evidence_change": results["decision"]["evidence_change"],
        }
    technical = load_json(root / state["inputs"]["technical"]["path"])
    notes["technical"]["technical_package_sha256"] = fingerprint(technical)
    genuine = state["mode"] == "research" and all(
        load_json(root / state["completed"][s]["execution_path"])["kind"] != "unavailable"
        for s in ("bull_opening", "bear_opening", "bull_reply", "bear_reply", "risk")
    )
    decision = results["decision"]
    debate = {
        "status": "one_rebuttal_round_completed"
        if genuine
        else "limited_not_independently_executed",
        "independent_agents_executed": genuine,
        "execution_attestation": "Saved coordinator tool receipts; metadata does not prove execution",
        "packet_sha256": fingerprint(results),
        "confidence_interval": None,
        "performance_evidence": False,
        "openings": {
            r: {
                "position": results[r + "_opening"]["position"],
                "claims": results[r + "_opening"]["findings"],
            }
            for r in ("bull", "bear")
        },
        "replies": {
            r: {
                "position_before": results[r + "_reply"]["position_before"],
                "position_after": results[r + "_reply"]["position"],
                "changes": results[r + "_reply"]["responses"],
            }
            for r in ("bull", "bear")
        },
        "risk": {"rulings": results["risk"]["rulings"]},
        "judgment": {
            "before": decision["position_before"],
            "after": decision["position"],
            "stronger_case": decision["stronger_case"],
            "evidence_that_changed_judgment": decision["evidence_change"],
            "unresolved": decision["gaps"],
            "revision_conditions": decision["conditions"],
            "rulings": decision["rulings"],
        },
    }
    return {
        "protocol": PROTOCOL,
        "stock_id": state["stock_id"],
        "as_of": state["as_of"],
        "analysts": notes,
        "debate": debate,
        "mode": state["mode"],
        "status": "complete_structure"
        if all(r["status"] == "complete" for r in results.values())
        else "limited",
        "semantic_truth_verified": False,
        "results": results,
    }


def report_inputs(run, snapshot, extension, review_input):
    from .company_database import read
    from .public_eps import build

    root, state = load(run)
    if state["mode"] == "mock" and extension.get("agent_report_mode") != "mock":
        raise ValueError("Mock agent run cannot enter a formal research report")
    if state["mode"] == "research" and extension.get("agent_report_mode") == "mock":
        raise ValueError("Do not relabel a research run as a mock")
    if fingerprint(snapshot) != state["snapshot_sha256"]:
        raise ValueError("Report snapshot changed after agent research")
    company = read(extension["company_snapshot"])
    original = read(root / "inputs/company_reference.json")
    if fingerprint(company) != fingerprint(original):
        raise ValueError("Report company version changed after agent research")
    if fingerprint(
        build(extension.get("public_eps"), state["stock_id"], state["as_of"])
    ) != fingerprint(load_json(root / "inputs/public_eps.json")):
        raise ValueError("Report EPS source changed after agent research")
    if fingerprint(review_input or {}) != fingerprint(load_json(root / "inputs/review.json")):
        raise ValueError("Report financial review changed after agent research")
    return assemble(root)


def export(run, destination):
    root, _ = load(run)
    assemble(root)
    target = SourceStore.path(destination)
    if target.exists():
        raise ValueError("Use a new prompt archive destination")
    shutil.copytree(root, target)
    assemble(target)
