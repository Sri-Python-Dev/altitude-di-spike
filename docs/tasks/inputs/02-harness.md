Build the evaluation harness in eval/.

1. eval/run.py takes --dataset data/golden/quotes-v1 --extractor <name> and runs the named extractor over every document.
2. Compare output to golden field by field. Normalise before comparing: strip whitespace, case-fold text, parse numbers and dates, map currency symbols to ISO codes. Line items match by part_number_quoted then by position.
3. Report per field: accuracy (exact match after normalisation), evidence_match_rate (extracted verbatim_span equals golden verbatim_span, or overlaps it by at least 80% of characters), ungrounded_rate, and missing_rate. Report overall document count and p50/p95 latency per document.
4. Write the report as eval/reports/<extractor>-<timestamp>.md (a markdown table, worst fields first) and .json.
5. Add eval/test_harness.py with a synthetic two-document fixture proving the metrics compute correctly, including one deliberately wrong field and one deliberately ungrounded field.
6. Add a pytest marker so `uv run pytest -m eval` runs the harness on quotes-v1 and fails if any field's accuracy drops more than 2 points below eval/baseline.json (create baseline.json from the first run).

Done when: `uv run eval/run.py --dataset data/golden/quotes-v1 --extractor current` produces a report and the test suite passes.
