"""Generate the synthetic demo set: edge-case documents with golden answers, and
.eml files for the dedupe demo. Every supplier, part and price here is invented.

Run: uv run python -m tools.make_synthetic
Writes data/synthetic/{edge-cases,emails}/ and data/synthetic/quote-*.{pdf,json}.

Golden values and spans are known by construction. Page and bbox come from the
Docling layout of the text PDF (the scanned copy reuses them: same page geometry).
"""
import hashlib, io, json, re
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1] / 'data/synthetic'
LABELLER = 'synthetic-generator tools/make_synthetic.py (values known by construction)'
STAMP = datetime(2026, 10, 13, tzinfo=timezone.utc)

QUOTES = {
    'A': dict(supplier='Halcyon Precision Parts', ref='HP-Q-4471', date='2026-10-02', valid='2026-11-01', currency='USD',
              incoterms='EXW', terms='Net 30', items=[('HPP-1120', 250, 12.40, 14, 50), ('HPP-2045', 40, 310.00, 28, 10)]),
    'B': dict(supplier='Brightwater Fluid Systems', ref='BFS-2026-118', date='2026-10-05', valid='2026-12-04', currency='EUR',
              incoterms='DAP', terms='Net 45', items=[('BW-VALVE-08', 12, 1475.50, 35, 4), ('BW-SEAL-2', 300, 2.15, 10, 100), ('BW-HOSE-90', 60, 48.00, 21, 20)]),
    'C': dict(supplier='Kestrel Avionics GmbH', ref='KA-77031', date='2026-10-07', valid='2026-11-06', currency='GBP',
              incoterms='FCA', terms='Net 60', items=[('KA-SENS-3', 8, 2210.00, 42, 2)]),
    'D': dict(supplier='Orchard Lane Components', ref='OLC-5520', date='2026-10-09', valid='2026-10-30', currency='CAD',
              incoterms='CPT', terms='2/10 Net 30', items=[('OLC-BRK-12', 1000, 0.85, 7, 500), ('OLC-BRK-14', 1000, 0.92, 7, 500)]),
    'E': dict(supplier='Northwind Fasteners Ltd', ref='NWF-Q-7781', date='2026-10-08', valid='2026-11-07', currency='EUR',
              incoterms='DAP', terms='Net 30', items=[('HX-210', 500, 1.85, 21, 250), ('HX-212', 200, 2.40, 21, 100)]),
    # Prices shown only with "$" and no currency name: the currency demo. Unresolved until the
    # supplier's currency is on file in the decision table.
    'G': dict(supplier='Lone Star Machining Inc', ref='LSM-24-0917', date='2026-10-11', valid='2026-11-10', currency='USD',
              incoterms='FOB Houston', terms='Net 30', dollar_only=True,
              items=[('LSM-SHAFT-22', 120, 18.75, 15, 40), ('LSM-BUSH-08', 600, 2.35, 10, 200)]),
    'F': dict(supplier='Saltmarsh Industrial Supply', ref='SIS-30914', date='2026-10-10', valid='2026-11-09', currency='USD',
              incoterms='FOB', terms='Net 30', items=[('SIS-GSK-7', 150, 6.75, 12, 50)]),
}

CURRENCY_LINE = 'Prices quoted in $'  # deliberately no currency name: only the supplier table can resolve it

def ext(qty, unit):
    return round(qty * unit, 2)

def flowables(q, styles):
    money = '$' if q.get('dollar_only') else ''
    head = [['Quote Ref: ' + q['ref'], 'Quote Date: ' + q['date']],
            ['Valid Until: ' + q['valid'], CURRENCY_LINE if q.get('dollar_only') else 'Currency: ' + q['currency']],
            ['Incoterms: ' + q['incoterms'], 'Payment Terms: ' + q['terms']]]
    rows = [['Part number', 'Qty', 'Unit price', 'Ext. price', 'Lead days', 'MOQ']]
    rows += [[pn, str(qty), f'{money}{unit:.2f}', f'{money}{ext(qty, unit):.2f}', str(lead), str(moq)] for pn, qty, unit, lead, moq in q['items']]
    total = sum(ext(qty, unit) for _, qty, unit, _, _ in q['items'])
    table = Table(rows, hAlign='LEFT')
    table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.5, colors.grey), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold')]))
    return [Paragraph(q['supplier'], styles['Heading1']),
            Table(head, colWidths=[270, 270], hAlign='LEFT'), Spacer(1, 12),
            Paragraph('Quoted line items', styles['Heading3']), table, Spacer(1, 8),
            Paragraph(f"Quote total: {money + f'{total:,.2f}' if money else q['currency'] + f' {total:,.2f}'}", styles['Normal']), Spacer(1, 24)]

def render_pdf(keys: list[str], same_page=False, encrypt=None) -> bytes:
    styles = getSampleStyleSheet()
    story = []
    for i, key in enumerate(keys):
        if i and not same_page:
            story.append(PageBreak())
        story += flowables(QUOTES[key], styles)
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=letter, encrypt=encrypt, title='Supplier quote', author='synthetic',
                      creator='tools/make_synthetic.py', invariant=1).build(story)
    return buf.getvalue()

def render_purchase_order() -> bytes:
    """A buyer's purchase order (not a quote): the classify step must reject it, although it cites a quotation."""
    styles = getSampleStyleSheet()
    rows = [['Item', 'Description', 'Qty', 'Unit price', 'Amount'],
            ['HPP-1120', 'Bracket, machined', '250', '12.40', '3100.00'], ['HPP-2045', 'Housing', '40', '310.00', '12400.00']]
    table = Table(rows, hAlign='LEFT')
    table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.5, colors.grey)]))
    story = [Paragraph('PURCHASE ORDER', styles['Heading1']),
             Table([['PO Number: PO-2026-4410', 'Order Date: 2026-10-12'], ['Supplier: Halcyon Precision Parts', 'Currency: USD']],
                   colWidths=[270, 270], hAlign='LEFT'), Spacer(1, 12),
             Paragraph('Ship to: Altitude Demo Manufacturing, 400 Industrial Way, Dayton, OH', styles['Normal']),
             Paragraph('Placed against your quotation HP-Q-4471. Please confirm delivery dates.', styles['Normal']),
             Spacer(1, 12), table]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=letter, title='Purchase order', author='synthetic', invariant=1).build(story)
    return buf.getvalue()

def rasterise(pdf: bytes, dpi=200) -> bytes:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf)
    images = [page.render(scale=dpi / 72).to_pil().convert('RGB') for page in doc]
    buf = io.BytesIO()
    images[0].save(buf, 'PDF', resolution=dpi, save_all=True, append_images=images[1:])
    return buf.getvalue()

def answers(q) -> list[tuple[str, object, str]]:
    money = '$' if q.get('dollar_only') else ''
    out = [('supplier_name', q['supplier'], q['supplier']), ('quote_reference', q['ref'], q['ref']),
           ('quote_date', q['date'], q['date']), ('valid_until', q['valid'], q['valid']),
           ('currency', q['currency'], CURRENCY_LINE if money else q['currency']), ('incoterms', q['incoterms'], q['incoterms']),
           ('payment_terms', q['terms'], q['terms'])]
    for i, (pn, qty, unit, lead, moq) in enumerate(q['items']):
        p = f'line_items[{i}].'
        out += [(p + 'part_number_quoted', pn, pn), (p + 'quantity', qty, str(qty)), (p + 'unit_price', unit, f'{money}{unit:.2f}'),
                (p + 'extended_price', ext(qty, unit), f'{money}{ext(qty, unit):.2f}'), (p + 'lead_time_days', lead, str(lead)), (p + 'moq', moq, str(moq))]
    return out

def locate_fields(segment, q) -> list[dict]:
    from extract.ground import FIELD_LABELS, locate_span, span_elements
    fields, rows = [], {}
    for name, value, span in answers(q):
        item = re.match(r'line_items\[(\d+)\]\.', name)
        page = next(p for p in segment.pages if locate_span(span, p))
        found = span_elements(span, page, rows.get(item.group(1)) if item else None, FIELD_LABELS.get(name.rsplit('.', 1)[-1], ()))
        if not found and page.number != 0:
            raise RuntimeError(f'{name}: no layout element')
        if item and name.endswith('part_number_quoted') and found and found[0].kind == 'cell':
            rows[item.group(1)] = (found[0].table, found[0].row)
        b = [e.bbox for e in found]
        # Email bodies (page 0) have no layout, so no bbox.
        bbox = [min(x[0] for x in b), min(x[1] for x in b), max(x[2] for x in b), max(x[3] for x in b)] if b else None
        fields.append({'name': name, 'value': value, 'page': page.number, 'bbox': bbox, 'verbatim_span': span,
                       'labeller': LABELLER, 'labelled_at': STAMP.isoformat()})
    return fields

def golden_for_pdf(folder: Path, pdf_name: str, keys: list[str], case: str, bbox_source: bytes | None = None, ocr=False):
    from extract.boundaries import split_quotes
    from parse.docling_parser import parse_pdf
    pdf = folder / pdf_name
    probe = folder / '.probe.pdf'
    probe.write_bytes(bbox_source or pdf.read_bytes())
    segments = split_quotes(parse_pdf(probe))
    probe.unlink()
    if len(segments) != len(keys):
        raise RuntimeError(f'{pdf_name}: expected {len(keys)} quotes, parser split {len(segments)}')
    for n, (key, segment) in enumerate(zip(keys, segments), 1):
        doc_id = Path(pdf_name).stem + (f'-q{n}' if len(keys) > 1 else '')
        golden = {'document_id': doc_id, 'sha256': hashlib.sha256(pdf.read_bytes()).hexdigest(), 'doc_type': 'quote',
                  'source': pdf_name, 'segment': n, 'segment_count': len(keys), 'case': case, 'ocr': ocr,
                  'fields': locate_fields(segment, QUOTES[key])}
        (folder / f'{doc_id}.json').write_text(json.dumps(golden, indent=2) + '\n')

def body_text(q, html=False) -> str:
    lines = ['Hello,', 'Thanks for the RFQ. Please find our quotation below.', '', f"Supplier: {q['supplier']}",
             f"Quote Ref: {q['ref']}", f"Quote Date: {q['date']}", f"Valid Until: {q['valid']}", f"Currency: {q['currency']}",
             f"Incoterms: {q['incoterms']}", f"Payment Terms: {q['terms']}", '']
    items = [f"Part {pn}: qty {qty} at {unit:.2f} each, extended {ext(qty, unit):.2f}, lead time {lead} days, MOQ {moq}"
             for pn, qty, unit, lead, moq in q['items']]
    if not html:
        return '\n'.join(lines + items + ['', 'Regards,', 'Sales desk']) + '\n'
    rows = ''.join(f'<tr><td>{pn}</td><td>{qty}</td><td>{unit:.2f}</td><td>{ext(qty, unit):.2f}</td><td>{lead}</td><td>{moq}</td></tr>'
                   for pn, qty, unit, lead, moq in q['items'])
    return ('<html><body>' + ''.join(f'<p>{l}</p>' for l in lines if l) +
            '<table><tr><th>Part</th><th>Qty</th><th>Unit</th><th>Extended</th><th>Lead days</th><th>MOQ</th></tr>' + rows +
            '</table><p>Regards,<br>Sales desk</p></body></html>')

def email(message_id: str, subject: str, body: str = '', html: bool = False, attachments=()) -> bytes:
    m = EmailMessage()
    m['From'] = 'sales@supplier.example'
    m['To'] = 'rfq@altitude.example'
    m['Subject'] = subject
    m['Date'] = format_datetime(STAMP)
    m['Message-ID'] = message_id
    if html:
        m.set_content('This message is HTML only.')
        m.add_alternative(body, subtype='html')
    else:
        m.set_content(body or 'Please see the attached quotation.')
    for filename, data in attachments:
        m.add_attachment(data, maintype='application', subtype='pdf', filename=filename)
    return bytes(m)

def golden_for_body(folder: Path, eml_name: str, key: str, case: str):
    from extract.body import parse_email_body
    from ingest.email_intake import read_eml
    message = read_eml(folder / eml_name)
    parsed = parse_email_body(Path(eml_name).stem, message.body, html=message.body_is_html)
    fields = locate_fields(parsed, QUOTES[key])
    golden = {'document_id': Path(eml_name).stem, 'sha256': hashlib.sha256((folder / eml_name).read_bytes()).hexdigest(),
              'body_sha256': parsed.source_sha256, 'doc_type': 'quote', 'source': eml_name, 'segment': 1,
              'segment_count': 1, 'case': case, 'fields': fields}
    (folder / f'{Path(eml_name).stem}.json').write_text(json.dumps(golden, indent=2) + '\n')

def main():
    edge, emails = ROOT / 'edge-cases', ROOT / 'emails'
    for d in (edge, emails):
        d.mkdir(parents=True, exist_ok=True)
        for old in d.iterdir():
            old.unlink()
    single_a, single_f = render_pdf(['A']), render_pdf(['F'])
    single_g = render_pdf(['G'])
    for name, data, key in (('quote-a.pdf', single_a, 'A'), ('quote-f.pdf', single_f, 'F'), ('quote-g-dollar.pdf', single_g, 'G')):
        (ROOT / name).write_bytes(data)
        golden_for_pdf(ROOT, name, [key], 'single quote (dedupe demo)')
    purchase_order = render_purchase_order()
    (ROOT / 'purchase-order.pdf').write_bytes(purchase_order)  # no golden file: it is not a quote

    (edge / 'three-quotes-separate-pages.pdf').write_bytes(render_pdf(['A', 'B', 'C']))
    golden_for_pdf(edge, 'three-quotes-separate-pages.pdf', ['A', 'B', 'C'], 'multi-quote, one per page')
    (edge / 'two-quotes-same-page.pdf').write_bytes(render_pdf(['C', 'D'], same_page=True))
    golden_for_pdf(edge, 'two-quotes-same-page.pdf', ['C', 'D'], 'multi-quote, same page')
    text_d = render_pdf(['D'])
    (edge / 'scanned-quote.pdf').write_bytes(rasterise(text_d))
    golden_for_pdf(edge, 'scanned-quote.pdf', ['D'], 'scanned (no text layer)', bbox_source=text_d, ocr=True)

    (edge / 'body-plain.eml').write_bytes(email('<body-plain@supplier.example>', 'Quotation NWF-Q-7781', body_text(QUOTES['E'])))
    golden_for_body(edge, 'body-plain.eml', 'E', 'quote in plaintext email body')
    (edge / 'body-html.eml').write_bytes(email('<body-html@supplier.example>', 'Quotation SIS-30914', body_text(QUOTES['F'], html=True), html=True))
    golden_for_body(edge, 'body-html.eml', 'F', 'quote in HTML email body')

    (edge / 'password-protected.pdf').write_bytes(render_pdf(['B'], encrypt='synthetic-secret'))
    (edge / 'corrupt.pdf').write_bytes(single_a[:len(single_a) // 3])
    for name, reason, case in (('password-protected', 'password_protected', 'password-protected PDF'), ('corrupt', 'corrupt', 'truncated PDF')):
        (edge / f'{name}.json').write_text(json.dumps({'document_id': name, 'sha256': hashlib.sha256((edge / f'{name}.pdf').read_bytes()).hexdigest(),
            'doc_type': 'quote', 'source': f'{name}.pdf', 'case': case, 'expect': 'unparseable', 'reason': reason, 'fields': []}, indent=2) + '\n')

    # Dedupe demo, run in order: 02 is skipped (same bytes, new name, new email); 03 re-sends 01's Message-ID with a new PDF, which is processed.
    demo = [
        ('01-original.eml', email('<rfq-1001@supplier.example>', 'Quote HP-Q-4471', attachments=[('Quote_HP-Q-4471.pdf', single_a)])),
        ('02-same-pdf-renamed.eml', email('<rfq-1002@supplier.example>', 'Fwd: our quote', attachments=[('RFQ reply FINAL (2).pdf', single_a)])),
        ('03-resent-with-new-pdf.eml', email('<rfq-1001@supplier.example>', 'Quote HP-Q-4471 (resent)', attachments=[('Quote_SIS-30914.pdf', single_f)])),
        ('04-body-only.eml', (edge / 'body-plain.eml').read_bytes()),
        ('05-two-quotes-same-page.eml', email('<rfq-1005@supplier.example>', 'Two quotes', attachments=[('quotes.pdf', (edge / 'two-quotes-same-page.pdf').read_bytes())])),
        ('06-scanned.eml', email('<rfq-1006@supplier.example>', 'Scanned quote', attachments=[('scan0001.pdf', (edge / 'scanned-quote.pdf').read_bytes())])),
        ('07-password-protected.eml', email('<rfq-1007@supplier.example>', 'Protected quote', attachments=[('quote.pdf', (edge / 'password-protected.pdf').read_bytes())])),
        ('08-dollar-prices.eml', email('<rfq-1008@supplier.example>', 'Quote LSM-24-0917', attachments=[('LSM-24-0917.pdf', single_g)])),
        ('09-purchase-order.eml', email('<po-4410@buyer.example>', 'PO-2026-4410', attachments=[('PO-2026-4410.pdf', purchase_order)])),
    ]
    for name, data in demo:
        (emails / name).write_bytes(data)
    print(f'Wrote {len(list(edge.iterdir()))} edge-case files and {len(demo)} emails under {ROOT}')

if __name__ == '__main__':
    main()
