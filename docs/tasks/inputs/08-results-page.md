Write docs/spikes/sp-02.md as the result of this spike. Structure:

1. Question the spike answered and the time box used.
2. Method: parsers, models, golden set size, who labelled, harness version.
3. Results: the harness table per field for each parser/model combination, worst fields first, with ungrounded_rate and evidence_match_rate. Include p50/p95 latency and cost per 100 documents for each model.
4. Edge cases: what happened on multi-quote PDFs, body-only quotes, scanned, password-protected.
5. What surprised us: three things, with the number that surprised us.
6. Recommendation: which parser, which model route, which fields need schema or prompt work before the accuracy bar is met, and what to delete from this repo.
7. Open questions only a tenant environment can answer.

Keep it to two pages. No screenshots of the UI. Every claim has a number next to it.
