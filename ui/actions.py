"""Temporal calls made by the viewer: process an uploaded PDF, reprocess a stored quote, check status.
Only IDs and file paths go to Temporal."""
import asyncio
import os
import uuid

TEMPORAL_ADDRESS = os.getenv('TEMPORAL_ADDRESS', 'localhost:7233')
TEMPORAL_UI = os.getenv('TEMPORAL_UI', 'http://localhost:8233')

def temporal_link(workflow_id: str) -> str:
    return f'{TEMPORAL_UI}/namespaces/default/workflows/{workflow_id}'

async def _client():
    from temporalio.client import Client
    return await Client.connect(TEMPORAL_ADDRESS)

def start_quote(document_id: str, file_path: str, extractor: str) -> str:
    from temporal_app.workflow import QuoteWorkflow
    from worker import TASK_QUEUE
    workflow_id = f'upload-{document_id}-{uuid.uuid4().hex[:6]}'
    async def go():
        client = await _client()
        await client.start_workflow(QuoteWorkflow.run, args=[document_id, file_path, 'pdf', extractor],
                                    id=workflow_id, task_queue=TASK_QUEUE)
    asyncio.run(go())
    return workflow_id

def reprocess(segment_id: str, document_id: str, extractor: str | None, timeout: float = 60) -> tuple[str, dict]:
    """Run ReprocessWorkflow and wait for its result (grounding only, so it is quick)."""
    from datetime import timedelta
    from temporal_app.workflow import ReprocessWorkflow
    from worker import TASK_QUEUE
    workflow_id = f'reprocess-{segment_id}-{uuid.uuid4().hex[:6]}'
    async def go():
        client = await _client()
        return await client.execute_workflow(ReprocessWorkflow.run, args=[segment_id, document_id, extractor],
                                             id=workflow_id, task_queue=TASK_QUEUE,
                                             execution_timeout=timedelta(seconds=timeout))
    return workflow_id, asyncio.run(go())

def status(workflow_id: str) -> tuple[str, dict | None]:
    """('RUNNING' | 'COMPLETED' | 'FAILED' | …, result when completed)."""
    async def go():
        client = await _client()
        handle = client.get_workflow_handle(workflow_id)
        description = await handle.describe()
        name = description.status.name if description.status else 'UNKNOWN'
        return name, (await handle.result() if name == 'COMPLETED' else None)
    return asyncio.run(go())

def resend_email(eml_path: str, extractor: str) -> str:
    """Test documents only: run the saved email through EmailWorkflow again, bypassing dedupe (new document IDs)."""
    from temporal_app.workflow import EmailWorkflow
    from worker import TASK_QUEUE
    workflow_id = f'resend-{uuid.uuid4().hex[:10]}'
    async def go():
        client = await _client()
        await client.start_workflow(EmailWorkflow.run, args=[eml_path, extractor, True], id=workflow_id, task_queue=TASK_QUEUE)
    asyncio.run(go())
    return workflow_id
