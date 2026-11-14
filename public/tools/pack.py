"""Import an explicitly approved public deployment from verified files, in portable chunks."""

import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path

from build import ROOT


def pack(source, approval, deployment, revision):
    manifest = json.loads(approval.read_text())
    files = {}
    with tempfile.TemporaryFile() as raw:
        with zipfile.ZipFile(
            raw, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as z:
            for row in sorted(manifest, key=lambda r: r["path"]):
                name = row["path"]
                path = (source / name).resolve()
                if (
                    not path.is_relative_to(source.resolve())
                    or name.startswith(("/", "."))
                    or ".." in Path(name).parts
                    or "\\" in name
                ):
                    raise ValueError("Invalid public source path")
                content = path.read_bytes()
                files[name] = hashlib.sha256(content).hexdigest()
                if files[name] != row["sha256"] or len(content) != row["size"]:
                    raise ValueError("Source bytes changed")
                info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, content)
        raw.seek(0)
        archive_hash = hashlib.sha256()
        chunks = []
        while chunk := raw.read(48 * 1024):
            checksum = hashlib.sha256(chunk).hexdigest()
            archive_hash.update(chunk)
            filename = checksum + ".part"
            (ROOT / "editions" / filename).write_bytes(chunk)
            chunks.append(checksum)
    release = {
        "host": "docs.onesa.eu",
        "deployment": deployment,
        "source_revision": revision,
        "archive_sha256": archive_hash.hexdigest(),
        "chunks": chunks,
        "files": files,
    }
    (ROOT / "release.json").write_text(
        json.dumps(release, sort_keys=True, indent=2) + "\n"
    )


if __name__ == "__main__":
    pack(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4])
