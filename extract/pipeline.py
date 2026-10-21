from extract.extractors import EXTRACTORS
from extract.ground import ground
from extract.schemas import SupplierQuote
from parse.models import ParsedDocument

def extract_raw(parsed: ParsedDocument, extractor: str = 'current') -> SupplierQuote:
    """Model output before the Ground step. Never store this directly."""
    if extractor not in EXTRACTORS:
        raise ValueError('Unknown extractor')
    return EXTRACTORS[extractor](parsed)

def extract_quote(parsed: ParsedDocument, extractor: str = 'current') -> SupplierQuote:
    # Only activity-local code calls this; never use inside a Temporal workflow.
    return ground(extract_raw(parsed, extractor), parsed)
