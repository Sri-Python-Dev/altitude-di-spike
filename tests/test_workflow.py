"""End to end through Temporal with a local dev server and the golden-echo extractor.
Slow (Docling + server start). Run tools.make_synthetic first."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from temporal_app import activities
from temporal_app.workflow import EmailWorkflow, QuoteWorkflow, ReprocessWorkflow

ROOT = Path(__file__).resolve().parents[1]
EMAILS = ROOT / 'data/synthetic/emails'
pytestmark = pytest.mark.skipif(not EMAILS.exists(), reason='run tools.make_synthetic first')

@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(activities, 'STATE_DIR', tmp_path)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    return tmp_path

@pytest.fixture
def test_db(monkeypatch):
    """Point the activities at the test database, emptied first; skip if unreachable."""
    import psycopg
    from tests.test_postgres import TEST_URL
    from storage.postgres import connect
    try:
        with connect(TEST_URL) as conn:
            conn.execute('truncate documents cascade')
    except psycopg.OperationalError:
        pytest.skip('test Postgres database not reachable')
    monkeypatch.setenv('DATABASE_URL', TEST_URL)
    return TEST_URL

async def run_all(starts):
    async with await WorkflowEnvironment.start_local() as env:
        with ThreadPoolExecutor(4) as pool:
            async with Worker(env.client, task_queue='test', workflows=[QuoteWorkflow, EmailWorkflow, ReprocessWorkflow],
                              activities=activities.ALL, activity_executor=pool):
                results = []
                for workflow, args in starts:
                    results.append(await env.client.execute_workflow(workflow, args=args, id=str(uuid.uuid4()), task_queue='test'))
                return results

async def test_demo_emails_end_to_end(state):
    names = ['01-original.eml', '02-same-pdf-renamed.eml', '03-resent-with-new-pdf.eml', '04-body-only.eml',
             '05-two-quotes-same-page.eml', '06-scanned.eml', '07-password-protected.eml', '09-purchase-order.eml']
    results = await run_all([(EmailWorkflow.run, [str(EMAILS / n), 'golden-echo']) for n in names])
    original, renamed, resent, body, two, scanned, protected, purchase_order = results
    # The classify step stops a purchase order before extraction.
    assert purchase_order['documents'][0]['status'] == 'not_a_quote' and purchase_order['documents'][0]['reason'] == 'po'

    assert original['documents'][0]['status'] == 'saved'
    assert renamed['documents'] == [] and renamed['skipped'][0]['reason'] == 'duplicate_attachment'
    assert resent['new_email'] is False and resent['documents'][0]['status'] == 'saved'
    assert body['documents'][0]['segments'][0]['pages'] == [0, 0]
    assert [s['pages'] for s in two['documents'][0]['segments']] == [[1, 1], [1, 1]]
    assert scanned['documents'][0]['ocr'] is True
    assert protected['documents'][0] == {'document_id': protected['documents'][0]['document_id'],
                                         'status': 'unparseable', 'reason': 'PasswordProtectedDocument'}
    for r in (original, resent, body, two, scanned):
        for segment in r['documents'][0]['segments']:
            assert segment['ungrounded'] == {}

    # Saved results: every stored value is grounded with evidence.
    saved = list((state / 'results').glob('*.json'))
    assert len(saved) == 6  # 01, 03, 04 body, 05 x2, 06
    for path in saved:
        quote = json.loads(path.read_text())
        for name in ('supplier_name', 'currency'):
            assert quote[name]['grounded'] and quote[name]['verbatim_span']

    # Workflow results carry no document text.
    dumped = json.dumps(results)
    for secret in ('Halcyon', 'Kestrel', 'Orchard', 'Northwind', '12.40'):
        assert secret not in dumped

async def test_results_are_stored_in_postgres(state, test_db):
    from storage.postgres import connect
    names = ['05-two-quotes-same-page.eml', '07-password-protected.eml', '09-purchase-order.eml']
    await run_all([(EmailWorkflow.run, [str(EMAILS / n), 'golden-echo']) for n in names])
    with connect(test_db) as conn:
        documents = dict(conn.execute('select status, count(*) from documents group by status').fetchall())
        assert documents == {'saved': 1, 'unparseable': 1, 'not_a_quote': 1}
        assert conn.execute("select reason from documents where status = 'not_a_quote'").fetchone() == ('po',)
        assert conn.execute("select reason from documents where status = 'unparseable'").fetchone() == ('password_protected',)
        assert conn.execute('select count(*), min(page_from), max(page_to) from quotes').fetchone() == (2, 1, 1)
        # Every stored value is grounded and carries its evidence.
        assert conn.execute('select count(*) from quote_fields where value is not null and (not grounded or bbox is null or verbatim_span is null)').fetchone() == (0,)
        assert conn.execute("select count(*) from quote_fields where field = 'part_number_quoted' and grounded").fetchone()[0] == 3
