"""Quote pipeline activities. Inputs and outputs are IDs, paths, counts and fixed
codes only; parsed text and extractions live in local state files.

Results go to Postgres when DATABASE_URL is set (storage/postgres.py), and to local
JSON files either way.
"""
import json
import os
from pathlib import Path
from temporalio import activity
from temporalio.exceptions import ApplicationError

STATE_DIR = Path(os.getenv('ALTITUDE_STATE_DIR', Path(__file__).resolve().parents[1] / 'data/state'))

def _path(kind: str, key: str, suffix: str = '.json') -> Path:
    path = STATE_DIR / kind / f'{key}{suffix}'
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

def _set_status(document_id: str, **status):
    _path('status', document_id).write_text(json.dumps({'document_id': document_id, **status}))

def _db(write):
    """Run write(conn) against Postgres if DATABASE_URL is set. Errors become content-free Temporal errors."""
    from storage.postgres import connect, database_url
    if not database_url():
        return
    import psycopg
    try:
        with connect() as conn:
            write(conn)
    except psycopg.Error as e:
        # psycopg messages can quote row values (document content), so only the error class is kept.
        # Retryable: the database may be down briefly.
        raise ApplicationError('Postgres write failed', type=type(e).__name__) from None

@activity.defn
def intake_email_activity(eml_path: str, resend: bool = False) -> dict:
    from ingest.email_intake import intake_email
    return intake_email(eml_path, STATE_DIR, resend)

@activity.defn
def parse_activity(document_id: str, file_path: str, kind: str = 'pdf', source_email: str | None = None) -> dict:
    """Activity 1-2: parse, detect OCR, split into one segment per quote."""
    from extract.boundaries import page_range, split_quotes
    from extract.errors import UnparseableDocument
    try:
        if kind == 'body':
            from extract.body import parse_email_body
            parsed = parse_email_body(document_id, Path(file_path).read_text(), html=file_path.endswith('.html'))
        else:
            from parse.docling_parser import parse_pdf
            parsed = parse_pdf(file_path, document_id)
    except UnparseableDocument as e:
        _set_status(document_id, status='unparseable', reason=e.reason, file_path=file_path)
        from storage.postgres import record_document
        _db(lambda conn: record_document(conn, document_id, kind=kind, status='unparseable', reason=e.reason,
                                         file_path=file_path, source_email=source_email))
        # Fixed message and type only; never document content. Retrying cannot help.
        raise ApplicationError(str(e), type=type(e).__name__, non_retryable=True) from None
    segments = split_quotes(parsed)
    for i, segment in enumerate(segments, 1):
        segment_id = document_id if len(segments) == 1 else f'{document_id}-quote-{i}'
        _path('parsed', segment_id).write_text(segment.model_copy(update={'document_id': segment_id}).model_dump_json())
    ids = [document_id] if len(segments) == 1 else [f'{document_id}-quote-{i}' for i in range(1, len(segments) + 1)]
    _set_status(document_id, status='parsed', ocr=parsed.ocr, segments=ids, file_path=file_path)
    from storage.postgres import record_document
    _db(lambda conn: record_document(conn, document_id, kind=kind, status='parsed', file_path=file_path,
                                     source_sha256=parsed.source_sha256, ocr=parsed.ocr, quote_count=len(ids),
                                     source_email=source_email))
    return {'status': 'parsed', 'ocr': parsed.ocr, 'segments': ids, 'page_ranges': [list(page_range(s)) for s in segments]}

@activity.defn
def classify_activity(segment_id: str) -> dict:
    """Activity 3: quote | po | invoice | other. Only quotes go on to extraction this sprint."""
    from extract.classify import classify
    return {'segment_id': segment_id, **classify(_parsed(segment_id))}

@activity.defn
def mark_not_quote_activity(document_id: str, kind: str, doc_type: str) -> dict:
    """Record a document the classify step rejected, with its type as the reason."""
    from storage.postgres import record_document
    _set_status(document_id, status='not_a_quote', reason=doc_type)
    _db(lambda conn: record_document(conn, document_id, kind=kind, status='not_a_quote', reason=doc_type))
    return {'document_id': document_id, 'status': 'not_a_quote', 'reason': doc_type}

def _parsed(segment_id: str):
    from parse.models import ParsedDocument
    return ParsedDocument.model_validate_json(_path('parsed', segment_id).read_text())

@activity.defn
def extract_activity(segment_id: str, extractor: str = 'current') -> dict:
    """Activity 5: model extraction. Output is untrusted until grounded."""
    from extract.pipeline import extract_raw
    quote = extract_raw(_parsed(segment_id), extractor)
    _path('extractions', segment_id, '.raw.json').write_text(quote.model_dump_json())
    return {'segment_id': segment_id, 'status': 'extracted'}

@activity.defn
def ground_activity(segment_id: str) -> dict:
    """Activity 6: the Ground step."""
    from extract.ground import ground
    from extract.schemas import SupplierQuote
    raw_file = _path('extractions', segment_id, '.raw.json')
    if not raw_file.exists() or not _path('parsed', segment_id).exists():
        # Reprocessing needs the stored parse and extraction; retrying cannot create them.
        raise ApplicationError('No stored extraction for this quote', type='NoStoredExtraction', non_retryable=True)
    raw = SupplierQuote.model_validate_json(raw_file.read_text())
    grounded = ground(raw, _parsed(segment_id))
    _path('extractions', segment_id, '.grounded.json').write_text(grounded.model_dump_json())
    fields = list(grounded.fields())
    return {'segment_id': segment_id, 'grounded': sum(f.grounded for _, f in fields),
            # Field names and reason codes only, never values or spans.
            'ungrounded': {n: f.ungrounded_reason for n, f in fields if f.ungrounded_reason}}

@activity.defn
def persist_activity(segment_id: str, document_id: str | None = None, extractor: str | None = None) -> dict:
    """Activity 7: refuses any field with grounded=False and a non-null value."""
    from extract.persistence import UngroundedValueError, verified_payload
    from extract.schemas import SupplierQuote
    quote = SupplierQuote.model_validate_json(_path('extractions', segment_id, '.grounded.json').read_text())
    try:
        payload = verified_payload(quote)
    except UngroundedValueError as e:
        raise ApplicationError(str(e), type='UngroundedValueError', non_retryable=True) from None
    _path('results', segment_id).write_text(json.dumps(payload))
    from extract.boundaries import page_range
    from extract.extractors import MODEL_TASKS
    from llm.router import model_prices
    from storage.postgres import save_quote
    from rules.engine import flags_for_quote
    model = model_prices(MODEL_TASKS[extractor])[0] if extractor in MODEL_TASKS else None
    flags = flags_for_quote(quote)
    _db(lambda conn: save_quote(conn, segment_id, document_id or segment_id, quote,
                                pages=page_range(_parsed(segment_id)), extractor=extractor, model=model, flags=flags))
    # Flag codes only (no messages or values) go back to the workflow.
    return {'segment_id': segment_id, 'status': 'saved', 'flags': sorted({f['flag_code'] for f in flags})}

ALL = [intake_email_activity, parse_activity, classify_activity, mark_not_quote_activity,
       extract_activity, ground_activity, persist_activity]
