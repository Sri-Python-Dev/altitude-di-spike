"""Postgres storage for quote results. Connection string from DATABASE_URL.

Values reach the database only after the Ground step and verified_payload();
the quote_fields check constraint refuses an ungrounded value as a second guard.
"""
import os
import re
from functools import lru_cache
from pathlib import Path
from extract.ground import NUMERIC
from extract.persistence import verified_payload
from extract.schemas import Evidenced, QuoteLineItem, SupplierQuote

SCHEMA = Path(__file__).with_name('schema.sql')
ITEM = re.compile(r'line_items\[(\d+)\]\.(.+)')

def database_url() -> str | None:
    return os.getenv('DATABASE_URL') or None

@lru_cache(maxsize=4)
def _ensure_schema(url: str):
    import psycopg
    with psycopg.connect(url) as conn:
        conn.execute(SCHEMA.read_text())

def connect(url: str | None = None):
    """A psycopg connection with the schema in place. Use as `with connect() as conn:` (commits on exit)."""
    import psycopg
    url = url or database_url()
    if not url:
        raise RuntimeError('DATABASE_URL is not set')
    _ensure_schema(url)
    return psycopg.connect(url)

def record_document(conn, document_id: str, *, kind: str, status: str, file_path: str | None = None,
                    source_sha256: str | None = None, ocr: bool | None = None, reason: str | None = None,
                    quote_count: int | None = None, source_email: str | None = None):
    conn.execute('''
        insert into documents (document_id, kind, source_sha256, file_path, status, reason, ocr, quote_count, source_email)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (document_id) do update set
            status = excluded.status, reason = excluded.reason,
            source_sha256 = coalesce(excluded.source_sha256, documents.source_sha256),
            file_path = coalesce(excluded.file_path, documents.file_path),
            ocr = coalesce(excluded.ocr, documents.ocr),
            quote_count = coalesce(excluded.quote_count, documents.quote_count),
            source_email = coalesce(excluded.source_email, documents.source_email),
            updated_at = now()''',
        (document_id, kind, source_sha256, file_path, status, reason, ocr, quote_count, source_email))

def save_quote(conn, segment_id: str, document_id: str, quote: SupplierQuote, *,
               pages: tuple[int, int] | None, extractor: str | None, model: str | None,
               flags: list[dict] | None = None):
    """Replace this quote's rows (fields and flags) in one transaction. Raises if any ungrounded field holds a value."""
    verified_payload(quote)
    fields = list(quote.fields())
    with conn.transaction():
        conn.execute('''
            insert into quotes (segment_id, document_id, page_from, page_to, extractor, model, grounded_fields, ungrounded_fields)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (segment_id) do update set
                document_id = excluded.document_id, page_from = excluded.page_from, page_to = excluded.page_to,
                extractor = excluded.extractor, model = excluded.model, grounded_fields = excluded.grounded_fields,
                ungrounded_fields = excluded.ungrounded_fields, saved_at = now()''',
            (segment_id, document_id, *(pages or (None, None)), extractor, model,
             sum(f.grounded for _, f in fields), sum(bool(f.ungrounded_reason) for _, f in fields)))
        conn.execute('delete from quote_fields where segment_id = %s', (segment_id,))
        with conn.cursor() as cur:
            cur.executemany('''
                insert into quote_fields (segment_id, name, line_item, field, value, value_number, page, bbox,
                                          verbatim_span, raw_confidence, grounded, ungrounded_reason, ungrounded_span)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                [_row(segment_id, name, f) for name, f in fields])
            conn.execute('delete from quote_flag_results where segment_id = %s', (segment_id,))
            if flags:
                cur.executemany('insert into quote_flag_results (segment_id, flag_code, severity, field, message) values (%s, %s, %s, %s, %s)',
                                [(segment_id, f['flag_code'], f['severity'], f.get('field'), f['message']) for f in flags])
        conn.execute("update documents set status = 'saved', updated_at = now() where document_id = %s", (document_id,))

def _row(segment_id: str, name: str, f: Evidenced) -> tuple:
    m = ITEM.fullmatch(name)
    line_item, field = (int(m.group(1)), m.group(2)) if m else (None, name)
    value = f.value if f.grounded else None
    return (segment_id, name, line_item, field,
            None if value is None else str(value),
            value if field in NUMERIC and value is not None else None,
            f.page, list(f.bbox) if f.bbox else None, f.verbatim_span if f.grounded else None,
            f.raw_confidence, f.grounded, f.ungrounded_reason, f.ungrounded_span)

def load_flags(conn, segment_id: str) -> list[dict]:
    rows = conn.execute('select flag_code, severity, field, message from quote_flag_results where segment_id = %s order by flag_code',
                        (segment_id,)).fetchall()
    return [dict(zip(('flag_code', 'severity', 'field', 'message'), r)) for r in rows]

def load_quote(conn, segment_id: str) -> tuple[SupplierQuote, str | None] | None:
    """The stored quote and its source file path, or None if not stored."""
    head = conn.execute('select d.file_path from quotes q join documents d using (document_id) where q.segment_id = %s',
                        (segment_id,)).fetchone()
    if head is None:
        return None
    quote = SupplierQuote()
    rows = conn.execute('''select line_item, field, value, value_number, page, bbox, verbatim_span, raw_confidence,
                                  grounded, ungrounded_reason, ungrounded_span
                           from quote_fields where segment_id = %s order by line_item nulls first, name''', (segment_id,))
    for line_item, field, value, number, page, bbox, span, confidence, grounded, reason, claimed in rows:
        if number is not None:
            value = int(number) if field == 'lead_time_days' else float(number)
        evidenced = Evidenced(value=value, page=page,
                              bbox=tuple(bbox) if bbox else None, verbatim_span=span, raw_confidence=confidence,
                              grounded=grounded, ungrounded_reason=reason, ungrounded_span=claimed)
        if line_item is None:
            setattr(quote, field, evidenced)
        else:
            while len(quote.line_items) <= line_item:
                quote.line_items.append(QuoteLineItem())
            setattr(quote.line_items[line_item], field, evidenced)
    return SupplierQuote.model_validate(quote.model_dump()), head[0]
