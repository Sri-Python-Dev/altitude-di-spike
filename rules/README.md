# Quote rules

All flag logic lives in the GoRules decision graph `quote_flags.json`, evaluated by
`rules/engine.py` with zen-engine. Edit it in the app (**Rules**, the GoRules editor) or in any
JDM editor; there is no flag logic in Python. `raw_confidence` is never an input.

## Graph

`Quote` (input) → **Supplier currency on file** → **Quote flags** → `Result`

### Supplier currency on file

| Input | Output |
|---|---|
| `supplier`: supplier name, lower case, without punctuation or a trailing Inc/LLC/Ltd/GmbH | `supplier_default_currency` (ISO code), or null when the supplier is not listed |

For the spike this table is the supplier master data (it replaces the `data/suppliers.json`
that prompt 09 describes, so there is one place to change it). **Later it comes from
`erp_staging`.** Lone Star Machining is deliberately not listed: the demo adds it live.

The same table drives currency normalisation (`extract/normalise.py`): a bare `$` resolves
only through the supplier's currency on file (USD supplier → USD, CAD supplier → CAD).
There is no global `$` → USD alias anywhere. Unknown supplier + `$` → the currency field is
ungrounded with reason `ambiguous_symbol`.

### Quote flags (hit policy: collect)

| Code | Inputs | Condition | Severity | Message |
|---|---|---|---|---|
| CURRENCY_MISMATCH | quote_currency, supplier_default_currency | both known and different | review | Quote currency differs from the supplier's currency on file |
| CURRENCY_MISMATCH | quote_currency, po_currency | both known and different | review | Quote currency differs from the PO currency |
| CURRENCY_UNRESOLVED | quote_currency | null (not resolved from the document) | review | Quote currency could not be resolved from the document |
| TOTAL_MISMATCH | stated_total, line_total_sum | both known and abs(stated_total − line_total_sum) > 1% of stated_total | review | Stated total differs from the sum of the line items by more than 1% |

`line_total_sum` is the sum of the line items' extended prices, passed only when every one of them
is grounded (a partial sum would raise a false alarm). It is prepared in `rules/engine.py`; the
comparison and the 1% threshold live in the table. The table also takes `currencies_seen_count`
and `price_vs_last_quote_pct` as inputs, ready for the next-round rules below.

`po_currency` comes from the RFQ/PO when known; the spike has no PO data, so it is null in the
pipeline and set only in tests. That second CURRENCY_MISMATCH row was added because prompt 09's
test case ("CAD supplier … flags mismatch against a USD PO") needs a PO comparison.

## Next round (not in the table yet)

The director's other rules, to be added as rows with their inputs and thresholds:

| Rule | Inputs | Threshold | Open question |
|---|---|---|---|
| Two currency symbols in one table | currencies_seen_in_table (per table) | > 1 | Needs per-table currency detection |
| Price more than 30% off the last quote | price_vs_last_quote_pct | abs(value) > 30 | Needs a last-quote store; "off" means either direction |
