"""Readable, append-only outcome views; primary and revision diagnostics stay separate."""

import html
import json

from .bundle import fingerprint
from .publication import read_release
from .tracking import _path, read_tracking


def render_tracking(release_path, tracking_path, *, mock=False):
    release, tracking = read_release(release_path), read_tracking(tracking_path)
    if (tracking["release_sha256"] != release["release_sha256"]
            or tracking["original_issued_at"] != release["issued_at"]
            or tracking["predictions_sha256"] != fingerprint(release["predictions"])):
        raise ValueError("Tracking report must match the sealed release")
    if not mock and (tracking.get("evaluation_mode") == "mock_rehearsal_not_performance"
                     or any(r.get("evidence_kind") == "mock" for r in tracking["actuals"])):
        raise ValueError("Mock actuals require an explicitly labelled mock report")
    primary = tracking["primary_initial_vintage_results"]
    def key(row):
        return tuple(row[k] for k in ("stock_id", "period", "metric", "basis", "model_version"))
    resolved = {key(r): r for r in primary["resolved"]}
    rows = []
    for prediction in release["predictions"]:
        outcome = resolved.get(key(prediction))
        rows.append({
            **{k: prediction[k] for k in ("stock_id", "period", "metric", "basis", "model_version")},
            "prediction": prediction["value"],
            "actual": outcome["actual_value"] if outcome else None,
            "error_prediction_minus_actual": outcome["error"] if outcome else None,
            "absolute_error": outcome["absolute_error"] if outcome else None,
            "status": "已核對首次公告" if outcome else "待合格實績（未計誤差）",
        })
    risk = release.get("debate", {}).get("stages", {}).get("risk", {}).get("content", {})
    report = {
        "evaluation_mode": "mock_rehearsal_not_performance" if mock else "official_tracking",
        "release_sha256": release["release_sha256"],
        "tracking_sha256": tracking["tracking_sha256"],
        "original_issued_at": release["issued_at"],
        "checked_at": tracking["checked_at"],
        "rows": rows,
        "primary_metrics": primary,
        "selected_revision_diagnostics_not_primary": tracking["results"],
        "assumption_attribution": {"status": "unavailable", "reason":
            "總誤差不等於因果歸因；須另核對收入、成本費用、業外、稅率、歸母比例及稀釋股數的同期間實績與橋接。"},
        "original_revision_conditions": risk.get("observable_revision_conditions", []),
        "condition_evaluation": "待逐項核對新證據；未因誤差或模擬值自動改判。",
        "actuals_with_source_provenance": tracking["actuals"],
        "exclusions": {k: tracking[k] for k in (
            "primary_vintage_unverified", "excluded_already_public", "unavailable_time_metadata")},
    }
    def esc(value):
        return html.escape(str(value))
    banner = "MOCK 演練｜虛構實績，不是正式績效" if mock else "正式追蹤｜僅首次公告通過者列主評分"
    cells = "".join("<tr>" + "".join("<td>" + esc(r[k] if r[k] is not None else "不可用") + "</td>"
        for k in ("stock_id", "period", "metric", "basis", "prediction", "actual",
                  "error_prediction_minus_actual", "status")) + "</tr>" for r in rows)
    page = ('<!doctype html><html lang="zh-Hant"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>實績追蹤</title><style>body{font:16px/1.7 system-ui;max-width:1200px;margin:30px auto;padding:18px}'
        'table{border-collapse:collapse}td,th{border:1px solid #bbb;padding:8px}'
        '.table{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}'
        '.banner{background:#fff0ca;padding:16px;font-weight:bold}@media print{body{margin:0}}</style>'
        '<h1>實績追蹤報告</h1><p class="banner">' + banner + '</p><p>原發布時間：'
        + esc(release["issued_at"]) + '<br>追蹤檢查時間：' + esc(tracking["checked_at"])
        + '</p><p>誤差＝原預測－實績；正值表示高估。更正版本僅列診斷，不能取代首次公告主評分。'
        '未配對到合格實績時不計誤差；同季營收與 EPS 並非獨立樣本。</p><div class="table"><table>'
        '<tr><th>公司</th><th>期間</th><th>指標</th><th>口徑</th><th>原預測</th><th>合格實績</th>'
        '<th>誤差</th><th>狀態</th></tr>' + cells + '</table></div><h2>假設偏差</h2><p>'
        + esc(report["assumption_attribution"]["reason"]) + '</p><h2>原封存改判條件</h2><pre>'
        + esc(json.dumps(report["original_revision_conditions"], ensure_ascii=False, indent=2))
        + '</pre><p>' + report["condition_evaluation"] + '</p><details><summary>完整評分、來源、排除與更正診斷</summary><pre>'
        + esc(json.dumps(report, ensure_ascii=False, indent=2)) + '</pre></details></html>')
    return report, page


def write_tracking_report(release_path, tracking_path, *, mock=False):
    """Content-addressed sidecars, never overwrite a previous report or release."""
    report, page = render_tracking(release_path, tracking_path, mock=mock)
    stem = _path(tracking_path).parent / (fingerprint(report) + ".report")
    for suffix, content in ((".json", json.dumps(report, ensure_ascii=False, indent=2) + "\n"),
                            (".html", page)):
        path = _path(str(stem) + suffix)
        if path.exists():
            if path.read_text(encoding="utf-8") != content:
                raise ValueError("Existing tracking report differs")
        else:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
    return _path(str(stem) + ".html")
