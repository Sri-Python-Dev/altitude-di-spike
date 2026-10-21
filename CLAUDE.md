# Altitude Document Intelligence — Spike Rules

This repo is a time-boxed spike for Altitude's document intelligence. These rules apply to every task.
Task specs live in `docs/tasks/`.

## Evidence

- Every extracted field is an `Evidenced[T]` with these parts:
  - `value`
  - `page` (1-based; `0` means the quote is in an email body, and then `bbox` is `None`)
  - `bbox` (`x0, y0, x1, y1` in PDF points, top-left origin, set only by the Ground step)
  - `verbatim_span` (exact text from the parsed document)
  - `raw_confidence` (the model's self-report, stored but never trusted)
- If a field's `verbatim_span` cannot be found in the parsed text, store the field with `value=None` and `grounded=False`.
- **Never store an ungrounded value.**

## Model calls

- All model calls go through one function, `route_and_call(task, payload)`, in `llm/router.py`.
- No module imports an LLM SDK directly.
- The router logs the task name, model, token counts and latency. It never logs the prompt or completion text.

## Data handling

- Document bytes and parsed text never appear in logs, Temporal workflow inputs or outputs, or exception messages. Pass document IDs and file paths instead of content.
- Use only synthetic documents or documents in `data/golden/`, which are already cleared.
- Do not fetch anything from a real mailbox.

## Sequencing

- No UI work until two tasks are complete and their tests pass:
  - the harness (`eval/`)
  - grounding (`extract/ground.py`)

## Code hygiene

- Prefer small, tested modules over changes to `worker.py`.
- Leave `worker.py` working at every commit.
- Stack: Python 3.12, Pydantic v2, pytest.
- Run `uv run pytest` before claiming a task is done.
