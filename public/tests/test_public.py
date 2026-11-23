import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build as edition


class PublicEdition(unittest.TestCase):
    def test_public_release_matches_reviewed_deployment_baseline(self):
        # The public site is anonymous. A change to its approved static corpus
        # must therefore be an explicit reviewed release, not merely a
        # self-consistent edit to release.json and its chunks.
        release = json.loads((edition.ROOT / "release.json").read_text())
        self.assertEqual(
            release["deployment"],
            "dpl_HgK26DEvNtJS4i5bzXUd6sSVM8fz",
        )
        self.assertEqual(
            release["source_revision"],
            "d8a0807f43dfd83388927cdb1e84bf058ed81286",
        )
        self.assertEqual(
            release["archive_sha256"],
            "2541cfd2745e5784949253987b133f72ee5ca28523cad1fc79ce4fac9a596176",
        )


    def test_approved_artifact_is_reconstructed_without_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "site"
            with patch.dict("os.environ", {}, clear=True):
                edition.build(output)
            expected = json.loads((edition.ROOT / "release.json").read_text())["files"]
            files = {
                p.relative_to(output).as_posix(): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in output.rglob("*")
                if p.is_file()
            }
            self.assertEqual(files, expected)
            self.assertIn(
                "Onesa Developer Docs",
                (output / "docs/public/introduction.html").read_text(),
            )
            self.assertTrue(list(output.glob("generated/**/*.pdf")))
            self.assertFalse((output / "docs/hr").exists())
            self.assertFalse(
                (output / "docs/architecture/ONESA_DESIGN_SYSTEM.html").exists()
            )
            self.assertFalse((output / "api/attachments").exists())
            for name in files:
                self.assertFalse(name.endswith((".sqlite", ".env", ".py")))

            scripts = output / "assets/js"
            runtime, = scripts.glob("runtime~main.*.js")
            chunk_map = re.search(
                r'\.u=e=>"assets/js/"\+\(\{([^}]+)\}\[e\]\|\|e\)\+"\."\+\{([^}]+)\}\[e\]\+"\.js"',
                runtime.read_text(),
            )
            self.assertIsNotNone(chunk_map, "Docusaurus runtime module map changed")
            names = dict(re.findall(r'(\d+):"([a-f\d]+)"', chunk_map[1]))
            hashes = dict(re.findall(r'(\d+):"([a-f\d]+)"', chunk_map[2]))
            self.assertTrue(hashes, "Docusaurus runtime has no chunks")
            expected_chunks = {f"{names.get(chunk, chunk)}.{digest}.js" for chunk, digest in hashes.items()}
            missing = sorted(name for name in expected_chunks if not (scripts / name).is_file())
            self.assertEqual(missing, [], "Routes depend on these absent JavaScript chunks")
