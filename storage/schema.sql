-- Supplier quote results. Idempotent: safe to run on every start.

-- One row per PDF or email body that entered the pipeline.
create table if not exists documents (
    document_id    text primary key,
    kind           text not null check (kind in ('pdf', 'body')),
    source_sha256  text,
    file_path      text,
    status         text not null check (status in ('parsed', 'saved', 'unparseable')),
    reason         text,            -- fixed code, e.g. password_protected
    ocr            boolean,
    quote_count    integer,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

-- One row per quote found in a document (a PDF can hold several).
create table if not exists quotes (
    segment_id         text primary key,
    document_id        text not null references documents (document_id) on delete cascade,
    page_from          integer,
    page_to            integer,
    extractor          text,
    model              text,
    grounded_fields    integer not null,
    ungrounded_fields  integer not null,
    saved_at           timestamptz not null default now()
);

-- One row per extracted field, with its evidence.
create table if not exists quote_fields (
    segment_id         text not null references quotes (segment_id) on delete cascade,
    name               text not null,   -- e.g. line_items[0].unit_price
    line_item          integer,         -- null for header fields
    field              text not null,   -- e.g. unit_price
    value              text,
    value_number       numeric,         -- numeric fields only
    page               integer,         -- 1-based; 0 = email body
    bbox               double precision[4],  -- x0, y0, x1, y1 in PDF points, top-left origin
    verbatim_span      text,
    raw_confidence     double precision,     -- model self-report: stored, never trusted
    grounded           boolean not null,
    ungrounded_reason  text,
    ungrounded_span    text,            -- the model's claimed span, kept for debugging only
    primary key (segment_id, name),
    -- Never store an ungrounded value.
    constraint grounded_or_empty check (grounded or (value is null and value_number is null))
);

create index if not exists quote_fields_field on quote_fields (field);

-- Flags raised by the decision table (rules/quote_flags.json) for a quote.
create table if not exists quote_flag_results (
    segment_id  text not null references quotes (segment_id) on delete cascade,
    flag_code   text not null,
    severity    text not null,
    field       text,
    message     text not null
);
create index if not exists quote_flag_results_segment on quote_flag_results (segment_id);

-- Where a document came from, so a test email can be resent through the workflow.
alter table documents add column if not exists source_email text;

-- Documents the classify step decided are not quotes (purchase orders, invoices, other).
alter table documents drop constraint if exists documents_status_check;
alter table documents add constraint documents_status_check
    check (status in ('parsed', 'saved', 'unparseable', 'not_a_quote'));
