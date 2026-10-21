"""Only authorized gateway for LLM calls. Logs metadata only."""
import logging, os, time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import yaml
from pydantic import BaseModel

logger=logging.getLogger('altitude.llm.router')
CONFIG=Path(__file__).parent/'config.yaml'

# Metadata of each call (task, model, tokens, latency) for whoever is tracking; never content.
_calls: ContextVar[list | None]=ContextVar('llm_calls',default=None)

@contextmanager
def track_usage():
    """Collect call metadata made inside the block: `with track_usage() as calls: ...`."""
    calls=[];token=_calls.set(calls)
    try: yield calls
    finally: _calls.reset(token)

def model_prices(task: str) -> tuple[str, float | None, float | None]:
    """(model, USD per 1M input tokens, USD per 1M output tokens) from config.yaml."""
    config=yaml.safe_load(CONFIG.read_text())['tasks'][task]
    return config['model'],config.get('usd_per_1m_input'),config.get('usd_per_1m_output')

def route_and_call(task: str, payload: dict, schema: type[BaseModel]) -> BaseModel:
    from openai import OpenAI
    config=yaml.safe_load(CONFIG.read_text())['tasks'][task]
    backend=config['backend'];model=config['model']
    if backend=='azure_foundry':
        client=OpenAI(api_key=os.environ['AZURE_FOUNDRY_API_KEY'],base_url=os.environ['AZURE_FOUNDRY_BASE_URL'])
    elif backend=='openai': client=OpenAI(api_key=os.environ['OPENAI_API_KEY'])
    else: raise ValueError('Unsupported backend')
    started=time.perf_counter()
    try:
        response=client.responses.parse(model=model,input=[{'role':'system','content':payload['instruction']},{'role':'user','content':payload['content']}],text_format=schema)
        return response.output_parsed
    finally:
        elapsed=time.perf_counter()-started
        # Avoid prompt, completion, exceptions and content-bearing payloads in logs.
        usage=locals().get('response',None)
        usage=getattr(usage,'usage',None)
        record={'task':task,'model':model,'input_tokens':getattr(usage,'input_tokens',None),
                'output_tokens':getattr(usage,'output_tokens',None),'latency_ms':round(elapsed*1000)}
        logger.info('llm_call task=%s model=%s input_tokens=%s output_tokens=%s latency_ms=%d',*record.values())
        if (calls:=_calls.get()) is not None: calls.append(record)
