"""Real synthetic PDFs through Docling (slow: ~1 min). Run tools.make_synthetic first."""
from pathlib import Path
import pytest
from extract.boundaries import split_quotes
from extract.errors import CorruptDocument, PasswordProtectedDocument
from extract.extractors import golden_echo
from extract.ground import ground
from parse.docling_parser import parse_pdf

EDGE = Path(__file__).resolve().parents[1] / 'data/synthetic/edge-cases'
pytestmark = pytest.mark.skipif(not EDGE.exists(), reason='run tools.make_synthetic first')

def test_password_protected_pdf_raises_typed_error():
    with pytest.raises(PasswordProtectedDocument) as e:
        parse_pdf(EDGE / 'password-protected.pdf')
    assert str(e.value) == 'Document is password protected'  # fixed text, no content

def test_corrupt_pdf_raises_typed_error():
    with pytest.raises(CorruptDocument):
        parse_pdf(EDGE / 'corrupt.pdf')

def test_scanned_pdf_takes_ocr_path_and_grounds():
    parsed = parse_pdf(EDGE / 'scanned-quote.pdf')
    assert parsed.ocr
    quote = ground(golden_echo(split_quotes(parsed)[0]), parsed)
    assert all(f.grounded and f.bbox for _, f in quote.fields() if f.value is not None)
    assert not any(f.ungrounded_reason for _, f in quote.fields())

def test_three_quotes_on_separate_pages():
    segments = split_quotes(parse_pdf(EDGE / 'three-quotes-separate-pages.pdf'))
    assert [(s.pages[0].number, s.pages[-1].number) for s in segments] == [(1, 1), (2, 2), (3, 3)]

def test_two_quotes_on_one_page_ground_to_their_own_values():
    segments = split_quotes(parse_pdf(EDGE / 'two-quotes-same-page.pdf'))
    assert len(segments) == 2 and segments[0].pages[0].number == segments[1].pages[0].number == 1
    for segment in segments:
        quote = ground(golden_echo(segment), segment)
        assert not any(f.ungrounded_reason for _, f in quote.fields())  # nothing claimed was rejected
    # Quote 1's reference must not be findable in quote 2's segment.
    assert 'KA-77031' not in segments[1].pages[0].text
