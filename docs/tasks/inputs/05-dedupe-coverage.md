Harden dedupe and cover the edge cases.

1. Dedupe key: an email is a duplicate if its RFC internet message ID has been seen; an attachment is a duplicate if its SHA-256 has been seen, regardless of filename or which email carried it. Record both. Add tests: same PDF with a different filename in a different email is skipped; a new PDF in a re-sent email is processed.
2. Multiple quotes in one PDF: after parsing, detect quote boundaries (new quote reference or supplier header) and emit one extraction per quote with page ranges. Test with a synthetic 3-quote PDF.
3. Quote in the email body with no attachment: run the same classify and extract activities on the body text, with page=0 and bbox=None, verbatim_span still required. Test with a synthetic HTML and plain-text body.
4. Scanned PDF: detect no text layer, run Docling's OCR path, record ocr=True on the document. Test with a rasterised synthetic quote.
5. Password-protected or corrupt PDF: fail the parse activity with a typed error, mark the document status=unparseable, do not retry, do not crash the workflow. Test both.
6. Add each case to the harness fixtures so they stay covered.

Done when: all new tests pass and the harness report includes the synthetic edge-case documents.
