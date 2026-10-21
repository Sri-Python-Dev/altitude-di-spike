Add a results page to the FastAPI workbench that renders the latest harness report.

1. Route GET /results renders eval/reports/latest.json (the harness writes latest.json alongside the timestamped report). Route GET /results?run=<id> renders an older one. Route POST /results/run starts a harness run in the background and the page polls until it finishes, then re-renders.
2. Header line, always visible: dataset name and document count, labellers, git commit, model, parser, run timestamp, and the exact command that produced the run.
3. Per-field table sorted by accuracy ascending. Columns: Field (plain-English name with the code name in small text), Accuracy, Grounded, Ungrounded, Missing, Delta vs previous run (signed, with colour only on the sign), Worst doc (link to /documents/<id>?field=<name>). Colour bands on Accuracy only: below 80 red, 80 to 95 amber, above 95 green. No overall average anywhere on the page. No confidence column.
4. Edge-case block below the table: one row each for multi-quote PDF, body-only quote, scanned PDF, password-protected PDF, duplicate resend. Columns: case, what happened, pass/fail.
5. A toggle that swaps the table between runs on different extractor configs (nano vs frontier, Docling vs Azure DI) with identical row order so differences are scannable. The compared runs are selected by run id.
6. The harness JSON must include everything the page needs; do not compute metrics in the view. Add previous_run_id and per-field delta to the harness output.
7. Make the full run on quotes-v1 finish in under two minutes: parallelise document processing with a bounded worker pool, cache parsed documents by sha256 so re-runs skip parsing, and run extraction calls concurrently.

Done when: /results renders from latest.json, a run started from the page finishes in under two minutes on quotes-v1 and the page updates, and the delta column shows a change after a rule edit.
