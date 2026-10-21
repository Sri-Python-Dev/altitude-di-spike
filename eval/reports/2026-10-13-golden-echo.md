# Supplier quote extraction — `golden-echo`

> **SMOKE TEST — not a measurement.** This extractor copies the golden answers, so accuracy is 100% by construction.
> It checks that parsing, the Ground step and this harness work end to end. Use `current` or `frontier` for real numbers.

Generated 2026-10-13T20:10:43+00:00 over data/golden/quotes-v1, data/synthetic/edge-cases.

- **Documents:** 28
- **Latency per document:** p50 0.478s, p95 2.794s
- **Worst fields:** currency, incoterms, line_items[i].extended_price

`Accuracy` = value correct after normalisation. `Traced to source` = the Ground step found the span on the stated page and attached its location.
`Evidence match` = traced, and the span equals or overlaps the golden span by ≥80%. `Ungrounded` = a value was claimed but rejected by the Ground step.

| Field | Accuracy | Traced to source | Evidence match | Ungrounded | Missing | n |
|---|---:|---:|---:|---:|---:|---:|
| currency | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 28 |
| incoterms | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 28 |
| line_items[i].extended_price | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 64 |
| line_items[i].lead_time_days | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 64 |
| line_items[i].moq | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 64 |
| line_items[i].part_number_quoted | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 64 |
| line_items[i].quantity | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 64 |
| line_items[i].unit_price | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 64 |
| payment_terms | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 28 |
| quote_date | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 28 |
| quote_reference | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 28 |
| supplier_name | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 28 |
| valid_until | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 28 |

## Ungrounded fields

None.

## Edge cases

| Document | Case | Expected | Got | Grounded fields | Pass |
|---|---|---|---|---:|---|
| body-html | quote in HTML email body | 1 quote(s) | 1 quote(s) | 13/13 | ✅ |
| body-plain | quote in plaintext email body | 1 quote(s) | 1 quote(s) | 19/19 | ✅ |
| corrupt | truncated PDF | corrupt | corrupt | — | ✅ |
| password-protected | password-protected PDF | password_protected | password_protected | — | ✅ |
| scanned-quote | scanned (no text layer) | 1 quote(s) | 1 quote(s), ocr | 19/19 | ✅ |
| three-quotes-separate-pages-q1 | multi-quote, one per page | 3 quote(s) | 3 quote(s) | 19/19 | ✅ |
| three-quotes-separate-pages-q2 | multi-quote, one per page | 3 quote(s) | 3 quote(s) | 25/25 | ✅ |
| three-quotes-separate-pages-q3 | multi-quote, one per page | 3 quote(s) | 3 quote(s) | 13/13 | ✅ |
| two-quotes-same-page-q1 | multi-quote, same page | 2 quote(s) | 2 quote(s) | 13/13 | ✅ |
| two-quotes-same-page-q2 | multi-quote, same page | 2 quote(s) | 2 quote(s) | 19/19 | ✅ |
