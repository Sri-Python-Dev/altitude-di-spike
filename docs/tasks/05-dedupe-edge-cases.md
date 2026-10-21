# Task 05 — Harden Dedupe and Cover Edge Cases

Harden dedupe and cover the edge cases.

## Steps

1. **Dedupe key.**
   - An email is a duplicate if its RFC Internet Message-ID has been seen.
   - An attachment is a duplicate if its SHA-256 has been seen, regardless of its filename or which email carried it.
   - Record both.
   - Tests:
     - The same PDF, with a different filename, in a different email is skipped.
     - A new PDF in a re-sent email is processed.

2. **Multiple quotes in one PDF.**
   - After parsing, detect quote boundaries (a new quote reference or supplier header).
   - Emit one extraction per quote, with page ranges.
   - Test with a synthetic 3-quote PDF.

3. **Quote in the email body, with no attachment.**
   - Run the same classify and extract activities on the body text.
   - Use `page=0` and `bbox=None`. A `verbatim_span` is still required.
   - Test with a synthetic HTML body and a synthetic plaintext body.

4. **Scanned PDF.**
   - Detect that there is no text layer.
   - Run Docling's OCR path.
   - Record `ocr=True` on the document.
   - Test with a rasterised synthetic quote.

5. **Password-protected or corrupt PDF.**
   - Fail the parse activity with a typed error.
   - Mark the document `status=unparseable`.
   - Do not retry, and do not crash the workflow.
   - Test both cases.

6. **Add each case to the harness fixtures** so they stay covered.

## Done when

- All new tests pass.
- The harness report includes the synthetic edge-case documents.
