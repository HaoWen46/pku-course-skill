"""Synthetic source fixtures: these tests do not claim live PKU verification."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from email.message import Message

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pku", ROOT / "scripts/pku.py")
pku = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pku)
TERM = "2026-2027-1"
OPTIONS = """<select name="yearandseme"><option value="25-26-3">25-26学年 第3学期</option><option value="26-27-1">26-27学年 第1学期</option></select><select name="yuanxi"><option value="0">全部</option><option value="048">信息科学技术学院</option></select><select name="coursetype"><option value="0">全部</option></select>"""


def row(n=1, **extra):
    return {"kch": f"04800{n:03d}", "jxbh": "1", "zxjhbh": f"BZ2627_{n}", "kcmc": "测试课程", "teacher": "<p>教师甲</p><p>教師乙</p>", "kkxsmc": "信息科学技术学院", "xf": "3", "sksj": "<p>星期二</p><p>第3-4节</p>", **extra}


def detail_html(course, syllabus=False):
    return f"""<header>Ignore this navigation</header><main><p>课程号： {course}</p><p>学分： 3</p><p>开课院系： 信息科学技术学院</p><div><span>中文简介：</span><p>简介第一段。</p><p>第二段含有<strong>强调</strong>。</p></div>{'<div>教学大纲：<p>第一章 算法。</p><p>第二章 系统。</p></div>' if syllabus else ''}</main><footer>footer noise</footer><script>malicious()</script>"""


class FakeTransport:
    def __init__(self, rows=None, details=None):
        self.data = [row()] if rows is None else rows
        self.details = details or {}
        self.calls = []

    def request(self, path, *, form=None, query=None):
        self.calls.append((path, form, query))
        if path == "courseSearch.php":
            return OPTIONS
        if path == "courseSearch_do.php":
            data = self.data
            if form.get("coursename") and form["coursename"].isdigit():
                data = [r for r in data if r["kch"] == form["coursename"]]
            offset = int(form["startrow"])
            return json.dumps({"status": "ok", "count": str(len(data)), "courselist": data[offset:offset + 10]})
        identity = query["zxjhbh"]
        if identity in self.details:
            result = self.details[identity]
            if isinstance(result, Exception):
                raise result
            return result
        match = next(r for r in self.data if r["zxjhbh"] == identity)
        return detail_html(match["kch"])


class TermAndOptionsTests(unittest.TestCase):
    def test_normalize_and_third_semester(self):
        options = pku.parse_options(OPTIONS)
        self.assertEqual(options["terms"][0]["value"], "2025-2026-3")
        self.assertEqual(pku.term_parts(TERM), ("2026-2027", 1))

    def test_invalid_terms(self):
        for term in ("2026", "2026-2028-1", "26-27-1", "2026-2027-0", "2026-2027-1\n"):
            with self.subTest(term=term), self.assertRaises(pku.Failure):
                pku.term_parts(term)

    def test_custom_dropdown(self):
        html = OPTIONS.replace('<select name="yearandseme">', '<div><label>学年学期</label><input name="yearandseme"><ul>').replace('</select>', '</ul></div>', 1)
        html = html.replace('<option value="25-26-3">', '<li data-value="25-26-3">').replace('<option value="26-27-1">', '<li data-value="26-27-1">').replace('</option>', '</li>', 2)
        self.assertEqual(pku.parse_options(html)["terms"][1]["value"], TERM)

    def test_bad_options_not_empty(self):
        with self.assertRaises(pku.Failure):
            pku.parse_options("<h1>Service unavailable</h1>")

    def test_challenge_options(self):
        with self.assertRaises(pku.Failure) as cm:
            pku.parse_options('<input type="password">请先登录')
        self.assertEqual(cm.exception.code, "access_restricted")

    def test_department_and_term_mapping(self):
        c = pku.Client(FakeTransport())
        params = c.filters(TERM, "信息科学技术学院", "算法", "教师甲")
        self.assertEqual(params["yearandseme"], "26-27-1")
        self.assertEqual(params["yuanxi"], "048")
        self.assertEqual(params["coursename"], "算法")

    def test_no_silent_term_or_department_guess(self):
        c = pku.Client(FakeTransport())
        for term, dep in (("2020-2021-1", "0"), (TERM, "计算机专业")):
            with self.assertRaises(pku.Failure):
                c.filters(term, dep, "", "")

    def test_filter_control_characters(self):
        fake = FakeTransport()
        with self.assertRaises(pku.Failure):
            pku.Client(fake).filters(TERM, "0", "q\nHost: evil", "")
        self.assertFalse(fake.calls)


class SearchTests(unittest.TestCase):
    def test_compact_result(self):
        fake = FakeTransport()
        result = pku.Client(fake).search(TERM)
        self.assertEqual(set(result["items"][0]), {"ref", "name", "teachers"})
        self.assertEqual(result["items"][0]["teachers"], "教师甲 教師乙")
        self.assertIsNone(result["next_offset"])
        self.assertNotIn("courseDetail.php", [x[0] for x in fake.calls])

    def test_custom_limit_and_offset(self):
        client = pku.Client(FakeTransport([row(n) for n in range(1, 26)]))
        first = client.search(TERM, limit=3)
        self.assertEqual(first["next_offset"], 3)
        second = client.search(TERM, offset=3, limit=3)
        self.assertNotEqual(first["items"][0]["ref"], second["items"][0]["ref"])
        self.assertEqual(client.search(TERM, offset=25)["items"], [])

    def test_output_budget_does_not_skip_rows(self):
        client = pku.Client(FakeTransport([row(n, kcmc="课" * 200) for n in range(1, 11)]))
        result = client.search(TERM)
        self.assertLessEqual(len(pku.encode(result).encode()), 4096)
        self.assertEqual(result["next_offset"], len(result["items"]))
        self.assertLess(len(result["items"]), 10)

    def test_oversized_item(self):
        with self.assertRaises(pku.Failure) as cm:
            pku.Client(FakeTransport([row(kcmc="大" * 5000)])).search(TERM)
        self.assertEqual(cm.exception.code, "output_too_large")

    def test_separate_sections_and_terms(self):
        self.assertNotEqual(pku.reference(TERM, row()), pku.reference(TERM, row(jxbh="2")))
        self.assertNotEqual(pku.reference(TERM, row()), pku.reference("2025-2026-1", row()))

    def test_invalid_pagination_before_network(self):
        fake = FakeTransport()
        for offset, limit in ((-1, 1), (0, 11), (0, 0)):
            with self.assertRaises(pku.Failure):
                pku.Client(fake).search(TERM, offset=offset, limit=limit)
        self.assertFalse(fake.calls)

    def test_zero_matches(self):
        result = pku.Client(FakeTransport([])).search(TERM)
        self.assertEqual(result["total"], 0)
        self.assertIsNone(result["next_offset"])

    def test_malformed_page_is_not_empty_success(self):
        cases = ['{}', '{"status":"error"}', '{"status":"ok","count":"oops","courselist":[]}', '{"status":"ok","count":true,"courselist":[]}', '{"status":"ok","count":0}', '<h1>maintenance</h1>', '{"status":"ok","count":10,"courselist":[]}']
        for body in cases:
            with self.subTest(body=body), self.assertRaises(pku.Failure):
                pku.parse_page(body, TERM, 0)

    def test_duplicate_page_rows(self):
        with self.assertRaises(pku.Failure):
            pku.Client(FakeTransport([row(), row()])).search(TERM)

    def test_challenge_not_empty_result(self):
        with self.assertRaises(pku.Failure) as cm:
            pku.parse_page("<p>请输入验证码</p>", TERM, 0)
        self.assertEqual(cm.exception.code, "access_restricted")


class DetailTests(unittest.TestCase):
    def test_intro_not_syllabus(self):
        result = pku.parse_detail(detail_html("04800001"), "04800001")
        self.assertEqual(result["syllabus_status"], "intro_only")
        self.assertIsNone(result["fields"]["syllabus"])
        self.assertEqual(result["fields"]["description"], "简介第一段。\n第二段含有强调。")

    def test_full_outline(self):
        result = pku.parse_detail(detail_html("04800001", True), "04800001")
        self.assertEqual(result["syllabus_status"], "full")
        self.assertNotIn("footer", result["fields"]["syllabus"])
        self.assertNotIn("malicious", result["fields"]["syllabus"])

    def test_table_fields(self):
        html = '<table><tr><th>课程号</th><td>04800001</td><th>学分</th><td>3</td></tr><tr><td>教学大纲</td><td><p>第一章</p><p>第二章</p></td></tr></table>'
        result = pku.parse_detail(html, "04800001")
        self.assertEqual(result["fields"]["credits"], "3")
        self.assertEqual(result["fields"]["syllabus"], "第一章\n第二章")

    def test_placeholder_not_full(self):
        html = '<p>课程号：04800001</p><p>教学大纲：暂无</p>'
        self.assertEqual(pku.parse_detail(html, "04800001")["syllabus_status"], "missing")

    def test_wrong_identity_rejected(self):
        with self.assertRaises(pku.Failure) as cm:
            pku.parse_detail(detail_html("99999999"), "04800001")
        self.assertEqual(cm.exception.code, "identity_mismatch")

    def test_missing_detail_is_partial(self):
        client = pku.Client(FakeTransport())
        result = client.detail(TERM, row(zxjhbh=""))
        self.assertEqual(result["retrieval_status"], "partial")
        self.assertIsNone(result["syllabus_status"])

    def test_failed_detail_is_not_missing_syllabus(self):
        fake = FakeTransport(details={"BZ2627_1": pku.Failure("access_restricted", "Blocked.")})
        result = pku.Client(fake).detail(TERM, row())
        self.assertEqual(result["retrieval_status"], "partial")
        self.assertIsNone(result["syllabus_status"])
        self.assertIn("error", result)

    def test_get_is_stateless_and_term_context_is_preserved(self):
        ref = pku.Client(FakeTransport()).search(TERM)["items"][0]["ref"]
        client = pku.Client(FakeTransport())
        term, data = client.resolve(ref)
        result = client.detail(term, data)
        self.assertEqual(result["teachers"], "教师甲 教師乙")
        self.assertEqual(result["academic_year"], "2026-2027")
        self.assertEqual(result["semester"], 1)
        self.assertIsNone(result["syllabus_term"])

    def test_stale_ref(self):
        with self.assertRaises(pku.Failure) as cm:
            pku.Client(FakeTransport([])).resolve(pku.reference(TERM, row()))
        self.assertEqual(cm.exception.code, "stale_ref")

    def test_ref_injection_rejected_before_requests(self):
        for ref in ('https://127.0.0.1/', 'file:///etc/passwd', f'pku:{TERM}:1:1:../../x', f'pku:{TERM}:1:1:x?flag=2', 'x' * 10000):
            with self.subTest(ref=ref[:50]), self.assertRaises(pku.Failure):
                pku.parse_reference(ref)


class ExportTests(unittest.TestCase):
    def run_export(self, client, out):
        return pku.export(client, TERM, client.filters(TERM, "0", "", ""), out)

    def test_pagination_files_and_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "export"
            receipt, code = self.run_export(pku.Client(FakeTransport([row(n) for n in range(1, 24)])), out)
            self.assertEqual((receipt["written"], receipt["failed"], code), (23, 0, 0))
            self.assertTrue(receipt["complete"])
            rows = [json.loads(x) for x in (out / "index.jsonl").read_text().splitlines()]
            self.assertEqual(set(rows[0]), {"file", "name", "teachers"})
            self.assertEqual(len(rows), 23)
            self.assertFalse((out / "INCOMPLETE").exists())
            self.assertTrue((out / rows[-1]["file"]).is_file())

    def test_failed_detail_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "export"
            fake = FakeTransport(details={"BZ2627_1": pku.Failure("identity_mismatch", "Wrong page.")})
            receipt, code = self.run_export(pku.Client(fake), out)
            self.assertEqual((receipt["written"], receipt["failed"], code), (1, 1, 1))
            self.assertFalse(receipt["complete"])
            self.assertTrue((out / "INCOMPLETE").exists())

    def test_rate_limit_stops_bulk_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeTransport([row(1), row(2)], {"BZ2627_1": pku.Failure("rate_limited", "Stop.")})
            receipt, code = self.run_export(pku.Client(fake), Path(tmp) / "export")
            self.assertEqual(receipt["written"], 1)
            self.assertEqual(code, 1)
            self.assertEqual(sum(x[0] == "courseDetail.php" for x in fake.calls), 1)

    def test_pagination_failure_retains_incomplete_marker(self):
        class Broken(pku.Client):
            def rows(self, term, filters):
                yield row()
                raise pku.Failure("source_changed", "Page failed.")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "export"
            receipt, code = self.run_export(Broken(FakeTransport()), out)
            self.assertEqual(receipt["written"], 1)
            self.assertEqual(receipt["error"]["code"], "source_changed")
            self.assertTrue((out / "INCOMPLETE").exists())
            self.assertEqual(code, 1)

    def test_changed_total_detected(self):
        class Changing(pku.Client):
            def page(self, term, filters, offset):
                return ([row(n) for n in range(1, 11)], 20) if offset == 0 else ([row(11)], 11)
        with self.assertRaises(pku.Failure) as cm:
            list(Changing(FakeTransport()).rows(TERM, {}))
        self.assertEqual(cm.exception.code, "source_changed")

    def test_repeated_pages_detected(self):
        class Repeating(pku.Client):
            def page(self, term, filters, offset):
                return [row(n) for n in range(1, 11)], 20
        with self.assertRaises(pku.Failure):
            list(Repeating(FakeTransport()).rows(TERM, {}))

    def test_existing_export_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileExistsError):
                self.run_export(pku.Client(FakeTransport()), Path(tmp))

    def test_existing_file_and_symlink_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            path.write_text("original")
            with self.assertRaises(FileExistsError):
                pku.write_json(path, {})
            link = Path(tmp) / "link.json"
            link.symlink_to(path)
            with self.assertRaises(FileExistsError):
                pku.write_json(link, {})
            self.assertEqual(path.read_text(), "original")


class TransportAndCLITests(unittest.TestCase):
    def test_fixed_endpoint_allowlist(self):
        for path in ("https://evil.example/", "../courseSearch.php", "courseSearch.php?x=y", "/etc/passwd"):
            with self.assertRaises(pku.Failure):
                pku.Transport().request(path)

    def test_redirects_are_not_followed(self):
        self.assertIsNone(pku.NoRedirects().redirect_request(None, None, 302, "", {}, "http://127.0.0.1/"))

    def test_http_failures_are_structured(self):
        for status, code in ((302, "access_restricted"), (403, "access_restricted"), (429, "rate_limited"), (503, "http_error")):
            transport = pku.Transport()
            with patch.object(transport.opener, "open", side_effect=HTTPError("url", status, "", {}, None)):
                with self.assertRaises(pku.Failure) as cm:
                    transport.request("courseSearch.php")
            self.assertEqual(cm.exception.code, code)

    def test_network_failure_no_raw_exception(self):
        transport = pku.Transport()
        with patch.object(transport.opener, "open", side_effect=URLError("secret raw body")):
            with self.assertRaises(pku.Failure) as cm:
                transport.request("courseSearch.php")
        self.assertNotIn("secret", cm.exception.message)

    def test_response_size_limit(self):
        class Response(io.BytesIO):
            headers = Message()
        transport = pku.Transport()
        with patch.object(transport.opener, "open", return_value=Response(b"x" * (pku.MAX_RESPONSE + 1))):
            with self.assertRaises(pku.Failure) as cm:
                transport.request("courseSearch.php")
        self.assertEqual(cm.exception.code, "response_too_large")

    def test_query_is_encoded(self):
        class Response(io.BytesIO):
            headers = Message()
        transport = pku.Transport()
        with patch.object(transport.opener, "open", return_value=Response(b"ok")) as mock:
            transport.request("courseSearch_do.php", form={"coursename": "甲&teachername=乙"})
        self.assertIn(b"%26teachername%3D", mock.call_args.args[0].data)

    def test_argument_error_json(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            code = pku.main(["search"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "invalid_arguments")

    def test_end_to_end_cli_commands(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(pku, "Transport", side_effect=FakeTransport):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(pku.main(["options"]), 0)
            self.assertNotIn("source_value", stdout.getvalue())
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(pku.main(["search", "--term", TERM]), 0)
            ref = json.loads(stdout.getvalue())["items"][0]["ref"]
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(pku.main(["get", ref, "--out", str(Path(tmp) / "course.json")]), 0)
            self.assertNotIn("description", json.loads(stdout.getvalue()))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pku.main(["export", "--term", TERM, "--out", str(Path(tmp) / "export")]), 0)

    def test_help_exits_without_network(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/pku.py"), "--help"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn("export", result.stdout)



class SecurityRegressionTests(unittest.TestCase):
    def test_symlink_parent_cannot_redirect_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "outside").mkdir()
            (root / "redirect").symlink_to(root / "outside", target_is_directory=True)
            with self.assertRaises(OSError):
                pku.write_json(root / "redirect" / "course.json", {})
            with self.assertRaises(OSError):
                pku.new_directory(root / "redirect" / "export")
            self.assertEqual(list((root / "outside").iterdir()), [])

    def test_parent_traversal_rejected(self):
        with self.assertRaises(pku.Failure):
            pku.write_json(Path("somewhere/../course.json"), {})

    def test_failed_atomic_write_leaves_no_final_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "course.json"
            with patch.object(pku.os, "fsync", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    pku.write_json(destination, {"x": "y"})
            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_readonly_file_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "course.json"
            pku.write_json(path, {})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_attachment_link_is_not_full_syllabus(self):
        html = '<p>课程号：04800001</p><p>教学大纲：<a href="/files/outline.pdf">下载大纲</a></p>'
        result = pku.parse_detail(html, "04800001")
        self.assertEqual(result["syllabus_status"], "link_only")
        self.assertIsNone(result["fields"]["syllabus"])
        self.assertEqual(result["fields"]["syllabus_links"], ["https://dean.pku.edu.cn/files/outline.pdf"])

    def test_footer_div_does_not_enter_last_field(self):
        html = '<p>课程号：04800001</p><p>英文简介：Introduction.</p><div>了解我们</div><div>版权导航</div>'
        self.assertEqual(pku.parse_detail(html, "04800001")["fields"]["description_en"], "Introduction.")

    def test_embedded_script_does_not_execute_or_enter_data(self):
        html = '<p>课程号：04800001</p><p>教学大纲：第一章<script>steal()</script>。</p>'
        value = pku.parse_detail(html, "04800001")["fields"]["syllabus"]
        self.assertEqual(value, "第一章。")

    def test_output_names_never_come_from_course_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = pku.Client(FakeTransport([row(kcmc="../../outside.json; echo hacked")]))
            out = Path(tmp) / "export"
            receipt, code = pku.export(client, TERM, client.filters(TERM, "0", "", ""), out)
            self.assertEqual(code, 0)
            self.assertEqual([x.name for x in (out / "courses").iterdir()], ["1.json"])
            self.assertEqual(list(Path(tmp).iterdir()), [out])



class AdditionalBoundaryTests(unittest.TestCase):
    def test_malformed_attachment_url_is_not_a_crash_or_syllabus(self):
        html = '<p>课程号：04800001</p><p>教学大纲：<a href="http://[invalid">下载</a></p>'
        result = pku.parse_detail(html, "04800001")
        self.assertEqual(result["syllabus_status"], "link_only")
        self.assertEqual(result["fields"]["syllabus_links"], [])

    def test_script_link_is_never_exposed_as_a_download_url(self):
        html = '<p>课程号：04800001</p><p>教学大纲：<a href="javascript:steal()">下载</a></p>'
        result = pku.parse_detail(html, "04800001")
        self.assertEqual(result["syllabus_status"], "link_only")
        self.assertEqual(result["fields"]["syllabus_links"], [])

    def test_captcha_input_response_is_access_restricted(self):
        with self.assertRaises(pku.Failure) as cm:
            pku.parse_page('<input placeholder="请输入验证码">', TERM, 0)
        self.assertEqual(cm.exception.code, "access_restricted")

    def test_encoded_newlines_in_output_are_not_extra_index_rows(self):
        encoded = pku.encode({"name": "A\nB", "teachers": "C"})
        self.assertEqual(len(encoded.splitlines()), 1)
        self.assertEqual(json.loads(encoded)["name"], "A\nB")


if __name__ == "__main__":
    unittest.main()
