Introduce the router and get a fair baseline.

1. Create llm/router.py with route_and_call(task: str, payload: dict, schema: type[BaseModel]) -> BaseModel. Task-to-model mapping lives in llm/config.yaml. Supported backends: openai (current), azure_foundry (same OpenAI-compatible API, endpoint from environment). Log task, model, tokens, latency; never log content.
2. Replace every direct OpenAI call with route_and_call. Add a lint check (a simple test that greps for "import openai" outside llm/) that fails if any other module imports an LLM SDK.
3. Add a second configuration running extraction on a frontier-class model (gpt-4.1 or the strongest model available on the dev endpoint) alongside gpt-4.1-nano.
4. Run the harness with both and produce two reports.
5. Write docs/spikes/data-path.md: where emails come from (personal Gmail, test only), where attachments are stored, what is sent to which model, what is logged, and the migration plan to Graph, Azure AI Foundry private endpoint and a storage container. State explicitly that no real supplier document has been processed outside data/golden/.

Done when: the lint test passes, both harness reports exist, and data-path.md is written.
