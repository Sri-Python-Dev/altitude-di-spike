"""Classify a parsed document (or quote segment): quote | po | invoice | other.

Deterministic, from the document's own title and references, so it costs nothing and cannot
drift between runs. Words in the body ("Net 30 days after invoice", "per your purchase order")
do not count: only a title or a document reference does. This sprint processes quotes only;
anything else is recorded as not a quote and not extracted.
"""
import re
from extract.boundaries import QUOTE_REF
from parse.models import ParsedDocument

HEAD_LINES = 12  # titles and references sit at the top of the first page
QUOTE_TITLE = re.compile(r'(?i)^\s*(supplier\s+)?(quotation|quote|price quote|request for quotation response)\b')
QUOTE_HINT = re.compile(r'(?i)\b(quotation|quote\s+(ref|no|number|date|#)|valid\s+until|validity)\b')
PO_TITLE = re.compile(r'(?i)^\s*purchase\s+order\b')
PO_REF = re.compile(r'(?i)\b(purchase\s+order|p\.?\s?o\.?)\s*(number|no\.?|#|:)\s*[:#]?\s*[A-Z0-9-]*\d')
INVOICE_TITLE = re.compile(r'(?i)^\s*(tax\s+|commercial\s+)?invoice\b')
INVOICE_REF = re.compile(r'(?i)\binvoice\s*(number|no\.?|#|date)\b')

def _head(doc: ParsedDocument) -> list[str]:
    if not doc.pages:
        return []
    first = doc.pages[0]
    lines = [e.text for e in first.elements] if first.elements else first.text.splitlines()
    return [l for l in lines if l.strip()][:HEAD_LINES]

def classify(doc: ParsedDocument) -> dict:
    """{'doc_type': 'quote'|'po'|'invoice'|'other', 'reason': fixed code}."""
    head = _head(doc)
    text = '\n'.join(head)
    titles = {'quote': any(QUOTE_TITLE.match(l) for l in head), 'po': any(PO_TITLE.match(l) for l in head),
              'invoice': any(INVOICE_TITLE.match(l) for l in head)}
    refs = {'quote': bool(QUOTE_REF.search(text)), 'po': bool(PO_REF.search(text)), 'invoice': bool(INVOICE_REF.search(text))}
    # A title decides; then a document reference; a quote wins ties because quotes often cite the buyer's PO/RFQ.
    for kind in ('quote', 'po', 'invoice'):
        if titles[kind] and not any(titles[k] for k in titles if k != kind):
            return {'doc_type': kind, 'reason': f'{kind}_title'}
    if refs['quote'] or titles['quote']:
        return {'doc_type': 'quote', 'reason': 'quote_reference'}
    for kind in ('po', 'invoice'):
        if refs[kind]:
            return {'doc_type': kind, 'reason': f'{kind}_reference'}
    if QUOTE_HINT.search('\n'.join(p.text for p in doc.pages)):
        return {'doc_type': 'quote', 'reason': 'quote_wording'}
    return {'doc_type': 'other', 'reason': 'no_quote_markers'}
