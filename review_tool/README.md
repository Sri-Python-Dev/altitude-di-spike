# Golden review tool

A person checks every known answer in a golden folder against its PDF and signs it off, so the
harness scores are measured against human-reviewed answers.

Standalone: nothing in the pipeline imports this folder, and it imports nothing from the
pipeline. Delete `review_tool/` and everything else works as before.

```bash
uv run python review_tool/review.py data/golden/quotes-v1 --reviewer "Srini"
# open http://127.0.0.1:8770
```

For each quote:

1. Click a field (or Tab to it): its box is drawn on the page.
2. Compare the value with the page. Type a correction if it is wrong; tick **not on page** if the
   page does not state it.
3. Press **Enter** to mark it checked and move to the next field.
4. When every field is checked, **Save review**. The tool opens the next quote still to review.

What to look for (also shown on every quote): right field (quote date vs valid until, quote
reference vs RFQ number); exact numbers; lead time in days; currency as an ISO code; nothing
missing or extra.

Saving writes into the golden JSON only: `labeller` and `labelled_at` on every field, and in
`metadata`: `human_reviewed: true`, `reviewed_by`, `reviewed_at`, `original_labeller`, and a
`review_changes` log of every corrected or removed value. Afterwards run the pipeline's validator:

```bash
uv run python -m tools.validate_golden data/golden/quotes-v1
```

The tool changes values only. If a value's source text on the page is wrong, relabel that field
with `uv run python -m tools.label` instead.

Tests: `uv run pytest review_tool` (not part of the main suite).
