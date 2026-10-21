# Task 03 — Grounding Step (Activity 6)

Replace activity 6 (Verify, currently a placeholder) with a real grounding step.

## Steps

1. **Change the extraction prompt in `extract/prompts.py`.**
   - For every field, the model must return the exact verbatim text it took the value from, copied character for character, plus the page number.
   - Use structured outputs with the `Evidenced` schema.
   - Do not let the model paraphrase the span.

2. **Write `extract/ground.py`** with `ground(extraction, parsed_doc) -> extraction`. For each field:
   - **Locate** `verbatim_span` in the Docling text, trying these in order:
     1. an exact match on the stated page
     2. a match with whitespace normalised
     3. a match on neighbouring pages
   - **On success:** attach the bbox from the Docling layout element that contains the span. If the span covers more than one element, use the union of their bboxes.
   - **On failure:**
     - set `value=None` and `grounded=False`
     - keep the model's span in `ungrounded_span` for debugging

3. **Add a post-check.** Fields that fail it are ungrounded.
   - Numeric fields must parse from the span. For example, a `unit_price` of `"4,500.00"` must appear in the span text.
   - Currency must be derivable from the span or from the document header.

4. **Wire the step into `worker.py`.**
   - `ground` becomes the new activity 6.
   - Activity 7 must refuse to save any field with `grounded=False` and a non-null value.

5. **Tests:**
   - A span that exists: grounded, with bbox populated.
   - A span the model invented: ungrounded.
   - A span with different whitespace: grounded.
   - A number not present in the span: ungrounded.

6. **Re-run the harness.**
   - `evidence_match_rate` must now be 100% on every grounded field, by construction.
   - Report how many fields became ungrounded, and which ones.

## Done when

- Tests pass.
- `worker.py` runs end to end on a synthetic quote.
- The harness report shows `evidence_match_rate` and `ungrounded_rate` per field.
