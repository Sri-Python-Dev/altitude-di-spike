Create the golden dataset format and a labelling helper.

1. Define data/golden/schema.json: one JSON file per document with document_id, sha256, doc_type (quote|po|invoice|other), and fields[]. Each field has name, value, page, bbox, verbatim_span, labeller, labelled_at. Line items are fields with name "line_items[i].<field>".
2. Define the SupplierQuote field list in extract/schemas.py as a Pydantic model: header (supplier_name, quote_reference, quote_date, valid_until, currency, incoterms, payment_terms) and line_items (part_number_quoted, quantity, unit_price, extended_price, lead_time_days, moq). Every field is Evidenced[T].
3. Write tools/label.py: given a PDF, run Docling, print each page's text with line numbers, and let the labeller enter field name, value and the exact span; the tool locates the span, records page and bbox from Docling's layout output, and writes the golden JSON. Refuse to save a field whose span is not found verbatim.
4. Add a validator (tools/validate_golden.py) that fails if any golden span cannot be found in the document's parsed text.
5. Label the 20 quotes in data/golden/quotes-v1/ using the tool. Record who labelled each.

Done when: 20 golden files validate cleanly and `uv run tools/validate_golden.py data/golden/quotes-v1` exits 0.
