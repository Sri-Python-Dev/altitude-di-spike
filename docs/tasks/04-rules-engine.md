# Task 04 — Move Flagging Logic into a GoRules Decision Table

Move all flagging logic out of code and into a GoRules decision table.

## Steps

1. **List every condition that currently produces a "needs a look" flag.**
   - Search `worker.py` and the extraction code.
   - Write each one down in `rules/README.md` with its rule name, inputs, threshold and message.

2. **Create `rules/quote_flags.json` as a GoRules JDM decision table.**
   - Inputs are plain fields:
     - `quote_currency`
     - `supplier_default_currency`
     - `line_total_sum`
     - `stated_total`
     - `currencies_seen_count`
     - `price_vs_last_quote_pct`
   - Outputs:
     - `flag_code`
     - `severity` (`info | review | block`)
     - `message`

3. **Write `rules/engine.py`.**
   - Use the `zen-engine` Python package to evaluate the table and return the list of flags.
   - No flag logic lives in Python outside this module.

4. **Keep confidence out of the rules.**
   - `raw_confidence` is recorded on each field.
   - It is not an input to any rule.
   - It is never displayed as a percentage anywhere.

5. **Tests:**
   - One case per rule, plus one case with no flags.
   - Each test feeds the inputs and asserts the exact flag codes.

6. **Add two rules we know we need:**
   - The currency differs from the supplier's default.
   - The stated total differs from the line-item sum by more than 1%.

## Done when

- `rules/README.md` lists every rule.
- The table evaluates in tests.
- `worker.py` calls `rules.engine.evaluate()` and has no inline flag conditions left.
