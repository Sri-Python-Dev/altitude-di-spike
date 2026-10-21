from pathlib import Path
import pytest
from extract.dedupe import DedupeRegistry
from ingest.email_intake import intake_email

EMAILS = Path(__file__).resolve().parents[1] / 'data/synthetic/emails'

def test_same_pdf_different_filename_and_email_skipped():
    r=DedupeRegistry()
    assert r.ingest('<a@synthetic>',[b'pdf bytes']) == [True]
    assert r.ingest('<b@synthetic>',[b'pdf bytes']) == [False]

def test_new_pdf_in_resent_email_processed():
    r=DedupeRegistry()
    assert r.ingest('<a@synthetic>',[b'old']) == [True]
    assert r.ingest('<a@synthetic>',[b'new']) == [True]

def test_registry_survives_restart(tmp_path):
    DedupeRegistry(tmp_path / 'd.sqlite').record_attachment(b'pdf bytes', '<a@synthetic>', 'a.pdf')
    reopened = DedupeRegistry(tmp_path / 'd.sqlite')
    assert reopened.record_attachment(b'pdf bytes', '<b@synthetic>', 'renamed.pdf') is False
    assert reopened.record_email('<a@synthetic>') is True  # emails table is separate from attachments

def test_filename_is_never_identity():
    r = DedupeRegistry()
    assert r.record_attachment(b'one', '<a@synthetic>', 'quote.pdf') is True
    assert r.record_attachment(b'two', '<a@synthetic>', 'quote.pdf') is True

@pytest.mark.skipif(not EMAILS.exists(), reason='run tools.make_synthetic first')
def test_demo_email_sequence(tmp_path):
    first = intake_email(EMAILS / '01-original.eml', tmp_path)
    assert first['new_email'] and len(first['items']) == 1
    # Same PDF bytes, different filename, different email: skipped.
    second = intake_email(EMAILS / '02-same-pdf-renamed.eml', tmp_path)
    assert second['new_email'] and second['items'] == []
    assert second['skipped'][0]['reason'] == 'duplicate_attachment'
    # Same Message-ID as 01, but a new PDF: processed.
    third = intake_email(EMAILS / '03-resent-with-new-pdf.eml', tmp_path)
    assert not third['new_email'] and len(third['items']) == 1
    # Work items carry IDs and paths only.
    assert set(third['items'][0]) == {'document_id', 'file_path', 'kind', 'source_email'}

@pytest.mark.skipif(not EMAILS.exists(), reason='run tools.make_synthetic first')
def test_body_only_email_becomes_a_body_document_once(tmp_path):
    first = intake_email(EMAILS / '04-body-only.eml', tmp_path)
    assert [i['kind'] for i in first['items']] == ['body']
    again = intake_email(EMAILS / '04-body-only.eml', tmp_path)
    assert again['items'] == [] and again['skipped'][0]['reason'] == 'duplicate_email'

def test_safe_filename():
    from ingest.email_intake import safe_filename
    assert safe_filename('RFQ reply FINAL (2).pdf') == 'RFQ reply FINAL _2_.pdf'
    assert safe_filename('../../etc/passwd') == 'passwd.pdf'
    assert safe_filename('') == 'document.pdf'

@pytest.mark.skipif(not EMAILS.exists(), reason='run tools.make_synthetic first')
def test_resend_bypasses_dedupe_with_a_new_document_id(tmp_path):
    first = intake_email(EMAILS / '01-original.eml', tmp_path)
    again = intake_email(EMAILS / '01-original.eml', tmp_path)
    assert again['items'] == []  # normal re-delivery: duplicate
    resent = intake_email(EMAILS / '01-original.eml', tmp_path, resend=True)
    assert len(resent['items']) == 1
    assert resent['items'][0]['document_id'].startswith(first['items'][0]['document_id'] + '-r')
