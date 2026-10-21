Replace activity 6 (Verify, currently a placeholder) with a real grounding step.

1. Change the extraction prompt in extract/prompts.py so the model must return, for every field, the exact verbatim text it took the value from, copied character for character, plus the page number. Use structured outputs with the Evidenced schema. Do not let the model paraphrase the span.
2. Write extract/ground.py with ground(extraction, parsed_doc) -> extraction. For each field: locate verbatim_span in the Docling text for the stated page, first exact, then with whitespace normalised, then on neighbouring pages. On success, attach bbox from Docling's layout element that contains the span (union of element bboxes if it spans more than one). On failure set value=None, grounded=False, keep the model's span in ungrounded_span for debugging.
3. Add a post-check: numeric fields must parse from the span (unit_price "4,500.00" must appear in the span text); currency must be derivable from the span or the document header. Fields that fail the post-check are ungrounded.
4. Wire ground() as the new activity 6 in worker.py. Activity 7 must refuse to save any field with grounded=False and a non-null value.
5. Tests: a span that exists (grounded, bbox populated), a span the model invented (ungrounded), a span with different whitespace (grounded), a number not present in the span (ungrounded).
6. Re-run the harness. evidence_match_rate must now be 100% on every grounded field by construction; report how many fields became ungrounded and which.

Done when: tests pass, worker.py runs end to end on a synthetic quote, and the harness report shows evidence_match_rate and ungrounded_rate per field.
