# Altitude Document Intelligence — supplier quote spike

**Status: prototype for the RFQ-workflow demo, not an approved spike result.**
Only synthetic documents and the cleared `data/golden/` set are used. No real mailbox is read.

Project rules (evidence, grounding, model calls, data handling) live in [CLAUDE.md](CLAUDE.md).
Task specs live in [docs/tasks/](docs/tasks/). Setting up on Windows (the VDI): [docs/vdi-setup.md](docs/vdi-setup.md). The demo run sheet is [docs/demo/run-sheet.md](docs/demo/run-sheet.md).

## Quick start

```bash
uv sync --python 3.12
uv run pytest                                   # 101 tests, ~20 s (Docling, Postgres test DB, a local Temporal server)
uv run tools/validate_golden.py data/golden/quotes-v1
```

Model calls need `OPENAI_API_KEY` in `.env`; `--extractor golden-echo` runs everything else without
one (its reports are labelled smoke test).

```bash
# Evaluation: writes eval/reports/<extractor>-<timestamp>.{md,json} and latest.json
uv run --env-file .env eval/run.py --dataset data/golden/quotes-v1 --dataset data/synthetic/edge-cases --dataset data/synthetic --extractor current
uv run --env-file .env pytest -m eval           # fails if any field drops >2 points below eval/baseline.json
uv run python -m eval.parsers --parser docling  # parser metrics against the golden spans (SP-13)

# Run it (four terminals; restart the worker and the app after any code change)
temporal server start-dev                                   # Temporal UI: http://localhost:8233
uv run --env-file .env python worker.py
uv run --env-file .env python email_monitor.py              # .eml files saved into data/inbox/ start workflows
uv run --env-file .env python -m ui.server                  # the app: http://127.0.0.1:8765

make demo-reset && make demo-check                          # before a demo: docs/demo/run-sheet.md
uv run python -m tools.make_synthetic                       # regenerate the synthetic documents and emails
uv run python review_tool/review.py data/golden/quotes-v1 --reviewer "Name"   # human review of golden answers (standalone)
```

## Pipeline

| Step | Module | What it does |
|---|---|---|
| Intake + dedupe | `email_monitor.py`, `ingest/email_intake.py`, `extract/dedupe.py` | `.eml` files saved into `data/inbox/` start a workflow. Duplicate email = Message-ID seen; duplicate attachment = SHA-256 seen, whatever its name. Resend (test documents) bypasses dedupe as a new document. |
| Parse | `parse/docling_parser.py` (`parse/azure_di.py` ready, not run) | Text, headings, table cells with row/column, bboxes in PDF points (top-left). Typed errors for password-protected and corrupt PDFs. No text layer → OCR, `ocr=True`. |
| Split | `extract/boundaries.py` | One segment per quote, across pages or on the same page. |
| Classify | `extract/classify.py` | quote / po / invoice / other from the title and references. Non-quotes are stored as `not_a_quote` and not extracted. |
| Extract | `extract/extractors.py`, `llm/router.py` | `current` (gpt-5.4-mini) / `frontier` (gpt-4.1); `golden-echo` for smoke tests. |
| **Ground** | `extract/ground.py`, `extract/normalise.py` | Span found on the page (exact → whitespace → leaked delimiters trimmed → separators ignored); value must be readable from it; bbox from the exact element, anchored to the line item's row; stored span is always the document's own text. Currency: ISO code, or a symbol resolved through the supplier's currency on file. Otherwise `value=None` with a reason. |
| Rules | `rules/quote_flags.json`, `rules/engine.py` | GoRules decision table (zen-engine): supplier currency on file; CURRENCY_MISMATCH, CURRENCY_UNRESOLVED, TOTAL_MISMATCH. Edited in the app (Rules). |
| Persist | `extract/persistence.py`, `storage/postgres.py` | Refuses any ungrounded value (in code and by a database constraint); stores fields, evidence and flags in Postgres. |
| Evaluate | `eval/run.py`, `eval/parsers.py` | Per-field accuracy, grounded / ungrounded / missing, evidence match, worst document, delta vs previous run, edge cases, latency, cost; parser metrics. |
| App | `ui/server.py` | Documents, Results, field evidence view, Accuracy, Rules (GoRules editor), Upload, Resend. |

Temporal workflows (`temporal_app/`) pass only IDs, paths, counts and fixed codes. The data path is
in [docs/spikes/data-path.md](docs/spikes/data-path.md); results in
[docs/spikes/sp-02.md](docs/spikes/sp-02.md) and [docs/spikes/sp-13.md](docs/spikes/sp-13.md).

## Still open

1. **Azure Document Intelligence (task 06).** Adapter, metrics and harness option are built and tested on a fake result; needs `AZURE_DI_ENDPOINT` / `AZURE_DI_KEY` (dev resource) to produce the Azure column of SP-13.
2. **Model route.** gpt-4.1-nano was switched off by OpenAI (27 Oct); the default is now gpt-5.4-mini, chosen on the harness (see sp-02). Evaluate a Foundry deployment the same way before the tenant move.
3. **Gmail.** Not connected; CLAUDE.md forbids reading a real mailbox until that is decided. The inbox folder stands in for it.
4. **Next-round flag rules** (two currencies in one table, price vs last quote): see [rules/README.md](rules/README.md).
5. **Golden set:** the 20 quotes in `quotes-v1` are human-reviewed (Srini, 15 Oct); the synthetic edge-case labels are generator-made. `stated_total` has no golden answers yet.
