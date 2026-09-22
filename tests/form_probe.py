"""Print public filter structure only; never emit page bodies, input values, or cookies."""
import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from bs4 import BeautifulSoup
from dean import HTTP, access_check

body = HTTP().request('courseSearch.php')
access_check(body, form_page=True)
soup = BeautifulSoup(body, 'html.parser')
result = []
for node in soup.find_all(string=True):
    value = ' '.join(str(node).split())
    if not re.fullmatch(r'(?:\d{2}|\d{4})-(?:\d{2}|\d{4})学年\s*第\s*\d\s*学期|学年\s*学期|开课\s*院系', value):
        continue
    item = {'public_label': value, 'ancestors': []}
    tag = node.parent
    for _ in range(4):
        if not tag or tag.name in {'html', 'body', '[document]'}:
            break
        attrs = {key: val for key, val in tag.attrs.items() if key in {'class', 'id', 'name'}}
        source_values = {key: val for key, val in tag.attrs.items() if isinstance(val, str) and re.fullmatch(r'(?:\d{2}|\d{4})-(?:\d{2}|\d{4})-\d', val)}
        item['ancestors'].append({'tag': tag.name, 'attributes': sorted(tag.attrs), 'structure': attrs, 'term_values': source_values, 'children': [{'tag': child.name, 'attributes': sorted(child.attrs)} for child in tag.find_all(recursive=False)[:8]]})
        tag = tag.parent
    result.append(item)
print(json.dumps(result[:8], ensure_ascii=False)[:12000])
