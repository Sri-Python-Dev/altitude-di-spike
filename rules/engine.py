"""Evaluate rules/quote_flags.json (GoRules JDM) with zen-engine.

The decision graph has two tables: "Supplier currency on file" (supplier -> currency;
stands in for erp_staging) and "Quote flags". No flag logic lives in Python; this module
only prepares inputs and reads the result. raw_confidence is never an input.
"""
import re
from pathlib import Path

RULES_FILE = Path(__file__).with_name('quote_flags.json')
LEGAL_SUFFIXES = {'inc', 'llc', 'ltd', 'limited', 'gmbh', 'co', 'corp', 'corporation', 'plc', 'sa', 'ag', 'bv'}
_cache: dict = {}

def supplier_key(name: str | None) -> str | None:
    """Matching key for the supplier table: lower case, no punctuation, no trailing legal suffix."""
    if not name:
        return None
    words = re.sub(r'[^\w\s]', ' ', name.casefold()).split()
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return ' '.join(words) or None

def _decision(path: Path = RULES_FILE):
    """Compiled decision, reloaded when the file changes (rules are edited while the app runs)."""
    import zen
    stamp = (str(path), path.stat().st_mtime_ns)
    if _cache.get('stamp') != stamp:
        _cache['decision'] = zen.ZenEngine().create_decision(path.read_text())
        _cache['stamp'] = stamp
    return _cache['decision']

def validate(content: str) -> None:
    """Raise if content is not a decision graph zen-engine can compile and run."""
    import zen
    zen.ZenEngine().create_decision(content).evaluate({})

def run(inputs: dict, path: Path = RULES_FILE) -> dict:
    data = dict(inputs)
    if 'supplier_name' in data:
        data['supplier'] = supplier_key(data.pop('supplier_name'))
    return _decision(path).evaluate(data)['result']

def supplier_currency(supplier_name: str | None, path: Path = RULES_FILE) -> str | None:
    """The supplier's currency on file, or None if the supplier is not in the table."""
    if not supplier_key(supplier_name):
        return None
    return run({'supplier_name': supplier_name}, path).get('supplier_default_currency')

def evaluate(inputs: dict, path: Path = RULES_FILE) -> list[dict]:
    """Flags for plain inputs (all optional): supplier_name, quote_currency, po_currency, stated_total,
    line_total_sum, currencies_seen_count, price_vs_last_quote_pct."""
    flags = run(inputs, path).get('flags') or []
    return [{k: f.get(k) for k in ('flag_code', 'severity', 'field', 'message')} for f in flags]

def line_total_sum(quote) -> float | None:
    """Sum of the line items' extended prices, only when every one of them is grounded
    (a partial sum would raise a false TOTAL_MISMATCH)."""
    amounts = [item.extended_price.value for item in quote.line_items]
    if not amounts or any(not item.extended_price.grounded for item in quote.line_items):
        return None
    return round(sum(amounts), 2)

def flags_for_quote(quote, po_currency: str | None = None, path: Path = RULES_FILE) -> list[dict]:
    """Flags for a grounded SupplierQuote. Only grounded values are inputs."""
    def grounded(field):
        return field.value if field.grounded else None
    return evaluate({'supplier_name': grounded(quote.supplier_name), 'quote_currency': grounded(quote.currency),
                     'po_currency': po_currency, 'stated_total': grounded(quote.stated_total),
                     'line_total_sum': line_total_sum(quote)}, path)
