Write docs/demo/run-sheet.md and make sure the repo supports every step.

1. Pre-demo: make demo-reset restores the synthetic mailbox and clears processed state; make demo-check runs the harness once and confirms it completes in under two minutes.
2. Step 1: open /results, start a run, narrate the architecture while it runs, table appears.
3. Step 2: click the worst field's Worst doc; show golden span vs extracted span on the page image for two documents.
4. Step 3: open rules/quote_flags.json in the gorules editor, add the supplier-scoped $ rule for one named USD supplier, save; resend the failing currency email; show the new document clears; re-run the harness; point at the currency row delta.
5. Step 4: show eval/baseline.json and the CI test that fails on a two-point drop.
6. Step 5: ten seconds on the Temporal UI showing the completed workflow.
7. Opening statement to read verbatim: synthetic documents only, personal Gmail and public OpenAI for the spike, tenant move (Graph, AI Foundry private endpoint, storage container) is next; nobody can use it until that is done and grounding is 100% on the golden set.
8. List the three things that could go wrong live (model latency spike, rule editor save failure, resend not picked up within the poll interval) and the recovery for each.

Done when: the run-sheet exists, make demo-reset and make demo-check work, and one full rehearsal has been done against the run-sheet with timings noted.
