"""Regression test: public additions cannot overwrite a pinned baseline artifact."""

import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


BUILD_PATH = Path(__file__).resolve().parents[1] / "public" / "tools" / "build.py"
spec = importlib.util.spec_from_file_location("onesa_public_builder", BUILD_PATH)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PublicIndexCollisionTest(unittest.TestCase):
    def test_publication_index_cannot_replace_approved_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "editions").mkdir()
            (root / "source").mkdir()
            baseline = b"<!doctype html><title>Approved baseline</title>"
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("publications/index.html", baseline)
            payload = archive.getvalue()
            chunk_hash = hashlib.sha256(payload).hexdigest()
            (root / "editions" / (chunk_hash + ".part")).write_bytes(payload)
            (root / "release.json").write_text(json.dumps({
                "chunks": [chunk_hash],
                "archive_sha256": chunk_hash,
                "files": {"publications/index.html": hashlib.sha256(baseline).hexdigest()},
            }))
            publication = b"An approved public example"
            (root / "source" / "example.txt").write_bytes(publication)
            (root / "publications.json").write_text(json.dumps([{
                "slug": "example",
                "format": "txt",
                "title": "Example",
                "sha256": hashlib.sha256(publication).hexdigest(),
            }]))
            output = root / "dist"
            with patch.object(builder, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "index collides"):
                    builder.build(output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
