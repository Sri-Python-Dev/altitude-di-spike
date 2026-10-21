Fix currency resolution and the currency rule.

1. Remove any global "$ -> USD" mapping from rules/quote_flags.json and from code.
2. In extract/normalise.py add resolve_currency(symbol_or_code, supplier_default_currency, document_header_text) -> str | None. Rules: an ISO code in the span wins; a bare symbol resolves only if it is unambiguous ("€" -> EUR, "£" -> GBP) or if the supplier default currency uses that symbol ("$" with supplier default USD -> USD; "$" with default CAD -> CAD); otherwise return None and the field stays ungrounded with reason "ambiguous_symbol".
3. The decision table keeps one rule: quote_currency != supplier_default_currency -> flag CURRENCY_MISMATCH, severity review. Add a second: quote_currency is None -> flag CURRENCY_UNRESOLVED, severity review.
4. Supplier default currency comes from data/suppliers.json for the spike (supplier_name -> currency). Note in rules/README.md that this will come from erp_staging later.
5. Tests: "$" with USD supplier resolves to USD and no flag; "$" with CAD supplier resolves to CAD and flags mismatch against a USD PO; "$" with unknown supplier is unresolved and flags; "USD 4,500" resolves regardless of supplier.
6. Re-run the harness; the currency row must not regress.

Done when: no global symbol alias exists, the four tests pass, and the harness currency accuracy is equal or better than the last run.
