# Task 01 — Golden Dataset Format and Labelling Helper

Create the golden dataset format and a labelling helper.

## Steps

1. **Define `data/golden/schema.json`.**
   - One JSON file per document, with these top-level keys:
     - `document_id`
     - `sha256`
     - `doc_type` (`quote | po | invoice | other`)
     - `fields[]`
   - Each field has:
     - `name`
     - `value`
     - `page`
     - `bbox`
     - `verbatim_span`
     - `labeller`
     - `labelled_at`
   - Line items are fields named `line_items[i].<field>`.

2. **Define the `SupplierQuote` field list in `extract/schemas.py`** as a Pydantic model. Every field is `Evidenced[T]`.
   - **Header:**
     - `supplier_name`
     - `quote_reference`
     - `quote_date`
     - `valid_until`
     - `currency`
     - `incoterms`
     - `payment_terms`
   - **Line items:**
     - `part_number_quoted`
     - `quantity`
     - `unit_price`
     - `extended_price`
     - `lead_time_days`
     - `moq`

3. **Write `tools/label.py`.** Given a PDF, the tool:
   1. Runs Docling and prints each page's text with line numbers.
   2. Lets the labeller enter the field name, value and exact span.
   3. Locates the span, then records its page and bbox from Docling's layout output.
   4. Writes the golden JSON.

   It must refuse to save any field whose span is not found verbatim.

4. **Add a validator, `tools/validate_golden.py`.** It fails if any golden span cannot be found in the document's parsed text.

5. **Label the 20 quotes in `data/golden/quotes-v1/` using the tool.** Record who labelled each one.

## Done when

All 20 golden files validate cleanly, and this command exits 0:

```bash
uv run tools/validate_golden.py data/golden/quotes-v1
```
