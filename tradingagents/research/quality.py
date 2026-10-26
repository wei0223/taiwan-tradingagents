"""Fail-closed quality package built from original snapshots, never edited derived bundles."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urlsplit

from tradingagents.dataflows.taiwan.provider import FINANCIAL

from .bundle import build_bundle, fingerprint
from .scenarios import known_date


class PortableHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.links, self.errors = set(), [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "iframe", "object", "embed", "form"} or any(
            k.startswith("on") for k in attrs
        ):
            self.errors.append("Active content is not allowed")
        if "id" in attrs:
            if attrs["id"] in self.ids:
                self.errors.append("Duplicate anchor")
            self.ids.add(attrs["id"])
        for key in ("href", "src"):
            if key not in attrs:
                continue
            link = attrs[key]
            if link.startswith("#"):
                self.links.append(link[1:])
            elif (
                urlsplit(link).scheme not in {"https", ""}
                or link.startswith(("/", "\\"))
                or re.match(r"^[A-Za-z]:", link)
            ):
                self.errors.append("Nonportable or unsafe URL")
            elif urlsplit(link).scheme == "" and ".." in link.replace("\\", "/").split("/"):
                self.errors.append("Parent path traversal")


def audit_html(text):
    parser = PortableHTML()
    parser.feed(text)
    if re.search(r"file:/|(?<![A-Za-z0-9])[A-Za-z]:[\\\\/]", text, re.I):
        parser.errors.append("Local machine path in HTML; package relative evidence instead")
    missing = sorted(set(parser.links) - parser.ids)
    return {
        "status": "passed" if not parser.errors and not missing else "failed",
        "errors": parser.errors,
        "missing_anchors": missing,
        "network_reachability_checked": False,
    }


def source_audit(snapshot):
    bundle = build_bundle(snapshot)
    lineage, issues = [], []
    for section in (snapshot["datasets"], snapshot.get("extensions", {})):
        for dataset, result in section.items():
            seen = set()
            for row in result["records"]:
                identity = (
                    row.get("raw", {}).get("date", row["data_date"]),
                    row.get("raw", {}).get("type"),
                    row.get("raw", {}).get("name"),
                    row.get("values", {}).get("url"),
                )
                if identity in seen:
                    issues.append({"dataset": dataset, "issue": "duplicate eligible record"})
                seen.add(identity)
                basis = (
                    "vendor_period_unknown_excluded_from_quarter_metrics"
                    if dataset == FINANCIAL
                    else (
                        "as_reported_unknown_cumulative_basis"
                        if "CashFlows" in dataset
                        else "source_observation"
                    )
                )
                lineage.append(
                    {
                        "dataset": dataset,
                        "period": row["period"],
                        "data_date": row["data_date"],
                        "published_at": row.get("published_at"),
                        "available_at": row.get("available_at"),
                        "retrieved_at": row["retrieved_at"],
                        "source": row["source"],
                        "units": row["units"],
                        "basis": basis,
                        "raw_sha256": fingerprint(row["raw"]),
                        "publication_proof_sha256": fingerprint(row["publication_evidence"])
                        if row.get("publication_evidence")
                        else None,
                    }
                )
    return {
        "stock_id": bundle["stock_id"],
        "as_of": bundle["as_of"],
        "snapshot_sha256": fingerprint(snapshot),
        "bundle_sha256": fingerprint(bundle),
        "status": "passed" if not issues else "failed",
        "issues": issues,
        "lineage": lineage,
        "forecast_basis_status": "requires_original_document_review",
        "limitations": [
            "MOPS publication timestamps do not verify the vendor's quarter/YTD semantics or original numeric versions",
            "Source/units integrity is not semantic financial-statement verification",
        ],
    }


def industry_review(bundle, sources):
    """Comparable-company facts and industry hypotheses require distinct source classes."""
    allowed = {"company_filing", "management_statement", "external_estimate", "analyst_assumption"}
    accepted, rejected = [], []
    for item in sources:
        if item.get("classification") not in allowed:
            raise ValueError("Explicit source classification required")
        if not item.get("claim") or not item.get("transmission") or not item.get("invalidation"):
            raise ValueError("Claim, revenue/margin transmission and invalidation required")
        if not item.get("source_url", "").startswith("https://"):
            raise ValueError("Industry original source required")
        if not re.fullmatch(r"[0-9a-f]{64}", item.get("content_sha256", "")):
            raise ValueError("Captured source hash required")
        for field in ("published_at", "captured_at"):
            date.fromisoformat(item[field][:10])
        if max(
            known_date(item["published_at"]), known_date(item["captured_at"])
        ) > date.fromisoformat(bundle["as_of"]):
            rejected.append(
                {"source_url": item["source_url"], "reason": "not available by analysis date"}
            )
        else:
            accepted.append(item)
    return {
        "status": "available" if accepted else "unavailable",
        "sources": accepted,
        "excluded": rejected,
        "peer_comparison_status": "requires same-period, same-unit comparable companies",
        "required_dimensions": [
            "demand",
            "product_mix",
            "pricing",
            "capacity",
            "competition",
            "customer_certification",
        ],
        "missing_dimensions": [
            d
            for d in (
                "demand",
                "product_mix",
                "pricing",
                "capacity",
                "competition",
                "customer_certification",
            )
            if not any(s.get("dimension") == d for s in accepted)
        ],
    }


def content_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compare_peers(bundle, rows):
    """Only compare aligned observations; stock tickers alone do not establish comparability."""
    accepted, excluded, seen = [], [], set()
    for row in rows:
        key = (row["stock_id"], row["metric"], row["period"], row["basis"])
        if key in seen:
            raise ValueError("Duplicate peer observation")
        seen.add(key)
        if not row.get("comparability_rationale") or not row.get("source_url", "").startswith(
            "https://"
        ):
            raise ValueError("Peer selection and original source must be explained")
        if max(
            known_date(row["published_at"]), known_date(row["captured_at"])
        ) > date.fromisoformat(bundle["as_of"]):
            excluded.append({"stock_id": row["stock_id"], "reason": "future/unavailable vintage"})
            continue
        from .scenarios import finite

        finite(row["value"])
        accepted.append(row)
    groups = {}
    for row in accepted:
        key = f"{row['metric']}:{row['period']}:{row['basis']}:{row['unit']}"
        groups.setdefault(key, []).append(row)
    comparable = {
        key: value for key, value in groups.items() if len({r["stock_id"] for r in value}) >= 2
    }
    return {
        "status": "available" if comparable else "unavailable",
        "groups": comparable,
        "excluded": excluded,
        "limitations": [
            "Same unit/period does not by itself prove similar product mix or sustainable valuation"
        ],
    }
