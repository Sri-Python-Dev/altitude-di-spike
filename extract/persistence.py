from extract.schemas import SupplierQuote

class UngroundedValueError(ValueError):
    def __str__(self):
        return 'Unverified extraction value cannot be persisted'

def verified_payload(quote: SupplierQuote) -> dict:
    # Fail closed: reject rather than silently persisting model guesses.
    for name, field in quote.fields():
        if field.value is not None and not field.grounded:
            raise UngroundedValueError()
    return quote.model_dump(mode='json')
