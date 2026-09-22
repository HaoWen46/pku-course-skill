"""Locked project, installer, and error-contract regressions."""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


class UVContractTests(unittest.TestCase):
    def test_single_project_manifest(self):
        manifest = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual(manifest["project"]["requires-python"], ">=3.11")
        self.assertEqual(manifest["project"]["dependencies"], ["beautifulsoup4==4.14.3"])
        self.assertFalse(manifest["tool"]["uv"]["package"])
        self.assertEqual(manifest["tool"]["uv"]["required-version"], ">=0.10.0")
        self.assertFalse((ROOT / "requirements.txt").exists())

    def test_lock_matches_project(self):
        manifest = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        lock = tomllib.loads((ROOT / "uv.lock").read_text())
        root = next(p for p in lock["package"] if p["name"] == manifest["name"])
        self.assertEqual(root["version"], manifest["version"])
        self.assertEqual(root["source"], {"virtual": "."})
        self.assertEqual(lock["requires-python"], manifest["requires-python"])
        self.assertEqual(root["metadata"]["requires-dist"], [{"name": "beautifulsoup4", "specifier": "==4.14.3"}])

    def test_all_transitive_dependencies_are_locked(self):
        packages = tomllib.loads((ROOT / "uv.lock").read_text())["package"]
        names = {p["name"] for p in packages}
        self.assertEqual(names, {"pku-course-skill", "beautifulsoup4", "soupsieve", "typing-extensions"})
        for package in packages:
            self.assertTrue(package["version"])
            for dependency in package.get("dependencies", []):
                self.assertIn(dependency["name"], names)

    def test_registry_artifacts_have_https_urls_and_hashes(self):
        for package in tomllib.loads((ROOT / "uv.lock").read_text())["package"]:
            if "registry" not in package["source"]:
                continue
            self.assertEqual(package["source"]["registry"], "https://pypi.org/simple")
            self.assertTrue(package["wheels"])
            for artifact in [package["sdist"], *package["wheels"]]:
                url = urlsplit(artifact["url"])
                self.assertEqual((url.scheme, url.hostname), ("https", "files.pythonhosted.org"))
                self.assertIsNone(url.username)
                self.assertIsNone(url.password)
                self.assertRegex(artifact["hash"], r"^sha256:[a-f0-9]{64}$")

    def test_ci_uses_pinned_actions_and_locked_uv(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        for action in re.findall(r"uses:\s*([^\s#]+)", workflow):
            self.assertRegex(action, r"^[\w/-]+@[a-f0-9]{40}$")
        self.assertIn("contents: read", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("astral-sh/setup-uv@", workflow)
        self.assertIn("uv sync --locked", workflow)
        self.assertIn("uv run --locked tests/uv_smoke.py", workflow)
        self.assertIn("uv run --locked tests/live.py", workflow)
        self.assertIn("git diff --exit-code", workflow)
        self.assertNotIn("setup-python", workflow)
        self.assertNotIn("pip install", workflow)

    def test_agent_docs_use_uv_without_parallel_dependency_metadata(self):
        for name in ("SKILL.md", "README.md"):
            content = (ROOT / name).read_text()
            self.assertIn("uv run --locked", content)
            self.assertIn("--project", content)
            self.assertNotIn("pip install", content)
            self.assertNotIn("requirements.txt", content)
        self.assertNotIn("# /// script", (ROOT / "scripts/pku.py").read_text())

    def test_missing_dependency_error_directs_to_uv(self):
        proc = subprocess.run([sys.executable, "-S", str(ROOT / "scripts/pku.py"), "options"], capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 2)
        error = json.loads(proc.stderr)["error"]
        self.assertEqual(error["code"], "dependency_missing")
        self.assertIn("uv run --locked", error["message"])
        self.assertEqual(proc.stdout, "")

    def test_incomplete_skill_is_not_misreported_as_dependency_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pku.py"
            shutil.copy2(ROOT / "scripts/pku.py", path)
            proc = subprocess.run([sys.executable, "-S", str(path), "options"], capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stderr)["error"]["code"], "installation_incomplete")

    def test_live_checks_cannot_be_disabled_with_python_optimization(self):
        import ast
        tree = ast.parse((ROOT / "tests/live.py").read_text())
        self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)))
        content = (ROOT / "tests/live.py").read_text()
        self.assertIn('"--locked"', content)
        self.assertNotIn("sys.executable", content)


if __name__ == "__main__":
    unittest.main()
