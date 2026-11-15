"""Guard the intended production defaults and exact-main CI release checks."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseDefaults(unittest.TestCase):
    def test_container_defaults_to_production_origin_enforcement(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("ENV DOCS_ENVIRONMENT=production", dockerfile)
        self.assertIn("ENV DOCS_DATA=/srv/docs", dockerfile)
        self.assertIn("USER docs", dockerfile)
        self.assertIn("--no-access-log", dockerfile)

    def test_container_healthcheck_uses_document_store_readiness(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("/readyz", dockerfile)

    def test_private_import_material_is_excluded_from_source_and_build_context(self):
        gitignore = (ROOT / ".gitignore").read_text().splitlines()
        dockerignore = (ROOT / ".dockerignore").read_text().splitlines()
        for pattern in ("objects/", "manifest*.json", "backups/"):
            self.assertIn(pattern, gitignore)
        for pattern in ("objects", "manifest*.json", "backups"):
            self.assertIn(pattern, dockerignore)
        self.assertIn("*.sqlite-wal", gitignore)
        self.assertIn("*.sqlite-shm", gitignore)

    def test_verification_runs_after_main_merges(self):
        workflow = (ROOT / ".github/workflows/verify.yml").read_text()
        self.assertIn("  pull_request:\n", workflow)
        self.assertIn("  push:\n    branches: [main]\n", workflow)
        self.assertIn("python -m unittest discover -s tests -v", workflow)
        self.assertIn("python -m unittest discover -s public/tests -v", workflow)


if __name__ == "__main__":
    unittest.main()
