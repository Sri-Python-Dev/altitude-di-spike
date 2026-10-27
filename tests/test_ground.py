from extract.schemas import SupplierQuote, Evidenced
from extract.ground import ground
from parse.models import ParsedDocument, Page, LayoutElement, render_text

def doc(text): return ParsedDocument(document_id='synthetic', pages=[Page(number=1,text=text,elements=[LayoutElement(text=text,bbox=(10,20,100,40))])])

def test_existing_span():
    q = SupplierQuote(supplier_name=Evidenced(value='Acme',page=1,verbatim_span='Acme'))
    result = ground(q, doc('Supplier Acme'))
    assert result.supplier_name.grounded and result.supplier_name.bbox

def test_invented_span():
    q = SupplierQuote(supplier_name=Evidenced(value='Imaginary',page=1,verbatim_span='Imaginary'))
    result = ground(q, doc('Supplier Acme'))
    assert result.supplier_name.value is None and not result.supplier_name.grounded
    assert result.supplier_name.ungrounded_span == 'Imaginary'
    assert result.supplier_name.ungrounded_reason == 'span_not_found'

def test_whitespace():
    q = SupplierQuote(supplier_name=Evidenced(value='Acme Corp',page=1,verbatim_span='Acme   Corp'))
    assert ground(q,doc('Acme Corp')).supplier_name.grounded

def test_number_not_in_span():
    q = SupplierQuote(line_items=[{'unit_price':{'value':4500,'page':1,'verbatim_span':'Unit price 4000'}}])
    f = ground(q,doc('Unit price 4000')).line_items[0].unit_price
    assert f.value is None and not f.grounded and f.ungrounded_reason == 'value_not_in_span'

def test_formatted_number_in_span():
    q = SupplierQuote(line_items=[{'unit_price':{'value':4500,'page':1,'verbatim_span':'4,500.00'}}])
    assert ground(q, doc('Unit price 4,500.00')).line_items[0].unit_price.grounded

# A quote page: header lines, then a table. '11' and '9' also appear inside the date and price.
def quote_doc():
    elements = [
        LayoutElement(text='Meridian Aerospace Supply', kind='heading', bbox=(38, 30, 235, 50)),
        LayoutElement(text='Valid Until: 2026-11-12', bbox=(40, 136, 150, 144)),
        LayoutElement(text='Currency: CAD', bbox=(310, 136, 370, 144)),
    ]
    for r, row in enumerate([['Part number', 'Qty', 'Unit price', 'Lead days', 'MOQ'],
                             ['ACT-300', '38', '29.05', '11', '9'],
                             ['ACT-310', '9', '31.00', '11', '4']]):
        for c, text in enumerate(row):
            elements.append(LayoutElement(text=text, kind='cell', table=0, row=r, col=c, bbox=(40 + 80 * c, 210 + 25 * r, 70 + 80 * c, 218 + 25 * r)))
    return ParsedDocument(document_id='q', pages=[Page(number=1, text=render_text(elements), elements=elements)])

def test_bbox_is_the_cell_not_a_substring_of_a_date_or_price():
    q = SupplierQuote(line_items=[{'part_number_quoted': {'value': 'ACT-300', 'page': 1, 'verbatim_span': 'ACT-300'},
                                   'lead_time_days': {'value': 11, 'page': 1, 'verbatim_span': '11'},
                                   'moq': {'value': 9, 'page': 1, 'verbatim_span': '9'}}])
    item = ground(q, quote_doc()).line_items[0]
    assert item.lead_time_days.bbox == (280, 235, 310, 243)  # the Lead days cell, not "2026-11-12"
    assert item.moq.bbox == (360, 235, 390, 243)             # the MOQ cell, not "29.05"

def test_line_item_fields_anchor_to_their_part_number_row():
    # Row 2's quantity is '9', which also appears as row 1's MOQ.
    q = SupplierQuote(line_items=[{'part_number_quoted': {'value': 'ACT-310', 'page': 1, 'verbatim_span': 'ACT-310'},
                                   'quantity': {'value': 9, 'page': 1, 'verbatim_span': '9'}}])
    assert ground(q, quote_doc()).line_items[0].quantity.bbox == (120, 260, 150, 268)

def test_text_value_must_come_from_its_span():
    q = SupplierQuote(supplier_name=Evidenced(value='Globex', page=1, verbatim_span='Meridian Aerospace Supply'))
    f = ground(q, quote_doc()).supplier_name
    assert f.value is None and f.ungrounded_reason == 'value_not_in_span'

def test_date_value_read_from_span_in_another_format():
    q = SupplierQuote(valid_until=Evidenced(value='2026-11-12', page=1, verbatim_span='12/11/2026'))
    assert ground(q, doc('Valid until 12/11/2026')).valid_until.grounded
    q = SupplierQuote(valid_until=Evidenced(value='2026-11-13', page=1, verbatim_span='12/11/2026'))
    assert not ground(q, doc('Valid until 12/11/2026')).valid_until.grounded

def test_currency_from_span_symbol_or_header():
    assert ground(SupplierQuote(currency=Evidenced(value='GBP', page=1, verbatim_span='£1,200.00')), doc('Total £1,200.00')).currency.grounded
    # Span is a bare amount; CAD is stated in the header.
    assert ground(SupplierQuote(currency=Evidenced(value='CAD', page=1, verbatim_span='29.05')), quote_doc()).currency.grounded
    f = ground(SupplierQuote(currency=Evidenced(value='EUR', page=1, verbatim_span='29.05')), quote_doc()).currency
    assert f.value is None and f.ungrounded_reason == 'currency_not_derivable'

def two_pages(*texts):
    return ParsedDocument(document_id='p', pages=[Page(number=i, text=t, elements=[LayoutElement(text=t, bbox=(0, 0, 9, 9))]) for i, t in enumerate(texts, 1)])

def test_span_on_neighbouring_page_is_grounded_with_corrected_page():
    q = SupplierQuote(incoterms=Evidenced(value='FCA', page=1, verbatim_span='FCA'))
    f = ground(q, two_pages('Header', 'Incoterms FCA')).incoterms
    assert f.grounded and f.page == 2

def test_span_two_pages_away_is_not_grounded():
    q = SupplierQuote(incoterms=Evidenced(value='FCA', page=1, verbatim_span='FCA'))
    assert not ground(q, two_pages('Header', 'Middle', 'Incoterms FCA')).incoterms.grounded

def test_email_body_grounds_without_bbox():
    body = ParsedDocument(document_id='b', pages=[Page(number=0, text='Quote Ref: Q-1\n', elements=[LayoutElement(text='Quote Ref: Q-1')])])
    f = ground(SupplierQuote(quote_reference=Evidenced(value='Q-1', page=0, verbatim_span='Q-1')), body).quote_reference
    assert f.grounded and f.page == 0 and f.bbox is None

def test_absent_field_is_not_reported_as_ungrounded():
    f = ground(SupplierQuote(), doc('anything')).incoterms
    assert f.value is None and not f.grounded and f.ungrounded_reason is None

def test_model_cannot_self_certify():
    q = SupplierQuote(supplier_name=Evidenced(value='Imaginary', page=1, verbatim_span='Imaginary', grounded=True, bbox=(1, 1, 2, 2)))
    f = ground(q, doc('Supplier Acme')).supplier_name
    assert not f.grounded and f.value is None and f.bbox is None

def test_bbox_prefers_the_most_specific_element():
    elements = [LayoutElement(text='Quote total: CAD 1,770.00', bbox=(76, 265, 198, 279)),
                LayoutElement(text='Currency: CAD', bbox=(352, 127, 423, 141))]
    parsed = ParsedDocument(document_id='o', pages=[Page(number=1, text=render_text(elements), elements=elements)])
    assert ground(SupplierQuote(currency=Evidenced(value='CAD', page=1, verbatim_span='CAD')), parsed).currency.bbox == (352, 127, 423, 141)

def test_bbox_prefers_the_element_carrying_the_field_label():
    elements = [LayoutElement(text='Quote Ref: HP-1 Currency: USD Incoterms: EXW', bbox=(84, 112, 462, 157)),
                LayoutElement(text='Quote total: USD 15,500.00', bbox=(78, 269, 201, 278))]
    parsed = ParsedDocument(document_id='o', pages=[Page(number=1, text=render_text(elements), elements=elements)])
    assert ground(SupplierQuote(currency=Evidenced(value='USD', page=1, verbatim_span='USD')), parsed).currency.bbox == (84, 112, 462, 157)

def test_span_over_a_header_table_points_at_the_value_cell():
    cells = [('Quote Reference', 0), ('Quote Date', 0), ('Valid Until', 0), ('NSI-Q-2026-1048', 1), ('2026-10-13', 1), ('2026-11-12', 1)]
    elements = [LayoutElement(text=t, kind='cell', table=0, row=r, col=i % 3, bbox=(100 * (i % 3), 20 * r, 100 * (i % 3) + 90, 20 * r + 10))
                for i, (t, r) in enumerate(cells)]
    parsed = ParsedDocument(document_id='h', pages=[Page(number=1, text=render_text(elements), elements=elements)])
    span = 'Quote Reference | Quote Date | Valid Until\nNSI-Q-2026-1048 | 2026-10-13 | 2026-11-12'
    q = ground(SupplierQuote(quote_reference=Evidenced(value='NSI-Q-2026-1048', page=1, verbatim_span=span),
                             quote_date=Evidenced(value='2026-10-13', page=1, verbatim_span=span)), parsed)
    assert q.quote_reference.bbox == (0, 20, 90, 30)
    assert q.quote_date.bbox == (100, 20, 190, 30)

def test_line_item_on_page_two_is_not_grounded_to_a_lookalike_on_page_one():
    # Page 1 has qty "60"; page 2 holds the item, with MOQ "6". The model says page 1.
    def cells(rows, y0):
        return [LayoutElement(text=t, kind='cell', table=0, row=r, col=c, bbox=(100 * c, y0 + 20 * r, 100 * c + 50, y0 + 20 * r + 10))
                for r, row in enumerate(rows) for c, t in enumerate(row)]
    p1 = cells([['Part', 'Qty', 'MOQ'], ['NS-FLT-407', '60', '25']], 100)
    p2 = [LayoutElement(text=e.text, kind='cell', table=1, row=e.row, col=e.col, bbox=e.bbox) for e in cells([['Part', 'Qty', 'MOQ'], ['NS-CPL-522', '18', '6']], 200)]
    parsed = ParsedDocument(document_id='two', pages=[Page(number=1, text=render_text(p1), elements=p1), Page(number=2, text=render_text(p2), elements=p2)])
    q = SupplierQuote(line_items=[{'part_number_quoted': {'value': 'NS-CPL-522', 'page': 2, 'verbatim_span': 'NS-CPL-522'},
                                   'moq': {'value': 6, 'page': 1, 'verbatim_span': '6'}}])
    moq = ground(q, parsed).line_items[0].moq
    assert moq.grounded and moq.page == 2 and moq.bbox == (200, 220, 250, 230)

def test_leaked_json_around_a_span_is_trimmed_and_the_stored_span_is_literal():
    page_doc = doc('Summit Electrical Parts')
    q = SupplierQuote(supplier_name=Evidenced(value='Summit Electrical Parts', page=1, verbatim_span='Summit Electrical Parts},'))
    f = ground(q, page_doc, supplier_currency=None).supplier_name
    assert f.grounded and f.verbatim_span == 'Summit Electrical Parts'
    assert f.verbatim_span in page_doc.pages[0].text

def test_lines_joined_with_separators_are_found_and_stored_as_document_text():
    elements = [LayoutElement(text='Coastal Precision Parts', kind='heading', bbox=(10, 10, 200, 30)),
                LayoutElement(text='SUPPLIER QUOTATION | SYNTHETIC TEST DOCUMENT', bbox=(10, 40, 300, 50))]
    parsed = ParsedDocument(document_id='c', pages=[Page(number=1, text=render_text(elements), elements=elements)])
    q = SupplierQuote(supplier_name=Evidenced(value='Coastal Precision Parts', page=1,
                                              verbatim_span='Coastal Precision Parts | SUPPLIER QUOTATION | SYNTHETIC TEST DOCUMENT'))
    f = ground(q, parsed, supplier_currency=None).supplier_name
    assert f.grounded and f.bbox == (10, 10, 200, 30) and f.verbatim_span == 'Coastal Precision Parts'

def test_trimming_never_rescues_an_invented_span():
    q = SupplierQuote(supplier_name=Evidenced(value='Globex', page=1, verbatim_span='Globex},'))
    assert ground(q, doc('Supplier Acme'), supplier_currency=None).supplier_name.ungrounded_reason == 'span_not_found'

def _page_doc():
    elements = [LayoutElement(text='Pioneer Fluid Controls', kind='heading', bbox=(38, 29, 199, 50)),
                LayoutElement(text='SUPPLIER QUOTATION | SYNTHETIC TEST DOCUMENT', bbox=(38, 60, 300, 70))]
    elements += [LayoutElement(text=f'Line {i} of the quote body', bbox=(40, 80 + 12 * i, 300, 90 + 12 * i)) for i in range(20)]
    return ParsedDocument(document_id='p', pages=[Page(number=1, text=render_text(elements), elements=elements)])

def test_whole_page_quoted_for_one_value_grounds_on_the_element_holding_it():
    d = _page_doc()
    q = SupplierQuote(supplier_name=Evidenced(value='Pioneer Fluid Controls', page=1, verbatim_span=d.pages[0].text))
    f = ground(q, d, supplier_currency=None).supplier_name
    assert f.grounded and f.bbox == (38, 29, 199, 50) and f.verbatim_span == 'Pioneer Fluid Controls'

def test_reordered_region_still_grounds_on_the_element_holding_the_value():
    d = _page_doc()
    reordered = 'SUPPLIER QUOTATION | SYNTHETIC TEST DOCUMENT\nPioneer Fluid Controls\nLine 3 of the quote body'
    q = SupplierQuote(supplier_name=Evidenced(value='Pioneer Fluid Controls', page=1, verbatim_span=reordered))
    assert ground(q, d, supplier_currency=None).supplier_name.verbatim_span == 'Pioneer Fluid Controls'

def test_a_value_not_in_the_quoted_region_is_still_rejected():
    d = _page_doc()
    q = SupplierQuote(supplier_name=Evidenced(value='Globex Corp', page=1, verbatim_span=d.pages[0].text))
    assert ground(q, d, supplier_currency=None).supplier_name.ungrounded_reason in ('value_not_in_span', 'no_bbox')

def test_a_value_carrying_its_own_label_is_cleaned_before_grounding():
    from extract.ground import strip_field_label
    assert strip_field_label('incoterms', 'Incoterms: FCA') == 'FCA'
    assert strip_field_label('quote_reference', 'Quote Ref: KA-77031') == 'KA-77031'
    assert strip_field_label('payment_terms', 'Payment Terms: Net 60') == 'Net 60'
    assert strip_field_label('quote_date', 'Quote Date: 2026-10-07') == '2026-10-07'
    # Not its own label, or no separator: unchanged.
    assert strip_field_label('supplier_name', 'Quote Solutions Ltd') == 'Quote Solutions Ltd'
    assert strip_field_label('incoterms', 'DAP Dayton (Incoterms 2020)') == 'DAP Dayton (Incoterms 2020)'
    assert strip_field_label('payment_terms', 'Net 30: due on receipt') == 'Net 30: due on receipt'
    assert strip_field_label('quantity', 25.0) == 25.0
    d = doc('Incoterms: FCA')
    f = ground(SupplierQuote(incoterms=Evidenced(value='Incoterms: FCA', page=1, verbatim_span='Incoterms: FCA')), d, supplier_currency=None).incoterms
    assert f.grounded and f.value == 'FCA'
