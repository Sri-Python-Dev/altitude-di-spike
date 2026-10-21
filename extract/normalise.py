"""Currency normalisation. There is no global symbol alias: "$" is used by USD, CAD, AUD and
others, so it resolves only through the supplier's currency on file."""
import re

KNOWN_ISO = {'USD', 'CAD', 'AUD', 'NZD', 'MXN', 'SGD', 'HKD', 'EUR', 'GBP', 'CHF', 'JPY', 'CNY', 'INR',
             'SEK', 'NOK', 'DKK', 'PLN', 'CZK', 'ZAR', 'BRL', 'KRW', 'TRY', 'AED', 'SAR'}
# A symbol that names exactly one currency.
UNAMBIGUOUS = {'€': 'EUR', '£': 'GBP', '₹': 'INR', 'US$': 'USD', 'C$': 'CAD', 'CA$': 'CAD', 'A$': 'AUD',
               'NZ$': 'NZD', 'S$': 'SGD', 'HK$': 'HKD'}
# Symbols each currency is written with, for resolving a shared symbol via the supplier's currency.
SYMBOLS_OF = {'USD': {'$', 'US$'}, 'CAD': {'$', 'C$', 'CA$'}, 'AUD': {'$', 'A$'}, 'NZD': {'$', 'NZ$'},
              'MXN': {'$'}, 'SGD': {'$', 'S$'}, 'HKD': {'$', 'HK$'}, 'JPY': {'¥'}, 'CNY': {'¥'},
              'EUR': {'€'}, 'GBP': {'£'}, 'INR': {'₹'}}
ALL_SYMBOLS = sorted({s for symbols in SYMBOLS_OF.values() for s in symbols} | set(UNAMBIGUOUS), key=len, reverse=True)
_ISO = re.compile(r'(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])')
# Currencies written out in words. Only names that identify one currency: a bare "dollars" or
# "pounds" (which may even be weight) stays ambiguous.
CURRENCY_NAMES = [
    (r'u\.?\s?s\.?\s+dollars?|united\s+states\s+dollars?|american\s+dollars?', 'USD'),
    (r'canadian\s+dollars?', 'CAD'), (r'australian\s+dollars?', 'AUD'), (r'new\s+zealand\s+dollars?', 'NZD'),
    (r'singapore\s+dollars?', 'SGD'), (r'hong\s+kong\s+dollars?', 'HKD'), (r'mexican\s+pesos?', 'MXN'),
    (r'euros?', 'EUR'), (r'pounds?\s+sterling|british\s+pounds?', 'GBP'), (r'swiss\s+francs?', 'CHF'),
    (r'japanese\s+yen', 'JPY'), (r'(chinese\s+)?renminbi|chinese\s+yuan', 'CNY'), (r'indian\s+rupees?', 'INR'),
]
_NAMES = [(re.compile(rf'(?i)(?<![a-z]){pattern}(?![a-z])'), code) for pattern, code in CURRENCY_NAMES]

def iso_codes(text: str) -> set[str]:
    return {c for c in _ISO.findall(text or '') if c in KNOWN_ISO}

def currency_names(text: str) -> set[str]:
    """ISO codes for currencies written out in words ("US dollars", "euros", "pounds sterling")."""
    return {code for pattern, code in _NAMES if pattern.search(text or '')}

def symbols(text: str) -> list[str]:
    """Currency symbols in text, longest first and consumed, so "US$" is not also read as "$"."""
    found, remaining = [], text or ''
    for symbol in ALL_SYMBOLS:
        if symbol in remaining:
            found.append(symbol)
            remaining = remaining.replace(symbol, ' ')
    return found

def resolve_currency(symbol_or_code: str | None, supplier_default_currency: str | None,
                     document_header_text: str | None) -> str | None:
    """The ISO currency the text states, or None.

    An ISO code in the text wins. Then a currency written out in words ("Prices in $ (US dollars)"),
    provided no symbol in the text contradicts it. A bare symbol resolves only if it is unambiguous
    ("€", "£", "US$") or the supplier's currency on file is written with it ("$" + USD supplier ->
    USD; "$" + CAD supplier -> CAD). With neither in the text, a single ISO code in the document
    header is used. Anything else returns None.
    """
    text = symbol_or_code or ''
    codes = iso_codes(text)
    if codes:
        return codes.pop() if len(codes) == 1 else None
    named = currency_names(text)
    if named:
        if len(named) != 1:
            return None
        code = named.pop()
        for symbol in symbols(text):  # "€ (US dollars)" contradicts itself
            if UNAMBIGUOUS.get(symbol, code) != code or (symbol not in UNAMBIGUOUS and symbol not in SYMBOLS_OF.get(code, ())):
                return None
        return code
    found = symbols(text)
    if found:
        resolved = set()
        default = (supplier_default_currency or '').upper() or None
        for symbol in found:
            if symbol in UNAMBIGUOUS:
                resolved.add(UNAMBIGUOUS[symbol])
            elif default and symbol in SYMBOLS_OF.get(default, ()):
                resolved.add(default)
            else:
                return None
        return resolved.pop() if len(resolved) == 1 else None
    # Header: ISO codes only. Currency names are not read there: company names ("Euro Fasteners") live there too.
    header = iso_codes(document_header_text or '')
    return header.pop() if len(header) == 1 else None

def unresolved_reason(symbol_or_code: str | None) -> str:
    """Why resolve_currency returned None: a symbol it could not attribute, or nothing to read."""
    return 'ambiguous_symbol' if symbols(symbol_or_code or '') else 'currency_not_derivable'
