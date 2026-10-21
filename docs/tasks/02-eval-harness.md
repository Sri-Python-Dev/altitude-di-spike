# Task 02 — Evaluation Harness

Build the evaluation harness in `eval/`.

## Steps

1. **Write `eval/run.py`.** It takes `--dataset data/golden/quotes-v1 --extractor <name>` and runs the named extractor over every document.

2. **Compare the output to golden, field by field.**
   - Normalise both sides before comparing:
     - strip whitespace
     - case-fold text
     - parse numbers and dates
     - map currency symbols to ISO codes
   - Match line items by `part_number_quoted` first, then by position.

3. **Report these metrics per field:**
   - `accuracy`: exact match after normalisation.
   - `evidence_match_rate`: the extracted `verbatim_span` equals the golden `verbatim_span`, or overlaps it by at least 80% of characters.
   - `ungrounded_rate`
   - `missing_rate`

   Also report the overall document count and p50/p95 latency per document.

4. **Write the report in two formats:**
   - `eval/reports/<date>-<extractor>.md`: a markdown table with the worst fields first.
   - `eval/reports/<date>-<extractor>.json`

5. **Add `eval/test_harness.py`.** Use a synthetic two-document fixture to prove the metrics compute correctly. The fixture must include:
   - one deliberately wrong field
   - one deliberately ungrounded field

6. **Add a pytest marker.**
   - `uv run pytest -m eval` runs the harness on `quotes-v1`.
   - It fails if any field's accuracy drops more than 2 points below `eval/baseline.json`.
   - Create `baseline.json` from the first run.

## Done when

This command produces a report, and the test suite passes:

```bash
uv run eval/run.py --dataset data/golden/quotes-v1 --extractor current
```
