This repo is a time-boxed spike for Altitude's document intelligence. Rules that apply to every task:

- Every extracted field is an Evidenced[T]: value, page (1-based), bbox (x0,y0,x1,y1 in PDF points), verbatim_span (exact text from the parsed document), raw_confidence (model self-report, stored but never trusted). A field whose verbatim_span cannot be located in the parsed text is stored with value=None and grounded=False. Never store an ungrounded value.
- All model calls go through one function, route_and_call(task, payload), in llm/router.py. No module imports an LLM SDK directly. The router logs task name, model, token counts and latency, never the prompt or completion text.
- Document bytes and parsed text never appear in logs, Temporal workflow inputs/outputs or exception messages. Pass document IDs and file paths, not content.
- Only synthetic documents or documents in data/golden/ (already cleared) are used. Do not fetch anything from a real mailbox.
- No UI work until the harness (eval/) and grounding (extract/ground.py) tasks are complete and their tests pass.
- Prefer small, tested modules over changes to worker.py. Leave worker.py working at every commit.
- Python 3.12, Pydantic v2, pytest. Run `uv run pytest` before claiming a task is done.
