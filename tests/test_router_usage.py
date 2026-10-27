import sys
from types import SimpleNamespace
import llm.router as router
from pydantic import BaseModel

class Out(BaseModel):
    x: int

class FakeClient:
    def __init__(self, **_): self.responses = self
    def parse(self, **_):
        return SimpleNamespace(output_parsed=Out(x=1), usage=SimpleNamespace(input_tokens=700, output_tokens=300))

def test_track_usage_records_metadata_only(monkeypatch):
    # A stand-in SDK module: tests must not import the real one (see test_import_policy).
    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=FakeClient))
    monkeypatch.setenv('OPENAI_API_KEY', 'test')
    with router.track_usage() as calls:
        router.route_and_call('extract_quote', {'instruction': 'SECRET PROMPT', 'content': 'SECRET DOC'}, Out)
    assert calls == [{'task': 'extract_quote', 'model': router.model_prices('extract_quote')[0], 'input_tokens': 700, 'output_tokens': 300,
                      'latency_ms': calls[0]['latency_ms']}]
    assert 'SECRET' not in str(calls)

def test_calls_outside_tracking_are_not_collected(monkeypatch):
    # A stand-in SDK module: tests must not import the real one (see test_import_policy).
    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=FakeClient))
    monkeypatch.setenv('OPENAI_API_KEY', 'test')
    router.route_and_call('extract_quote', {'instruction': 'i', 'content': 'c'}, Out)  # must not raise

def test_cost_per_100_docs():
    from eval.run import usage_summary
    calls = [{'model': 'gpt-4.1', 'input_tokens': 1_000_000, 'output_tokens': 100_000}] * 2
    u = usage_summary(calls, documents=2, extractor='frontier')
    assert u['usd_per_100_docs'] == round((1_000_000 * 2.00 + 100_000 * 8.00) / 1e6 * 100, 4)
