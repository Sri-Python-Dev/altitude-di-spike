"""Ground step: a value is kept only if its verbatim span is found in the parsed
document, the value can be read from that span, and a layout bbox is attached.

Failure reasons are fixed codes (never document text) so they are safe to log.
"""
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from extract.schemas import Evidenced, SupplierQuote
from parse.models import BBox, LayoutElement, ParsedDocument, Page

NUMERIC = {'quantity', 'unit_price', 'extended_price', 'lead_time_days', 'moq', 'stated_total'}
DATES = {'quote_date', 'valid_until'}
# Currency: no global symbol alias. See extract/normalise.py and the supplier table in rules/quote_flags.json.
MAX_ELEMENTS_PER_SPAN = 16  # e.g. a 6-column header row plus one data row
# Labels that usually sit next to a field's value; used only to choose between elements that all contain the span.
FIELD_LABELS = {
    'currency': ('currency',), 'incoterms': ('incoterm',), 'payment_terms': ('payment', 'terms'),
    'quote_reference': ('ref', 'quote no', 'quote #', 'quotation'), 'quote_date': ('date',), 'valid_until': ('valid', 'expir'),
    'quantity': ('qty', 'quantity'), 'unit_price': ('unit',), 'extended_price': ('ext', 'total', 'amount'),
    'lead_time_days': ('lead',), 'moq': ('moq', 'minimum'), 'stated_total': ('total',),
}

def _norm(s: str) -> str:
    return ' '.join(s.split())

def _flat(s: str) -> str:
    """Whitespace-normalised, with table cell separators removed (spans may copy ' | ')."""
    return _norm(s.replace('|', ' '))

def _contains(span: str, text: str) -> bool:
    return span in text or _norm(span) in _norm(text)

def _bounded(span: str, text: str) -> bool:
    return re.search(r'(?<!\w)' + re.escape(_norm(span)) + r'(?!\w)', _norm(text)) is not None

DEBRIS = '{}[],;"\'' + '.'  # characters models leak around spans (e.g. JSON "},"); never trimmed inside a span
MAX_DEBRIS = 4

def trim_debris(span: str) -> str:
    """The span without leaked structural characters at either end ("Acme Ltd}," -> "Acme Ltd")."""
    text = span.strip()
    for _ in range(MAX_DEBRIS):
        trimmed = text.rstrip(DEBRIS).lstrip('{["\'').strip()
        if trimmed == text:
            break
        text = trimmed
    return text

def locate_span(span: str, page: Page) -> bool:
    """Exact match first, then whitespace-normalised."""
    return _contains(span, page.text)

def _union(boxes: list[BBox]) -> BBox:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))

def span_elements(span: str, page: Page, row: tuple[int, int] | None = None, labels: tuple[str, ...] = (),
                  accept=None) -> list[LayoutElement]:
    """The layout element(s) that contain the span, most specific first.

    Ranking: element text equals the span > span is a whole token in the element >
    span is a substring. Within a rank, an element that also holds one of the
    field's labels wins, then the shortest text. If row is given (a line item's
    table row), cells in that row win. accept(element), if given, vetoes single-element
    candidates (the value must be readable from the element itself). If no single
    element holds the span, the shortest run of consecutive elements that does is returned.
    """
    target = _norm(span)
    elements = [e for e in page.elements if e.bbox]
    ranks = (
        [e for e in elements if _norm(e.text) == target],
        [e for e in elements if _bounded(span, e.text)],
        [e for e in elements if target in _norm(e.text)],
    )
    for candidates in ranks:
        if accept is not None:
            candidates = [e for e in candidates if accept(e)]
        if not candidates:
            continue
        # "Currency: CAD" over "Quote total: CAD 1,770.00": labelled first, then most specific; stable, so ties keep reading order.
        candidates = sorted(candidates, key=lambda e: (not any(l in e.text.casefold() for l in labels), len(_norm(e.text))))
        if row is not None:
            in_row = [e for e in candidates if (e.table, e.row) == row]
            if in_row:
                return in_row[:1]
        return candidates[:1]
    flat = _flat(span)
    for width in range(2, MAX_ELEMENTS_PER_SPAN + 1):
        for i in range(len(elements) - width + 1):
            run = elements[i:i + width]
            if flat in _flat(' '.join(e.text for e in run)):
                return run
    return []

def span_bbox(span: str, page: Page, row: tuple[int, int] | None = None, labels: tuple[str, ...] = ()) -> BBox | None:
    found = span_elements(span, page, row, labels)
    return _union([e.bbox for e in found]) if found else None

def _numbers(text: str) -> list[Decimal]:
    out = []
    for raw in re.findall(r'(?<![\w.])-?\d[\d,]*(?:\.\d+)?', text):
        try:
            out.append(Decimal(raw.replace(',', '')))
        except InvalidOperation:
            pass
    return out

def _number_in_span(value, span: str) -> bool:
    try:
        expected = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return False
    return expected in _numbers(span)

def _date_renderings(d: date) -> set[str]:
    day, month = str(d.day), str(d.month)
    out = {d.strftime(f) for f in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d.%m.%Y', '%d-%m-%Y',
                                   '%d %b %Y', '%d %B %Y', '%b %d, %Y', '%B %d, %Y', '%Y/%m/%d')}
    out |= {f'{day}/{month}/{d.year}', f'{month}/{day}/{d.year}', f'{day}.{month}.{d.year}',
            f'{day} {d:%b} {d.year}', f'{day} {d:%B} {d.year}', f'{d:%b} {day}, {d.year}', f'{d:%B} {day}, {d.year}'}
    return {r.casefold() for r in out}

def _date_in_span(value, span: str) -> bool:
    try:
        d = value if isinstance(value, date) else date.fromisoformat(str(value).strip())
    except ValueError:
        return False
    text = _norm(span).casefold()
    return any(r in text for r in _date_renderings(d))

def header_text(doc: ParsedDocument) -> str:
    """Text on the first page that comes before the first table."""
    if not doc.pages:
        return ''
    first = doc.pages[0]
    if not first.elements:
        return first.text
    head = []
    for e in first.elements:
        if e.kind == 'cell':
            break
        head.append(e.text)
    return '\n'.join(head)

def _value_supported(field_name: str, value, span: str, doc: ParsedDocument,
                     supplier_currency: str | None = None) -> str | None:
    """Return a failure code, or None if the value can be read from the span."""
    if field_name in NUMERIC:
        return None if _number_in_span(value, span) else 'value_not_in_span'
    if field_name in DATES:
        return None if _date_in_span(value, span) else 'value_not_in_span'
    if field_name == 'currency':
        from extract.normalise import resolve_currency, unresolved_reason
        resolved = resolve_currency(span, supplier_currency, header_text(doc))
        if resolved is None:
            return unresolved_reason(span)
        return None if resolved == str(value).strip().upper() else 'currency_not_derivable'
    return None if _norm(str(value)).casefold() in _norm(span).casefold() else 'value_not_in_span'

def _candidate_pages(doc: ParsedDocument, stated: int | None) -> list[Page]:
    """Stated page first, then its neighbours. No stated page: every page."""
    by_number = {p.number: p for p in doc.pages}
    if stated is None or stated not in by_number:
        return list(doc.pages)
    return [by_number[n] for n in (stated, stated - 1, stated + 1) if n in by_number]

def _fail(field: Evidenced, reason: str):
    field.ungrounded_span = field.verbatim_span
    field.ungrounded_reason = reason
    field.value = None
    field.grounded = False
    field.bbox = None

def _within_quoted_region(span: str, pages: list[Page], row, labels, readable) -> tuple[Page, LayoutElement] | None:
    """When the model quotes a whole region (e.g. half a page) for one value, find the single layout
    element inside that region that holds the value. The element's own text becomes the evidence."""
    region = _flat(span)
    for page in pages:
        candidates = [e for e in page.elements if e.bbox and _flat(e.text) and _flat(e.text) in region and readable(e)]
        if row is not None:
            candidates = [e for e in candidates if (e.table, e.row) == row] or candidates
        if candidates:
            # The element carrying the field's label first, then the most specific (shortest) one.
            best = sorted(candidates, key=lambda e: (not any(l in e.text.casefold() for l in labels), len(_norm(e.text))))[0]
            return page, best
    return None

def _ground_on(field: Evidenced, page: Page, element: LayoutElement) -> LayoutElement:
    field.page = page.number
    field.verbatim_span = element.text  # the document's own characters
    field.bbox = element.bbox
    field.grounded = True
    field.ungrounded_span = None
    return element

def ground_field(field_name: str, field: Evidenced, doc: ParsedDocument,
                 row: tuple[int, int] | None = None, row_page: int | None = None,
                 supplier_currency: str | None = None) -> LayoutElement | None:
    """Ground one field in place; return the anchoring element when grounded."""
    field.grounded = False
    field.bbox = None
    field.ungrounded_reason = None
    if field.value is None:
        # Nothing claimed: absent, not ungrounded.
        field.ungrounded_span = field.verbatim_span
        return None
    span = field.verbatim_span
    if not span or not span.strip():
        _fail(field, 'no_span')
        return None
    # A line item's fields are looked for on its part number's page first.
    pages = _candidate_pages(doc, row_page if row_page is not None else field.page)
    # Stated page exact, stated page whitespace-normalised, then neighbouring pages.
    page = next((p for p in pages[:1] if span in p.text), None) or next((p for p in pages if locate_span(span, p)), None)
    if page is None:
        # Leaked JSON or punctuation at the ends ("Acme Ltd},"): retry without it.
        cleaned = trim_debris(span)
        if cleaned and cleaned != span:
            page = next((p for p in pages if locate_span(cleaned, p)), None)
            if page is not None:
                span = cleaned
    if page is None:
        # Lines joined with " | " or other separators the document does not have.
        page = next((p for p in pages if _flat(span) and _flat(span) in _flat(p.text)), None)
    labels = FIELD_LABELS.get(field_name, ())
    # Only an element the value can be read from: a bare "6" must not land inside "60".
    readable = lambda e: _value_supported(field_name, field.value, e.text, doc, supplier_currency) is None
    if page is None:
        # The quoted text is not on the page as written (e.g. a whole page re-ordered); the value may
        # still sit in one element inside what the model quoted. Currency is resolved from the span, so not here.
        found = _within_quoted_region(span, pages, row, labels, readable) if field_name != 'currency' else None
        if found:
            return _ground_on(field, *found)
        _fail(field, 'span_not_found')
        return None
    if field_name == 'currency':
        # Store ISO codes only. The document decides the currency; the model's own answer
        # ("$", "usd", "USD") must agree with it.
        from extract.normalise import iso_codes, resolve_currency, unresolved_reason
        code = resolve_currency(span, supplier_currency, header_text(doc))
        if code is None:
            _fail(field, unresolved_reason(span))
            return None
        claimed = str(field.value).strip().upper()
        if claimed in iso_codes(claimed) and claimed != code:
            _fail(field, 'currency_not_derivable')
            return None
        field.value = code
    reason = _value_supported(field_name, field.value, span, doc, supplier_currency)
    if reason:
        _fail(field, reason)
        return None
    elements = span_elements(span, page, row, labels, accept=readable)
    if len(elements) > 1:
        # A span covering several elements (e.g. a whole header table): point at the one holding the value.
        holding = [e for e in elements if readable(e)]
        elements = holding[:1] or elements
    if not elements and page.number != 0:
        # Too broad to pin down (e.g. the whole page quoted for one name): use the element inside it holding the value.
        found = _within_quoted_region(span, [page], row, labels, readable)
        if found:
            return _ground_on(field, *found)
        _fail(field, 'no_bbox')
        return None
    field.page = page.number
    if span not in page.text and len(elements) == 1:
        # Store the document's own characters, so the stored span is always literally on the page.
        span = elements[0].text
    field.verbatim_span = span
    # Email bodies (page 0) have no layout, so bbox stays None.
    field.bbox = _union([e.bbox for e in elements]) if elements else None
    field.grounded = True
    field.ungrounded_span = None
    return elements[0] if elements else None

_LOOKUP = object()

def ground(extraction: SupplierQuote, parsed_doc: ParsedDocument, supplier_currency=_LOOKUP) -> SupplierQuote:
    """Ground every field. supplier_currency defaults to the grounded supplier's currency on
    file (rules/quote_flags.json); pass a value or None to override, e.g. in tests."""
    result = extraction.model_copy(deep=True)
    ground_field('supplier_name', result.supplier_name, parsed_doc)
    if supplier_currency is _LOOKUP:
        from rules.engine import supplier_currency as on_file
        supplier_currency = on_file(result.supplier_name.value) if result.supplier_name.grounded else None
    for name in SupplierQuote.HEADER_FIELDS:
        if name != 'supplier_name':
            ground_field(name, getattr(result, name), parsed_doc, supplier_currency=supplier_currency)
    for item in result.line_items:
        anchor = ground_field('part_number_quoted', item.part_number_quoted, parsed_doc)
        row = (anchor.table, anchor.row) if anchor is not None and anchor.kind == 'cell' else None
        row_page = item.part_number_quoted.page if anchor is not None else None
        for name in type(item).model_fields:
            if name != 'part_number_quoted':
                ground_field(name, getattr(item, name), parsed_doc, row, row_page)
    return result
