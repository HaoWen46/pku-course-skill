"""Exercise the real uv launcher in a fresh skill copy; no PKU requests."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class UVLauncherTests(unittest.TestCase):
    def setUp(self):
        self.uv = shutil.which("uv")
        self.assertIsNotNone(self.uv, "uv is required; launcher tests must not be skipped")
        self.temporary = tempfile.TemporaryDirectory(prefix="pku-uv-")
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name).resolve()
        self.skill, self.caller = base / "skill copy 中文", base / "caller"
        self.skill.mkdir()
        self.caller.mkdir()
        for name in ("pyproject.toml", "uv.lock"):
            shutil.copy2(ROOT / name, self.skill / name)
        for name in ("scripts", "tests"):
            shutil.copytree(ROOT / name, self.skill / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.original_lock = (self.skill / "uv.lock").read_bytes()
        self.env = os.environ.copy()
        for name in ("VIRTUAL_ENV", "PYTHONPATH", "UV_PROJECT_ENVIRONMENT"):
            self.env.pop(name, None)
        # Reuse the matrix interpreter, not its site-packages, and never download Python here.
        self.env.update(UV_PYTHON=sys.executable, UV_PYTHON_DOWNLOADS="never")

    def run_uv(self, *args):
        return subprocess.run([self.uv, "run", "--locked", "--offline", "--project", str(self.skill), *args], cwd=self.caller, env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=60)

    def test_clean_environment_uses_locked_dependencies_from_other_directory(self):
        # A foreign project's invalid metadata must not control this skill.
        foreign = self.caller / "pyproject.toml"
        foreign.write_text("not valid TOML [[", encoding="utf-8")
        self.assertFalse((self.skill / ".venv").exists())
        code = "import json,sys; from importlib.metadata import version; print(json.dumps({'prefix':sys.prefix,'versions':{n:version(n) for n in ['beautifulsoup4','soupsieve','typing-extensions']}}))"
        proc = self.run_uv("python", "-c", code)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(Path(data["prefix"]).resolve(), self.skill / ".venv")
        lock = tomllib.loads(self.original_lock.decode("utf-8"))
        self.assertEqual(data["versions"], {p["name"]: p["version"] for p in lock["package"] if "registry" in p["source"]})
        self.assertFalse((self.caller / ".venv").exists())
        self.assertEqual(foreign.read_text(), "not valid TOML [[")
        self.assertEqual((self.skill / "uv.lock").read_bytes(), self.original_lock)

    def test_help_and_argument_errors_go_through_uv(self):
        cli = str(self.skill / "scripts/pku.py")
        proc = self.run_uv(cli, "--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("export", proc.stdout)
        proc = self.run_uv(cli, "search")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(json.loads(proc.stderr)["error"]["code"], "invalid_arguments")

    def test_relative_output_stays_in_caller_directory(self):
        # Invoke the real file writer through the existing fixture client, without a network call.
        code = "import sys; from pathlib import Path; sys.path.insert(0,str(Path(sys.argv[1])/'tests')); from test_pku import invoke,TERM,dean,row; status,out,err=invoke(['get',dean.ref(TERM,row()),'--out','course.json']); print(out,end=''); print(err,end='',file=sys.stderr); raise SystemExit(status)"
        proc = self.run_uv("python", "-c", code, str(self.skill))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["file"], "course.json")
        self.assertTrue((self.caller / "course.json").is_file())
        self.assertFalse((self.skill / "course.json").exists())
        self.assertLessEqual(len(proc.stdout.encode("utf-8")), 4096)

    def test_stale_lock_prevents_execution_without_rewriting_lock(self):
        manifest = self.skill / "pyproject.toml"
        manifest.write_text(manifest.read_text().replace('dependencies = ["beautifulsoup4==4.14.3"]', 'dependencies = []'), encoding="utf-8")
        proc = self.run_uv("python", "-c", "from pathlib import Path; Path('executed').touch()")
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((self.caller / "executed").exists())
        self.assertEqual((self.skill / "uv.lock").read_bytes(), self.original_lock)

    def test_missing_lock_prevents_execution_and_is_not_generated(self):
        (self.skill / "uv.lock").unlink()
        proc = self.run_uv("python", "-c", "from pathlib import Path; Path('executed').touch()")
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((self.caller / "executed").exists())
        self.assertFalse((self.skill / "uv.lock").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
