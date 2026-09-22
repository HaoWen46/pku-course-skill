#!/usr/bin/env python3
"""Read-only PKU course retrieval. No persistent state or cache."""
from __future__ import annotations

import argparse
import contextlib
import secrets
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from bs4 import BeautifulSoup

BASE = "https://dean.pku.edu.cn/service/web/"
PATHS = {"courseSearch.php", "courseSearch_do.php", "courseDetail.php"}
MAX_RESPONSE = 2 * 1024 * 1024
MAX_OUTPUT = 4096
PAGE_SIZE = 10
TERM = re.compile(r"(20\d{2}|21\d{2})-(20\d{2}|21\d{2})-([1-9]\d?)\Z")
TOKEN = re.compile(r"[A-Za-z0-9_-]{1,96}\Z")
LABELS = {
    "课程号": "course_id", "課程號": "course_id", "学分": "credits", "學分": "credits",
    "先修课程": "prerequisites", "先修課程": "prerequisites",
    "开课院系": "department", "開課院系": "department",
    "中文简介": "description", "中文簡介": "description", "课程简介": "description",
    "英文简介": "description_en", "英文簡介": "description_en",
    "教学大纲": "syllabus", "教學大綱": "syllabus", "课程大纲": "syllabus", "課程大綱": "syllabus",
    "英文名称": "name_en", "授课语言": "language", "教材": "textbooks",
    "参考书": "references", "參考書": "references", "教学评估": "assessment",
    "考核方式": "assessment", "教学安排": "teaching_schedule",
}
LABEL_RE = re.compile(r"^(" + "|".join(map(re.escape, LABELS)) + r")(?:\s*[:：]\s*(.*)|\s*)$")
EMPTY_TEXT = {"", "无", "無", "暂无", "暫無", "暂无内容", "待定", "-", "n/a", "null"}


class Failure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def data(self) -> dict:
        return {"code": self.code, "message": self.message}


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def term_parts(value: str) -> tuple[str, int]:
    m = TERM.fullmatch(value)
    if not m or int(m[2]) != int(m[1]) + 1:
        raise Failure("invalid_term", "Use an academic term from options, formatted YYYY-YYYY-N.")
    return f"{m[1]}-{m[2]}", int(m[3])


def source_term(value: str) -> str:
    m = re.fullmatch(r"(\d{2}|\d{4})-(\d{2}|\d{4})-([1-9]\d?)", value)
    if not m:
        raise Failure("source_shape", "Unrecognized source term value.")
    start = int(m[1]) + (2000 if len(m[1]) == 2 else 0)
    end = int(m[2]) + (2000 if len(m[2]) == 2 else 0)
    if end < start and len(m[2]) == 2:
        end += 100
    result = f"{start}-{end}-{m[3]}"
    term_parts(result)
    return result


def clean_dom(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    for tag in list(soup.select("script,style,noscript,iframe,nav,header,footer,.footer,.header,.nav,[hidden],[aria-hidden='true']")):
        tag.decompose()
    for tag in list(soup.select("[style]")):
        if re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", tag.get("style", ""), re.I):
            tag.decompose()
    return soup


def text(html: str | None, paragraphs: bool = False) -> str:
    if html is None:
        return ""
    if not isinstance(html, str):
        raise Failure("source_shape", "Expected a text field in the source response.")
    soup = clean_dom(html)
    for tag in list(soup.find_all(["br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"])):
        if tag.name == "br":
            tag.replace_with("\n")
        else:
            tag.insert_before("\n")
            tag.insert_after("\n")
    value = soup.get_text()
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    if paragraphs:
        return "\n".join(" ".join(line.split()) for line in value.splitlines() if line.strip())
    return " ".join(value.split())


def is_challenge(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    visible = soup.get_text(" ", strip=True).lower()
    return bool(soup.select("input[type='password']")) or any("验证码" in node.get("placeholder", "") or re.search(r"captcha|vcode|verifycode", node.get("name", ""), re.I) for node in soup.find_all("input")) or any(
        x in visible for x in ("access denied", "verify you are human", "验证码错误", "请输入验证码", "请先登录", "访问过于频繁", "访问受限")
    )


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Transport:
    def __init__(self):
        self.opener = build_opener(NoRedirects())

    def request(self, path: str, *, form: dict | None = None, query: dict | None = None) -> str:
        if path not in PATHS:
            raise Failure("unsafe_request", "Only fixed public Dean endpoints are allowed.")
        url = BASE + path + (("?" + urlencode(query)) if query else "")
        headers = {"User-Agent": "pku-course-skill/0.1 (read-only)", "Accept-Encoding": "identity", "Referer": BASE + "courseSearch.php"}
        data = None
        if form is not None:
            data = urlencode(form).encode("utf-8")
            headers.update({"Content-Type": "application/x-www-form-urlencoded", "Origin": BASE.split("/service/")[0], "X-Requested-With": "XMLHttpRequest"})
        try:
            with self.opener.open(Request(url, data=data, headers=headers), timeout=15) as response:
                if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                    raise Failure("source_encoding", "Unexpected compressed response.")
                parts, size, deadline = [], 0, time.monotonic() + 30
                while True:
                    if time.monotonic() > deadline:
                        raise Failure("network_error", "Response exceeded the read deadline.")
                    chunk = response.read1(min(65536, MAX_RESPONSE + 1 - size))
                    if not chunk:
                        break
                    parts.append(chunk)
                    size += len(chunk)
                    if size > MAX_RESPONSE:
                        raise Failure("response_too_large", "Source response exceeded 2 MiB.")
                body = b"".join(parts)
                charset = (response.headers.get_content_charset() or "utf-8").lower()
                if charset not in {"utf-8", "utf8", "gb2312", "gbk", "gb18030", "ascii"}:
                    raise Failure("source_encoding", "Unsupported source character encoding.")
                codec = "gb18030" if charset in {"gb2312", "gbk", "gb18030"} else "utf-8-sig"
                return body.decode(codec)
        except HTTPError as error:
            if error.code in {401, 403} or 300 <= error.code < 400:
                raise Failure("access_restricted", "Source denied access or redirected; no redirect or login was attempted.") from None
            if error.code == 429:
                raise Failure("rate_limited", "Source rate limit reached; stop requests and retry later.") from None
            raise Failure("http_error", f"Source returned HTTP {error.code}.") from None
        except (URLError, OSError, TimeoutError, HTTPException):
            raise Failure("network_error", "Could not read the public PKU source; check network access.") from None
        except UnicodeError:
            raise Failure("source_encoding", "Source text could not be decoded without data loss.") from None


def choice_nodes(container) -> list[dict]:
    result = []
    for node in container.select("option,[data-value],[data-val],li[value],li[rel]"):
        raw = next((node.get(key) for key in ("value", "data-value", "data-val", "rel") if node.get(key) is not None), None)
        if isinstance(raw, list):
            raw = " ".join(raw)
        label = " ".join(node.stripped_strings)
        if isinstance(raw, str) and raw and label and not node.has_attr("disabled"):
            entry = {"value": raw, "label": label}
            if entry not in result:
                result.append(entry)
    return result


def form_choices(soup: BeautifulSoup, field: str, label: str) -> list[dict]:
    anchors = list(soup.select(f"[name='{field}'],[id='{field}']"))
    anchors += [node.parent for node in soup.find_all(string=re.compile(r"^\s*" + re.escape(label) + r"\s*[:：]?\s*$"))]
    for anchor in anchors:
        for _ in range(4):
            if anchor is None or anchor.name in {"body", "html", "[document]"}:
                break
            choices = choice_nodes(anchor)
            if choices:
                return choices
            anchor = anchor.parent
    raise Failure("source_shape", f"Could not identify source filter {field}; source markup may have changed.")


def parse_options(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    try:
        terms = form_choices(soup, "yearandseme", "学年学期")
        departments = form_choices(soup, "yuanxi", "开课院系")
    except Failure:
        if is_challenge(html):
            raise Failure("access_restricted", "Source requires verification or authentication.") from None
        raise
    normalized = []
    for choice in terms:
        # Ignore a source placeholder, never fabricate an available term.
        if not re.fullmatch(r"(?:\d{2}|\d{4})-(?:\d{2}|\d{4})-\d{1,2}", choice["value"]):
            continue
        normalized.append({"value": source_term(choice["value"]), "label": choice["label"], "source_value": choice["value"]})
    if not normalized or len({t["value"] for t in normalized}) != len(normalized):
        raise Failure("source_shape", "Source terms are absent or ambiguous.")
    return {"terms": normalized, "departments": departments}


def token(value: object, field: str) -> str:
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise Failure("source_shape", f"Invalid source identifier: {field}.")
    return value


def reference(term: str, row: dict) -> str:
    return ":".join(("pku", term, token(row.get("kch"), "course"), token(row.get("jxbh"), "section"), token(row.get("zxjhbh") or "-", "detail")))


def parse_reference(ref: str) -> tuple[str, str, str, str]:
    if len(ref) > 330:
        raise Failure("invalid_ref", "Use an unchanged ref returned by search.")
    parts = ref.split(":")
    if len(parts) != 5 or parts[0] != "pku" or any(not TOKEN.fullmatch(v) for v in parts[2:]):
        raise Failure("invalid_ref", "Use an unchanged ref returned by search; URLs are not accepted.")
    term_parts(parts[1])
    return tuple(parts[1:])


def parse_page(body: str, term: str, offset: int) -> tuple[list[dict], int]:
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        code = "access_restricted" if is_challenge(body) else "source_shape"
        raise Failure(code, "Expected a course JSON response; no results were inferred.") from None
    if not isinstance(data, dict) or data.get("status") != "ok":
        code = "access_restricted" if is_challenge(body) else "source_error"
        raise Failure(code, "Source did not report a successful course query.")
    count = data.get("count")
    if isinstance(count, bool) or not isinstance(count, (str, int)) or not re.fullmatch(r"\d{1,7}", str(count)):
        raise Failure("source_shape", "Source did not provide a valid total.")
    total, rows = int(count), data.get("courselist")
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
        raise Failure("source_shape", "Invalid source result page.")
    if len(rows) != min(PAGE_SIZE, max(0, total - offset)):
        raise Failure("source_changed", "Result count and page length disagree; retry the query.")
    refs = set()
    for row in rows:
        if not isinstance(row, dict) or not text(row.get("kcmc")):
            raise Failure("source_shape", "Source course is missing its name.")
        ref = reference(term, row)
        if ref in refs:
            raise Failure("source_changed", "Duplicate offering identity in a result page.")
        refs.add(ref)
        text(row.get("teacher"))
    return rows, total


def parse_detail(html: str, course_id: str) -> dict:
    soup = clean_dom(html)
    fields: dict = {}
    # Field labels, not fragile positional selectors; use direct cells to avoid nested table duplication.
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        for i in range(len(cells) - 1):
            label = text(str(cells[i])).strip(" :：")
            if label in LABELS:
                fields[LABELS[label]] = text(str(cells[i + 1]), True)
    # Handle label/value blocks and label-colon paragraphs outside tables.
    current, lines = None, []
    raw_lines = text(str(soup), True).splitlines()
    for i, line in enumerate(raw_lines):
        if line.startswith("Copyright") or line in {"了解我们", "友情链接", "常用服务"}:
            raw_lines = raw_lines[:i]
            break
    for line in raw_lines + ["__END__"]:
        match = LABEL_RE.fullmatch(line)
        if match or line == "__END__":
            if current is not None and current not in fields:
                fields[current] = "\n".join(lines).strip()
            current = LABELS[match[1]] if match else None
            lines = [match[2]] if match and match[2] else []
        elif current is not None:
            lines.append(line)
    if fields.get("course_id", "").strip() != course_id:
        if is_challenge(html):
            raise Failure("access_restricted", "Course detail requires verification or authentication.")
        raise Failure("identity_mismatch", "Detail page does not identify the requested course; nothing was saved as a syllabus.")
    if len(fields) < 2:
        raise Failure("source_shape", "Detail page did not contain recognizable course fields.")
    for key in ("description", "description_en", "syllabus"):
        if fields.get(key, "").strip().lower() in EMPTY_TEXT:
            fields[key] = None
    status = "full" if fields.get("syllabus") else "intro_only" if fields.get("description") or fields.get("description_en") else "missing"
    if fields.get("syllabus"):
        remainder, links, had_anchor = fields["syllabus"], [], False
        for anchor in soup.select("a[href]"):
            label = text(str(anchor))
            if label and label in fields["syllabus"]:
                had_anchor = True
                remainder = remainder.replace(label, "")
                try:
                    target = urljoin(BASE, anchor["href"])
                    if urlsplit(target).scheme in {"https", "http"}:
                        links.append(target)
                except ValueError:
                    pass  # Invalid source URLs are never fetched or treated as outline text.
        if had_anchor:
            fields["syllabus_links"] = list(dict.fromkeys(links))
            if remainder.strip(" \n\t:：，,。.") in {"", "下载", "下載", "点击下载", "點擊下載", "请下载附件", "請下載附件"}:
                fields["syllabus_notice"] = fields["syllabus"]
                fields["syllabus"] = None
                status = "link_only"
    return {"fields": fields, "syllabus_status": status}


class Client:
    def __init__(self, transport: Transport | None = None):
        self.transport = transport or Transport()

    def options(self) -> dict:
        return parse_options(self.transport.request("courseSearch.php"))

    def filters(self, term: str, department: str, query: str, teacher: str) -> dict:
        term_parts(term)
        for value in (department, query, teacher):
            if len(value) > 256 or any(ord(c) < 32 for c in value):
                raise Failure("invalid_filter", "Filters must be at most 256 characters without control characters.")
        opts = self.options()
        terms = [v for v in opts["terms"] if v["value"] == term]
        if len(terms) != 1:
            raise Failure("unsupported_term", "Requested term is not listed by the source; run options.")
        depts = [v for v in opts["departments"] if department in {v["value"], v["label"]}]
        if len(depts) != 1:
            raise Failure("invalid_department", "Use an exact department value or label from options.")
        return {"yearandseme": terms[0]["source_value"], "yuanxi": depts[0]["value"], "coursetype": "0", "coursename": query, "teachername": teacher}

    def page(self, term: str, filters: dict, offset: int) -> tuple[list[dict], int]:
        body = self.transport.request("courseSearch_do.php", form={**filters, "startrow": str(offset)})
        return parse_page(body, term, offset)

    def search(self, term: str, department: str = "0", query: str = "", teacher: str = "", offset: int = 0, limit: int = 10) -> dict:
        if not 0 <= offset <= 1_000_000 or not 1 <= limit <= PAGE_SIZE:
            raise Failure("invalid_pagination", "Use offset 0..1000000 and limit 1..10.")
        rows, total = self.page(term, self.filters(term, department, query, teacher), offset)
        result = {"term": term, "items": [], "total": total, "next_offset": None}
        for row in rows[:limit]:
            item = {"ref": reference(term, row), "name": text(row["kcmc"]), "teachers": text(row.get("teacher")) or None}
            result["items"].append(item)
            next_offset = offset + len(result["items"])
            result["next_offset"] = next_offset if next_offset < total else None
            if len(encode(result).encode("utf-8")) > MAX_OUTPUT:
                result["items"].pop()
                if not result["items"]:
                    raise Failure("output_too_large", "A result exceeds the compact output budget; export this query to files.")
                next_offset -= 1
                result["next_offset"] = next_offset if next_offset < total else None
                break
        return result

    def rows(self, term: str, filters: dict):
        offset, expected, seen = 0, None, set()
        while True:
            rows, total = self.page(term, filters, offset)
            if expected is not None and total != expected:
                raise Failure("source_changed", "Source total changed during pagination; export is incomplete.")
            expected = total
            for row in rows:
                ref = reference(term, row)
                if ref in seen:
                    raise Failure("source_changed", "Repeated offering across pages; export is incomplete.")
                seen.add(ref)
                yield row
            offset += len(rows)
            if offset >= total:
                break

    def resolve(self, ref: str) -> tuple[str, dict]:
        term, course, section, detail = parse_reference(ref)
        candidates = []
        for row in self.rows(term, self.filters(term, "0", course, "")):
            if row.get("kch") == course and row.get("jxbh") == section and (row.get("zxjhbh") or "-") == detail:
                candidates.append(row)
        if len(candidates) != 1:
            raise Failure("stale_ref", "Offering reference no longer resolves uniquely; run search again.")
        return term, candidates[0]

    def detail(self, term: str, row: dict) -> dict:
        academic_year, semester = term_parts(term)
        result = {
            "ref": reference(term, row), "term": term, "academic_year": academic_year, "semester": semester,
            "course_id": row["kch"], "section": row["jxbh"], "name": text(row["kcmc"]),
            "teachers": text(row.get("teacher")) or None, "department": text(row.get("kkxsmc")) or None,
            "credits": text(row.get("xf")) or None, "schedule": text(row.get("sksj"), True) or None,
            "weeks": text(row.get("qzz")) or None, "category": text(row.get("kctxm")) or None,
            "remarks": text(row.get("bz"), True) or None, "description": None, "syllabus": None,
            "syllabus_status": None, "syllabus_term": None, "retrieval_status": "ok",
            "source_urls": [BASE + "courseSearch.php"], "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            detail_id = row.get("zxjhbh")
            if not detail_id:
                raise Failure("detail_unavailable", "Source did not provide a course detail identifier.")
            query = {"flag": "1", "zxjhbh": token(detail_id, "detail")}
            result["source_urls"].append(BASE + "courseDetail.php?" + urlencode(query))
            parsed = parse_detail(self.transport.request("courseDetail.php", query=query), row["kch"])
            fields = parsed["fields"]
            result.update({"description": fields.pop("description", None), "syllabus": fields.pop("syllabus", None), "teaching_fields": fields, "syllabus_status": parsed["syllabus_status"]})
        except Failure as error:
            result["retrieval_status"] = "partial"
            result["error"] = error.data()
        return result


@contextlib.contextmanager
def parent_fd(path: Path):
    """Resolve each parent through directory descriptors, refusing symlinks and traversal."""
    if os.name != "posix":
        raise Failure("unsupported_platform", "Safe file output requires Linux or macOS.")
    if ".." in path.parts or not path.name or path.name in {".", ".."}:
        raise Failure("unsafe_path", "Use a destination without parent traversal.")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/" if path.is_absolute() else ".", flags)
    try:
        parts = path.parent.parts[1:] if path.is_absolute() else path.parent.parts
        for part in parts:
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


def new_file(path: Path, content: str) -> None:
    # Link a fully written temporary file into place without replacing any existing path.
    with parent_fd(path) as fd:
        temporary = ".pku-" + secrets.token_hex(12)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path.name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        finally:
            os.unlink(temporary, dir_fd=fd)


def new_directory(path: Path) -> None:
    # Parents must already exist, keeping directory creation explicit and bounded.
    with parent_fd(path) as fd:
        os.mkdir(path.name, mode=0o700, dir_fd=fd)


@contextlib.contextmanager
def new_index(path: Path):
    with parent_fd(path) as fd:
        descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        yield stream


def write_json(path: Path, value: object) -> None:
    new_file(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def export(client: Client, term: str, filters: dict, out: Path) -> tuple[dict, int]:
    new_directory(out)
    courses = out / "courses"
    new_directory(courses)
    receipt = {"term": term, "index": str(out / "index.jsonl"), "written": 0, "failed": 0, "complete": False}
    # A durable marker distinguishes an interrupted export from a completed one.
    marker = out / "INCOMPLETE"
    new_file(marker, "Export not finalized; consult receipt.json when present.\n")
    try:
        with new_index(out / "index.jsonl") as index:
            for row in client.rows(term, filters):
                detail = client.detail(term, row)
                number = receipt["written"] + 1
                relative = f"courses/{number}.json"
                write_json(out / relative, detail)
                index.write(encode({"file": relative, "name": detail["name"], "teachers": detail["teachers"]}) + "\n")
                index.flush()
                receipt["written"] += 1
                if detail["retrieval_status"] != "ok":
                    receipt["failed"] += 1
                    if detail["error"]["code"] in {"access_restricted", "rate_limited", "network_error", "source_shape", "source_encoding", "http_error"}:
                        raise Failure(detail["error"]["code"], "Export stopped after a source access or network failure.")
        receipt["complete"] = receipt["failed"] == 0
    except Failure as error:
        receipt["error"] = error.data()
    except KeyboardInterrupt:
        receipt["error"] = {"code": "interrupted", "message": "Export interrupted; saved rows are partial."}
    write_json(out / "receipt.json", receipt)
    if receipt["complete"]:
        marker.unlink()
    return receipt, 0 if receipt["complete"] else 1


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Failure("invalid_arguments", message)


def main(argv: list[str] | None = None) -> int:
    parser = Parser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    commands.add_parser("options", help="List currently available terms and source filter values.", allow_abbrev=False)
    for name in ("search", "export"):
        sub = commands.add_parser(name, allow_abbrev=False)
        sub.add_argument("--term", required=True)
        sub.add_argument("--department", default="0")
        sub.add_argument("--query", default="")
        sub.add_argument("--teacher", default="")
        if name == "search":
            sub.add_argument("--offset", type=int, default=0)
            sub.add_argument("--limit", type=int, default=10)
        else:
            sub.add_argument("--out", required=True, type=Path)
    get = commands.add_parser("get", allow_abbrev=False)
    get.add_argument("ref")
    get.add_argument("--out", required=True, type=Path)
    try:
        args = parser.parse_args(argv)
        client, code = Client(), 0
        if args.command == "options":
            result = client.options()
            result["terms"] = [{"value": x["value"], "label": x["label"]} for x in result["terms"]]
            if len(encode(result).encode("utf-8")) > 16384:
                raise Failure("output_too_large", "Source options exceed the 16 KiB output limit; inspect source markup.")
        elif args.command == "search":
            result = client.search(args.term, args.department, args.query, args.teacher, args.offset, args.limit)
        elif args.command == "get":
            if os.path.lexists(args.out):
                raise Failure("output_exists", "Output already exists; choose a new path.")
            term, row = client.resolve(args.ref)
            detail = client.detail(term, row)
            write_json(args.out, detail)
            code = 0 if detail["retrieval_status"] == "ok" else 1
            result = {"file": str(args.out), "term": term, "retrieval_status": detail["retrieval_status"], "syllabus_status": detail["syllabus_status"]}
            if "error" in detail:
                result["error"] = detail["error"]
        else:
            if os.path.lexists(args.out):
                raise Failure("output_exists", "Export directory already exists; choose a new path.")
            filters = client.filters(args.term, args.department, args.query, args.teacher)
            result, code = export(client, args.term, filters, args.out)
        print(encode(result))
        return code
    except Failure as error:
        print(encode({"error": error.data()}), file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        code = "output_exists" if isinstance(error, FileExistsError) else "io_error"
        print(encode({"error": {"code": code, "message": "Output could not be written safely; check the destination and retained partial files."}}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(encode({"error": {"code": "interrupted", "message": "Command interrupted."}}), file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
