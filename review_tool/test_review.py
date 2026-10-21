"""Tests for the standalone review tool. Run: uv run pytest review_tool
(Not part of the main suite, so deleting review_tool/ leaves `uv run pytest` unchanged.)"""
import importlib.util
import json
import shutil
from pathlib import Path
import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('review', HERE / 'review.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)
GOLDEN = HERE.parent / 'data/golden/quotes-v1'

GENERATOR = 'synthetic-generator-v1 (not human-reviewed)'

@pytest.fixture
def folder(tmp_path):
    """An unreviewed copy of quote_004, whatever state the real golden file is in."""
    shutil.copy(GOLDEN / 'quote_004.pdf', tmp_path / 'quote_004.pdf')
    data = json.loads((GOLDEN / 'quote_004.json').read_text())
    data['metadata'] = {'label_source': 'generated', 'human_reviewed': False}
    for field in data['fields']:
        field['labeller'] = GENERATOR
    (tmp_path / 'quote_004.json').write_text(json.dumps(data))
    return tmp_path

def decisions(path, **overrides):
    fields = json.loads(path.read_text())['fields']
    out = [{'name': f['name'], 'value': str(f['value']), 'remove': False, 'checked': True} for f in fields]
    for d in out:
        d.update(overrides.get(d['name'], {}))
    return out

def test_review_signs_off_every_field_and_logs_changes(folder):
    path = folder / 'quote_004.json'
    summary = review.apply_review(path, 'Srini', decisions(path, **{
        'currency': {'value': 'USD'},                       # corrected
        'line_items[0].moq': {'value': '9'},                # unchanged (9 == 9)
        'incoterms': {'remove': True, 'checked': False},    # not on the page
    }), notes='checked against print-out', now='2026-10-15T09:00:00+00:00')
    data = json.loads(path.read_text())
    assert summary == {'changed': 1, 'removed': 1, 'fields': 12}
    assert {f['labeller'] for f in data['fields']} == {'Srini'}
    assert {f['labelled_at'] for f in data['fields']} == {'2026-10-15T09:00:00+00:00'}
    assert next(f for f in data['fields'] if f['name'] == 'currency')['value'] == 'USD'
    assert not any(f['name'] == 'incoterms' for f in data['fields'])
    moq = next(f for f in data['fields'] if f['name'] == 'line_items[0].moq')
    assert moq['value'] == 9 and isinstance(moq['value'], int)  # number stays a number
    meta = data['metadata']
    assert meta['human_reviewed'] is True and meta['reviewed_by'] == 'Srini'
    assert meta['original_labeller'] == GENERATOR
    assert [c['change'] for c in meta['review_changes']] == ['value', 'removed']
    assert meta['review_changes'][0] == {'field': 'currency', 'change': 'value', 'from': 'CAD', 'to': 'USD',
                                         'by': 'Srini', 'at': '2026-10-15T09:00:00+00:00'}
    assert meta['review_notes'][0]['note'] == 'checked against print-out'

def test_every_field_must_be_checked(folder):
    path = folder / 'quote_004.json'
    before = path.read_text()
    with pytest.raises(ValueError, match='Every field must be checked'):
        review.apply_review(path, 'Srini', decisions(path, currency={'checked': False}))
    assert path.read_text() == before  # nothing written

def test_pages_render(folder):
    assert review.render_index(folder, 'Srini').count('to review') == 1
    page = review.render_quote(folder, 'Srini', 'quote_004')
    assert 'Meridian Aerospace Supply' in page and 'line_items[0].moq' in page
    assert review.render_quote(folder, 'Srini', '../etc') is None
    assert review.page_png(folder / 'quote_004.pdf', 1)[:4] == b'\x89PNG'

def test_independent_of_the_pipeline():
    import re
    source = (HERE / 'review.py').read_text()
    for package in ('extract', 'parse', 'eval', 'rules', 'storage', 'temporal_app', 'ui', 'llm', 'ingest', 'tools'):
        assert not re.search(rf'^\s*(from|import)\s+{package}\b', source, re.M), f'review.py imports {package}'
    root = HERE.parent
    for path in root.rglob('*.py'):
        if '.venv' in path.parts or HERE in path.parents:
            continue
        assert 'review_tool' not in path.read_text(), f'{path} depends on review_tool'
