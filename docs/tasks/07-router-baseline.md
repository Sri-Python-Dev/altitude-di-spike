# Task 07 — LLM Router and a Fair Baseline

Introduce the router and get a fair baseline.

## Steps

1. **Create `llm/router.py`** with `route_and_call(task: str, payload: dict, schema: type[BaseModel]) -> BaseModel`.
   - The task-to-model mapping lives in `llm/config.yaml`.
   - Supported backends:
     - `openai` (current)
     - `azure_foundry` (the same OpenAI-compatible API, with the endpoint read from the environment)
   - Log the task, model, tokens and latency. Never log content.

2. **Replace every direct OpenAI call with `route_and_call`.**
   - Add a lint check: a simple test that greps for `import openai` outside `llm/`.
   - It fails if any other module imports an LLM SDK.

3. **Add a second extraction configuration** that runs on a frontier-class model (`gpt-4.1`, or the strongest model available on the dev endpoint) alongside `gpt-4.1-nano`.

4. **Run the harness with both configurations** and produce two reports.

5. **Write `docs/spikes/data-path.md`.** It covers:
   - where emails come from (personal Gmail, test only)
   - where attachments are stored
   - what is sent to which model
   - what is logged
   - the migration plan to Microsoft Graph, an Azure AI Foundry private endpoint and a storage container

   It must state explicitly that no real supplier document has been processed outside `data/golden/`.

## Done when

- The lint test passes.
- Both harness reports exist.
- `data-path.md` is written.
