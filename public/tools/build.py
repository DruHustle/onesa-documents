"""Build a public-only static document root from the approved release and public sources."""

import hashlib
import html
import io
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def build(output):
    release = json.loads((ROOT / "release.json").read_text())
    baseline = io.BytesIO()
    for checksum in release["chunks"]:
        if not re.fullmatch("[a-f0-9]{64}", checksum):
            raise ValueError("Invalid public edition part")
        content = (ROOT / "editions" / (checksum + ".part")).read_bytes()
        if hashlib.sha256(content).hexdigest() != checksum:
            raise ValueError("Public edition part checksum mismatch")
        baseline.write(content)
    baseline.seek(0)
    if hashlib.sha256(baseline.getbuffer()).hexdigest() != release["archive_sha256"]:
        raise ValueError("Public edition checksum mismatch")
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Build destination already exists; choose a fresh directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as directory:
        stage = Path(directory) / "built"
        stage.mkdir()
        with zipfile.ZipFile(baseline) as z:
            entries = z.namelist()
            expected = release["files"]
            if len(entries) != len(set(entries)) or set(entries) != set(expected):
                raise ValueError("Public file list changed")
            for name in entries:
                path = PurePosixPath(name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in name
                    or name.startswith(".")
                ):
                    raise ValueError("Invalid public file path")
                data = z.read(name)
                if hashlib.sha256(data).hexdigest() != expected[name]:
                    raise ValueError("Public file checksum mismatch")
                dest = stage / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
        # Explicit reviewed publications only. Never scan internal source, ACLs,
        # private objects or the repository root to discover public material.
        catalog = json.loads((ROOT / "publications.json").read_text())
        links = []
        for entry in catalog:
            slug = entry["slug"]
            kind = entry["format"]
            if not re.fullmatch("[a-z0-9]+(?:-[a-z0-9]+)*", slug) or kind not in (
                "txt",
                "pdf",
            ):
                raise ValueError("Invalid public publication")
            source = ROOT / "source" / (slug + "." + kind)
            if (
                source.is_symlink()
                or source.resolve().parent != (ROOT / "source").resolve()
            ):
                raise ValueError("Public sources must be local regular files")
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != entry["sha256"]:
                raise ValueError("Publication differs from approval")
            if kind == "pdf" and not content.startswith(b"%PDF-"):
                raise ValueError("Invalid public PDF")
            target = (
                stage / "publications" / (slug + (".pdf" if kind == "pdf" else ".html"))
            )
            target.parent.mkdir(exist_ok=True)
            if target.exists():
                raise ValueError("Duplicate publication")
            title = html.escape(entry["title"])
            if kind == "pdf":
                target.write_bytes(content)
            else:
                target.write_text(
                    '<!doctype html><html lang="en"><meta charset="utf-8"><title>'
                    + title
                    + "</title><main><h1>"
                    + title
                    + '</h1><pre style="white-space:pre-wrap">'
                    + html.escape(content.decode("utf-8"))
                    + "</pre></main></html>"
                )
            links.append((slug, title, target.suffix))
        if links:
            index = stage / "publications" / "index.html"
            if index.exists():
                raise ValueError("Approved publication index collides with baseline")
            index.write_text(
                '<!doctype html><html lang="en"><title>Public documents</title><h1>Public documents</h1><ul>'
                + "".join(
                    '<li><a href="/publications/'
                    + slug
                    + suffix
                    + '">'
                    + title
                    + "</a></li>"
                    for slug, title, suffix in links
                )
                + "</ul></html>"
            )
        os.replace(stage, output)


if __name__ == "__main__":
    build(sys.argv[1])
