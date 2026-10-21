"""Prompt 05: the classify step (quote | po | invoice | other)."""
from pathlib import Path
import pytest
from extract.classify import classify
from parse.models import LayoutElement, Page, ParsedDocument, render_text

def doc(*lines):
    elements = [LayoutElement(text=l, kind='heading' if i == 0 else 'text') for i, l in enumerate(lines)]
    return ParsedDocument(document_id='d', pages=[Page(number=1, text=render_text(elements), elements=elements)])

def kind(*lines):
    return classify(doc(*lines))['doc_type']

def test_quote_by_title_or_reference():
    assert kind('QUOTATION', 'Quote No.: Q-2026-0775') == 'quote'
    assert kind('Northstar Industrial Components', 'Quote Reference', 'NSI-Q-2026-1048', 'Quote Ref: NSI-Q-2026-1048') == 'quote'

def test_body_wording_does_not_make_a_quote_an_invoice_or_po():
    assert kind('Proseware Motion LLC', 'Quote No.: Q-2026-0775', 'Payment: Net 30 days after invoice',
                'Delivery against your purchase order') == 'quote'

def test_purchase_order_even_when_it_cites_a_quotation():
    assert kind('PURCHASE ORDER', 'PO Number: PO-2026-4410', 'Placed against your quotation HP-Q-4471') == 'po'

def test_invoice():
    assert kind('INVOICE', 'Invoice Number: INV-7781', 'Invoice Date: 2026-10-01') == 'invoice'
    assert kind('Acme Ltd', 'Invoice No: 42') == 'invoice'

def test_other():
    assert classify(doc('Meeting notes', 'Agenda for Tuesday')) == {'doc_type': 'other', 'reason': 'no_quote_markers'}

SYNTHETIC = Path(__file__).resolve().parents[1] / 'data/synthetic'

@pytest.mark.skipif(not (SYNTHETIC / 'purchase-order.pdf').exists(), reason='run tools.make_synthetic first')
def test_real_documents():
    from parse.docling_parser import parse_pdf
    assert classify(parse_pdf(SYNTHETIC / 'purchase-order.pdf')) == {'doc_type': 'po', 'reason': 'po_title'}
    assert classify(parse_pdf(SYNTHETIC / 'quote-a.pdf'))['doc_type'] == 'quote'
