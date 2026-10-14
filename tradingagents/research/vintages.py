"""Immutable captured versions, replayed only after their actual archive receipt."""

from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from tradingagents.dataflows.taiwan.provider import FINANCIAL, REVENUE, day
from tradingagents.dataflows.taiwan.publications import TAIPEI, instant

from .bundle import build_bundle, canonical, fingerprint, reject_secret


def verify_receipt_times(value, captured):
    """Every retained source receipt must precede this archive receipt, including MOPS/calendar."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"retrieved_at", "captured_at"} and isinstance(item, str) and instant(item) > captured:
                raise ValueError("Source retrieval after archive receipt")
            verify_receipt_times(item, captured)
    elif isinstance(value, list):
        for item in value:
            verify_receipt_times(item, captured)


class VintageStore:
    def __init__(self, root, clock=None):
        self.root = Path(root)
        self.clock = clock or (lambda: datetime.now(UTC))

    def put(self, snapshot):
        import os

        reject_secret(snapshot, os.getenv("FINMIND_TOKEN", ""))
        build_bundle(snapshot)
        code = snapshot["instrument"]["stock_id"]
        if not re.fullmatch(r"[0-9]{4,6}", code):
            raise ValueError("Invalid archive stock code")
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("Archive clock requires timezone")
        verify_receipt_times(snapshot, now)
        snapshot_hash = fingerprint(snapshot)
        folder = self.root / code
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (snapshot_hash + ".json")
        if path.exists():
            saved = self.read(path)
            if saved["snapshot"] != snapshot:
                raise ValueError("Archive collision or tampering")
            return path
        receipt = {"schema_version": 1, "captured_at": now.astimezone(UTC).isoformat(),
                   "snapshot_sha256": snapshot_hash, "snapshot": snapshot}
        receipt["receipt_sha256"] = fingerprint(receipt)
        # Exclusive create: concurrent captures cannot overwrite an existing vintage.
        with path.open("x", encoding="utf-8") as handle:
            handle.write(canonical(receipt) + "\n")
        return path

    @staticmethod
    def read(path):
        receipt = json.loads(Path(path).read_text(encoding="utf-8"))
        supplied = receipt["receipt_sha256"]
        unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        if supplied != fingerprint(unsigned) or receipt["snapshot_sha256"] != fingerprint(receipt["snapshot"]):
            raise ValueError("Archived version changed")
        if Path(path).stem != receipt["snapshot_sha256"]:
            raise ValueError("Archive filename/content mismatch")
        verify_receipt_times(receipt["snapshot"], instant(receipt["captured_at"]))
        build_bundle(receipt["snapshot"])
        return receipt

    def select(self, stock_id, as_of):
        if not re.fullmatch(r"[0-9]{4,6}", stock_id):
            raise ValueError("Invalid archive stock code")
        cutoff = day(as_of)
        receipts = []
        for path in sorted((self.root / stock_id).glob("*.json")):
            receipt = self.read(path)  # Corruption is an error, never silently skipped.
            snapshot = receipt["snapshot"]
            if snapshot["instrument"]["stock_id"] != stock_id:
                raise ValueError("Archived company mismatch")
            captured = instant(receipt["captured_at"]).astimezone(TAIPEI).date()
            if captured <= cutoff and day(snapshot["as_of"]) <= cutoff:
                receipts.append(receipt)
        if not receipts:
            raise ValueError("No version was archived by this analysis date; cannot reconstruct past knowledge")
        chosen = max(receipts, key=lambda item: (instant(item["captured_at"]), item["snapshot_sha256"]))
        snapshot = copy.deepcopy(chosen["snapshot"])
        snapshot["as_of"] = as_of
        for group in (snapshot["datasets"], snapshot.get("extensions", {})):
            for dataset, result in group.items():
                selected = []
                for record in result["records"]:
                    observed = day(record["data_date"])
                    published = record.get("published_at")
                    available = record.get("available_at")
                    if observed > cutoff or (published and day(published) > cutoff) or (available and day(available) > cutoff):
                        continue
                    if not published and dataset not in (FINANCIAL, REVENUE) and observed >= cutoff:
                        continue
                    selected.append(record)
                result["records"] = selected
                result["as_of"] = as_of
                if not selected and not result.get("absence_verified"):
                    result["status"] = "unavailable"
        snapshot["vintage_archive"] = {"captured_at": chosen["captured_at"],
                                        "original_snapshot_sha256": chosen["snapshot_sha256"],
                                        "receipt_sha256": chosen["receipt_sha256"]}
        snapshot["limitations"].append("Replayed archived capture; no newer data substituted and historical listing identity remains unverified")
        build_bundle(snapshot)
        return snapshot
