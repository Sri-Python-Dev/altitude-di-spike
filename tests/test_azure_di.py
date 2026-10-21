"""The Azure DI adapter's conversion, against a fake AnalyzeResult (no credentials, no network)."""
from types import SimpleNamespace as NS
import pytest
from extract.ground import ground, locate_span
from extract.schemas import Evidenced, SupplierQuote
from parse.azure_di import parse_azure, to_parsed_document

def region(page, x0, y0, x1, y1):  # inches, as Azure returns them for PDFs
    return [NS(page_number=page, polygon=[x0, y0, x1, y0, x1, y1, x0, y1])]

def fake_result():
    paragraphs = [
        NS(content='Kestrel Avionics GmbH', role='title', bounding_regions=region(1, 1, 1, 4, 1.3)),
        NS(content='Currency: GBP', role=None, bounding_regions=region(1, 5, 1.6, 6.5, 1.8)),
        NS(content='Part number', role=None, bounding_regions=region(1, 1, 3, 2, 3.2)),
        NS(content='Unit price', role=None, bounding_regions=region(1, 3, 3, 4, 3.2)),
        NS(content='KA-SENS-3', role=None, bounding_regions=region(1, 1, 3.3, 2, 3.5)),
        NS(content='2210.00', role=None, bounding_regions=region(1, 3, 3.3, 4, 3.5)),
        NS(content='Quote total: GBP 2,210.00', role=None, bounding_regions=region(1, 1, 4, 4, 4.2)),
    ]
    cells = [NS(row_index=r, column_index=c, content=paragraphs[p].content, elements=[f'/paragraphs/{p}'],
                bounding_regions=paragraphs[p].bounding_regions)
             for r, c, p in ((0, 0, 2), (0, 1, 3), (1, 0, 4), (1, 1, 5))]
    return NS(pages=[NS(page_number=1, unit='inch', width=8.5, height=11)], paragraphs=paragraphs,
              tables=[NS(row_count=2, column_count=2, cells=cells, bounding_regions=region(1, 1, 3, 4, 3.5))])

def test_converts_to_the_docling_shape():
    doc = to_parsed_document(fake_result(), 'k', 'ab' * 32)
    page = doc.pages[0]
    assert (page.width, page.height) == (612.0, 792.0)  # inches -> PDF points
    kinds = [(e.kind, e.text) for e in page.elements]
    assert kinds[0] == ('heading', 'Kestrel Avionics GmbH')
    assert [e for e in page.elements if e.kind == 'cell'][2].text == 'KA-SENS-3'
    cell = next(e for e in page.elements if e.text == '2210.00')
    assert (cell.table, cell.row, cell.col) == (0, 1, 1) and cell.bbox == (216.0, 237.6, 288.0, 252.0)
    assert sum(1 for e in page.elements if e.text == 'KA-SENS-3') == 1  # table text is not duplicated as running text
    assert 'KA-SENS-3 | 2210.00' in page.text and doc.tables == [{'page': 1, 'rows': 2, 'cols': 2, 'cells': 4}]
    assert doc.ocr and doc.source_sha256 == 'ab' * 32

def test_grounding_works_on_azure_output():
    doc = to_parsed_document(fake_result(), 'k')
    q = SupplierQuote(currency=Evidenced(value='GBP', page=1, verbatim_span='Currency: GBP'),
                      line_items=[{'part_number_quoted': {'value': 'KA-SENS-3', 'page': 1, 'verbatim_span': 'KA-SENS-3'},
                                   'unit_price': {'value': 2210, 'page': 1, 'verbatim_span': '2210.00'}}])
    g = ground(q, doc, supplier_currency=None)
    assert g.currency.grounded and g.line_items[0].unit_price.bbox == (216.0, 237.6, 288.0, 252.0)
    assert locate_span('Quote total: GBP 2,210.00', doc.pages[0])

def test_refuses_documents_outside_the_approved_folders(tmp_path):
    outside = tmp_path / 'x.pdf'
    outside.write_bytes(b'%PDF-1.4')
    with pytest.raises(PermissionError):
        parse_azure(outside)

def test_parser_metrics_helpers():
    from eval.parsers import iou, levenshtein, span_errors
    assert levenshtein('kitten', 'sitting') == 3
    assert span_errors('QT-2026-0004', 'Quote Ref: QT-2026-0004') == 0
    assert span_errors('QT-2026-0004', 'Quote Ref: QT-2O26-0004') == 1   # OCR read a zero as the letter O
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1 and iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0
    assert round(iou((0, 0, 10, 10), (5, 0, 15, 10)), 3) == 0.333
