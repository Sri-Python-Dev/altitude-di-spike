"""Postgres storage against a local test database (skipped if unreachable).
Override with ALTITUDE_TEST_DATABASE_URL."""
import os
import pytest
from extract.schemas import Evidenced, SupplierQuote

TEST_URL = os.getenv('ALTITUDE_TEST_DATABASE_URL', 'postgresql:///altitude_di_test')

@pytest.fixture
def conn():
    psycopg = pytest.importorskip('psycopg')
    from storage.postgres import connect
    try:
        connection = connect(TEST_URL)
    except psycopg.OperationalError:
        pytest.skip('test Postgres database not reachable')
    with connection:
        connection.execute('truncate documents cascade')
        yield connection

def quote():
    q = SupplierQuote(
        currency=Evidenced(value='CAD', page=1, bbox=(352.0, 127.0, 423.0, 141.0), verbatim_span='Currency: CAD', raw_confidence=0.9, grounded=True),
        incoterms=Evidenced(value=None, page=1, grounded=False, ungrounded_reason='span_not_found', ungrounded_span='FOB invented'),
        line_items=[{'part_number_quoted': {'value': 'ACT-300', 'page': 1, 'bbox': (40, 235, 76, 243), 'verbatim_span': 'ACT-300', 'grounded': True},
                     'unit_price': {'value': 29.05, 'page': 1, 'bbox': (223, 235, 246, 243), 'verbatim_span': '29.05', 'grounded': True}}])
    return q

def test_save_and_load_round_trip(conn):
    from storage.postgres import load_quote, record_document, save_quote
    record_document(conn, 'doc1', kind='pdf', status='parsed', file_path='/tmp/x.pdf', source_sha256='ab' * 32, ocr=False, quote_count=1)
    save_quote(conn, 'doc1', 'doc1', quote(), pages=(1, 1), extractor='current', model='gpt-4.1-nano')
    loaded, path = load_quote(conn, 'doc1')
    assert path == '/tmp/x.pdf'
    assert loaded.currency.value == 'CAD' and loaded.currency.bbox == (352.0, 127.0, 423.0, 141.0) and loaded.currency.grounded
    assert loaded.line_items[0].unit_price.value == pytest.approx(29.05)
    assert loaded.incoterms.value is None and loaded.incoterms.ungrounded_reason == 'span_not_found'
    assert conn.execute("select status from documents where document_id = 'doc1'").fetchone() == ('saved',)
    assert conn.execute("select grounded_fields, ungrounded_fields, model from quotes").fetchone() == (3, 1, 'gpt-4.1-nano')

def test_saving_again_replaces_fields(conn):
    from storage.postgres import record_document, save_quote
    record_document(conn, 'doc1', kind='pdf', status='parsed')
    save_quote(conn, 'doc1', 'doc1', quote(), pages=(1, 1), extractor='current', model=None)
    save_quote(conn, 'doc1', 'doc1', quote(), pages=(1, 1), extractor='current', model=None)
    assert conn.execute("select count(*) from quote_fields where segment_id = 'doc1'").fetchone()[0] == len(list(quote().fields()))

def test_ungrounded_value_is_refused_by_code(conn):
    from extract.persistence import UngroundedValueError
    from storage.postgres import record_document, save_quote
    record_document(conn, 'doc1', kind='pdf', status='parsed')
    bad = SupplierQuote(currency=Evidenced(value='EUR', verbatim_span='EUR', grounded=False))
    with pytest.raises(UngroundedValueError):
        save_quote(conn, 'doc1', 'doc1', bad, pages=None, extractor=None, model=None)

def test_ungrounded_value_is_refused_by_the_database(conn):
    import psycopg
    from storage.postgres import record_document, save_quote
    record_document(conn, 'doc1', kind='pdf', status='parsed')
    save_quote(conn, 'doc1', 'doc1', quote(), pages=(1, 1), extractor=None, model=None)
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute("update quote_fields set value = 'EUR' where name = 'incoterms'")
