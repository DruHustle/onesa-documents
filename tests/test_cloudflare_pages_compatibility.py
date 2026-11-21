"""Offline fixtures verify public artifact packaging boundaries."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pages", ROOT / "public" / "tools" / "verify_pages.py")
pages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pages)


class PagesPublicPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "static"
        self.root.mkdir()
        (self.root / "index.html").write_text("Welcome")
        (self.root / "404.html").write_text("Missing")

    def test_public_entrypoints_are_hash_bound(self):
        report = pages.verify(self.root)
        self.assertEqual(report["asset_count"], 2)
        self.assertEqual(len(report["files"]["index.html"]), 64)

    def test_symlink_rejected(self):
        (self.root / "private.html").symlink_to(self.root / "index.html")
        with self.assertRaisesRegex(ValueError, "symlink"):
            pages.verify(self.root)

    def test_file_limit_rejected(self):
        original = pages.MAX_FILES
        pages.MAX_FILES = 1
        try:
            with self.assertRaisesRegex(ValueError, "file count"):
                pages.verify(self.root)
        finally:
            pages.MAX_FILES = original

    def test_file_size_limit_rejected(self):
        original = pages.MAX_SIZE
        pages.MAX_SIZE = 1
        try:
            with self.assertRaisesRegex(ValueError, "file size"):
                pages.verify(self.root)
        finally:
            pages.MAX_SIZE = original


if __name__ == "__main__":
    unittest.main()
