"""Readable, offline report presentation. Does not change financial/forecast decisions."""

import base64
import hashlib
import html
import re
from datetime import datetime

from tradingagents.dataflows.taiwan.publications import TAIPEI

ROLES = ("fundamental", "technical", "institutional")
FIELDS = ("position", "stronger_case", "disagreement", "evidence_change", "conditions")
RESEARCH_FIELDS = ("primary_claim", "quantified_evidence", "strongest_counterevidence", "confidence")
ROLE_LABELS = {
    "fundamental": "基本面分析師",
    "technical": "技術面分析師",
    "institutional": "籌碼面分析師",
}


def normalize_notes(prose, stock, as_of):
    prose = prose or {}
    if not isinstance(prose, dict):
        raise ValueError("Analysts must be an object")
    if ("stock_id" in prose and prose["stock_id"] != stock) or (
        "as_of" in prose and prose["as_of"] != as_of
    ):
        raise ValueError("Analyst company/date metadata does not match the snapshot")
    structured = any(isinstance(prose.get(role), dict) for role in ROLES)
    if structured and (prose.get("stock_id") != stock or prose.get("as_of") != as_of):
        raise ValueError("Structured analyst notes require matching company and analysis date")
    notes, missing = {}, {}
    for role in ROLES:
        value = prose.get(role, [])
        if isinstance(value, list):
            value = {"paragraphs": value}
        if not isinstance(value, dict):
            raise ValueError("Analyst notes require paragraphs or a structured object")
        fields = (*FIELDS, *RESEARCH_FIELDS) if prose.get("schema_version", 1) >= 3 else FIELDS
        note = {
            field: value.get(field, [] if field in ("paragraphs", "conditions") else "")
            for field in (*fields, "paragraphs")
        }
        for field, text in note.items():
            if field in ("conditions", "paragraphs"):
                if not isinstance(text, list) or any(
                    not isinstance(p, str) or not p.strip() for p in text
                ):
                    raise ValueError(
                        "Analyst paragraphs and conditions must be nonempty text lists"
                    )
            elif not isinstance(text, str):
                raise ValueError("Analyst judgments must be text")
        notes[role] = note
        missing[role] = [field for field in (*fields, "paragraphs") if not note[field]]
    return notes, missing


def prepare_sources(review_input, stock):
    entries, assets = [], {}
    refs = {"M01": "evidence-market", "T01": "evidence-rules"}
    for key, doc in (review_input or {}).get("documents", {}).items():
        try:
            raw = base64.b64decode(doc["content_base64"], validate=True)
            sha = doc["sha256"]
            if hashlib.sha256(raw).hexdigest() != sha or doc.get("stock_id") != stock:
                continue
            if not doc.get("source_url", "").startswith("https://"):
                continue
        except (ValueError, KeyError, TypeError):
            continue
        # Archive non-PDF documents as text, so downloaded page scripts are never executed.
        path = "documents/" + sha + (".pdf" if raw.startswith(b"%PDF-") else ".txt")
        anchor = "source-" + hashlib.sha256(key.encode()).hexdigest()[:16]
        refs[key] = anchor
        if key in ("q1", "q2"):
            refs[{"q1": "F01", "q2": "F02"}[key]] = anchor
        locators = [
            packet["locator"]
            for packet in (review_input or {}).values()
            if isinstance(packet, dict)
            and key in packet.get("document_ids", [])
            and packet.get("locator")
        ]
        entries.append(
            {
                "key": key,
                "doc": doc,
                "path": path,
                "anchor": anchor,
                "locators": list(dict.fromkeys(locators)),
            }
        )
        assets[path] = raw
    return entries, refs, assets


def text(value, refs, unresolved):
    def citation(match):
        key = match[1]
        if key in refs:
            return '<a class="citation" href="#' + refs[key] + '">[' + key + "]</a>"
        unresolved.add(key)
        return '<span class="unbound-ref">[' + key + "：未綁定來源]</span>"

    return re.sub(r"\[([A-Za-z][A-Za-z0-9_-]*)\]", citation, html.escape(value))


def role_notes(role, note, refs, unresolved):
    out = (
        '<div class="analyst-position"><span class="eyebrow">分析師立場</span><p>'
        + text(note["position"] or "未提供獨立論述裁決；請參閱分項程式結果。", refs, unresolved)
        + "</p></div>"
    )
    for field, label in (
        ("primary_claim", "主要主張"),
        ("quantified_evidence", "量化證據"),
        ("strongest_counterevidence", "最強反證"),
        ("confidence", "信心及區間限制"),
        ("evidence_change", "新增證據改變了什麼"),
        ("stronger_case", "哪個論點較有說服力"),
        ("disagreement", "關鍵分歧"),
    ):
        if field in RESEARCH_FIELDS and field not in note:
            continue
        out += (
            '<div class="judgment"><h3>'
            + label
            + "</h3><p>"
            + text(note[field] or "尚未提供；不由模板代填結論。", refs, unresolved)
            + "</p></div>"
        )
    out += (
        '<div class="analyst-prose"><h3>完整論述</h3>'
        + "".join("<p>" + text(p, refs, unresolved) + "</p>" for p in note["paragraphs"])
        + "</div>"
    )
    out += (
        '<div class="conditions"><h3>改判條件</h3><ol>'
        + "".join("<li>" + text(p, refs, unresolved) + "</li>" for p in note["conditions"])
        + "</ol>"
    )
    if not note["conditions"]:
        out += "<p>尚未提供具體條件；不得視為已驗收研究。</p>"
    return out + "</div>"


def introduction(stock, as_of, generated_at, market_date):
    issued = datetime.fromisoformat(generated_at).astimezone(TAIPEI).isoformat(timespec="seconds")
    return (
        '<header class="report-header"><span class="eyebrow">TAIWAN TRADINGAGENTS · 三分析師研究</span><h1>'
        + html.escape(stock)
        + ' 研究報告</h1><dl class="report-dates"><div><dt>實際產生時間</dt><dd>'
        + html.escape(issued)
        + "</dd></div><div><dt>資料分析日</dt><dd>"
        + html.escape(as_of)
        + "</dd></div><div><dt>行情最後觀測</dt><dd>"
        + html.escape(market_date or "不可用")
        + '</dd></div></dl><p class="edition-note">產生時間不代表資料已刷新；各財務期間及來源可用時間另列。原封存預測與發布時間保持原值。</p></header>'
        + '<nav aria-label="報告導覽"><a href="#verdict">分項裁決</a>'
        + "".join('<a href="#analyst-' + role + '">' + ROLE_LABELS[role] + "</a>" for role in ROLES)
        + '<a href="#evidence">證據與方法</a></nav>'
    )


def sources_section(entries, review_input):
    out = '<section id="evidence"><h2>證據與方法</h2><p>來源連結指向封存原件；能下載與雜湊一致不等於財務數字已通過採用條件。是否採用以分項審查及截止時間為準。</p>'
    for entry in entries:
        doc = entry["doc"]
        out += (
            '<article class="source-entry" id="'
            + entry["anchor"]
            + '"><h3>'
            + html.escape(entry["key"])
            + '</h3><p><a href="'
            + entry["path"]
            + '">封存原件</a> · <a href="'
            + html.escape(doc["source_url"], quote=True)
            + '">原始來源</a></p><dl>'
        )
        for label, value in (
            ("公布／上傳時間", doc.get("published_at") or "未知：不得推定初次公布日"),
            ("捕獲時間", doc.get("captured_at", "未知")),
            ("版本", doc.get("revision_id", "未知")),
            ("SHA256", doc["sha256"]),
        ):
            out += "<div><dt>" + label + "</dt><dd>" + html.escape(str(value)) + "</dd></div>"
        out += (
            "</dl><p>原件定位："
            + html.escape("；".join(entry["locators"]) or "未提供頁碼定位")
            + "</p></article>"
        )
    out += '<article id="evidence-market"><h3>M01｜行情與法人觀測</h3><p><a href="snapshot.json">本次原始資料包</a> · <a href="research.json">計算與完整觀測視窗</a>。來源、更新時間及缺日狀態保留在資料包；還原價不等於股息再投資總報酬，法人視窗重疊不算獨立樣本。</p></article>'
    out += '<article id="evidence-rules"><h3>T01｜短線規則與長期估值分開</h3><p><a href="review.json">量價旗標、失效價及量測假設</a>。量測目標不是合理價；參考收盤風報比不是成交條件。候選須待下一實際開盤重算，規則不保證成本後優勢。</p></article>'
    out += (
        "<p>審查截止："
        + html.escape(str((review_input or {}).get("cutoff_at", "未提供")))
        + '。<a href="review-input.json">原件、抄錄、版本橋接與審查輸入</a>。本版由程式計算與編輯論述組成，未執行新獨立 Agent 辯論，不下單。</p></section>'
    )
    return out


CSS = """
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#eaf0f4;color:#23364b;font:16px/1.85 "Microsoft JhengHei",system-ui,sans-serif}main{max-width:1120px;margin:28px auto;background:#fff;padding:36px 44px;min-width:0}h1{font-size:32px;line-height:1.4;margin:12px 0 22px}h2{font-size:23px;border-bottom:2px solid #d5e0e9;padding-bottom:8px;margin:34px 0 18px}h3{font-size:17px;margin:16px 0 8px}p{margin:12px 0}a{color:#086b88;overflow-wrap:anywhere}a:focus-visible,summary:focus-visible,.scroll:focus-visible{outline:3px solid #bd6d17;outline-offset:3px}.report-header{border-top:7px solid #1c405c;padding-top:18px}.eyebrow{font-size:12px;color:#4c697e;letter-spacing:1px}.report-dates{display:grid;grid-template-columns:2fr 1fr 1fr;gap:16px;margin:0}.report-dates div{min-width:0}dt{font-size:12px;color:#506b80}dd{margin:0;overflow-wrap:anywhere}.edition-note{font-size:13px;color:#506575}nav{display:flex;gap:18px;flex-wrap:wrap;padding:16px 0;border-block:1px solid #d8e1e8;margin:22px 0}nav a{font-size:14px}.scroll{overflow-x:auto;max-width:100%;margin:18px 0;overscroll-behavior-x:contain}table{border-collapse:collapse;width:100%;font-size:13px;line-height:1.65;min-width:480px}td,th{padding:10px 12px;border-bottom:1px solid #dbe3e9;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#eff4f8}tbody tr:nth-child(even){background:#fafcfd}.analyst{margin:38px 0}.analyst-position{padding:18px 22px;background:#edf5f8;border-left:5px solid #26738c}.analyst-position p{font-size:18px;font-weight:600;margin:6px 0}.judgment{border-left:2px solid #d2dee7;padding-left:18px;margin:22px 0}.conditions{padding:18px 22px;background:#f5f6f1;border:1px solid #dce1d5;margin:24px 0}.conditions h3{margin-top:0}.conditions li{margin:10px 0;padding-left:4px}.citation{white-space:nowrap;font-size:.85em;margin-inline:3px}.unbound-ref{color:#93431c}.source-entry{border-bottom:1px solid #dbe3e9;padding:14px 0}.source-entry dl{font-size:12px}.source-entry dl div{display:grid;grid-template-columns:130px minmax(0,1fr);gap:12px}.machine-audit{margin-top:24px;padding:16px 0;border-top:1px solid #cad6df}summary{cursor:pointer;font-weight:600}pre{font-size:11px;white-space:pre-wrap;overflow-wrap:anywhere}.reading-gap{padding:12px;border:1px solid #d2a16a;color:#70431e;font-size:13px}footer{margin-top:28px;border-top:1px solid #dbe3e9;font-size:12px;color:#526c80}section,article{scroll-margin-top:16px}
@media(max-width:640px){main{padding:20px 16px;margin:0;width:100%}h1{font-size:26px}h2{font-size:21px}.report-dates{grid-template-columns:1fr}.analyst-position{padding:16px}.analyst-position p{font-size:17px}.conditions{padding:15px}.source-entry dl div{grid-template-columns:1fr;gap:0;margin:10px 0}nav{gap:12px}table{font-size:13px}ol{padding-left:24px}}
@page{size:A4;margin:14mm 12mm}
@media print{html{scroll-behavior:auto}body{background:white;color:#172b3a;font-size:10pt;line-height:1.65}main{max-width:none;width:auto;margin:0;padding:0}nav,.no-print{display:none!important}h1{font-size:22pt}h2{font-size:16pt}h3{font-size:11pt}h1,h2,h3{break-after:avoid}.analyst{break-before:page;margin-top:0}.scroll{overflow:visible;max-width:none}table{min-width:0!important;table-layout:fixed;font-size:8pt;width:100%}thead{display:table-header-group}tr{break-inside:avoid}td,th{padding:5px;white-space:normal}.analyst-position,.conditions,.judgment{background:white;border-left:2px solid #526b7b;padding:10px}.analyst-position p{font-size:12pt}.report-dates{grid-template-columns:2fr 1fr 1fr}.source-entry dl div{display:block}.source-entry dt{font-size:8pt}.source-entry dd{font-size:8pt}.machine-audit{display:none}a{color:inherit;text-decoration:underline}.report-header{border-top:3px solid #233b50}}
"""
