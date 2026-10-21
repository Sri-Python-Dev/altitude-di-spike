"""The viewer: pages, scoring against known answers, currency rules, uploads (test database; skipped if unreachable)."""
import json
from tests.test_postgres import conn, quote  # noqa: F401  (fixture and sample quote)

def test_index_and_document_pages(conn):
    from storage.postgres import record_document, save_quote
    from ui.server import APP_NAME, render_document, render_index
    record_document(conn, 'doc1', kind='pdf', status='parsed', file_path='/nowhere/x.pdf')
    save_quote(conn, 'doc1', 'doc1', quote(), pages=(1, 1), extractor='current', model='gpt-4.1-nano',
               flags=[{'flag_code': 'CURRENCY_MISMATCH', 'severity': 'review', 'field': 'currency', 'message': 'Quote currency differs'}])
    record_document(conn, 'locked', kind='pdf', status='unparseable', reason='password_protected')
    index = render_index(conn)
    assert APP_NAME in index and 'Doc AI' not in index
    assert 'doc1' in index and 'password_protected' in index and '<b>2</b><span>Documents' in index
    page = render_document(conn, 'doc1', {'field': 'line_items[0].unit_price'})
    assert '29.05' in page and 'ACT-300' in page and 'class="tag grounded"' in page
    assert 'CURRENCY_MISMATCH' in page and 'Quote currency differs' in page          # rule flags shown with the field
    assert 'ungrounded · span_not_found' in page and 'FOB invented' in page            # what the model claimed, shown greyed
    assert 'not scored' in page                                                       # no golden file
    assert 'raw_confidence' not in page and 'uncalibrated' in render_document(conn, 'doc1', {'debug': ''})
    assert 'Resend' in page
    assert render_document(conn, 'missing', {}) is None

def test_values_are_html_escaped(conn):
    from extract.schemas import Evidenced, SupplierQuote
    from storage.postgres import record_document, save_quote
    from ui.server import render_document
    record_document(conn, 'doc2', kind='pdf', status='parsed')
    q = SupplierQuote(supplier_name=Evidenced(value='<script>x</script>', page=1, bbox=(1, 1, 2, 2), verbatim_span='<script>x</script>', grounded=True))
    save_quote(conn, 'doc2', 'doc2', q, pages=(1, 1), extractor=None, model=None)
    page = render_document(conn, 'doc2', {'field': 'supplier_name'})
    assert '<script>x</script>' not in page and '&lt;script&gt;' in page

def test_files_outside_the_repo_are_not_served():
    from ui.server import _safe_file
    assert _safe_file('/etc/passwd') is None and _safe_file('/tmp/x.pdf') is None and _safe_file('/etc/x.eml', ('.eml',)) is None

def _report(run_id, accuracy, delta=None, worst=None):
    return {'run_id': run_id, 'extractor': 'current', 'model': 'gpt-4.1-nano', 'parser': 'docling 2', 'generated_at': run_id,
            'document_count': 2, 'labellers': ['srini'], 'git_commit': None, 'command': 'uv run python -m eval.run --x',
            'dataset_summary': [{'name': 'quotes-v1', 'documents': 2}], 'previous_run_id': None,
            'fields': {'currency': {'label': 'Currency', 'accuracy': accuracy, 'grounded_rate': 1.0, 'ungrounded_rate': 0.0,
                                    'missing_rate': 0.0, 'delta': delta, 'worst_doc': worst, 'worst_doc_field': 'currency'},
                       'line_items[i].moq': {'label': 'MOQ', 'accuracy': 1.0, 'grounded_rate': 1.0, 'ungrounded_rate': 0.0,
                                             'missing_rate': 0.0, 'delta': None, 'worst_doc': None}},
            'edge_case_summary': [{'case': 'Scanned PDF', 'what_happened': 'OCR used', 'pass': True}]}

def test_results_page_renders_the_report_as_written(tmp_path, monkeypatch):
    import ui.server as server
    monkeypatch.setattr(server, 'REPORTS', tmp_path)
    (tmp_path / 'A.json').write_text(json.dumps(_report('A', 0.75, delta=-0.05, worst='quote_003')))
    (tmp_path / 'B.json').write_text(json.dumps(_report('B', 1.0)))
    (tmp_path / 'latest.json').write_text(json.dumps(_report('A', 0.75, delta=-0.05, worst='quote_003')))
    page = server.render_results({})
    assert page.index('Currency') < page.index('MOQ')                      # worst first, as the report orders it
    assert 'score poor' in page and '<span class="sign down">−</span>5.0' in page
    assert '/documents/quote_003?run=A&amp;field=currency' in page or '/documents/quote_003?run=A&field=currency' in page
    assert 'Scanned PDF' in page and 'not under version control' in page and 'uv run python -m eval.run --x' in page
    assert 'confidence' not in page.lower().replace('never model confidence', '').replace('not model confidence', '')
    assert 'Overall' not in page and 'average' not in page.lower()
    compared = server.render_results({'run': 'A', 'compare': 'B', 'show': 'B'})
    assert 'toggle' in compared and compared.index('Currency') < compared.index('MOQ')  # same row order as run A

def test_rules_save_validates_before_writing(tmp_path, monkeypatch):
    import rules.engine as engine
    import ui.server as server
    target = tmp_path / 'quote_flags.json'
    target.write_text(engine.RULES_FILE.read_text())
    monkeypatch.setattr(engine, 'RULES_FILE', target)
    assert server.save_rules(b'{"nodes": "nonsense"}')['ok'] is False
    assert json.loads(target.read_text())['nodes'][0]['type'] == 'inputNode'  # unchanged
    graph = json.loads(target.read_text())
    table = next(n for n in graph['nodes'] if n['id'] == 'supplier_currency')
    table['content']['rules'].insert(0, {'_id': 'demo', 's': '"lone star machining"', 'c': '"USD"'})
    assert server.save_rules(json.dumps(graph).encode())['ok'] is True
    assert engine.supplier_currency('Lone Star Machining Inc', target) == 'USD'
    assert list((tmp_path / '.history').glob('*.json'))

def test_network_mode_requires_the_access_key():
    from ui.server import Handler
    h = Handler.__new__(Handler)
    h.access_key = 'k123'
    h.headers = {}
    assert not h._allowed({}) and not h._allowed({'key': 'wrong'}) and h._allowed({'key': 'k123'})
    h.headers = {'Cookie': 'viewer_key=k123'}
    assert h._allowed({})

# ---------- scoring ----------

GOLDEN = {'document_id': 'g', 'fields': [
    {'name': 'currency', 'value': 'USD', 'verbatim_span': 'USD'},
    {'name': 'line_items[0].part_number_quoted', 'value': 'A-1', 'verbatim_span': 'A-1'},
    {'name': 'line_items[0].unit_price', 'value': 4.5, 'verbatim_span': '4.50'},
    {'name': 'line_items[1].part_number_quoted', 'value': 'B-2', 'verbatim_span': 'B-2'},
    {'name': 'line_items[1].unit_price', 'value': 7, 'verbatim_span': '7.00'}]}

def stored(name, value, grounded=True):
    return {name: {'value': value if grounded else None, 'grounded': grounded}}

def test_score_uses_known_answers_and_matches_items_by_part_number():
    from ui.scoring import band, by_field, score_quote
    fields = {**stored('currency', None, grounded=False),             # rejected: counts as wrong
              **stored('line_items[0].part_number_quoted', 'B-2'),   # items stored in the other order
              **stored('line_items[0].unit_price', 7.0),
              **stored('line_items[1].part_number_quoted', 'A-1'),
              **stored('line_items[1].unit_price', 4.4)}              # wrong value
    s = score_quote(fields, GOLDEN)
    assert (s['correct'], s['total']) == (3, 5) and s['pct'] == 0.6
    assert s['fields']['line_items[1].unit_price']['expected'] == 4.5 and not s['fields']['line_items[1].unit_price']['correct']
    assert s['fields']['line_items[0].unit_price']['correct']
    per_field = by_field([s])
    assert per_field['currency']['pct'] == 0 and per_field['part_number_quoted']['pct'] == 1
    assert [band(p) for p in (0.96, 0.95, 0.8, 0.79, None)] == ['good', 'fair', 'fair', 'poor', 'none']

def test_golden_found_by_source_hash_and_quote_position():
    from ui.scoring import find_golden
    index = {('abc', 2): {'document_id': 'second'}, 'doc': {'document_id': 'byid'}}
    assert find_golden(index, 'doc', 'doc-quote-2', 'abc')['document_id'] == 'second'
    assert find_golden(index, 'doc', 'doc', 'zzz')['document_id'] == 'byid'
    assert find_golden(index, 'other', 'other', None) is None

# ---------- upload ----------

def _multipart(fields: dict[str, tuple[str | None, bytes]]) -> tuple[str, bytes]:
    boundary = 'XyZ123'
    parts = b''
    for name, (filename, data) in fields.items():
        disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else '')
        parts += f'--{boundary}\r\nContent-Disposition: {disposition}\r\n\r\n'.encode() + data + b'\r\n'
    return f'multipart/form-data; boundary={boundary}', parts + f'--{boundary}--\r\n'.encode()

def test_upload_stores_pdf_by_content_hash(tmp_path, monkeypatch):
    import hashlib
    import pytest
    from temporal_app import activities
    from ui.server import save_upload
    monkeypatch.setattr(activities, 'STATE_DIR', tmp_path)
    pdf = b'%PDF-1.4 synthetic test bytes'
    content_type, body = _multipart({'file': ('my quote.pdf', pdf), 'extractor': (None, b'frontier')})
    path, document_id, extractor = save_upload(content_type, body)
    assert path.read_bytes() == pdf and document_id == hashlib.sha256(pdf).hexdigest()[:16] and extractor == 'frontier'
    assert path.name == 'my quote.pdf' and path.parent.name == hashlib.sha256(pdf).hexdigest()
    content_type, body = _multipart({'file': ('notes.txt', b'hello')})
    with pytest.raises(ValueError):
        save_upload(content_type, body)

# ---------- inbox monitor ----------

async def test_inbox_monitor_starts_one_workflow_per_new_email(tmp_path, monkeypatch):
    import email_monitor
    from temporalio.exceptions import WorkflowAlreadyStartedError
    started = []
    class FakeClient:
        async def start_workflow(self, workflow, args, id, task_queue):
            if id in started:
                raise WorkflowAlreadyStartedError(id, 'EmailWorkflow')
            started.append(id)
    monkeypatch.setattr(email_monitor, '_stable', lambda path, wait=0.5: True)
    (tmp_path / 'a.eml').write_bytes(b'Message-ID: <a@x>\r\n\r\nquote')
    (tmp_path / 'b.eml').write_bytes(b'Message-ID: <b@x>\r\n\r\nquote')
    first = await email_monitor.poll_once(FakeClient(), tmp_path, 'golden-echo')
    assert len(first) == 2 and not list(tmp_path.glob('*.eml'))
    assert len(list((tmp_path / 'processed').glob('*.eml'))) == 2
    (tmp_path / 'a-again.eml').write_bytes(b'Message-ID: <a@x>\r\n\r\nquote')  # same bytes as a.eml
    assert await email_monitor.poll_once(FakeClient(), tmp_path, 'golden-echo') == []
