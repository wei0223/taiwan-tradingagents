"""Close bounded investigation tasks without promoting financial/PIT eligibility."""

import base64
import hashlib
from collections import Counter
from html import escape
from urllib.parse import urlparse

from .bundle import fingerprint
from .scenarios import timestamp
from .vintage import OFFICIAL_HOSTS

DISPOSITIONS = {"recoverable": "可補齊", "alternative_scope": "可採替代口徑",
                "unreconstructable_in_reviewed_scope": "無法重建（本輪公開範圍）"}


def audit(ledger, documents, evaluated_at, stock_id=None):
    """Validate source-bound decisions; this function never certifies EPS or a vintage.

    A reviewer must state a concrete search boundary and missing semantic fields.
    Network refusals alone cannot support a closed historical exclusion. Alternatives
    have their own period and may not replace the original missing metric.
    """
    if ledger.get("schema_version") != 1 or not ledger.get("reviewed_by"):
        raise ValueError("Named gap reviewer and schema version required")
    if timestamp(ledger["reviewed_at"]) > timestamp(evaluated_at):
        raise ValueError("Gap review is not yet available")
    items, seen = [], set()
    for item in ledger["items"]:
        key = item["id"]
        if key in seen:
            raise ValueError("Duplicate gap identity")
        seen.add(key)
        if stock_id is not None and item["stock_id"] != stock_id:
            raise ValueError("Gap company mismatch")
        if item.get("disposition") not in DISPOSITIONS:
            raise ValueError("Explicit gap disposition required")
        for field in ("target_document", "missing_fields", "search_scope", "finding",
                      "excluded_uses", "reopen_requirements", "evidence"):
            if not item.get(field):
                raise ValueError("Missing concrete gap field: " + field)
        checked = []
        for ref in item["evidence"]:
            doc = documents[ref["source_id"]]
            body = base64.b64decode(doc["content_base64"], validate=True)
            digest = hashlib.sha256(body).hexdigest()
            parsed = urlparse(doc["url"])
            if (not body or digest != ref["sha256"] or digest != doc["sha256"]
                    or parsed.scheme != "https" or parsed.hostname not in OFFICIAL_HOSTS
                    or parsed.username or parsed.password or parsed.port not in (None, 443)
                    or doc.get("stock_id") != item["stock_id"]
                    or timestamp(doc["captured_at"]) > timestamp(ledger["reviewed_at"])
                    or not ref.get("locator")):
                raise ValueError("Gap original identity/hash/time/locator invalid")
            checked.append({"source_id": ref["source_id"], "sha256": digest,
                            "url": doc["url"], "captured_at": doc["captured_at"],
                            "published_at": doc.get("published_at"), "locator": ref["locator"]})
        disposition = item["disposition"]
        if disposition == "alternative_scope":
            alt = item.get("alternative", {})
            if (alt.get("basis") != "direct_reported_annual_diluted_eps"
                    or alt.get("period") != "2025-FY" or not alt.get("reviewed_value")
                    or alt.get("source_id") not in {r["source_id"] for r in checked}
                    or alt.get("permitted_uses") != ["same_fiscal_year_annual_comparison"]
                    or not {"Q4_diluted_EPS", "rolling_TTM", "strict_historical_performance"}
                    .issubset(item["excluded_uses"])):
                raise ValueError("Annual alternative cannot fill Q4, TTM or PIT")
        if (disposition == "unreconstructable_in_reviewed_scope"
                and (item.get("finding_code") != "captured_official_lists_lack_event_version_binding"
                    or not item.get("event_id") or not item.get("candidate_filenames")
                    or "strict_historical_performance" not in item["excluded_uses"])):
            raise ValueError("Refusal or absent download alone cannot close historical proof")
        if disposition == "recoverable" and not item.get("next_action"):
            raise ValueError("Recoverable item needs a concrete action")
        items.append({**item, "evidence": checked, "disposition_label": DISPOSITIONS[disposition],
                      "work_item_status": "actionable" if disposition == "recoverable" else "closed_scoped",
                      "qualification_promoted": False, "original_missing_metric_available": False})
    return {"schema_version": 1, "reviewed_at": ledger["reviewed_at"],
            "reviewed_by": ledger["reviewed_by"], "ledger_sha256": fingerprint(ledger),
            "parent_ledger_sha256": ledger.get("parent_ledger_sha256"),
            "counts": {k: sum(r["disposition"] == k for r in items) for k in DISPOSITIONS},
            "work_states": dict(Counter(r["work_item_status"] for r in items)), "items": items,
            "qualifications_unchanged": True, "historical_performance_granted": False,
            "new_forecast_issued": False,
            "reopen_policy": "new_qualifying_official_evidence_then_new_review_version; never_elapsed_time_only"}


def presentation_groups(review):
    """Share identical restrictions only; keep each event and distinct criterion."""
    groups = {}
    for item in review["items"]:
        key = (item["disposition_label"], tuple(item["excluded_uses"]), tuple(item["reopen_requirements"]))
        groups.setdefault(key, []).append(item)
    return list(groups.values())


def render(review, table):
    if not review:
        return ""
    rows = [[r["id"], r["target_document"], "；".join(r["missing_fields"]),
             r["disposition_label"], r["finding"],
             "可用：" + "；".join(r.get("alternative", {}).get("permitted_uses", r.get("permitted_uses", [])))
             + "。排除：" + "；".join(r["excluded_uses"]),
             "；".join(r["reopen_requirements"])] for r in review["items"]]
    return ('<section id="gap-closure"><h2>缺口結案審查</h2><p>工單結案不代表缺失數值已補齊。'
            '年度替代僅供同年度比較；歷史對應無法重建只適用本輪查證範圍，不宣稱永久不存在。'
            '目前已審查事實繼續使用，歷史績效仍逐筆檢驗版本資格。</p>'
            + table(["工單", "所需文件", "仍缺欄位", "處置", "查證結論", "用途限制", "重新開案條件"], rows)
            + '<p>審查時間：' + escape(review["reviewed_at"])
            + '；<a href="gap-closure-review.json">證據、排除用途與重新開案條件</a>。'
            '新資料持續封存；新證據須另建審查版本，原預測不變。</p></section>')
