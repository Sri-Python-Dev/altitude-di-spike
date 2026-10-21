from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from temporal_app.activities import (classify_activity, extract_activity, ground_activity, intake_email_activity,
                                         mark_not_quote_activity, parse_activity, persist_activity)

TIMEOUT = timedelta(minutes=5)
MODEL_RETRY = RetryPolicy(maximum_attempts=3)

# Workflow history holds IDs, paths, counts and fixed codes only. Activities load content locally.
@workflow.defn
class QuoteWorkflow:
    @workflow.run
    async def run(self, document_id: str, file_path: str, kind: str = 'pdf', extractor: str = 'current',
                  source_email: str | None = None) -> dict:
        try:
            parsed = await workflow.execute_activity(parse_activity, args=[document_id, file_path, kind, source_email],
                                                     start_to_close_timeout=TIMEOUT)
        except ActivityError as e:
            if isinstance(e.cause, ApplicationError) and e.cause.non_retryable:
                return {'document_id': document_id, 'status': 'unparseable', 'reason': e.cause.type}
            raise
        # Classify each quote segment; this sprint extracts quotes only.
        kept, not_quotes = [], []
        for segment_id, pages in zip(parsed['segments'], parsed['page_ranges']):
            kind_of = await workflow.execute_activity(classify_activity, segment_id, start_to_close_timeout=TIMEOUT)
            (kept if kind_of['doc_type'] == 'quote' else not_quotes).append((segment_id, pages, kind_of['doc_type']))
        if not kept:
            doc_type = not_quotes[0][2] if not_quotes else 'other'
            return await workflow.execute_activity(mark_not_quote_activity, args=[document_id, kind, doc_type],
                                                   start_to_close_timeout=TIMEOUT)
        segments = []
        for segment_id, pages, _ in kept:
            await workflow.execute_activity(extract_activity, args=[segment_id, extractor],
                                            start_to_close_timeout=TIMEOUT, retry_policy=MODEL_RETRY)
            grounded = await workflow.execute_activity(ground_activity, segment_id, start_to_close_timeout=TIMEOUT)
            saved = await workflow.execute_activity(persist_activity, args=[segment_id, document_id, extractor],
                                                    start_to_close_timeout=TIMEOUT)
            segments.append({'segment_id': segment_id, 'pages': pages, 'grounded': grounded['grounded'],
                             'ungrounded': grounded['ungrounded'], 'flags': saved.get('flags', []), 'status': saved['status']})
        return {'document_id': document_id, 'status': 'saved', 'ocr': parsed['ocr'], 'segments': segments,
                'not_quotes': [{'segment_id': s, 'doc_type': t} for s, _, t in not_quotes]}

@workflow.defn
class ReprocessWorkflow:
    """Re-run the Ground step and save on a stored extraction, e.g. after a currency rule changed.
    No new model call, so the only difference is the rules."""
    @workflow.run
    async def run(self, segment_id: str, document_id: str, extractor: str | None = None) -> dict:
        grounded = await workflow.execute_activity(ground_activity, segment_id, start_to_close_timeout=TIMEOUT)
        saved = await workflow.execute_activity(persist_activity, args=[segment_id, document_id, extractor],
                                                start_to_close_timeout=TIMEOUT)
        return {'segment_id': segment_id, 'grounded': grounded['grounded'], 'ungrounded': grounded['ungrounded'],
                'flags': saved.get('flags', []), 'status': saved['status']}

@workflow.defn
class EmailWorkflow:
    @workflow.run
    async def run(self, eml_path: str, extractor: str = 'current', resend: bool = False) -> dict:
        intake = await workflow.execute_activity(intake_email_activity, args=[eml_path, resend], start_to_close_timeout=TIMEOUT)
        documents = []
        for item in intake['items']:
            documents.append(await workflow.execute_child_workflow(
                QuoteWorkflow.run, args=[item['document_id'], item['file_path'], item['kind'], extractor, item.get('source_email')],
                id=f"{workflow.info().workflow_id}-{item['document_id']}"))
        return {'email_key': intake['email_key'], 'new_email': intake['new_email'],
                'skipped': intake['skipped'], 'documents': documents}
