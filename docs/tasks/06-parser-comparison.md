# Task 06 — Compare Parsers on the Golden Set (SP-13)

Compare parsers on the golden set.

## Steps

1. **Write `parse/azure_di.py`.** It calls Azure Document Intelligence in layout model mode.
   - Read the endpoint and key from environment variables.
   - Use the dev resource only.
   - Never send a document from outside `data/golden/` or `synthetic/`.
   - Produce the same `ParsedDocument` structure Docling produces: pages, text, layout elements with bboxes, and tables.

2. **Run both parsers over `quotes-v1` and `synthetic/scanned/`.** For each document, measure:
   - character error rate against golden spans
   - percentage of golden spans that can be located exactly
   - table cell count recovered
   - bbox IoU for located spans
   - parse latency

3. **Run the full extract + ground pipeline** on each parser's output, and compare the harness results side by side.

4. **Write `docs/spikes/sp-13.md`.** It contains:
   - a table of the metrics
   - three example documents where the parsers differ most, with screenshots of the problem
   - a recommendation for which parser to use on the controlled (local-only) route, and the gap to close

## Done when

`sp-13.md` exists with the metrics table and the recommendation.
