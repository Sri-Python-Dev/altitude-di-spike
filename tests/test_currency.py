"""Prompt 09: supplier-scoped currency resolution and the currency rules, evaluated through
zen-engine against the real rules/quote_flags.json."""
import json
from extract.normalise import resolve_currency
from extract.schemas import Evidenced, SupplierQuote
from parse.models import LayoutElement, Page, ParsedDocument, render_text
from rules.engine import evaluate, supplier_currency

USD_SUPPLIER = 'Everest Components'          # on file as USD
CAD_SUPPLIER = 'Beacon Materials Group'      # on file as CAD
UNKNOWN_SUPPLIER = 'Lone Star Machining Inc'  # not on file (added live in the demo)

def codes(flags):
    return sorted(f['flag_code'] for f in flags)

# ---- the four tests prompt 09 asks for ----

def test_dollar_with_usd_supplier_resolves_to_usd_and_no_flag():
    default = supplier_currency(USD_SUPPLIER)
    assert default == 'USD'
    assert resolve_currency('$4,500.00', default, '') == 'USD'
    assert evaluate({'supplier_name': USD_SUPPLIER, 'quote_currency': 'USD'}) == []

def test_dollar_with_cad_supplier_resolves_to_cad_and_flags_mismatch_against_usd_po():
    default = supplier_currency(CAD_SUPPLIER)
    assert default == 'CAD'
    quote_currency = resolve_currency('$4,500.00', default, '')
    assert quote_currency == 'CAD'
    flags = evaluate({'supplier_name': CAD_SUPPLIER, 'quote_currency': quote_currency, 'po_currency': 'USD'})
    assert codes(flags) == ['CURRENCY_MISMATCH']
    assert flags[0]['severity'] == 'review' and flags[0]['field'] == 'currency'

def test_dollar_with_unknown_supplier_is_unresolved_and_flags():
    default = supplier_currency(UNKNOWN_SUPPLIER)
    assert default is None
    assert resolve_currency('$4,500.00', default, '') is None
    assert codes(evaluate({'supplier_name': UNKNOWN_SUPPLIER, 'quote_currency': None})) == ['CURRENCY_UNRESOLVED']

def test_iso_code_resolves_regardless_of_supplier():
    for default in ('USD', 'CAD', 'EUR', None):
        assert resolve_currency('USD 4,500', default, '') == 'USD'

# ---- supporting cases ----

def test_resolution_rules():
    assert resolve_currency('€1.200,00', None, '') == 'EUR'
    assert resolve_currency('£80', 'USD', '') == 'GBP'            # unambiguous symbol beats the supplier's currency
    assert resolve_currency('US$ 12', 'CAD', '') == 'USD'          # "US$" is not read as "$"
    assert resolve_currency('$12', 'EUR', '') is None              # supplier's currency is not written with "$"
    assert resolve_currency('¥500', None, '') is None              # yen and yuan share "¥"
    assert resolve_currency('29.05', None, 'Currency: CAD') == 'CAD'  # nothing in the span: one code in the header
    assert resolve_currency('Delivery DAP, FCA or VAT', None, '') is None  # three capitals are not a currency

def test_mismatch_against_supplier_currency_on_file():
    assert codes(evaluate({'supplier_name': USD_SUPPLIER, 'quote_currency': 'GBP'})) == ['CURRENCY_MISMATCH']
    assert evaluate({'supplier_name': 'everest components inc.', 'quote_currency': 'USD'}) == []  # name matching ignores case and "Inc."

def test_no_global_symbol_alias():
    from eval.metrics import normalize
    from extract.normalise import UNAMBIGUOUS
    assert '$' not in UNAMBIGUOUS and normalize('$') != normalize('USD')
    graph = json.loads(open('rules/quote_flags.json').read())
    assert '"$"' not in json.dumps(graph)

def _doc(lines):
    elements = [LayoutElement(text=t, bbox=(10, 10 + 20 * i, 300, 20 + 20 * i)) for i, t in enumerate(lines)]
    return ParsedDocument(document_id='d', pages=[Page(number=1, text=render_text(elements), elements=elements)])

def test_ground_uses_supplier_currency_on_file():
    from extract.ground import ground
    doc = _doc(['Lone Star Machining Inc', 'Prices quoted in $'])
    q = SupplierQuote(supplier_name=Evidenced(value='Lone Star Machining Inc', page=1, verbatim_span='Lone Star Machining Inc'),
                      currency=Evidenced(value='$', page=1, verbatim_span='Prices quoted in $'))
    unknown = ground(q, doc)  # looks the supplier up in rules/quote_flags.json: not on file
    assert unknown.currency.value is None and unknown.currency.ungrounded_reason == 'ambiguous_symbol'
    known = ground(q, doc, supplier_currency='USD')
    assert known.currency.grounded and known.currency.value == 'USD' and known.currency.bbox
    # The model's answer must agree with the document.
    q.currency.value = 'CAD'
    assert ground(q, doc, supplier_currency='USD').currency.ungrounded_reason == 'currency_not_derivable'

def test_currency_written_in_words_resolves_without_the_supplier():
    from extract.ground import ground
    for text, code in [('Prices quoted in $ (US dollars)', 'USD'), ('All prices in Canadian dollars', 'CAD'),
                       ('Amounts in euros', 'EUR'), ('Payable in pounds sterling', 'GBP'), ('Prices in U.S. dollars', 'USD')]:
        assert resolve_currency(text, None, '') == code, text
    doc = _doc(['Lone Star Machining Inc', 'Prices quoted in $ (US dollars)'])
    q = SupplierQuote(currency=Evidenced(value='$', page=1, verbatim_span='Prices quoted in $ (US dollars)'))
    f = ground(q, doc, supplier_currency=None).currency      # supplier not on file: the words decide
    assert f.grounded and f.value == 'USD'

def test_currency_words_never_guess():
    assert resolve_currency('Prices in dollars', None, '') is None            # which dollar?
    assert resolve_currency('Weight 40 pounds', None, '') is None              # not money
    assert resolve_currency('€ 120 (US dollars)', None, '') is None            # contradicts itself
    assert resolve_currency('Prices in $ (Canadian dollars)', 'USD', '') == 'CAD'  # the document beats the supplier file
    assert resolve_currency('29.05', None, 'Euro Fasteners GmbH') is None      # company names in the header are not currencies
