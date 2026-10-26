"""Content-addressed public captures and append-only company version snapshots."""

import base64
import copy
import hashlib
import json
import os
import re
import shutil
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from tradingagents.research.bundle import fingerprint
from tradingagents.research.scenarios import timestamp


class SourceStore:
    def __init__(self, root):
        self.root = self.path(root)

    def recent_refusals(self, now):
        """Carry server restrictions across runs; do not reset them on restart."""
        current, result = timestamp(now.isoformat()), {}
        for path in (self.root / "receipts").glob("*.json"):
            data = json.loads(path.read_text("utf8"))
            if fingerprint(data) != path.stem:
                raise ValueError("Receipt metadata changed")
            if data.get("http_status") not in {403, 429} and data.get("status") != "source_rejected":
                continue
            captured = timestamp(data["captured_at"])
            until = captured + timedelta(days=1)
            if captured <= current < until:
                host = urlsplit(data["source_url"]).netloc
                result[host] = max(result.get(host, until.isoformat()), until.isoformat())
        return result

    @staticmethod
    def path(value):
        path = Path(value).resolve()
        return (
            Path("\\\\?\\" + str(path))
            if os.name == "nt" and not str(path).startswith("\\\\?\\")
            else path
        )

    def _object(self, kind, key, raw):
        if not re.fullmatch(r"[a-f0-9]{64}", key):
            raise ValueError("Invalid content identity")
        target = self.root / kind / (key + (".bin" if kind == "blobs" else ".json"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != raw:
                raise ValueError("Archived object changed")
        else:
            with target.open("xb") as handle:
                handle.write(raw)
        return target

    def capture(self, receipt):
        data = dict(receipt)
        if "content_base64" in data:
            raw = base64.b64decode(data.pop("content_base64"), validate=True)
            sha = hashlib.sha256(raw).hexdigest()
            if sha != data.get("sha256"):
                raise ValueError("Capture bytes changed")
            self._object("blobs", sha, raw)
            data["blob_sha256"] = sha
        timestamp(data["captured_at"])
        key = fingerprint(data)
        self._object(
            "receipts",
            key,
            json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
        )
        return key

    def receipt(self, key):
        if not re.fullmatch(r"[a-f0-9]{64}", key):
            raise ValueError("Invalid receipt reference")
        data = json.loads((self.root / "receipts" / (key + ".json")).read_text(encoding="utf8"))
        if fingerprint(data) != key:
            raise ValueError("Receipt metadata changed")
        if data.get("blob_sha256"):
            raw = (self.root / "blobs" / (data["blob_sha256"] + ".bin")).read_bytes()
            if hashlib.sha256(raw).hexdigest() != data["blob_sha256"]:
                raise ValueError("Archived original changed")
            data["content_base64"] = base64.b64encode(raw).decode()
        return data

    def read_snapshot(self, key):
        if not re.fullmatch(r"[a-f0-9]{64}", key):
            raise ValueError("Invalid snapshot reference")
        data = json.loads((self.root / "snapshots" / (key + ".json")).read_text(encoding="utf8"))
        if fingerprint(data) != key:
            raise ValueError("Snapshot changed")
        for resource in data["resources"]:
            hashes = set()
            for receipt_id in resource.get("receipt_ids", []):
                receipt = self.receipt(receipt_id)
                hashes.add(receipt.get("sha256"))
                if timestamp(receipt["captured_at"]) > timestamp(data["captured_at"]):
                    raise ValueError("Receipt after snapshot")
            if (
                resource.get("status") == "verified_current"
                and resource.get("content_sha256") not in hashes
            ):
                raise ValueError("Resource is not bound to captured original")
        return data

    def append(self, stock, plan, resources, captured_at, event_kind="source_capture"):
        if not re.fullmatch(r"\d{4,6}", stock):
            raise ValueError("Invalid company")
        pointer = self.root / "pointers" / (stock + ".json")
        pointer.parent.mkdir(parents=True, exist_ok=True)
        lock = pointer.with_suffix(".lock")
        with lock.open("x"):
            pass
        try:
            previous_id = (
                json.loads(pointer.read_text(encoding="utf8"))["snapshot_sha256"]
                if pointer.exists()
                else None
            )
            previous = self.read_snapshot(previous_id) if previous_id else None
            if previous and (
                previous["stock_id"] != stock
                or timestamp(previous["captured_at"]) > timestamp(captured_at)
            ):
                raise ValueError("Snapshot company or chronology mismatch")
            resources = copy.deepcopy(resources)
            prior = {r["key"]: r for r in previous["resources"]} if previous else {}
            if len({r["key"] for r in resources}) != len(resources):
                raise ValueError("Duplicate resource identity")
            changes = []
            for resource in resources:
                for receipt_id in resource.get("receipt_ids", []):
                    receipt = self.receipt(receipt_id)
                    if timestamp(receipt["captured_at"]) > timestamp(captured_at):
                        raise ValueError("Receipt captured after snapshot")
                old = prior.get(resource["key"])
                base = (
                    old
                    if old and old.get("status") == "verified_current"
                    else (old or {}).get("last_verified_version")
                )
                comparable = (
                    resource.get("content_sha256") is not None
                    and resource.get("status") == "verified_current"
                )
                change = (
                    "not_comparable_source_unavailable"
                    if not comparable
                    else "first_observed"
                    if not base
                    else "unchanged"
                    if base["content_sha256"] == resource["content_sha256"]
                    else "content_changed_not_necessarily_official_correction"
                )
                resource["first_seen_at"] = (
                    base.get("first_seen_at")
                    if base
                    and comparable
                    and base.get("content_sha256") == resource.get("content_sha256")
                    else captured_at
                    if comparable
                    else None
                )
                if not comparable and base:
                    resource["last_verified_version"] = base
                changes.append(
                    {
                        "key": resource["key"],
                        "change": change,
                        "previous_sha256": base.get("content_sha256") if base else None,
                        "current_sha256": resource.get("content_sha256"),
                    }
                )
            # Removed plan resources are scope changes, never proof of deletion.
            for key in prior.keys() - {r["key"] for r in resources}:
                changes.append({"key": key, "change": "not_observed_in_current_scope_not_deletion"})
            data = {
                "stock_id": stock,
                "captured_at": captured_at,
                "plan": plan,
                "plan_sha256": fingerprint(plan),
                "previous_snapshot_sha256": previous_id,
                "resources": resources,
                "changes": changes,
                "initial_historical_vintage_proven": False,
                "event_kind": event_kind,
            }
            key = fingerprint(data)
            self._object(
                "snapshots",
                key,
                json.dumps(
                    data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode(),
            )
            temporary = pointer.with_suffix(".tmp")
            temporary.write_text(json.dumps({"snapshot_sha256": key}), encoding="utf8")
            temporary.replace(pointer)
            return key
        finally:
            lock.unlink()

    def export(self, snapshot_id, target):
        """Portable current company chain; preserve prior snapshots and their bytes."""
        target = self.path(target)
        target.mkdir(parents=True, exist_ok=False)
        visited = set()

        def copy_snapshot(key):
            if key in visited:
                return
            visited.add(key)
            data = self.read_snapshot(key)
            keys = {i for r in data["resources"] for i in r.get("receipt_ids", [])}
            paths = [self.root / "snapshots" / (key + ".json")]
            for receipt_id in keys:
                receipt = self.receipt(receipt_id)
                paths.append(self.root / "receipts" / (receipt_id + ".json"))
                if receipt.get("blob_sha256"):
                    paths.append(self.root / "blobs" / (receipt["blob_sha256"] + ".bin"))
            for source in paths:
                destination = target / source.relative_to(self.root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    shutil.copyfile(source, destination)
            if data["previous_snapshot_sha256"]:
                copy_snapshot(data["previous_snapshot_sha256"])

        copy_snapshot(snapshot_id)
