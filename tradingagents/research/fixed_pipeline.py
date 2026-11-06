"""One fixed preparation entry: immutable technical calculations plus company SOP tasks."""

import copy
import json
from pathlib import Path

from .bundle import fingerprint
from .company_database import plan, read
from .technical_engine import build

INSTRUCTIONS = """依company-plan.json逐項建立公司資料。只從官方原件抄錄，不從既有分析結論倒填事實。
1. 按年度/季別/月分/來源類別建立有界查閱清單，優先沿用已保存原件，保留實際捕獲時間。
2. sources每份須含stock_id/category/revision_id/source_url/published_at(未知null)/captured_at/sha256/content_base64。
3. facts每筆含kind=numeric或company_statement、id、metric、source_id、page、quote、column_label、context_quotes、reviewer、reviewed_at。
4. numeric另含raw_value、multiplier、unit、period_start、period_end、basis、scope；原始值須出現在quote。
5. EPS與股數另標eps_kind；不差分EPS/加權股數；所有衍生值走既有計算核心，不冒充原始抄錄。
6. 前瞻假設只寫assumptions並附evidence_fact_ids/rationale/uncertainty，不進facts。
7. 同業每家公司單獨建檔，注明主要/鄰近/排除與理由；比較前核對同季/合併/單位。
8. 缺件寫gaps(status/reason)，禁止填零、編造日期、僅勾review旗標冒充查證。
9. 執行company-import，再company-audit，將所選company-reference.json交給upgrade；不要改來源快照。
10. 技術分析只讀technical-package.json及固定圖表，不自行補公式或挑選另一套參數。引用technical_sha256。
11. 預估EPS優先讀公開鉅亨轉引FactSet的年度中位數表；另存public-eps-input.json，不混入公司實績facts。不採新聞目標價或最高最低值作本次預估。
只有實際看過原件與表格才能填reviewer。程式能核對原文存在，不能自動證明表格語意或公司說法屬實。
本工作只在本次請求執行，不建立監測、不發布預測、不下單、不呼叫付費LLM API。
"""


def prepare(snapshot, output, agent_protocol="fixed-research-agents-v1"):
    from .technical_chart import svg

    root = Path(output)
    if root.exists() and any(root.iterdir()):
        raise ValueError("Use a new fixed preparation directory")
    root.mkdir(parents=True, exist_ok=True)
    company_plan = plan(
        snapshot["instrument"]["stock_id"], snapshot["as_of"], snapshot["instrument"]["exchange"]
    )
    technical = build(snapshot)
    skeleton = {
        "stock_id": company_plan["stock_id"],
        "as_of": snapshot["as_of"],
        "exchange": snapshot["instrument"]["exchange"],
        "sources": {},
        "facts": [],
        "assumptions": [],
        "gaps": [
            {
                "category": category,
                "status": "pending",
                "reason": "Original source/value review not completed in this preparation",
            }
            for category in company_plan["source_categories"]
        ],
    }
    for name, value in [
        ("public-eps-input-template.json", {"stock_id": company_plan["stock_id"], "as_of": snapshot["as_of"], "sources": {}}),
        ("company-plan.json", company_plan),
        ("company-input-template.json", skeleton),
        ("technical-package.json", technical),
    ]:
        (root / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf8"
        )
    (root / "company-instructions.md").write_text(INSTRUCTIONS, encoding="utf8")
    if technical["status"] == "ready":
        (root / "candles.svg").write_text(svg(technical), encoding="utf8")
        (root / "weekly-candles.svg").write_text(svg(technical, True), encoding="utf8")
    if agent_protocol == "taiwan-eight-stage-v2":
        from .eight_stage import start as start_agents
    elif agent_protocol == "fixed-research-agents-v1":
        from .agent_protocol import start as start_agents
    else:
        raise ValueError("Unknown research protocol")
    start_agents(snapshot, root / "agents", technical=technical)
    state = {
        "agent_protocol": agent_protocol,
        "agent_run": "agents",
        "status": "technical_ready_company_review_required"
        if technical["status"] == "ready"
        else "technical_blocked_company_review_required",
        "technical_sha256": fingerprint(technical),
        "technical_status": technical["status"],
        "company_plan_sha256": fingerprint(company_plan),
        "company_database_complete": False,
        "report_generated": False,
        "source_snapshot_sha256": fingerprint(snapshot),
    }
    (root / "fixed-state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf8")
    return state


def report_inputs(snapshot, extension, prose, *, sealed_technical=None):
    """Require AI technical prose to bind the exact program package; otherwise use program text."""
    technical = build(snapshot) if sealed_technical is None else sealed_technical
    identity = fingerprint(technical)
    company = read(extension["company_snapshot"]) if extension.get("company_snapshot") else None
    if company and (
        company["stock_id"] != snapshot["instrument"]["stock_id"]
        or company["as_of"] != snapshot["as_of"]
    ):
        raise ValueError("Company research snapshot company/cutoff mismatch")
    effective = copy.deepcopy(prose or {})
    effective.setdefault("schema_version", 3)
    effective["stock_id"] = snapshot["instrument"]["stock_id"]
    effective["as_of"] = snapshot["as_of"]
    technical_note = effective.get("technical", {})
    bound = (
        isinstance(technical_note, dict)
        and technical_note.get("technical_package_sha256") == identity
    )
    if technical["status"] != "ready" or not bound:
        ready = technical["status"] == "ready"
        message = (
            "固定指標已由程式完成；本次未提供綁定該計算包的AI論述，僅列程式圖表與數值。"
            if ready
            else "未通過1000個官方交易日/套件驗收，停止技術指標、K線及技術推論。"
        )
        effective["technical"] = dict.fromkeys(
            (
                "position",
                "primary_claim",
                "quantified_evidence",
                "strongest_counterevidence",
                "confidence",
                "stronger_case",
                "disagreement",
                "evidence_change",
            ),
            message,
        )
        effective["technical"].update(
            paragraphs=[message],
            conditions=["取得足夠官方交易日及完整OHLCV，再執行固定引擎並綁定本次計算雜湊。"],
            technical_package_sha256=identity,
        )
    if company is None:
        message = "本次尚未提供制式公司資料快照；公司專屬基本面論述暫不採用，既有財務原件/數值驗收留在稽核版。"
        effective["fundamental"] = dict.fromkeys(
            (
                "position",
                "primary_claim",
                "quantified_evidence",
                "strongest_counterevidence",
                "confidence",
                "stronger_case",
                "disagreement",
                "evidence_change",
            ),
            message,
        )
        effective["fundamental"].update(
            paragraphs=[message],
            conditions=["依固定公司查閱計畫完成來源與事實匯入，再選定公司資料快照。"],
        )
    return (
        technical,
        company,
        effective,
        {
            "technical_sha256": identity,
            "technical_prose_bound": bound,
            "company_status": company["status"] if company else "missing_company_snapshot",
            "fixed_sop": True,
        },
    )
