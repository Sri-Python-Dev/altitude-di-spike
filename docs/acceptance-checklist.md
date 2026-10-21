# Acceptance checklist (prompts 00–12)

Checked 15 Oct 2026 against the prompt texts in `~/Downloads/Inputs` (copies in `docs/tasks/`),
with `uv run pytest` on Python 3.12, Postgres and a local Temporal server.

| # | Task | Done-when | Status |
|---|---|---|---|
| 00 | Project rules | — | Met. CLAUDE.md in place; no LLM SDK outside `llm/` (lint test); no content in logs or workflow history. |
| 01 | Golden set and labelling tool | 20 files validate; `uv run tools/validate_golden.py data/golden/quotes-v1` exits 0 | **Met.** Exits 0. All 20 human-reviewed by Srini (review_tool), 0 corrections. |
| 02 | Harness | `uv run eval/run.py --dataset data/golden/quotes-v1 --extractor current` writes a report; suite passes | **Met.** Reports named `<extractor>-<timestamp>`; `pytest -m eval` guards a 2-point drop. |
| 03 | Grounding | Tests pass; worker runs end to end; report shows evidence match and ungrounded rate | **Met.** Evidence match 100% on every grounded field. |
| 04 | Flag rules | README lists every rule; table evaluates in tests; no inline flag conditions | **Met.** CURRENCY_MISMATCH, CURRENCY_UNRESOLVED, TOTAL_MISMATCH; one test per rule plus no-flag. Flags are evaluated in the workflow's persist activity (registered by `worker.py`). |
| 05 | Dedupe and edge cases | New tests pass; harness includes edge cases | **Met.** Includes the classify step (quote / po / invoice / other). |
| 06 | Docling vs Azure DI | sp-13.md with metrics and recommendation | **Partly.** Docling measured; Azure blocked on dev credentials (adapter, metrics and harness option built and tested on a fake result). |
| 07 | Router and baseline | Lint test; two reports; data-path.md | **Met.** |
| 08 | Spike write-up (file named `08-results-page.md`) | sp-02.md, two pages, a number on every claim | **Met.** Parser comparison marked as blocked. |
| 09 | Currency fix | No global alias; four tests; currency does not regress | **Met.** |
| 10 | Results page | Renders latest.json; run < 2 min; delta after a rule edit | **Met.** 18–24 s runs; +3.2 after the rule edit. |
| 11 | Field evidence view | Click highlights span; golden vs extracted; resend makes a new document | **Met.** |
| 12 | Demo run sheet | Run sheet; demo-reset and demo-check work; one rehearsal | **Met** for every system step (timed); narration not yet rehearsed by a person. |

Decisions taken where prompts conflicted or were silent are recorded in `rules/README.md`
(supplier currency in the decision table, PO-currency rule) and `docs/demo/run-sheet.md`
(opening statement wording).
