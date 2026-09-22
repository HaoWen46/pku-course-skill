"""Review regressions for changing catalogs, interrupted output, and packaging."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_pku import ROOT, TERM, FakeHTTP, dean, html, invoke, output, row


class PaginationChanges(unittest.TestCase):
    class Changed(FakeHTTP):
        def request(self, endpoint, params=None):
            body = super().request(endpoint, params)
            if endpoint == 'courseSearch_do.php' and params['startrow'] == '10':
                result = json.loads(body)
                result['count'] = '21'
                return json.dumps(result)
            return body

    class Repeated(FakeHTTP):
        def request(self, endpoint, params=None):
            if endpoint == 'courseSearch_do.php' and params['startrow'] == '10':
                return json.dumps({'status': 'ok', 'count': 20, 'courselist': self.data[:10]})
            return super().request(endpoint, params)

    def test_search_detects_changed_count_between_pages(self):
        code, data, error = invoke(['search', '--term', TERM, '--offset', '8', '--limit', '5'], self.Changed([row(n) for n in range(1, 21)]))
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(error)['error']['code'], 'source_changed')
        self.assertEqual(data, '')

    def test_export_detects_changed_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp).resolve() / 'export'
            code, data, _ = invoke(['export', '--term', TERM, '--out', str(out)], self.Changed([row(n) for n in range(1, 21)]))
            result = json.loads(data)
            self.assertEqual((code, result['written']), (1, 10))
            self.assertFalse(result['enumeration_complete'])
            self.assertTrue((out / 'INCOMPLETE').exists())

    def test_export_detects_repeated_page(self):
        client = dean.Client(self.Repeated([row(n) for n in range(1, 21)]))
        with self.assertRaises(dean.Error) as caught:
            list(client.rows(TERM, client.filters(TERM)))
        self.assertEqual(caught.exception.code, 'source_changed')

    def test_search_at_or_past_end_has_no_continuation(self):
        for offset in (1, 10, 100):
            code, data, _ = invoke(['search', '--term', TERM, '--offset', str(offset)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(data)['items'], [])
            self.assertIsNone(json.loads(data)['next_offset'])

    def test_options_budget_continuation(self):
        choices = [{'value': str(i), 'label': '院' * 300} for i in range(10)]
        result = output.page(choices, 0, 10, field='departments')
        self.assertLessEqual(len((dean.compact(result) + '\n').encode()), 4096)
        self.assertEqual(result['next_offset'], len(result['items']))


class FailureBoundaries(unittest.TestCase):
    def test_disk_failure_writes_incomplete_receipt_when_possible(self):
        real = output.write_json
        def fail_course(path, value):
            if path.parent.name == 'courses':
                raise OSError('disk failure')
            real(path, value)
        with tempfile.TemporaryDirectory() as tmp, patch.object(output, 'write_json', side_effect=fail_course):
            out = Path(tmp).resolve() / 'export'
            code, data, _ = invoke(['export', '--term', TERM, '--out', str(out)])
            result = json.loads(data)
            self.assertEqual(code, 1)
            self.assertFalse(result['complete'])
            self.assertEqual(result['error']['code'], 'io_error')
            self.assertEqual(result['written'], 0)
            self.assertTrue((out / 'receipt.json').exists())
            self.assertTrue((out / 'INCOMPLETE').exists())

    def test_receipt_write_failure_does_not_report_success(self):
        real = output.write_json
        def fail_receipt(path, value):
            if path.name == 'receipt.json':
                raise OSError('disk failure')
            real(path, value)
        with tempfile.TemporaryDirectory() as tmp, patch.object(output, 'write_json', side_effect=fail_receipt):
            out = Path(tmp).resolve() / 'export'
            code, data, error = invoke(['export', '--term', TERM, '--out', str(out)])
            self.assertEqual(code, 2)
            self.assertEqual(data, '')
            self.assertTrue((out / 'INCOMPLETE').exists())
            self.assertEqual(json.loads(error)['error']['code'], 'io_error')

    def test_missing_parent_fails_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            http = FakeHTTP()
            code, _, _ = invoke(['get', dean.ref(TERM, row()), '--out', str(Path(tmp).resolve() / 'missing/file.json')], http)
            self.assertEqual(code, 2)
            self.assertFalse(http.calls)

    def test_no_persisted_cookie_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            invoke(['get', dean.ref(TERM, row()), '--out', str(root / 'course.json')])
            self.assertEqual([p.name for p in root.iterdir()], ['course.json'])

    def test_nested_table_content_keeps_paragraphs(self):
        body = '<table><tr><th>课程号</th><td>04800001</td></tr><tr><th>教学大纲</th><td><table><tr><td>Chapter 1</td><td>Chapter 2</td></tr></table></td></tr></table>'
        result = dean.parse_detail(body, '04800001')
        self.assertEqual(result['syllabus'], 'Chapter 1\nChapter 2')

    def test_conflicting_table_fields_are_rejected(self):
        body = '<table><tr><th>课程号</th><td>04800001</td></tr><tr><th>课程号</th><td>99999999</td></tr><tr><th>教学大纲</th><td>Chapter</td></tr></table>'
        with self.assertRaises(dean.Error):
            dean.parse_detail(body, '04800001')

    def test_syllabus_date_is_not_offering_date(self):
        body = html(outline='Chapter').replace('</main>', '<p>大纲适用学期：2023-2024-1</p></main>')
        value = dean.parse_detail(body, '04800001')
        self.assertEqual(value['syllabus_term'], '2023-2024-1')

    def test_source_teacher_split_is_not_inferred(self):
        _, data, _ = invoke(['search', '--term', TERM], FakeHTTP([row(teacher='Smith, John; 王小明/李某')]))
        self.assertEqual(json.loads(data)['items'][0]['teachers'], 'Smith, John; 王小明/李某')


class ObservedFormRegression(unittest.TestCase):
    def test_pku_span_data_controls(self):
        form = '<div class="courseSearch"><div class="courseTop"><div class="ctLeft"><span>学年学期</span></div><div class="ctRight"><span class="yearandseme" data="25-26-2">25-26学年 第2学期</span><span class="yearandseme" data="25-26-3">25-26学年 第3学期</span><span class="ctActive yearandseme" data="26-27-1">26-27学年 第1学期</span><input id="yearandseme" type="hidden" value="26-27-1"></div></div><div class="courseBottom"><div class="ctLeft"><span>开课院系</span></div><div class="ctRight"><span data="0">全部</span><span data="048">信息科学技术学院</span><input id="yuanxi" type="hidden" value="0"></div></div></div>'
        value = dean.parse_options(form)
        self.assertEqual([v['value'] for v in value['terms']], ['2025-2026-2', '2025-2026-3', TERM])
        self.assertEqual([v['value'] for v in value['departments']], ['0', '048'])


class Packaging(unittest.TestCase):
    def test_skill_name_matches_install_directory(self):
        skill = (ROOT / 'SKILL.md').read_text()
        self.assertTrue(skill.startswith('---\nname: pku-course-skill\n'))
        self.assertIn('description:', skill.split('---', 2)[1])

    def test_documented_files_exist(self):
        for file in ('scripts/pku.py', 'scripts/dean.py', 'scripts/output.py', 'pyproject.toml', 'uv.lock', 'references/sources.md', 'references/review.md'):
            self.assertTrue((ROOT / file).is_file(), file)

    def test_no_translations_or_database_scaffolding(self):
        self.assertFalse((ROOT / 'locales').exists())
        self.assertFalse(list(ROOT.glob('*.sqlite*')))

    def test_readme_has_install_and_verification_commands(self):
        readme = (ROOT / 'README.md').read_text()
        self.assertIn('.agents/skills/pku-course-skill', readme)
        self.assertIn('uv run --locked python -m unittest discover', readme)
        self.assertIn('uv run --locked tests/live.py', readme)


if __name__ == '__main__':
    unittest.main()
