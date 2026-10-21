import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from temporalio.client import Client
from temporalio.worker import Worker
from temporal_app.workflow import EmailWorkflow, QuoteWorkflow, ReprocessWorkflow
from temporal_app.activities import ALL as ACTIVITIES

TASK_QUEUE = 'altitude-di-quotes'

async def main():
    client = await Client.connect(os.getenv('TEMPORAL_ADDRESS', 'localhost:7233'))
    # Activities are blocking (Docling, model calls), so they run in threads.
    with ThreadPoolExecutor(max_workers=4) as executor:
        worker = Worker(client, task_queue=TASK_QUEUE, workflows=[QuoteWorkflow, EmailWorkflow, ReprocessWorkflow],
                        activities=ACTIVITIES, activity_executor=executor)
        await worker.run()

if __name__ == '__main__':
    asyncio.run(main())
