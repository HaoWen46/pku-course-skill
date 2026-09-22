"""Bounded agent responses and exclusive file output on POSIX filesystems."""
from __future__ import annotations

import contextlib
import json
import os
import secrets
from pathlib import Path

from dean import Error, compact

MAX_STDOUT = 4096
MAX_INDEX_ROW = 2048


def page(items: list[dict], offset: int, total: int, **context) -> dict:
    result = {**context, "items": [], "total": total, "next_offset": None}
    for item in items:
        result["items"].append(item)
        consumed = offset + len(result["items"])
        result["next_offset"] = consumed if consumed < total else None
        if len((compact(result) + "\n").encode("utf-8")) > MAX_STDOUT:
            result["items"].pop()
            consumed -= 1
            result["next_offset"] = consumed if consumed < total else None
            break
    if items and not result["items"]:
        raise Error("output_too_large", "One result exceeds the 4 KiB stdout budget; export to files instead.")
    return result


def index_row(filename: str, course: dict) -> str:
    def preview(value, limit):
        if value is None:
            return None
        return value if len(value) <= limit else value[:limit - 1] + "…"
    item = {"file": filename, "name": preview(course["name"], 160), "teachers": preview(course["teachers"], 320)}
    # Byte-aware fallback also handles JSON escapes and four-byte characters.
    while len((compact(item) + "\n").encode("utf-8")) > MAX_INDEX_ROW:
        key = max(("name", "teachers"), key=lambda k: len(item[k] or ""))
        item[key] = item[key][:-2] + "…"
    return compact(item) + "\n"


def destination(raw: str) -> Path:
    if not raw or len(raw.encode("utf-8")) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise Error("invalid_path", "Use an output path of at most 512 UTF-8 bytes without control characters.")
    path = Path(raw).expanduser()
    if ".." in path.parts or not path.name:
        raise Error("invalid_path", "Destination must name a new file or directory without parent traversal.")
    with parent_fd(path) as fd:
        try:
            os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return path
    raise Error("output_exists", "Destination already exists; choose a new path.")


@contextlib.contextmanager
def parent_fd(path: Path):
    if os.name != "posix":
        raise Error("unsupported_platform", "File output requires Linux, macOS, or WSL.")
    if ".." in path.parts or not path.name:
        raise Error("invalid_path", "Parent traversal is not allowed.")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/" if path.is_absolute() else ".", flags)
    try:
        parts = path.parent.parts[1:] if path.is_absolute() else path.parent.parts
        for part in parts:
            new_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = new_fd
        yield fd
    finally:
        os.close(fd)


def mkdir(path: Path) -> None:
    with parent_fd(path) as fd:
        os.mkdir(path.name, mode=0o700, dir_fd=fd)


def write(path: Path, content: str) -> None:
    with parent_fd(path) as fd:
        temporary = ".pku-" + secrets.token_hex(12)
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path.name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        finally:
            os.unlink(temporary, dir_fd=fd)


def write_json(path: Path, value: object) -> None:
    write(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


@contextlib.contextmanager
def new_index(path: Path):
    with parent_fd(path) as fd:
        handle = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
        yield stream
        stream.flush()
        os.fsync(stream.fileno())


def export(client, term: str, filters: dict, out: Path) -> tuple[dict, int]:
    mkdir(out)
    marker = out / "INCOMPLETE"
    write(marker, "Export is not complete; inspect receipt.json when present.\n")
    receipt = {"term": term, "index": str(out / "index.jsonl"), "written": 0, "failed": 0, "enumeration_complete": False, "complete": False}
    code = 1
    try:
        mkdir(out / "courses")
        with new_index(out / "index.jsonl") as index:
            for row in client.rows(term, filters):
                course = client.detail(term, row)
                relative = f"courses/{receipt['written'] + 1}.json"
                write_json(out / relative, course)
                index.write(index_row(relative, course))
                index.flush()
                receipt["written"] += 1
                if course["retrieval_status"] != "ok":
                    receipt["failed"] += 1
                    error = course["error"]
                    if error["code"] not in {"detail_unavailable", "identity_mismatch"}:
                        raise Error(error["code"], "Export stopped after a detail retrieval failure.")
        receipt["enumeration_complete"] = True
        receipt["complete"] = receipt["failed"] == 0
        code = 0 if receipt["complete"] else 1
    except Error as exc:
        receipt["error"] = exc.data()
    except OSError:
        receipt["error"] = {"code": "io_error", "message": "File output failed; retained files are incomplete."}
    except KeyboardInterrupt:
        receipt["error"] = {"code": "interrupted", "message": "Export interrupted; saved files are partial."}
        code = 130
    write_json(out / "receipt.json", receipt)
    if receipt["complete"]:
        with parent_fd(marker) as fd:
            os.unlink(marker.name, dir_fd=fd)
    return receipt, code
