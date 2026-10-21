Add field-level evidence to the document view at /documents/<id>.

1. Left pane: page image rendered from the PDF (pdf2image or PyMuPDF). Right pane: the extracted fields and line items.
2. For each field show: name, value (or "ungrounded" with the reason), state badge (grounded | ungrounded | missing), and any rule flags from the decision table with their message. Do not show raw_confidence; if it must be visible for debugging, put it behind a "debug" query parameter and label it "uncalibrated".
3. Clicking a field scrolls the left pane to the field's page and draws its bbox. The verbatim span is shown under the field with the exact characters, so a reviewer can compare span and value.
4. When the page is opened with ?field=<name> (from the results page's Worst doc link), that field is selected on load.
5. When a golden label exists for the document, show it next to the extracted value with the golden span, and highlight both bboxes in different colours. This is the comparison view used in the demo.
6. For ungrounded fields, show what the model claimed (ungrounded_span) in grey so the miss is visible.
7. Add a "resend" action for test documents that re-queues the email through the workflow and links to the new document, so a rule change can be demonstrated end to end.

Done when: clicking any field highlights its span on the page image, golden-vs-extracted comparison renders for golden documents, and resend produces a new document record processed by the workflow.
