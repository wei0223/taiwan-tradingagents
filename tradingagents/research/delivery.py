"""Complete reproducibility bundle, rather than a PDF-only evidence dead end."""

import hashlib
import json
import os
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


def read_bytes(path):
    absolute = str(Path(path).resolve())
    if os.name == "nt" and not absolute.startswith("\\\\?\\"):
        absolute = "\\\\?\\" + absolute
    return Path(absolute).read_bytes()


def seal(directory):
    root = Path(directory)
    if not (root / "research.json").exists():
        raise ValueError("Research output required")
    portability = audit_package(root, pending_generated={"evidence.zip", "delivery-manifest.json"})
    if portability["status"] != "passed":
        raise ValueError("Offline package links/anchors failed: " + str(portability["errors"][:3]))
    (root / "portability.json").write_text(json.dumps(portability, indent=2) + "\n", "utf8")
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
    if audit_package(root)["status"] != "passed":
        raise ValueError("Final generated package is not portable")
    return manifest


def audit_package(directory, pending_generated=()):
    """Resolve every local HTML dependency in the copied package, without network."""
    from .quality import audit_html

    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.refs, self.ids = [], set()

        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key in {"href", "src"} and value:
                    self.refs.append(value)
                if key == "id":
                    self.ids.add(value)

    root = Path(directory).resolve()
    errors, count, pages = [], 0, {}
    for path in root.rglob("*.html"):
        text = read_bytes(path).decode("utf8")
        parser = Links()
        parser.feed(text)
        pages[path.resolve()] = parser
        for problem in audit_html(text)["errors"]:
            errors.append({"file": path.relative_to(root).as_posix(), "reason": problem})
    for path, parser in pages.items():
        for ref in parser.refs:
            link = urlsplit(ref)
            if link.scheme:
                continue
            target = (path.parent / unquote(link.path)).resolve() if link.path else path
            if not target.is_relative_to(root):
                errors.append({"file": path.name, "reason": "escaping_package", "link": ref})
                continue
            # The self-containing evidence.zip is created immediately after this
            # check; no other missing dependency is permitted.
            if target.name in pending_generated and target.parent == root:
                continue
            count += 1
            if not target.is_file():
                errors.append({"file": path.name, "reason": "missing_local_file", "link": ref})
            elif link.fragment and target in pages and unquote(link.fragment) not in pages[target].ids:
                errors.append({"file": path.name, "reason": "missing_local_anchor", "link": ref})
    return {"status": "failed" if errors else "passed", "errors": errors,
            "html_files": len(pages), "local_dependencies_checked": count,
            "external_network_verified": False, "scope": "packaged_HTML_links_and_anchors"}
