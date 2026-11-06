"""Fixed company research plan and source-bound append-only fact snapshots."""

import base64
import hashlib
import html
import io
import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tradingagents.dataflows.taiwan.source_store import SourceStore

from .bundle import fingerprint
from .review import _end_of_day
from .scenarios import timestamp

SOP_VERSION = "company-research-v1"
MONEY_METRICS = {
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_expenses",
    "operating_profit",
    "nonoperating_profit",
    "pretax_profit",
    "tax",
    "consolidated_profit",
    "parent_profit",
    "cfo",
    "capex_ppe",
    "capex_intangible",
    "depreciation",
    "cash",
    "accounts_receivable",
    "inventory",
    "accounts_payable",
    "debt",
}
SHARE_METRICS = {
    "basic_weighted_shares",
    "diluted_weighted_shares",
    "issued_shares",
    "free_float_shares",
}
EPS_METRICS = {"basic_eps", "diluted_eps"}
STATEMENT_METRICS = {
    "issuer_identity",
    "product_mix",
    "customer_certification",
    "pricing",
    "capacity",
    "yield",
    "competition",
    "cash_funding",
    "capital_changes",
    "guidance",
}
BALANCE_METRICS = {
    "cash",
    "accounts_receivable",
    "inventory",
    "accounts_payable",
    "debt",
    "issued_shares",
    "free_float_shares",
}
SOURCE_CATEGORIES = (
    "annual_report",
    "financial_statement",
    "eps_note",
    "cashflow_statement",
    "investor_presentation",
    "monthly_revenue",
    "capital_event",
    "correction_index",
)
PROFILES = {
    "2330": ("foundry", [("2303", "adjacent_mature_foundry")]),
    "3017": ("thermal", [("3324", "primary_thermal"), ("3653", "adjacent_precision")]),
    "3231": ("odm", [("2382", "primary_odm")]),
}
QUESTIONS = {
    "foundry": ["製程/封裝營收組合", "利用率及產能", "資本支出與折舊", "客戶需求及指引"],
    "thermal": [
        "產品與應用分類分開",
        "液冷ASP/毛利及認證證據",
        "擴產利用率/良率",
        "整合能力及競爭",
    ],
    "odm": ["AI/其他出貨與價值", "ODM費用/毛利", "付款條件及營運資金", "融資與股數"],
    "generic": ["需求與產品組合", "定價與成本", "產能與競爭", "現金及股數"],
}


REQUIRED_RESEARCH = {
    "foundry": [
        ("process_mix", "先進製程、封裝與利用率如何影響毛利？",
         "製程組合與利用率→營收及毛利率", ["investor_presentation", "financial_statement"]),
        ("fab_cost", "資本支出、折舊與指引能否支持獲利延續？",
         "設備與折舊→毛利、現金", ["cashflow_statement", "investor_presentation"]),
    ],
    "odm": [
        ("server_mix", "伺服器出貨與產品組合是否改善 ODM 營益？",
         "出貨與組合→收入及營益率", ["investor_presentation", "financial_statement"]),
        ("working_capital", "擴張如何影響回款、融資與稀釋？",
         "應收、存貨、付款及股數→現金與EPS", ["cashflow_statement", "eps_note", "capital_event"]),
    ],
    "thermal": [
        ("product_economics", "拆伺服器應用與液冷產品，哪些量、價、毛利有原件？",
         "出貨、單價與組合→收入與毛利", ["investor_presentation", "annual_report"]),
        ("capacity_cost", "擴產、利用率及折舊如何影響獲利？有何反證？",
         "利用率與折舊→毛利率、費用率及現金", ["financial_statement", "cashflow_statement"]),
    ],
    "generic": [
        ("segment_profit", "依公司揭露辨識各事業、轉投資與非控制權益，哪一項解釋營益和歸母的差距？",
         "分部營收與成本、業外與非控制權益→歸母及EPS", ["annual_report", "financial_statement"]),
        ("cash_quality", "獲利改善能否支應同期設備支出？最強反證與待核欄位為何？",
         "營運資金、設備與融資→現金持續性", ["cashflow_statement", "financial_statement"]),
    ],
}


def required_research(profile):
    return [
        {"id": key, "question": question, "financial_transmission": bridge, "required_sources": categories}
        for key, question, bridge, categories in REQUIRED_RESEARCH.get(profile, REQUIRED_RESEARCH["generic"])
    ]


def plan(stock, as_of, exchange=None):
    if not re.fullmatch(r"\d{4,6}", stock):
        raise ValueError("Invalid stock")
    cutoff = date.fromisoformat(as_of)
    if exchange not in (None, "twse", "tpex"):
        raise ValueError("Invalid market")
    profile, candidates = PROFILES.get(stock, ("generic", []))
    quarter_index = cutoff.year * 4 + (cutoff.month - 1) // 3
    quarters = [f"{q // 4}-Q{q % 4 + 1}" for q in range(quarter_index - 8, quarter_index)]
    month_index = cutoff.year * 12 + cutoff.month - 1
    months = [f"{m // 12:04d}-{m % 12 + 1:02d}" for m in range(month_index - 60, month_index)]
    return {
        "sop_version": SOP_VERSION,
        "stock_id": stock,
        "as_of": as_of,
        "exchange": exchange,
        "annual_years": list(range(cutoff.year - 3, cutoff.year)),
        "financial_quarters": quarters,
        "revenue_months": months,
        "presentation_scope": {
            "start": (cutoff - timedelta(days=550)).isoformat(),
            "end": as_of,
            "select_latest_available": 4,
        },
        "source_categories": list(SOURCE_CATEGORIES),
        "profile": profile,
        "questions": QUESTIONS[profile],
        "required_research": required_research(profile),
        "numeric_metrics": sorted(
            MONEY_METRICS | SHARE_METRICS | EPS_METRICS | {"product_mix_pct", "capacity_units"}
        ),
        "statement_metrics": sorted(STATEMENT_METRICS),
        "peer_candidates": [
            {"stock_id": code, "classification": kind, "selection_requires_source_review": True}
            for code, kind in candidates
        ],
        "source_order": [
            "MOPS/TWSE/TPEx originals",
            "issuer IR originals",
            "official peer originals",
            "announcement index cross-check only",
        ],
        "fact_contract": [
            "id",
            "metric",
            "period_start",
            "period_end",
            "basis",
            "scope",
            "unit",
            "raw_value",
            "multiplier",
            "source_id",
            "page",
            "quote",
            "column_label",
            "context_quotes",
            "reviewer",
            "reviewed_at",
        ],
        "technical_policy": {
            "engine_version": "taiwan-technical-v1",
            "minimum_complete_sessions": 1000,
        },
        "missing_policy": "pending/not_published/unavailable/not_applicable need reason; never zero-fill",
        "publications_not_assumed": True,
        "create_monitor": False,
        "publish_forecasts": False,
    }


def _text(raw, page, encoding="utf-8-sig"):
    if raw.startswith(b"%PDF"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        if (
            not isinstance(page, int)
            or isinstance(page, bool)
            or not 1 <= page <= len(reader.pages)
        ):
            raise ValueError("Invalid PDF page")
        return reader.pages[page - 1].extract_text()
    if page != 1:
        raise ValueError("Non-PDF sources use page 1")
    if encoding not in ("utf-8", "utf-8-sig", "big5", "cp950"):
        raise ValueError("Explicit supported source encoding required")
    text = raw.decode(encoding)
    # HTML tags are never executed; citations bind the original text bytes conservatively.
    return html.unescape(re.sub(r"<[^>]*>", " ", text))


def _normal(text):
    return re.sub(r"\s+", "", str(text))


def validate(packet):
    stock = packet["stock_id"]
    as_of = packet["as_of"]
    plan(stock, as_of, packet.get("exchange"))
    cutoff = _end_of_day(as_of)
    sources = packet.get("sources", {})
    texts = {}
    document_status = {}
    for key, doc in sources.items():
        if doc.get("stock_id") != stock:
            raise ValueError("Company source identity mismatch")
        if doc.get("category") not in SOURCE_CATEGORIES or not doc.get("revision_id"):
            raise ValueError("Source category/version required")
        if not doc.get("source_url", "").startswith("https://"):
            raise ValueError("Official source URL required")
        raw = base64.b64decode(doc["content_base64"], validate=True)
        if hashlib.sha256(raw).hexdigest() != doc["sha256"]:
            raise ValueError("Company original hash mismatch")
        captured = timestamp(doc["captured_at"])
        published = doc.get("published_at")
        published_time = (
            _end_of_day(published)
            if published and len(published) == 10
            else timestamp(published)
            if published
            else captured
        )
        if max(captured, published_time) > cutoff:
            raise ValueError("Company source after cutoff")
        document_status[key] = {
            "sha256": doc["sha256"],
            "captured_at": doc["captured_at"],
            "published_at": published,
            "category": doc["category"],
            "revision_id": doc["revision_id"],
            "available_from": max(captured, published_time).isoformat(),
            "historical_initial_proven": False,
        }
        texts[key] = raw
    facts = []
    seen = set()
    for candidate in packet.get("facts", []):
        row = dict(candidate)
        identity = row["id"]
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]+", identity):
            raise ValueError("Stable fact ID required")
        if identity in seen:
            raise ValueError("Duplicate company fact ID")
        seen.add(identity)
        key = row["source_id"]
        if key not in sources:
            raise ValueError("Unbound company fact")
        if not row.get("reviewer") or not row.get("column_label") or not row.get("context_quotes"):
            raise ValueError("Named review and exact table column/context required")
        reviewed = timestamp(row["reviewed_at"])
        if reviewed < timestamp(sources[key]["captured_at"]):
            raise ValueError("Review before original capture")
        original = _normal(
            _text(texts[key], row["page"], sources[key].get("encoding", "utf-8-sig"))
        )
        anchors = [row["quote"], row["column_label"], *row["context_quotes"]]
        if any(not _normal(q) or _normal(q) not in original for q in anchors):
            raise ValueError("Fact quote/column/context absent from original page")
        if row.get("kind") == "company_statement":
            if row.get("metric") not in STATEMENT_METRICS:
                raise ValueError("Unregistered statement metric")
            row["normalized_value"] = None
            row["qualification"] = "attributed_statement_not_independent_confirmation"
        elif row.get("kind") == "numeric":
            metric = row.get("metric")
            if metric not in MONEY_METRICS | SHARE_METRICS | EPS_METRICS | {
                "product_mix_pct",
                "capacity_units",
            }:
                raise ValueError("Unregistered numeric metric")
            if row.get("scope") not in ("consolidated", "issuer", "individual"):
                raise ValueError("Explicit statement scope required")
            if row.get("basis") not in ("single_quarter", "ytd", "annual", "month", "balance"):
                raise ValueError("Explicit period basis required")
            start, end = (
                date.fromisoformat(row["period_start"]),
                date.fromisoformat(row["period_end"]),
            )
            if start > end or end > date.fromisoformat(as_of):
                raise ValueError("Fact period mismatch")
            if (metric in BALANCE_METRICS) != (row["basis"] == "balance"):
                raise ValueError("Balance and period amounts use separate paths")
            if row["basis"] == "balance" and start != end:
                raise ValueError("Balance uses one point date")
            if row["basis"] == "single_quarter":
                import calendar

                if (
                    start.day != 1
                    or start.month not in (1, 4, 7, 10)
                    or end.year != start.year
                    or end.month != start.month + 2
                    or end.day != calendar.monthrange(end.year, end.month)[1]
                ):
                    raise ValueError("Single-quarter boundaries required")
            if row["basis"] == "ytd" and (start.month, start.day) != (1, 1):
                raise ValueError("Cumulative start required")
            if row["basis"] == "annual" and (
                start.month,
                start.day,
                end.year,
                end.month,
                end.day,
            ) != (1, 1, start.year, 12, 31):
                raise ValueError("Annual boundaries required")
            if row["basis"] == "month":
                import calendar

                if start.day != 1 or (end.year, end.month, end.day) != (
                    start.year,
                    start.month,
                    calendar.monthrange(start.year, start.month)[1],
                ):
                    raise ValueError("Monthly boundaries required")
            if row.get("metric") in (
                "diluted_eps",
                "basic_eps",
                "diluted_weighted_shares",
                "basic_weighted_shares",
            ):
                if row.get("derivation", "direct") != "direct":
                    raise ValueError("EPS/weighted shares must not be differenced")
                if "diluted" in row["metric"] and row.get("eps_kind") != "diluted":
                    raise ValueError("Diluted field requires explicit diluted note")
                if "basic" in row["metric"] and row.get("eps_kind") != "basic":
                    raise ValueError("Basic and diluted fields separate")
                context = " ".join(row["context_quotes"]).lower()
                if (
                    "diluted" in row["metric"]
                    and "稀釋" not in context
                    and "diluted" not in context
                ):
                    raise ValueError("Diluted disclosure text required")
                if "basic" in row["metric"] and "基本" not in context and "basic" not in context:
                    raise ValueError("Basic disclosure text required")
            if row.get("derivation", "direct") != "direct":
                raise ValueError("Derived values use calculation packets, not raw fact ingestion")
            raw_text = str(row["raw_value"])
            if _normal(raw_text) not in _normal(row["quote"]):
                raise ValueError("Numeric transcription absent from quote")
            try:
                number = Decimal(raw_text.replace(",", "").replace("(", "-").replace(")", ""))
                multiplier = Decimal(str(row["multiplier"]))
            except InvalidOperation:
                raise ValueError("Invalid decimal transcription") from None
            if not number.is_finite() or multiplier not in (1, 1000, 1000000, 100000000):
                raise ValueError("Invalid unit multiplier")
            if row.get("unit") not in ("TWD", "shares", "TWD/share", "percent", "units"):
                raise ValueError("Normalized unit required")
            expected = (
                "TWD"
                if metric in MONEY_METRICS
                else "shares"
                if metric in SHARE_METRICS
                else "TWD/share"
                if metric in EPS_METRICS
                else "percent"
                if metric == "product_mix_pct"
                else "units"
            )
            if row["unit"] != expected:
                raise ValueError("Metric/unit mismatch")
            if row["unit"] == "TWD/share" and multiplier != 1:
                raise ValueError("EPS cannot inherit statement thousands multiplier")
            if row["unit"] == "percent" and (multiplier != 1 or not 0 <= number <= 100):
                raise ValueError("Product mix percentage must be in 0..100")
            row["normalized_value"] = str(number * multiplier)
            row["qualification"] = "source_quote_bound_numeric; semantic_review_required"
            row["permitted_uses"] = (
                ["annual_comparison_only"]
                if metric in EPS_METRICS and row["basis"] == "annual"
                else ["declared_period_comparison_only"]
            )
        else:
            raise ValueError(
                "Facts must be numeric or attributed statements; assumptions are separate"
            )
        row["source_sha256"] = sources[key]["sha256"]
        row["available_from"] = document_status[key]["available_from"]
        row["semantic_review_note"] = (
            "Program verifies quote binding and declared scope; it cannot prove the selected column meaning or reviewer honesty"
        )
        facts.append(row)
    vintages = facts
    selection = packet.get("selected_fact_ids", [f["id"] for f in facts])
    if len(set(selection)) != len(selection) or any(key not in seen for key in selection):
        raise ValueError("Invalid fact selection")
    facts = [f for f in facts if f["id"] in selection]
    signatures = [
        (
            f["metric"],
            f.get("period_start"),
            f.get("period_end"),
            f.get("basis"),
            f.get("scope"),
            f.get("unit"),
        )
        for f in facts
        if f["kind"] == "numeric"
    ]
    if len(set(signatures)) != len(signatures):
        raise ValueError("Competing fact vintages require explicit selection")
    selected_ids = set(selection)
    assumptions = packet.get("assumptions", [])
    for row in assumptions:
        if (
            not row.get("rationale")
            or not row.get("uncertainty")
            or not row.get("evidence_fact_ids")
            or any(k not in selected_ids for k in row["evidence_fact_ids"])
        ):
            raise ValueError("Assumption needs selected fact basis and uncertainty")
    coverage = {
        category: {
            "status": "original_available"
            if any(d["category"] == category for d in sources.values())
            else "missing",
            "numeric_reviewed_facts": sum(
                sources[f["source_id"]]["category"] == category and f["kind"] == "numeric"
                for f in facts
            ),
        }
        for category in SOURCE_CATEGORIES
    }
    gaps = packet.get("gaps", [])
    for gap in gaps:
        if gap.get("status") not in (
            "pending",
            "not_published",
            "unavailable",
            "not_applicable",
        ) or not gap.get("reason"):
            raise ValueError("Gap status/reason required")
    return {
        "sop_version": SOP_VERSION,
        "stock_id": stock,
        "as_of": as_of,
        "exchange": packet.get("exchange"),
        "sources": document_status,
        "facts": facts,
        "fact_vintages": vintages,
        "selected_fact_ids": selection,
        "assumptions": assumptions,
        "gaps": gaps,
        "coverage": coverage,
        "status": "structured_with_gaps"
        if any(v["status"] == "missing" for v in coverage.values())
        else "structured_sources_available",
        "strict_pit_ready": False,
        "financial_qualification_automatically_promoted": False,
        "semantic_review_automated": False,
    }


def append(packet, root, clock=None):
    """Use the existing immutable source store; facts and assumptions get their own snapshot."""
    data = validate(packet)
    store = SourceStore(root)
    now = (clock or (lambda: datetime.now(UTC)))()
    if now.tzinfo is None:
        raise ValueError("Timezone required")
    if date.fromisoformat(data["as_of"]) > (now.astimezone(UTC) + timedelta(hours=8)).date():
        raise ValueError("Future company cutoff")
    if any(timestamp(f["reviewed_at"]) > now for f in data["facts"]):
        raise ValueError("Review timestamp in future")
    receipts = {key: store.capture(doc) for key, doc in packet.get("sources", {}).items()}
    data.update(receipt_ids=receipts, created_at=now.astimezone(UTC).isoformat())
    key = fingerprint(data)
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    path = store._object("company-snapshots", key, raw)
    # Explicit snapshot references are authoritative; updating a current pointer is unnecessary.
    return {
        "snapshot_sha256": key,
        "stock_id": data["stock_id"],
        "as_of": data["as_of"],
        "snapshot_path": str(path),
        "source_store": str(store.root),
        "status": data["status"],
    }


def read(reference):
    if isinstance(reference, (str, Path)):
        location = Path(reference).resolve()
        reference = json.loads(location.read_text("utf8"))
        if not Path(reference["source_store"]).is_absolute():
            reference["source_store"] = str(location.parent / reference["source_store"])
    store = SourceStore(reference["source_store"])
    key = reference["snapshot_sha256"]
    if not re.fullmatch(r"[a-f0-9]{64}", key):
        raise ValueError("Invalid company snapshot identity")
    data = json.loads((store.root / "company-snapshots" / (key + ".json")).read_text("utf8"))
    if (
        fingerprint(data) != key
        or data["stock_id"] != reference["stock_id"]
        or data["as_of"] != reference["as_of"]
    ):
        raise ValueError("Company snapshot changed/mismatched")
    for source_key, receipt in data["receipt_ids"].items():
        doc = store.receipt(receipt)
        if doc["sha256"] != data["sources"][source_key]["sha256"]:
            raise ValueError("Source revision changed")
    return data


def export(reference, destination):
    data = read(reference)
    origin = SourceStore(reference["source_store"])
    target = SourceStore(destination)
    for key in data["receipt_ids"].values():
        if target.capture(origin.receipt(key)) != key:
            raise ValueError("Company export receipt changed")
    key = reference["snapshot_sha256"]
    raw = (origin.root / "company-snapshots" / (key + ".json")).read_bytes()
    target._object("company-snapshots", key, raw)
    return {
        **reference,
        "source_store": "company-database",
        "snapshot_path": "company-database/company-snapshots/" + key + ".json",
    }


def latest(root, stock, as_of):
    plan(stock, as_of)
    store = SourceStore(root)
    candidates = []
    for path in (store.root / "company-snapshots").glob("*.json"):
        item = json.loads(path.read_text("utf8"))
        if fingerprint(item) != path.stem:
            raise ValueError("Company database object changed")
        if item["stock_id"] == stock and item["as_of"] <= as_of:
            candidates.append((item["as_of"], item["created_at"], path.stem))
    if not candidates:
        return {"status": "not_found", "stock_id": stock, "as_of": as_of}
    candidates.sort()
    chosen = candidates[-1]
    if sum(c[:2] == chosen[:2] for c in candidates) > 1:
        raise ValueError("Ambiguous company versions; select snapshot explicitly")
    reference = {
        "stock_id": stock,
        "as_of": chosen[0],
        "source_store": str(store.root),
        "snapshot_sha256": chosen[2],
    }
    read(reference)
    return {**reference, "status": "selected_previous_snapshot_not_proof_of_current_freshness"}
