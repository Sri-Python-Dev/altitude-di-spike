Compare parsers on the golden set.

1. Write parse/azure_di.py calling Azure Document Intelligence layout model (endpoint and key from environment; use the dev resource only; never send a document outside data/golden/ or synthetic/). Produce the same ParsedDocument structure Docling produces: pages, text, layout elements with bboxes, tables.
2. Run both parsers over quotes-v1 and synthetic/scanned/. For each document measure: character error rate against golden spans, percentage of golden spans locatable exactly, table cell count recovered, bbox IoU for located spans, parse latency.
3. Run the full extract+ground pipeline on each parser's output and compare harness results side by side.
4. Write docs/spikes/sp-13.md: a table of the metrics, three example documents where the parsers differ most with screenshots of the problem, and a recommendation for which parser to use on the controlled (local-only) route and the gap to close.

Done when: sp-13.md exists with the metrics table and recommendation.
