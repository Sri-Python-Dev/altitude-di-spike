from extract.schemas import SupplierQuote, Evidenced

def test_unverified_value_never_persists():
    q = SupplierQuote(supplier_name=Evidenced(value='Invented',grounded=False))
    assert q.safe_for_storage().supplier_name.value is None
