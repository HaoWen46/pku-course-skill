"""Live PKU smoke test through uv; failures never fall back to fixtures."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/pku.py"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def command(cwd, *args):
    uv = shutil.which("uv")
    require(uv is not None, "uv is required")
    proc = subprocess.run([uv, "run", "--locked", "--project", str(ROOT), str(CLI), *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=90)
    if proc.returncode:
        print(json.dumps({"step": args[0], "exit": proc.returncode, "error": (proc.stderr or proc.stdout)[:1000]}), flush=True)
        raise RuntimeError("Live command failed")
    require(len(proc.stdout.encode("utf-8")) <= 4096, "stdout budget exceeded")
    value = json.loads(proc.stdout)
    print(json.dumps({"step": args[0], "status": "passed"}), flush=True)
    return value


def main():
    lock = (ROOT / "uv.lock").read_bytes()
    with tempfile.TemporaryDirectory(prefix="pku-live-") as temporary:
        cwd = Path(temporary).resolve()
        terms = command(cwd, "options")
        departments = command(cwd, "options", "--field", "departments")
        require(terms["items"] and departments["items"], "Source has no filter choices")
        term = terms["items"][0]["value"]
        print(json.dumps({"test_term": term}), flush=True)
        sample = command(cwd, "search", "--term", term, "--limit", "10")
        require(sample["items"], "Test term has no offerings; live retrieval is not verified")
        if sample["next_offset"] is not None:
            second = command(cwd, "search", "--term", term, "--offset", str(sample["next_offset"]), "--limit", "2")
            require(second["items"], "Continuation unexpectedly returned no rows")
            require(not {i["ref"] for i in sample["items"]} & {i["ref"] for i in second["items"]}, "Live pages overlap")
        selected = None
        for candidate in sample["items"][:3]:
            code = candidate["ref"].split(":")[2]
            scoped = command(cwd, "search", "--term", term, "--query", code)
            if 0 < scoped["total"] <= 5:
                selected = scoped["items"][0]
                break
        require(selected, "No small query found; refusing an unbounded live export")
        single = command(cwd, "get", selected["ref"], "--out", "course.json")
        course = json.loads((cwd / "course.json").read_text(encoding="utf-8"))
        require(course["ref"] == selected["ref"] and course["term"] == term, "Offering identity changed")
        require(course["name"] and course["teachers"], "Required content missing")
        require(course["description"] or course["description_en"] or course["syllabus"], "No teaching content retrieved")
        result = command(cwd, "export", "--term", term, "--query", course["course_id"], "--out", "export")
        index = cwd / "export/index.jsonl"
        rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
        require(result["complete"] and result["written"] == len(rows) > 0, "Export is incomplete")
        require(not (cwd / "export/INCOMPLETE").exists(), "Incomplete marker remains")
        for row in rows:
            require(set(row) == {"file", "name", "teachers"}, "Index schema changed")
            require((cwd / "export" / row["file"]).is_file(), "Indexed file is missing")
        require((ROOT / "uv.lock").read_bytes() == lock, "uv changed the committed lockfile")
        print(json.dumps({"status": "passed", "launcher": "uv run --locked", "term": term, "exported": len(rows), "syllabus_status": single["syllabus_status"]}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)[:500]}), flush=True)
        raise SystemExit(1)
