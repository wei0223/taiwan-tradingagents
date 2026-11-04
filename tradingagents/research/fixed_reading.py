"""Present accepted research rulings without promoting financial eligibility."""
import base64
import hashlib
import html
import json
from pathlib import Path

AXES = {"accounting_earnings": "當期會計獲利", "cash_sustainability": "現金持續性", "valuation": "估值與長期價格", "short_term": "短期交易"}
VERDICTS = {"supported": "獲支持", "challenged": "受挑戰", "uncertain": "不確定", "unavailable": "不可用"}


def ruling_rows(data):
    rows = data.get("debate_review", {}).get("judgment", {}).get("rulings", [])
    return [[AXES[r["axis"]], VERDICTS[r["verdict"]], r["reason"]] for r in rows]


def debate_rows(data, material_only=False):
    debate = data.get("debate_review", {})
    openings, replies = debate.get("openings", {}), debate.get("replies", {})
    result = []
    for role, other, label in [("bull", "bear", "多方主張 → 空方反駁"), ("bear", "bull", "空方主張 → 多方反駁")]:
        responses = {r["claim_id"]: r for r in replies.get(other, {}).get("changes", [])}
        for claim in openings.get(role, {}).get("claims", []):
            response = responses.get(claim["id"])
            if response and (not material_only or response["changed_my_position"] or response.get("assessment") in ("dispute", "uncertain")):
                result.append([label, claim["claim"], response["reason"], "有改變立場" if response["changed_my_position"] else "未改變立場"])
    return result


def source_index(reference, destination):
    from tradingagents.dataflows.taiwan.source_store import SourceStore

    from .company_database import read
    company = read(reference)
    store = SourceStore(reference["source_store"])
    root = Path(destination)
    originals = root / "company-originals"
    originals.mkdir(exist_ok=True)
    links, documents = {}, {}
    for key, receipt_id in company["receipt_ids"].items():
        doc = store.receipt(receipt_id)
        documents[key] = {k: v for k, v in doc.items() if k != "content_base64"}
        raw = base64.b64decode(doc["content_base64"], validate=True)
        if hashlib.sha256(raw).hexdigest() != doc["sha256"]:
            raise ValueError("Company original hash mismatch")
        suffix = ".pdf" if raw.startswith(b"%PDF") else ".txt"
        path = originals / (doc["sha256"] + suffix)
        if not path.exists():
            path.write_bytes(raw)
        links[key] = "company-originals/" + path.name
    def esc(value):
        return html.escape(str(value))
    out = '<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>公司原件與數值索引</title><style>body{max-width:1100px;margin:30px auto;padding:0 18px;font:16px/1.65 sans-serif}article{border-top:1px solid #ccd;padding:18px 0}blockquote{white-space:pre-wrap;overflow-wrap:anywhere;margin:10px 0;background:#f4f7fa;padding:12px}code{overflow-wrap:anywhere}a{color:#17628c}@media print{article{break-inside:avoid}}</style><a href="report.html">回研究報告</a><h1>公司原件與已核事實</h1><p>來源、頁碼、期間、單位與原句逐筆綁定。公司陳述仍是歸屬敘述，不等於獨立確認；此索引不解除估值或歷史績效資格。</p>'
    for f in company["facts"]:
        doc = documents[f["source_id"]]
        link = links[f["source_id"]] + ("#page=" + str(f["page"]) if links[f["source_id"]].endswith(".pdf") else "")
        period = " / ".join(str(f.get(k) or "未適用") for k in ["period_start", "period_end", "basis", "scope", "unit"])
        out += '<article id="' + esc(f["id"]) + '"><h2>' + esc(f["id"]) + '</h2><p>' + esc(period) + '</p><p>已核值：' + esc(f.get("normalized_value")) + '｜欄位：' + esc(f["column_label"]) + '</p><p><a href="' + esc(link) + '">原件實體頁 ' + esc(f["page"]) + '</a> · <a href="' + esc(doc["source_url"]) + '">官方來源</a></p><p>公布：' + esc(doc.get("published_at") or "未知") + '｜捕獲：' + esc(doc["captured_at"]) + '</p><code>SHA256 ' + esc(doc["sha256"]) + '</code><blockquote>' + esc(f["quote"]) + '</blockquote></article>'
    (root / "company-evidence.html").write_text(out + '</html>', encoding="utf8")
    (root / "company-source-index.json").write_text(json.dumps(documents, ensure_ascii=False, indent=2), encoding="utf8")
    return links

