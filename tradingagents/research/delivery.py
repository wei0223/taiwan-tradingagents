"""Complete reproducibility bundle, rather than a PDF-only evidence dead end."""

import hashlib
import json
import os
import zipfile
from pathlib import Path


def read_bytes(path):
    absolute = str(Path(path).resolve())
    if os.name == "nt" and not absolute.startswith("\\\\?\\"):
        absolute = "\\\\?\\" + absolute
    return Path(absolute).read_bytes()


def seal(directory):
    root = Path(directory)
    if not (root / "research.json").exists():
        raise ValueError("Research output required")
    files = sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.relative_to(root).as_posix() not in {"evidence.zip", "delivery-manifest.json"}
    )
    hashes = {
        p.relative_to(root).as_posix(): hashlib.sha256(read_bytes(p)).hexdigest() for p in files
    }
    manifest = {
        "algorithm": "sha256",
        "files": hashes,
        "scope": "all packaged sources, calculations, analyst views, model/debate inputs and checks",
        "excludes": ["delivery-manifest.json (self)", "evidence.zip (container)"],
    }
    (root / "delivery-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with zipfile.ZipFile(root / "evidence.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for p in [*files, root / "delivery-manifest.json"]:
            archive.writestr(p.relative_to(root).as_posix(), read_bytes(p))
    with zipfile.ZipFile(root / "evidence.zip") as archive:
        if archive.testzip() is not None:
            raise ValueError("Corrupt delivery archive")
        for name, digest in hashes.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError("Delivery hash mismatch")
    return manifest
