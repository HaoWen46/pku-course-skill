"""PKU's public Dean adapter. Read-only requests; no cache or persisted session."""
from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timezone
from http.client import HTTPException
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

from bs4 import BeautifulSoup, Comment, Tag

BASE = "https://dean.pku.edu.cn/service/web/"
ENDPOINTS = {"courseSearch.php", "courseSearch_do.php", "courseDetail.php"}
PAGE_SIZE = 10
MAX_ROWS = 100_000
MAX_BODY = 2 * 1024 * 1024
TOKEN = re.compile(r"[A-Za-z0-9_-]{1,96}\Z", re.ASCII)
TERM = re.compile(r"([1-9][0-9]{3})-([1-9][0-9]{3})-([1-9][0-9]?)\Z")
CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
LABELS = {
    "课程号": "course_id", "课程编号": "course_id", "課程號": "course_id",
    "学分": "credits", "學分": "credits", "课程名称": "name", "英文名称": "name_en",
    "先修课程": "prerequisites", "先修課程": "prerequisites",
    "开课院系": "department", "開課院系": "department",
    "中文简介": "description", "中文簡介": "description", "课程简介": "description",
    "英文简介": "description_en", "英文簡介": "description_en",
    "教学大纲": "syllabus", "教學大綱": "syllabus", "课程大纲": "syllabus",
    "授课语言": "language", "教材": "textbooks", "参考书": "references",
    "参考书目": "references", "參考書": "references", "教学评估": "teaching_evaluation",
    "考核方式": "assessment", "成绩评定": "assessment", "教学安排": "teaching_schedule",
    "大纲适用学期": "syllabus_term", "教学大纲学期": "syllabus_term",
    "通选课领域": "general_education_area", "是否属于艺术与美育": "arts_education",
    "平台课性质": "platform_nature", "平台课类型": "platform_type",
}
EMPTY = {"", "无", "無", "暂无", "暫無", "暂无内容", "未填写", "未提供", "待定", "-", "--", "n/a", "none", "null"}


class Error(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message

    def data(self) -> dict:
        return {"code": self.code, "message": self.message}


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def term_parts(term: str) -> tuple[str, int]:
    match = TERM.fullmatch(term)
    if not match or int(match[2]) != int(match[1]) + 1:
        raise Error("invalid_term", "Use YYYY-YYYY-N from options; the years must be consecutive.")
    return f"{match[1]}-{match[2]}", int(match[3])


def normalize_term(raw: str) -> str:
    match = re.fullmatch(r"([0-9]{2}|[0-9]{4})-([0-9]{2}|[0-9]{4})-([1-9][0-9]?)", raw)
    if not match or len(match[1]) != len(match[2]):
        raise Error("source_shape", "Unrecognized academic-term value in the source form.")
    start = int(match[1]) + (2000 if len(match[1]) == 2 else 0)
    end = start + 1
    if end % (100 if len(match[2]) == 2 else 10000) != int(match[2]):
        raise Error("source_shape", "The source academic year is inconsistent.")
    value = f"{start}-{end}-{match[3]}"
    term_parts(value)
    return value


def scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)) or isinstance(value, float) and not math.isfinite(value):
        raise Error("source_shape", "Unexpected source field type.")
    value = str(value)
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise Error("source_shape", "Source field contains invalid Unicode.") from None
    return value


def identifier(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not TOKEN.fullmatch(str(value)):
        raise Error("source_shape", "Invalid course, section, or detail identifier.")
    return str(value)


def dom(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    for comment in soup.find_all(string=lambda x: isinstance(x, Comment)):
        comment.extract()
    for node in list(soup.select("script,style,noscript,iframe,object,embed,template,nav,footer,header,.footer,#footer,.header,#header,.breadcrumb,[hidden],[aria-hidden='true']")):
        node.decompose()
    for node in list(soup.select("[style]")):
        if node.attrs and re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", node.get("style", ""), re.I):
            node.decompose()
    return soup


def text(value: object, paragraphs: bool = False) -> str:
    soup = dom(scalar(value))
    for node in list(soup.find_all(["br", "p", "div", "li", "tr", "td", "th", "dt", "dd", "h1", "h2", "h3", "h4"])):
        if node.name == "br":
            node.replace_with("\n")
        else:
            node.insert_before("\n")
            node.insert_after("\n")
    value = CONTROLS.sub("", soup.get_text())
    lines = [" ".join(line.split()) for line in value.splitlines() if line.strip()]
    return ("\n" if paragraphs else " ").join(lines)


def access_check(html: str, form_page: bool = False) -> None:
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one('input[type="password"]'):
        raise Error("access_restricted", "Source requires sign-in; no login was attempted.")
    visible = dom(html).get_text(" ", strip=True)
    if re.search(r"access denied|verify you are human|验证码.{0,12}(错误|不正确|失效)|请先登[录陆]|访问受限|访问过于频繁|会话.{0,6}(失效|过期)", visible, re.I):
        raise Error("access_restricted", "Source requires authorization or verification; stop requests.")
    # The public filter page contains CAPTCHA widgets; their presence does not identify a failed request.
    if not form_page and (soup.select_one('input[name*="captcha"],input[name*="vcode"]') or re.search(r"请输入验证码|sign in|captcha", visible, re.I)):
        raise Error("access_restricted", "Source returned an authentication or verification challenge.")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTP:
    def __init__(self):
        self.opener = build_opener(NoRedirect(), HTTPCookieProcessor(CookieJar()))

    def request(self, endpoint: str, params: dict | None = None) -> str:
        if endpoint not in ENDPOINTS:
            raise Error("unsafe_request", "Only the three fixed public Dean endpoints are allowed.")
        params = params or {}
        headers = {"User-Agent": "pku-course-skill/2.0 (read-only)", "Accept-Encoding": "identity", "Referer": BASE + "courseSearch.php"}
        url, data = BASE + endpoint, None
        if endpoint == "courseSearch_do.php":
            data = urlencode(params).encode("ascii")
            headers.update({"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://dean.pku.edu.cn", "X-Requested-With": "XMLHttpRequest"})
        elif params:
            url += "?" + urlencode(params)
        try:
            with self.opener.open(Request(url, data=data, headers=headers), timeout=15) as response:
                if response.status != 200:
                    raise Error("http_error", f"Source returned HTTP {response.status}.")
                if response.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
                    raise Error("source_encoding", "Unexpected response compression.")
                chunks, size, deadline = [], 0, time.monotonic() + 30
                while True:
                    if time.monotonic() > deadline:
                        raise Error("network_error", "Source read deadline exceeded.")
                    chunk = response.read1(min(65536, MAX_BODY + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_BODY:
                        raise Error("response_too_large", "Source response exceeded 2 MiB.")
                    chunks.append(chunk)
                charset = (response.headers.get_content_charset() or "utf-8").lower()
                if charset not in {"utf-8", "utf8", "ascii", "gbk", "gb2312", "gb18030"}:
                    raise Error("source_encoding", "Unsupported source character encoding.")
                codec = "gb18030" if charset in {"gbk", "gb2312", "gb18030"} else "utf-8-sig"
                return b"".join(chunks).decode(codec)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            code = "access_restricted" if status in {401, 403} or 300 <= status < 400 else "rate_limited" if status == 429 else "http_error"
            raise Error(code, f"Source returned HTTP {status}; no redirect or verification bypass was attempted.") from None
        except (URLError, OSError, HTTPException):
            raise Error("network_error", "Cannot reach PKU; check network, DNS, and TLS. No results were inferred.") from None
        except UnicodeError:
            raise Error("source_encoding", "Response could not be decoded without data loss.") from None


def choices(soup: BeautifulSoup, field: str, label: str) -> list[tuple[str, str]]:
    candidates = list(soup.select(f'[name="{field}"],#{field},[data-filter="{field}"]'))
    candidates += [s.parent for s in soup.find_all(string=re.compile(r"^\s*" + re.escape(label) + r"\s*[:：]?\s*$"))]
    attrs = ("value", "val", "data-value", "data-val", "data-id", "rel")
    for start in candidates:
        scope = start
        for _ in range(5):
            if not isinstance(scope, Tag) or scope.name in {"body", "html", "[document]", "form"}:
                break
            unrelated = scope.select('input[name],select[name]')
            if any(n.get("name") in {"yearandseme", "yuanxi", "coursetype"} - {field} for n in unrelated):
                break
            found = []
            for n in scope.select('option,li[value],li[val],a[value],a[val],[data-value],[data-val],[data-id],li[rel]'):
                raw = next((n.get(a) for a in attrs if n.has_attr(a)), None)
                if isinstance(raw, list):
                    raw = " ".join(raw)
                title = n.get_text("", strip=True)
                if isinstance(raw, str) and raw and title and not n.has_attr("disabled"):
                    found.append((raw, title))
            if found:
                return list(dict.fromkeys(found))
            scope = scope.parent
    raise Error("source_shape", f"Cannot locate {field} choices in the public form; source markup is unsupported.")


def parse_options(html: str) -> dict:
    access_check(html, form_page=True)
    soup = BeautifulSoup(html, "html.parser")
    result = {}
    for field, category, label in (("yearandseme", "terms", "学年学期"), ("yuanxi", "departments", "开课院系")):
        items, seen = [], {}
        for raw, title in choices(soup, field, label):
            if category == "terms":
                if not re.fullmatch(r"[0-9]{2,4}-[0-9]{2,4}-[0-9]{1,2}", raw):
                    continue
                value = normalize_term(raw)
                match = re.search(r"([0-9]{2}|[0-9]{4})-([0-9]{2}|[0-9]{4})学年\s*第\s*([1-9][0-9]?)\s*学期", title)
                if match and normalize_term("-".join(match.groups())) != value:
                    raise Error("source_shape", "Source term label and value disagree.")
            else:
                value = identifier(raw)
            if value in seen:
                if seen[value] != (raw, title):
                    raise Error("source_shape", "Source filter choices conflict.")
                continue
            seen[value] = (raw, title)
            items.append({"value": value, "label": title, "source_value": raw})
        if not items:
            raise Error("source_shape", f"No valid {category} found in the source form.")
        result[category] = items
    return result


def ref(term: str, row: dict) -> str:
    return ":".join(("pku2", term, identifier(row["kch"]), identifier(row["jxbh"]), identifier(row.get("zxjhbh") or "-")))


def read_ref(value: str) -> tuple[str, str, str, str]:
    try:
        if len(value) > 320:
            raise ValueError
        version, term, course, section, detail = value.split(":")
        if version != "pku2":
            raise ValueError
        term_parts(term)
        for item in (course, section, detail):
            identifier(item)
        return term, course, section, detail
    except (ValueError, Error):
        raise Error("invalid_ref", "Use an unchanged pku2 ref returned by search; URLs and legacy refs are not accepted.") from None


def parse_page(body: str, term: str, offset: int) -> tuple[int, list[dict]]:
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        access_check(body)
        raise Error("source_shape", "Course query did not return JSON; no empty result was inferred.") from None
    if not isinstance(data, dict) or data.get("status") != "ok":
        error_text = " ".join(str(data.get(k, "")) for k in ("status", "msg", "message")) if isinstance(data, dict) else ""
        code = "access_restricted" if re.search(r"captcha|验证|登[录陆]|auth|session", error_text, re.I) else "source_error"
        raise Error(code, "Source did not report a successful query.")
    count, rows = data.get("count"), data.get("courselist")
    if isinstance(count, bool) or not isinstance(count, (int, str)) or not re.fullmatch(r"[0-9]{1,6}", str(count)) or int(count) > MAX_ROWS:
        raise Error("source_shape", "Course query returned an invalid or excessive total.")
    total = int(count)
    if not isinstance(rows, list) or len(rows) != min(PAGE_SIZE, max(0, total - offset)):
        raise Error("source_changed", "Page length disagrees with the reported total.")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not {"kch", "kcmc", "jxbh", "teacher"} <= row.keys():
            raise Error("source_shape", "Course row lacks required fields.")
        identity = ref(term, row)
        if identity in seen:
            raise Error("source_changed", "Source repeated an offering.")
        seen.add(identity)
        for key in ("kcmc", "teacher", "xf", "kkxsmc", "sksj", "qzz", "kctxm", "bz"):
            scalar(row.get(key))
        if not text(row["kcmc"]):
            raise Error("source_shape", "Course row has no readable name.")
    return total, rows


def label_key(value: str) -> str | None:
    return LABELS.get(re.sub(r"[\s:：]+", "", value))


def parse_detail(html: str, course: str) -> dict:
    access_check(html, form_page=True)
    soup = dom(html)
    fields, markup = {}, {}

    def put(key: str, value: str, fragment: str = "") -> None:
        value = value.strip()
        if key in fields and fields[key] != value:
            raise Error("source_shape", f"Conflicting {key} fields in course detail.")
        fields[key] = value
        if fragment:
            markup[key] = fragment

    parsed_tables = []
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        i = 0
        while i < len(cells):
            key = label_key(text(str(cells[i])))
            i += 1
            if key is None:
                continue
            pieces = []
            while i < len(cells) and label_key(text(str(cells[i]))) is None:
                pieces.append(str(cells[i]))
                i += 1
            fragment = "".join(pieces)
            put(key, text(fragment, True), fragment)
            parsed_tables.append(tr)
    for dt in soup.select("dt"):
        key, dd = label_key(text(str(dt))), dt.find_next_sibling()
        if key and dd and dd.name == "dd":
            put(key, text(str(dd), True), str(dd))
            parsed_tables.extend([dt, dd])
    for node in parsed_tables:
        node.extract()

    # The public detail page also uses label-colon blocks. Associate links with their own field, never the entire document.
    marker = re.compile(r"^(" + "|".join(re.escape(k) for k in sorted(LABELS, key=len, reverse=True)) + r")\s*[:：]\s*(.*)$", re.S)
    standalone = re.compile(r"^(" + "|".join(re.escape(k) for k in LABELS) + r")\s*[:：]?\s*$")
    if "\ue000PKULINK" in soup.get_text():
        raise Error("source_shape", "Source contains a reserved parser marker.")
    link_sentinels = {}
    for n, anchor in enumerate(soup.select("a[href]")):
        key = f"\ue000PKULINK{n}\ue001"
        link_sentinels[key] = str(anchor)
        anchor.replace_with(key)
    lines = text(str(soup), True).splitlines()
    current, content = None, []

    def finish() -> None:
        if current is not None:
            fragment = "\n".join(content)
            # Escape literal source text before restoring only the source anchor nodes.
            from html import escape
            fragment = escape(fragment).replace("\n", "<br>")
            fragment = re.sub(r"\ue000PKULINK[0-9]+\ue001", lambda m: link_sentinels[m[0]], fragment)
            put(current, text(fragment, True), fragment)

    for line in lines:
        if re.match(r"^(Copyright|版权所有|了解我们|友情链接|常用服务)", line, re.I):
            break
        match = marker.fullmatch(line) or standalone.fullmatch(line)
        if match:
            finish()
            current = LABELS[match[1]]
            content = [match[2]] if match.lastindex == 2 and match[2] else []
        elif current is not None:
            content.append(line)
    finish()
    if fields.get("course_id") != course:
        access_check(html)
        raise Error("identity_mismatch", "Detail page does not identify the requested course.")
    if not {"description", "description_en", "syllabus"} & fields.keys():
        raise Error("source_shape", "No introduction or outline field was recognized; absence cannot be inferred.")
    links, linked = [], False
    syllabus_fragment = dom(markup.get("syllabus", ""))
    anchors = list(syllabus_fragment.select("a[href]"))
    for anchor in anchors:
        try:
            target = urljoin(BASE + "courseDetail.php", anchor["href"])
            parsed = urlsplit(target)
            if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password and not re.search(r"[\x00-\x20\x7f]", target):
                links.append(target)
        except ValueError:
            pass
        anchor.decompose()
    if anchors:
        residue = re.sub(r"[\s:：，,。.]+", "", text(str(syllabus_fragment)))
        linked = residue in {"", "下载", "下載", "点击下载", "點擊下載", "请下载附件", "請下載附件"}
    values = {}
    for key in ("description", "description_en", "syllabus"):
        value = fields.pop(key, "").strip()
        values[key] = None if value.casefold() in EMPTY else value
    if linked:
        values["syllabus"] = None
    status = "full" if values["syllabus"] else "link_only" if linked else "intro_only" if values["description"] or values["description_en"] else "missing"
    syllabus_term = fields.pop("syllabus_term", None)
    fields.pop("course_id", None)
    return {**values, "syllabus_status": status, "syllabus_links": list(dict.fromkeys(links)), "syllabus_term": syllabus_term, "teaching_fields": fields}


class Client:
    def __init__(self, http: HTTP | None = None):
        self.http = http or HTTP()

    def options(self) -> dict:
        return parse_options(self.http.request("courseSearch.php"))

    def filters(self, term: str, department: str = "0", query: str = "", teacher: str = "") -> dict:
        term_parts(term)
        for value in (department, query, teacher):
            if len(value) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise Error("invalid_filter", "Filters must be at most 256 characters without controls.")
            scalar(value)
        options = self.options()
        terms = [t for t in options["terms"] if t["value"] == term]
        if len(terms) != 1:
            raise Error("unsupported_term", "Term is not listed by the source; run options.")
        departments = [d for d in options["departments"] if department in {d["value"], d["label"]}]
        if len(departments) != 1:
            raise Error("invalid_department", "Use an exact department value or label from options --field departments.")
        return {"yearandseme": terms[0]["source_value"], "yuanxi": departments[0]["source_value"], "coursetype": "0", "coursename": query, "teachername": teacher}

    def page(self, term: str, filters: dict, offset: int) -> tuple[int, list[dict]]:
        return parse_page(self.http.request("courseSearch_do.php", {**filters, "startrow": str(offset)}), term, offset)

    def rows(self, term: str, filters: dict):
        offset, expected, seen = 0, None, set()
        while True:
            total, rows = self.page(term, filters, offset)
            if expected is not None and total != expected:
                raise Error("source_changed", "Catalog total changed during pagination.")
            expected = total
            for row in rows:
                identity = ref(term, row)
                if identity in seen:
                    raise Error("source_changed", "Offering repeated across pages.")
                seen.add(identity)
                yield row
            offset += len(rows)
            if offset >= total:
                return

    def resolve(self, value: str) -> tuple[str, dict]:
        term, course, section, detail = read_ref(value)
        matches = [r for r in self.rows(term, self.filters(term, query=course)) if ref(term, r) == value]
        if len(matches) != 1:
            raise Error("stale_ref", "Reference no longer resolves uniquely; search again.")
        return term, matches[0]

    def detail(self, term: str, row: dict) -> dict:
        year, semester = term_parts(term)
        result = {"ref": ref(term, row), "term": term, "academic_year": year, "semester": semester, "course_id": identifier(row["kch"]), "section": identifier(row["jxbh"]), "detail_id": row.get("zxjhbh"), "source_urls": [BASE + "courseSearch.php"], "retrieved_at": datetime.now(timezone.utc).isoformat(), "retrieval_status": "ok", "description": None, "description_en": None, "syllabus": None, "syllabus_status": None, "syllabus_term": None}
        for field, key in (("name", "kcmc"), ("teachers", "teacher"), ("department", "kkxsmc"), ("credits", "xf"), ("schedule", "sksj"), ("weeks", "qzz"), ("category", "kctxm"), ("remarks", "bz")):
            result[field] = text(row.get(key), field in {"schedule", "remarks"}) or None
        try:
            if not row.get("zxjhbh"):
                raise Error("detail_unavailable", "Source provides no detail identifier for this offering.")
            params = {"flag": "1", "zxjhbh": identifier(row["zxjhbh"])}
            result["source_urls"].append(BASE + "courseDetail.php?" + urlencode(params))
            result.update(parse_detail(self.http.request("courseDetail.php", params), result["course_id"]))
        except Error as exc:
            result.update(retrieval_status="partial", error=exc.data())
        return result
