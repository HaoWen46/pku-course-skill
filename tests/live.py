"""Explicit network smoke test. Fail on upstream restrictions; never substitute fixtures."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts/pku.py'


def command(*args):
    proc = subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, timeout=75)
    if proc.returncode:
        print(json.dumps({'step': args[0], 'exit': proc.returncode, 'error': (proc.stderr or proc.stdout)[:1000]}), flush=True)
        raise SystemExit(1)
    assert len(proc.stdout.encode()) <= 4096, 'stdout budget exceeded'
    value = json.loads(proc.stdout)
    print(json.dumps({'step': args[0], 'status': 'passed'}), flush=True)
    return value


def main():
    terms = command('options')
    departments = command('options', '--field', 'departments')
    assert terms['items'] and departments['items'], 'source has no filter choices'
    term = terms['items'][0]['value']
    print(json.dumps({'test_term': term}), flush=True)
    sample = command('search', '--term', term, '--limit', '10')
    assert sample['items'], 'test term has no offerings; live retrieval is not verified'
    selected = None
    for candidate in sample['items'][:3]:
        code = candidate['ref'].split(':')[2]
        scoped = command('search', '--term', term, '--query', code)
        if 0 < scoped['total'] <= 5:
            selected = scoped['items'][0]
            break
    assert selected, 'no small query found; refuse an unbounded live export'
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        single = command('get', selected['ref'], '--out', str(root / 'course.json'))
        course = json.loads((root / 'course.json').read_text())
        assert course['ref'] == selected['ref'] and course['term'] == term
        assert course['name'] and course['teachers'], 'required course content missing'
        assert course['description'] or course['description_en'] or course['syllabus'], 'no teaching content retrieved'
        result = command('export', '--term', term, '--query', course['course_id'], '--out', str(root / 'export'))
        index = root / 'export/index.jsonl'
        rows = [json.loads(line) for line in index.read_text().splitlines()]
        assert result['complete'] and result['written'] == len(rows) > 0
        assert not (root / 'export/INCOMPLETE').exists()
        for row in rows:
            assert set(row) == {'file', 'name', 'teachers'}
            assert (root / 'export' / row['file']).is_file()
        print(json.dumps({'status': 'passed', 'term': term, 'exported': len(rows), 'syllabus_status': single['syllabus_status']}), flush=True)


if __name__ == '__main__':
    main()
