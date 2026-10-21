import re
from parse.models import LayoutElement, ParsedDocument, Page, render_text

# "Quote Ref: QT-2026-0004", "Quotation No. 7781", "QUOTE NSI-Q-2026-1048 | CONTINUED".
# The label must end at a word boundary and the reference must contain a digit, so
# headings like "Quote Reference" or "Quotation Notes" are not read as references.
QUOTE_REF = re.compile(r'(?i)\b(?:quote|quotation)\b\s*(?:(?:ref(?:erence)?|number|no)\b\.?|#)?\s*[:#-]?\s*(?=[A-Z/-]*\d)([A-Z0-9][A-Z0-9/-]*\d[A-Z0-9/-]*|\d[A-Z0-9/-]*)')
HEADER_LOOKBACK = 3  # a supplier header within this many elements before a quote ref starts the quote

def split_quotes(document: ParsedDocument) -> list[ParsedDocument]:
    """One ParsedDocument per quote, split at a new quote reference.

    Works at layout-element level, so two quotes on the same page are separated.
    A repeated reference (e.g. a continuation page header) does not split.
    """
    flat: list[tuple[Page, LayoutElement]] = [(p, e) for p in document.pages for e in p.elements]
    if not flat:
        return [document.model_copy(update={'segment': 1})]
    starts, current_ref = [0], None
    for i, (page, element) in enumerate(flat):
        match = QUOTE_REF.search(element.text)
        if not match:
            continue
        ref = match.group(1).upper()
        if current_ref is not None and ref != current_ref:
            start = i
            for j in range(i - 1, max(starts[-1], i - HEADER_LOOKBACK - 1), -1):
                if flat[j][0].number == page.number and flat[j][1].kind == 'heading':
                    start = j
                    break
            starts.append(start)
        current_ref = ref
    if len(starts) == 1:
        return [document.model_copy(update={'segment': 1})]
    segments = []
    for n, (a, b) in enumerate(zip(starts, starts[1:] + [len(flat)]), 1):
        pages: dict[int, Page] = {}
        for page, element in flat[a:b]:
            pages.setdefault(page.number, Page(number=page.number, text='', width=page.width, height=page.height)).elements.append(element)
        for page in pages.values():
            page.text = render_text(page.elements)
        numbers = set(pages)
        segments.append(ParsedDocument(
            document_id=f'{document.document_id}-quote-{n}', pages=list(pages.values()), ocr=document.ocr,
            tables=[t for t in document.tables if t.get('page') in numbers],
            source_sha256=document.source_sha256, segment=n))
    return segments

def page_range(document: ParsedDocument) -> tuple[int, int]:
    numbers = [p.number for p in document.pages]
    return min(numbers), max(numbers)
