import pytest
from extract.body import parse_email_body
from extract.boundaries import page_range, split_quotes
from extract.persistence import verified_payload
from extract.schemas import SupplierQuote, Evidenced
from parse.models import LayoutElement, ParsedDocument, Page, render_text

def page(number, *elements):
    return Page(number=number, text=render_text(list(elements)), elements=list(elements))

def heading(text): return LayoutElement(text=text, kind='heading', bbox=(0, 0, 1, 1))
def line(text): return LayoutElement(text=text, bbox=(0, 0, 1, 1))

def test_body_plain_and_html():
    assert parse_email_body('a','Quote Q1').pages[0].number == 0
    assert 'Quote Q1' in parse_email_body('a','<p>Quote Q1</p>',html=True).pages[0].text

def test_html_body_table_rows_become_lines():
    parsed = parse_email_body('a', '<table><tr><td>HX-1</td><td>500</td></tr><tr><td>HX-2</td><td>9</td></tr></table>', html=True)
    assert [e.text for e in parsed.pages[0].elements] == ['HX-1 500', 'HX-2 9']

def test_three_quote_pages():
    parsed=ParsedDocument(document_id='three',pages=[
        page(i, heading(f'Supplier {i}'), line(f'Quote Reference: Q{i}')) for i in range(1,4)])
    segments = split_quotes(parsed)
    assert len(segments) == 3
    assert [page_range(s) for s in segments] == [(1, 1), (2, 2), (3, 3)]
    assert [s.segment for s in segments] == [1, 2, 3]

def test_two_quotes_on_one_page_split_at_supplier_header():
    parsed = ParsedDocument(document_id='two', pages=[page(1,
        heading('Acme Ltd'), line('Quote Ref: A-1'), line('Total 10'),
        heading('Beta GmbH'), line('Quote Ref: B-7'), line('Total 20'))])
    first, second = split_quotes(parsed)
    assert [e.text for e in first.pages[0].elements] == ['Acme Ltd', 'Quote Ref: A-1', 'Total 10']
    assert [e.text for e in second.pages[0].elements] == ['Beta GmbH', 'Quote Ref: B-7', 'Total 20']

def test_repeated_reference_on_continuation_page_does_not_split():
    parsed = ParsedDocument(document_id='one', pages=[
        page(1, heading('Acme Ltd'), line('Quote Ref: A-1')), page(2, line('Quote Ref: A-1 (continued)'), line('Total 10'))])
    assert len(split_quotes(parsed)) == 1

def test_persistence_rejects_unverified():
    quote=SupplierQuote(supplier_name=Evidenced[str](value='Invented',verbatim_span='Invented'))
    with pytest.raises(ValueError,match='Unverified'):
        verified_payload(quote)

def test_headings_are_not_read_as_quote_references():
    # One quote over two pages: "Quote Reference" / "Quotation Notes" headings and a continuation header.
    parsed = ParsedDocument(document_id='one', pages=[
        page(1, heading('NORTHSTAR'), line('Quote Reference'), line('NSI-Q-2026-1048'), heading('Quotation Notes'), line('All prices in USD')),
        page(2, heading('NORTHSTAR'), line('QUOTE NSI-Q-2026-1048 | CONTINUED | PAGE 2 OF 2'), line('Total 10'))])
    assert len(split_quotes(parsed)) == 1

def test_reference_formats():
    from extract.boundaries import QUOTE_REF
    for text, ref in [('Quote Ref: QT-2026-0004', 'QT-2026-0004'), ('Quotation No. 7781', '7781'),
                      ('QUOTE NSI-Q-2026-1048 | CONTINUED', 'NSI-Q-2026-1048'), ('Quote #A12', 'A12')]:
        assert QUOTE_REF.search(text).group(1) == ref, text
    for text in ('Quote Reference', 'Quotation Notes', 'Quote Date: 2026-10-13', 'Quote total: CAD 1,103.90', 'Quoted Line Items'):
        m = QUOTE_REF.search(text)
        assert m is None, (text, m.group(1))
