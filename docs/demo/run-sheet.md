# Demo run sheet — Altitude Document Intelligence

Synthetic documents only. About 12 minutes including narration. Rehearsed 2026-10-15 against this
sheet on an isolated copy of the stack (timings below are from that rehearsal; narration not timed).

## Before the demo (15 minutes ahead)

```bash
temporal server start-dev                                 # terminal 1 — Temporal and its UI (http://localhost:8233)
make demo-reset                                           # empty inbox, clear state and results DB, restore demo rules
uv run --env-file .env python worker.py                   # terminal 2 — start after the reset
uv run --env-file .env python email_monitor.py            # terminal 3 — watches data/inbox
uv run --env-file .env python -m ui.server                # terminal 4 — http://127.0.0.1:8765
make demo-check                                           # services up, harness < 2 min, edge cases pass, demo armed
cp data/synthetic/emails/08-dollar-prices.eml data/inbox/ # the failing currency email arrives (stored in ~11 s)
```

`make demo-check` must end with every line `ok`, including **Harness under 120s** (rehearsal: 17.4 s)
and **Currency row shows the demo miss**. Open three browser tabs: `/results`, `/rules`, and the
Temporal UI. Have `eval/baseline.json` and `eval/test_harness.py` open in the editor.

| Rehearsal timing | |
|---|---|
| `make demo-reset` | < 1 s |
| `make demo-check` (harness, 31 documents) | 18 s |
| Email dropped into the inbox → stored with its flag | 11.4 s |

## Opening statement (read verbatim)

> Everything you will see runs on synthetic documents only. For this spike the documents arrive
> through a local test inbox that stands in for a personal test Gmail account, and extraction uses
> the public OpenAI API. The next step is the move into our tenant: Microsoft Graph for mail, an
> Azure AI Foundry private endpoint for the model, and a storage container for documents. Nobody
> can use this until that move is done and grounding is 100% on the golden set.

*Prompt 12 asked for "personal Gmail" here. No Gmail account is connected (CLAUDE.md forbids
reading a real mailbox until that is decided), so the sentence says what actually runs. If the
Gmail connection is made before the demo, change it back.*

## Step 1 — Results (≈ 3 min)

1. Open **Results**. Choose *gpt-4.1-nano*, **Start run**.
2. While it runs (rehearsal: **18.3 s** to the updated page), narrate the architecture: email →
   Temporal workflow → Docling parse (cached by SHA-256) → extraction → **grounding** (every value
   must be found on the page, with its location) → GoRules decision table → Postgres. The harness
   runs the same pipeline over 31 documents with known answers.
3. The table appears, worst field first. Point out: the header line (dataset, labellers, model,
   parser, exact command); no overall average and no confidence column — accuracy is measured
   against known answers only; colour bands red < 80, amber 80–95, green > 95.
4. **Currency** is the worst field (96.8%) with worst doc `quote-g-dollar`. Scroll to the edge-case
   block: multi-quote, body-only, scanned, password-protected, duplicate resend — all pass.

## Step 2 — Golden vs extracted (≈ 2 min)

1. Click **quote-g-dollar** in Worst doc. The currency field is selected: *ungrounded ·
   ambiguous_symbol*. The page shows only the **blue dashed** box (the known answer, "Prices quoted
   in $"); there is no orange box because the system refused to guess. Under the field:
   what the model claimed, struck through.
2. Second document: go back, open any `quote_00x` row (or `/documents/quote_004?run=<run>&field=line_items[0].moq`).
   Click a few fields: the **orange** extracted box and the **blue dashed** known-answer box land on
   the same cell; the exact source characters are shown under each value.

## Step 3 — Change a rule, resend, re-run (≈ 4 min)

1. **Documents** → the row whose source file is `LSM-24-0917.pdf` (the email from Lone Star
   Machining you dropped in before the demo) → its quote. Currency is ungrounded and the flag
   **CURRENCY_UNRESOLVED** is shown on the field.
2. **Rules** → node **Supplier currency on file** → **Edit Table**. Add a row **above the last (empty)
   row**: Supplier `"lone star machining"`, Currency on file `"USD"` (with the quotes — they are
   GoRules expressions). Close the table, **Save** (status shows *Saved hh:mm*; the file is checked by
   zen-engine before it is written).
3. Back on the document → **Resend**. The same email goes through the workflow again as a new
   document (rehearsal: **4.2 s**). Open it: currency **USD**, grounded, no flags, 100% of 19 fields.
4. **Results** → **Start run** again (rehearsal: **18.3 s**). The currency row shows **+3.2** in
   Δ vs previous run, and 100%.

## Step 4 — Regression guard (≈ 1 min)

Show `eval/baseline.json` (per-field accuracy of the reference run) and
`eval/test_harness.py::test_no_field_regresses_against_baseline`, run with `uv run pytest -m eval`:
it fails if any field drops more than two points below the baseline. (There is no CI server in the
spike; this is the test CI would run.)

## Step 5 — Temporal (10 seconds)

Temporal UI → the `resend-…` EmailWorkflow and its child QuoteWorkflow, completed, each activity
visible: intake, parse, extract, ground, persist. Inputs are IDs and paths only — no document text.

## If something goes wrong live

| What | Recovery |
|---|---|
| **Model latency spike** — the harness run in step 1 or 3 is still running after a minute | Keep talking; the page refreshes itself. If it passes two minutes, open **Results** with the run picker on the `make demo-check` run (it is identical in shape) and continue; run the new one again at the end. A resend that waits on the model shows *Processing…* and retries on its own — show the Temporal UI meanwhile. |
| **Rule editor will not save** — the status says *Not saved: …* or nothing happens | Run `make demo-rule` in a terminal: it adds the same Lone Star row, validates it with zen-engine and writes the file. Then continue with **Resend**. `make demo-reset` undoes it afterwards. |
| **Resend not picked up within the poll interval** — the status page stays on *Processing…* | Check terminal 2: the worker must be running (restart it with the same command; Temporal resumes the waiting workflow). If the worker is fine, use **Re-check with current rules** on the original document instead — it re-runs grounding and the decision table on the stored extraction, with no model call, and clears the same flag. |

## After the demo

`make demo-reset` — restores the rules (removes the Lone Star row), empties the inbox and clears
processed state and the results database. Harness reports are kept for the next delta.
