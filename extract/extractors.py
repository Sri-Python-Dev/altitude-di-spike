"""Named extractors. Each returns an UNGROUNDED SupplierQuote; callers run ground().

- current:     extraction prompt on the default model (llm/config.yaml: extract_quote)
- frontier:    same prompt on the frontier model (extract_quote_frontier)
- golden-echo: copies the answers from the golden JSON. Not a model. It exists to
               test the pipeline, grounding and harness without an API key, and
               its reports are labelled as smoke tests.
"""
import json
from functools import lru_cache
from pathlib import Path
from pydantic import BaseModel
from extract.prompts import QUOTE_EXTRACTION
from extract.schemas import Evidenced, QuoteLineItem, SupplierQuote
from parse.models import ParsedDocument

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOTS = (ROOT / 'data/golden', ROOT / 'data/synthetic')

# What the model may return: value, page, span and self-reported confidence. bbox and
# grounded are not in this schema, so the model cannot claim them; only ground() sets them.
class _Text(BaseModel):
    value: str | None
    page: int | None
    verbatim_span: str | None
    raw_confidence: float | None

class _Number(_Text):
    value: float | None

class _Integer(_Text):
    value: int | None

class _ModelLineItem(BaseModel):
    part_number_quoted: _Text
    quantity: _Number
    unit_price: _Number
    extended_price: _Number
    lead_time_days: _Integer
    moq: _Number

class _ModelQuote(BaseModel):
    supplier_name: _Text
    quote_reference: _Text
    quote_date: _Text
    valid_until: _Text
    currency: _Text
    incoterms: _Text
    payment_terms: _Text
    stated_total: _Number
    line_items: list[_ModelLineItem]

def _model(task: str):
    def run(parsed: ParsedDocument) -> SupplierQuote:
        from llm.router import route_and_call
        # Content stays in this process; it is never a Temporal workflow argument.
        content = '\n'.join(f'Page {p.number}:\n{p.text}' for p in parsed.pages)
        answer = route_and_call(task, {'instruction': QUOTE_EXTRACTION, 'content': content}, _ModelQuote)
        return SupplierQuote.model_validate(answer.model_dump())
    return run

@lru_cache(maxsize=1)
def _golden_index() -> dict:
    """Golden files by document_id and by (source sha256, segment)."""
    index = {}
    for root in GOLDEN_ROOTS:
        for label in root.rglob('*.json'):
            if label.name == 'schema.json':
                continue
            try:
                g = json.loads(label.read_text())
                index[g['document_id']] = label
                for sha in (g.get('sha256'), g.get('body_sha256')):
                    if sha:
                        index[(sha, g.get('segment', 1))] = label
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
    return index

def golden_to_quote(golden: dict) -> SupplierQuote:
    quote = SupplierQuote()
    for field in golden['fields']:
        evidenced = Evidenced(value=field['value'], page=field['page'], verbatim_span=field['verbatim_span'])
        name = field['name']
        if name.startswith('line_items['):
            index, attr = int(name[len('line_items['):name.index(']')]), name.split('.', 1)[1]
            while len(quote.line_items) <= index:
                quote.line_items.append(QuoteLineItem())
            setattr(quote.line_items[index], attr, evidenced)
        else:
            setattr(quote, name, evidenced)
    return SupplierQuote.model_validate(quote.model_dump())

def golden_echo(parsed: ParsedDocument) -> SupplierQuote:
    index = _golden_index()
    label = index.get(parsed.document_id) or index.get((parsed.source_sha256, parsed.segment or 1))
    if label is None:
        raise LookupError('No golden answers for this document id')
    return golden_to_quote(json.loads(label.read_text()))

# Extractor name -> llm/config.yaml task, for model and price lookup.
MODEL_TASKS = {'current': 'extract_quote', 'frontier': 'extract_quote_frontier'}

EXTRACTORS = {
    'current': _model(MODEL_TASKS['current']),
    'frontier': _model(MODEL_TASKS['frontier']),
    'golden-echo': golden_echo,
}
