# Demo targets. See docs/demo/run-sheet.md.
UV ?= uv
RUN = $(UV) run --env-file .env python

.PHONY: demo-reset demo-check demo-rule test

demo-reset:  ## empty the inbox, clear processed state and results, restore the demo rules
	$(RUN) -m tools.demo reset

demo-check:  ## check services, then run the harness once; fails unless it finishes in under two minutes
	$(RUN) -m tools.demo check

demo-rule:   ## fallback for step 3: add the Lone Star Machining row without the rule editor
	$(RUN) -m tools.demo add-rule

test:
	$(UV) run pytest
