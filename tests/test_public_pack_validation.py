"""The public release packer must reject malformed or duplicate approval paths."""

import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "public" / "tools"
sys.path.insert(0, str(TOOLS))
import pack as packer


class PublicPackValidation(unittest.TestCase):
    def fixture(self, root, path="docs/public/guide.txt"):
        (root / "editions").mkdir()
        source = root / "source"
        (source / "docs" / "public").mkdir(parents=True)
        body = b"Approved anonymous documentation"
        (source / "docs" / "public" / "guide.txt").write_bytes(body)
        approval = root / "approved.json"
        row = {"path": path, "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
        approval.write_text(json.dumps([row]))
        return source, approval, row, body

    def test_pack_rejects_noncanonical_paths_before_writing_release(self):
        for invalid in ("docs//public/guide.txt", "docs/./public/guide.txt",
                        "docs/public/guide.txt/", "docs\\public\\guide.txt"):
            with self.subTest(path=invalid), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source, approval, _, _ = self.fixture(root, invalid)
                with patch.object(packer, "ROOT", root):
                    with self.assertRaisesRegex(ValueError, "Invalid or duplicate"):
                        packer.pack(source, approval, "approved-deployment", "a" * 40)
                self.assertFalse((root / "release.json").exists())
                self.assertEqual(list((root / "editions").iterdir()), [])

    def test_pack_rejects_duplicate_source_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, approval, row, _ = self.fixture(root)
            approval.write_text(json.dumps([row, row]))
            with patch.object(packer, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "Invalid or duplicate"):
                    packer.pack(source, approval, "approved-deployment", "a" * 40)
            self.assertFalse((root / "release.json").exists())

    def test_pack_rejects_symlinked_public_file_and_directory(self):
        for directory_link in (False, True):
            with self.subTest(directory_link=directory_link), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source, approval, _, body = self.fixture(root)
                public_dir = source / "docs" / "public"
                public_file = public_dir / "guide.txt"
                private_dir = source / "private"
                private_dir.mkdir()
                private_file = private_dir / "guide.txt"
                private_file.write_bytes(body)
                if directory_link:
                    public_file.unlink()
                    public_dir.rmdir()
                    public_dir.symlink_to(private_dir, target_is_directory=True)
                else:
                    public_file.unlink()
                    public_file.symlink_to(private_file)
                with patch.object(packer, "ROOT", root):
                    with self.assertRaisesRegex(ValueError, "symlink"):
                        packer.pack(source, approval, "approved-deployment", "a" * 40)
                self.assertFalse((root / "release.json").exists())
                self.assertEqual(list((root / "editions").iterdir()), [])

    def test_canonical_approved_source_produces_digest_pinned_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, approval, row, body = self.fixture(root)
            with patch.object(packer, "ROOT", root):
                packer.pack(source, approval, "approved-deployment", "a" * 40)
            release = json.loads((root / "release.json").read_text())
            self.assertEqual(release["files"][row["path"]], row["sha256"])
            archive = b"".join((root / "editions" / (part + ".part")).read_bytes()
                               for part in release["chunks"])
            self.assertEqual(hashlib.sha256(archive).hexdigest(), release["archive_sha256"])
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                self.assertEqual(bundle.namelist(), [row["path"]])
                self.assertEqual(bundle.read(row["path"]), body)


if __name__ == "__main__":
    unittest.main()
