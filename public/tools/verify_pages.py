"""Validate approved public-only static assets for Cloudflare Pages Free."""
import hashlib
import json
import os
import sys
from pathlib import Path

MAX_FILES = 20_000
MAX_SIZE = 25 * 1024 * 1024


def verify(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Expected static public build directory")
    manifest = {}
    for parent, folders, names in os.walk(root, followlinks=False):
        for folder in folders:
            if (Path(parent) / folder).is_symlink():
                raise ValueError("Public bundle contains symlinks")
        for name in names:
            file = Path(parent) / name
            if file.is_symlink() or not file.is_file():
                raise ValueError("Unsafe public bundle file")
            key = file.relative_to(root).as_posix()
            if key.startswith(".") or ".." in file.relative_to(root).parts:
                raise ValueError("Unsafe public asset path")
            size = file.stat().st_size
            if size > MAX_SIZE:
                raise ValueError("Cloudflare Pages Free file size exceeded")
            if len(manifest) >= MAX_FILES:
                raise ValueError("Cloudflare Pages Free file count exceeded")
            manifest[key] = hashlib.sha256(file.read_bytes()).hexdigest()
    if not {"index.html", "404.html"}.issubset(manifest):
        raise ValueError("Missing public entry or 404.html")
    return {"schema": 1, "asset_count": len(manifest), "files": manifest}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: verify_pages.py BUILT_PUBLIC_ROOT OUTPUT_MANIFEST")
    report = verify(sys.argv[1])
    out = Path(sys.argv[2])
    if out.exists():
        raise SystemExit("Refusing to overwrite existing manifest")
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"files": report["asset_count"], "within_free_limits": True}))
