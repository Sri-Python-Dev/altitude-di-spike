Move all flagging logic out of code into a gorules decision table.

1. List every condition currently producing a "needs a look" flag (search worker.py and the extraction code). Write them down in rules/README.md as rule name, inputs, threshold, message.
2. Create rules/quote_flags.json as a gorules JDM decision table. Inputs are plain fields (quote_currency, supplier_default_currency, line_total_sum, stated_total, currencies_seen_count, price_vs_last_quote_pct). Outputs are flag_code, severity (info|review|block), message.
3. Write rules/engine.py using the zen-engine Python package to evaluate the table and return the list of flags. No flag logic in Python outside this module.
4. Keep confidence out of the rules. raw_confidence is recorded on each field but is not an input to any rule and is not displayed as a percentage anywhere.
5. Tests: one case per rule, plus a case with no flags. Each test feeds the inputs and asserts the exact flag codes.
6. Add two rules we know we need: currency differs from supplier default; stated total differs from line item sum by more than 1%.

Done when: rules/README.md lists every rule, the table evaluates in tests, and worker.py calls rules.engine.evaluate() with no inline flag conditions left.
