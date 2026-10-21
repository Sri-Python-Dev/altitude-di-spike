# Task 08 — Spike Write-up (SP-02)

Write `docs/spikes/sp-02.md` as the result of this spike.

## Structure

1. **Question and time box.** The question the spike answered, and the time box used.

2. **Method:**
   - parsers
   - models
   - golden set size
   - who labelled
   - harness version

3. **Results.**
   - The harness table per field, for each parser/model combination, worst fields first.
   - Include `ungrounded_rate` and `evidence_match_rate`.
   - For each model, include p50/p95 latency and cost per 100 documents.

4. **Edge cases.** What happened on:
   - multi-quote PDFs
   - body-only quotes
   - scanned PDFs
   - password-protected PDFs

5. **What surprised us.** Three things, each with the number that surprised us.

6. **Recommendation:**
   - which parser
   - which model route
   - which fields need schema or prompt work before the accuracy bar is met
   - what to delete from this repo

7. **Open questions** that only a tenant environment can answer.

## Constraints

- Keep it to two pages.
- No screenshots of the UI.
- Every claim has a number next to it.
