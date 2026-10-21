"""Prompt 04: one case per rule in rules/quote_flags.json, plus a case with no flags.
Each feeds plain inputs through zen-engine and asserts the exact flag codes."""
from extract.schemas import Evidenced, QuoteLineItem, SupplierQuote
from rules.engine import evaluate, flags_for_quote, line_total_sum

def codes(inputs):
    return sorted(f['flag_code'] for f in evaluate(inputs))

CLEAN = {'supplier_name': 'Everest Components', 'quote_currency': 'USD', 'stated_total': 1000.0, 'line_total_sum': 1000.0}

def test_no_flags():
    assert codes(CLEAN) == []

def test_currency_mismatch_against_supplier_currency_on_file():
    assert codes({**CLEAN, 'quote_currency': 'GBP'}) == ['CURRENCY_MISMATCH']

def test_currency_mismatch_against_po_currency():
    assert codes({**CLEAN, 'po_currency': 'EUR'}) == ['CURRENCY_MISMATCH']

def test_currency_unresolved():
    assert codes({**CLEAN, 'quote_currency': None}) == ['CURRENCY_UNRESOLVED']

def test_total_mismatch_above_one_percent():
    assert codes({**CLEAN, 'line_total_sum': 1010.01}) == ['TOTAL_MISMATCH']
    assert codes({**CLEAN, 'line_total_sum': 989.0}) == ['TOTAL_MISMATCH']

def test_total_within_one_percent_or_unknown_is_not_flagged():
    assert codes({**CLEAN, 'line_total_sum': 1009.99}) == []        # 0.999%
    assert codes({**CLEAN, 'line_total_sum': None}) == []
    assert codes({**CLEAN, 'stated_total': None}) == []

def _quote(amounts, grounded=True, total=None):
    items = [QuoteLineItem(extended_price=Evidenced(value=a, grounded=grounded)) for a in amounts]
    return SupplierQuote(supplier_name=Evidenced(value='Everest Components', grounded=True),
                         currency=Evidenced(value='USD', grounded=True),
                         stated_total=Evidenced(value=total, grounded=total is not None), line_items=items)

def test_line_total_sum_only_when_every_line_amount_is_grounded():
    assert line_total_sum(_quote([100.0, 200.5])) == 300.5
    assert line_total_sum(_quote([100.0, 200.5], grounded=False)) is None
    assert line_total_sum(_quote([])) is None

def test_flags_for_a_grounded_quote():
    assert flags_for_quote(_quote([600.0, 400.0], total=1000.0)) == []
    flags = flags_for_quote(_quote([600.0, 400.0], total=1100.0))
    assert [f['flag_code'] for f in flags] == ['TOTAL_MISMATCH'] and flags[0]['field'] == 'stated_total'

def test_confidence_is_never_a_rule_input():
    import json
    graph = json.loads(open('rules/quote_flags.json').read())
    fields = {i['field'] for n in graph['nodes'] if n['type'] == 'decisionTableNode' for i in n['content']['inputs']}
    assert not any('confidence' in f for f in fields)
