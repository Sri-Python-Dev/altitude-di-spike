"""Start a workflow for a synthetic quote.

  uv run python start_workflow.py data/synthetic/quote-a.pdf          # one PDF
  uv run python start_workflow.py data/synthetic/emails/01-original.eml # an email (dedupe + intake)
  add --extractor golden-echo to run without a model API key (smoke test)
"""
import argparse, asyncio, hashlib, json, os, uuid
from pathlib import Path
from temporalio.client import Client
from temporal_app.workflow import EmailWorkflow, QuoteWorkflow
from worker import TASK_QUEUE

async def start(path: Path, extractor: str):
    client = await Client.connect(os.getenv('TEMPORAL_ADDRESS', 'localhost:7233'))
    path = path.resolve()
    if path.suffix == '.eml':
        return await client.execute_workflow(EmailWorkflow.run, args=[str(path), extractor],
            id=f'email-{uuid.uuid4()}', task_queue=TASK_QUEUE)
    # Same bytes, same document ID: the PDF's identity is its content, not its name.
    doc_id = path.stem if 'golden' in path.parts else hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return await client.execute_workflow(QuoteWorkflow.run, args=[doc_id, str(path), 'pdf', extractor],
        id=f'quote-{doc_id}-{uuid.uuid4().hex[:8]}', task_queue=TASK_QUEUE)

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('path', type=Path)
    ap.add_argument('--extractor', default='current'); args = ap.parse_args()
    print(json.dumps(asyncio.run(start(args.path, args.extractor)), indent=2))
