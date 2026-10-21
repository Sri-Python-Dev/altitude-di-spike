"""Altitude Document Intelligence: the workbench used in the demo.

Run: uv run --env-file .env python -m ui.server   then open http://127.0.0.1:8765
From another computer on the same network: add --host 0.0.0.0 and open the printed link,
which carries a one-off access key (required whenever the app is reachable from the network).

Pages
  /                   documents stored in Postgres
  /results            the latest harness report (eval/reports/latest.json); start a run; compare runs
  /documents/<id>     field-level evidence: page image with spans, golden vs extracted, flags, resend
  /scores             accuracy of stored documents against known answers
  /rules              the GoRules decision table (rules/quote_flags.json) in the GoRules editor
  /upload             process a PDF without email

Uploading, resending and reprocessing go through Temporal, so the worker must be running.
Standard library HTTP server; no new Python dependencies. The GoRules editor is a vendored
bundle in ui/static (see ui/jdm_editor/build.sh).
"""
import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import time
from email.parser import BytesParser
from email.policy import default as email_policy
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

from ui.scoring import FIELD_LABELS, band, by_field, find_golden, golden_index, score_quote

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).with_name('static')
REPORTS = ROOT / 'eval/reports'
APP_NAME = 'Altitude Document Intelligence'
ITEM = re.compile(r'line_items\[(\d+)\]\.(.+)')
HEADER_FIELDS = ['supplier_name', 'quote_reference', 'quote_date', 'valid_until', 'currency', 'incoterms', 'payment_terms', 'stated_total']
ITEM_FIELDS = ['part_number_quoted', 'quantity', 'unit_price', 'extended_price', 'lead_time_days', 'moq']
MONEY = {'unit_price', 'extended_price', 'stated_total'}
MAX_UPLOAD = 25 * 1024 * 1024
PAGE_SCALE = 1.5
from eval.datasets import golden_dataset, harness_datasets  # set in .env: ALTITUDE_GOLDEN_DATASET
HARNESS: dict = {}  # the one background harness run started from /results

def _fmt(value, field: str | None = None) -> str:
    if value is None:
        return ''
    if field in MONEY and isinstance(value, (int, float)):
        return f'{value:,.2f}'
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)

def _pct(p: float | None) -> str:
    return '—' if p is None else f'{p:.0%}' if p in (0, 1) else f'{p:.1%}'

def score_pill(p: float | None) -> str:
    if p is None:
        return '<span class="score none" title="No known answers for this document">not scored</span>'
    return f'<span class="score {band(p)}">{_pct(p)}</span>'

def _label(name: str) -> str:
    m = ITEM.fullmatch(name)
    return FIELD_LABELS.get(m.group(2) if m else name, name)

# ---------- data: stored documents ----------

def documents(conn) -> list[dict]:
    rows = conn.execute('''
        select d.document_id, d.kind, d.status, d.reason, d.ocr, d.file_path, d.updated_at, d.source_sha256,
               coalesce(array_agg(q.segment_id order by q.page_from, q.segment_id) filter (where q.segment_id is not null), '{}')
        from documents d left join quotes q using (document_id)
        group by d.document_id order by d.updated_at desc''').fetchall()
    keys = ('document_id', 'kind', 'status', 'reason', 'ocr', 'file_path', 'updated_at', 'source_sha256', 'segments')
    return [dict(zip(keys, r)) for r in rows]

def quote_detail(conn, segment_id: str) -> dict | None:
    head = conn.execute('''
        select q.segment_id, q.document_id, q.page_from, q.page_to, q.extractor, q.model, q.grounded_fields,
               q.ungrounded_fields, q.saved_at, d.file_path, d.kind, d.ocr, d.source_sha256, d.source_email
        from quotes q join documents d using (document_id) where q.segment_id = %s''', (segment_id,)).fetchone()
    if head is None:
        return None
    keys = ('segment_id', 'document_id', 'page_from', 'page_to', 'extractor', 'model', 'grounded_fields',
            'ungrounded_fields', 'saved_at', 'file_path', 'kind', 'ocr', 'source_sha256', 'source_email')
    detail = dict(zip(keys, head))
    fields = {}
    for name, field, value, number, page, bbox, span, confidence, grounded, reason, claimed in conn.execute('''
            select name, field, value, value_number, page, bbox, verbatim_span, raw_confidence, grounded,
                   ungrounded_reason, ungrounded_span
            from quote_fields where segment_id = %s''', (segment_id,)):
        fields[name] = {'name': name, 'field': field, 'value': float(number) if number is not None else value, 'page': page,
                        'bbox': list(bbox) if bbox else None, 'span': span, 'grounded': grounded,
                        'reason': reason, 'claimed': claimed, 'confidence': confidence}
    detail['fields'] = fields
    detail['line_items'] = sorted({int(ITEM.fullmatch(n).group(1)) for n in fields if ITEM.fullmatch(n)})
    return detail

def score_for(detail: dict, index: dict | None = None) -> dict | None:
    golden = find_golden(index if index is not None else golden_index(), detail['document_id'],
                         detail['segment_id'], detail['source_sha256'])
    return score_quote(detail['fields'], golden) if golden else None

# ---------- data: one document for the evidence view (stored, or from a harness run) ----------

def _run_file(run_id: str, document_id: str) -> Path | None:
    if not re.fullmatch(r'[\w.-]+', run_id or '') or not re.fullmatch(r'[\w.-]+', document_id or ''):
        return None
    path = REPORTS / 'runs' / run_id / f'{document_id}.json'
    return path if path.exists() else None

def evidence_detail(conn, doc_id: str, run_id: str | None) -> dict | None:
    """Everything the document view shows, from Postgres or from a harness run's saved result."""
    if run_id:
        path = _run_file(run_id, doc_id)
        if path is None:
            return None
        data = json.loads(path.read_text())
        fields = {f['name']: {'name': f['name'], 'field': _field_key(f['name']), 'value': f['value'], 'page': f['page'],
                              'bbox': f['bbox'], 'span': f['verbatim_span'] if f['grounded'] else None,
                              'grounded': f['grounded'], 'reason': f.get('ungrounded_reason'),
                              'claimed': f.get('ungrounded_span'), 'confidence': f.get('raw_confidence')}
                  for f in data['fields']}
        score = score_quote(fields, data['golden']) if data.get('golden') else None
        return {'id': doc_id, 'source': 'run', 'run_id': run_id, 'document_id': doc_id, 'file_path': data['source_path'],
                'kind': data['kind'], 'page_numbers': data['page_numbers'], 'body_text': data.get('body_text'),
                'fields': fields, 'flags': data.get('flags', []), 'score': score, 'model': data.get('extractor'),
                'saved_at': None, 'source_email': None, 'extractor': data.get('extractor')}
    from storage.postgres import load_flags
    q = quote_detail(conn, doc_id)
    if q is None:
        return None
    body = Path(q['file_path']).read_text() if q['kind'] == 'body' and q['file_path'] and Path(q['file_path']).exists() else None
    if body and q['file_path'].endswith('.html'):
        from extract.body import html_to_text
        body = html_to_text(body)
    return {'id': doc_id, 'source': 'db', 'run_id': None, 'document_id': q['document_id'], 'file_path': q['file_path'],
            'kind': q['kind'], 'page_numbers': list(range(q['page_from'] or 1, (q['page_to'] or 1) + 1)) if q['kind'] == 'pdf' else [0],
            'body_text': body, 'fields': q['fields'], 'flags': load_flags(conn, doc_id), 'score': score_for(q),
            'model': q['model'] or q['extractor'], 'saved_at': q['saved_at'], 'source_email': q['source_email'],
            'extractor': q['extractor'], 'grounded_fields': q['grounded_fields'], 'ungrounded_fields': q['ungrounded_fields']}

def _field_key(name: str) -> str:
    m = ITEM.fullmatch(name)
    return m.group(2) if m else name

def _safe_file(path: str | None, suffixes=('.pdf',)) -> Path | None:
    """Only serve files inside this repository or the workflow state directory."""
    if not path or not path.endswith(suffixes):
        return None
    from temporal_app.activities import STATE_DIR
    resolved = Path(path).resolve()
    if not resolved.is_file():
        return None
    for root in (ROOT, Path(STATE_DIR).resolve()):
        if resolved.is_relative_to(root):
            return resolved
    return None

def page_sizes(pdf: Path, numbers: list[int]) -> dict[int, tuple[float, float]]:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    try:
        return {n: tuple(doc[n - 1].get_size()) for n in numbers if 0 < n <= len(doc)}
    finally:
        doc.close()

def page_png(pdf: Path, number: int) -> bytes | None:
    from tools.where import render_highlight
    out = io.BytesIO()
    render_highlight(str(pdf), number, None, scale=PAGE_SCALE).save(out, 'PNG')
    return out.getvalue()

# ---------- layout ----------

STYLE = '''
:root {
  --bg: #f3f5f8; --surface: #ffffff; --ink: #14202e; --muted: #5a6675; --faint: #8a95a3; --rule: #dfe4ea; --rule-strong: #c9d1db;
  --brand: #0f2d4a; --brand-ink: #ffffff; --brand-muted: #a9bdd1; --accent: #1d5c96; --accent-soft: #e6eef7;
  --good: #146c43; --good-soft: #dff3e8; --fair: #8a5a00; --fair-soft: #fdf1d6; --poor: #b42318; --poor-soft: #fde7e5;
  --mark: #fff0a3; --extracted: #d9480f; --golden: #1c7ed6;
  --sans: "IBM Plex Sans", "Helvetica Neue", Arial, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root {
  --bg: #0e141b; --surface: #151d27; --ink: #e4e9ef; --muted: #98a4b2; --faint: #6f7b89; --rule: #253141; --rule-strong: #33414f;
  --brand: #0a1f33; --brand-ink: #f1f5f9; --brand-muted: #8ea6be; --accent: #7fb2e5; --accent-soft: #1b2b3d;
  --good: #5fd39a; --good-soft: #12301f; --fair: #f2c35b; --fair-soft: #33290f; --poor: #ff8a7a; --poor-soft: #3a1714;
  --mark: #5a4c10; --extracted: #ff8c42; --golden: #4dabf7; color-scheme: dark; } }
* { box-sizing: border-box; }
html, body { margin: 0; }
body { background: var(--bg); color: var(--ink); font: 400 14px/1.5 var(--sans); -webkit-font-smoothing: antialiased; }
a { color: var(--accent); text-decoration: none; } a:hover { text-decoration: underline; }
.appbar { background: var(--brand); color: var(--brand-ink); position: sticky; top: 0; z-index: 10; }
.appbar .inner { max-width: 1600px; margin: 0 auto; padding: 0 28px; display: flex; align-items: center; gap: 32px; height: 56px; }
.brand { display: flex; align-items: center; gap: 10px; color: var(--brand-ink); font-weight: 600; font-size: 15px; white-space: nowrap; }
.brand:hover { text-decoration: none; }
.brand .mark { width: 26px; height: 26px; border: 1.5px solid var(--brand-ink); display: grid; place-items: center; font-size: 13px; }
nav { display: flex; gap: 4px; overflow-x: auto; }
nav a { color: var(--brand-muted); padding: 17px 12px 15px; border-bottom: 2px solid transparent; font-weight: 500; white-space: nowrap; }
nav a:hover { color: var(--brand-ink); text-decoration: none; }
nav a.on { color: var(--brand-ink); border-bottom-color: var(--brand-ink); }
.page-head { max-width: 1600px; margin: 0 auto; padding: 22px 28px 4px; display: flex; justify-content: space-between; align-items: end; gap: 16px; flex-wrap: wrap; }
.page-head h1 { font-size: 22px; font-weight: 600; margin: 0; letter-spacing: -.01em; }
.page-head p { margin: 2px 0 0; color: var(--muted); }
.actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
main { max-width: 1600px; margin: 0 auto; padding: 16px 28px 56px; display: grid; gap: 18px; }
.panel { background: var(--surface); border: 1px solid var(--rule); border-radius: 6px; min-width: 0; }
.panel > header { padding: 12px 18px; border-bottom: 1px solid var(--rule); display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.panel > header h2 { font-size: 13px; font-weight: 600; margin: 0; }
.panel .body { padding: 14px 18px; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
.kpi { padding: 16px 18px; border-right: 1px solid var(--rule); } .kpi:last-child { border-right: 0; }
.kpi b { display: block; font-size: 24px; font-weight: 600; font-variant-numeric: tabular-nums; }
.kpi span { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 9px 14px; border-bottom: 1px solid var(--rule); white-space: nowrap; vertical-align: middle; }
thead th { font-size: 11.5px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: .05em; background: var(--bg); border-bottom: 1px solid var(--rule-strong); }
tbody tr:last-child td { border-bottom: 0; }
td.num, th.num { text-align: right; }
.mono { font-family: var(--mono); font-size: 12.5px; }
.code { display: block; font-family: var(--mono); font-size: 11.5px; color: var(--faint); }
.muted, .missing { color: var(--muted); }
.tag { display: inline-block; padding: 1px 8px; border-radius: 3px; font-size: 12px; font-weight: 500; white-space: nowrap; }
.tag.saved, .tag.ok, .tag.grounded, .tag.pass { background: var(--good-soft); color: var(--good); }
.tag.unparseable, .tag.bad, .tag.ungrounded, .tag.fail { background: var(--poor-soft); color: var(--poor); }
.tag.parsed { background: var(--accent-soft); color: var(--accent); }
.tag.not_a_quote { background: var(--bg); color: var(--muted); border: 1px solid var(--rule-strong); }
.tag.missing { background: var(--bg); color: var(--muted); border: 1px solid var(--rule-strong); }
.tag.review { background: var(--fair-soft); color: var(--fair); }
.score { display: inline-block; min-width: 56px; text-align: center; padding: 2px 8px; border-radius: 3px; font-weight: 600; font-size: 12.5px; font-variant-numeric: tabular-nums; }
.score.good { background: var(--good-soft); color: var(--good); }
.score.fair { background: var(--fair-soft); color: var(--fair); }
.score.poor { background: var(--poor-soft); color: var(--poor); }
.score.none { background: transparent; color: var(--muted); font-weight: 400; border: 1px dashed var(--rule-strong); }
.kpi b .score { font-size: 20px; padding: 2px 10px; }
.sign.up { color: var(--good); font-weight: 700; } .sign.down { color: var(--poor); font-weight: 700; }
.legend { display: flex; gap: 10px; flex-wrap: wrap; color: var(--muted); font-size: 12.5px; align-items: center; }
dl.kv { display: grid; grid-template-columns: max-content 1fr; gap: 6px 18px; margin: 0; }
dl.kv dt { color: var(--muted); } dl.kv dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.runline { display: flex; flex-wrap: wrap; gap: 6px 22px; font-size: 13px; }
.runline span b { font-weight: 500; color: var(--muted); margin-right: 6px; }
.cmd { font-family: var(--mono); font-size: 12px; background: var(--bg); border: 1px solid var(--rule); border-radius: 4px; padding: 6px 10px; overflow-x: auto; white-space: nowrap; }
.note { color: var(--muted); font-size: 12.5px; margin: 0; }
.notice { padding: 10px 14px; border-radius: 4px; background: var(--accent-soft); border: 1px solid var(--rule-strong); }
.notice.error { background: var(--poor-soft); }
button, .button { display: inline-block; font: 500 13px var(--sans); padding: 7px 14px; border-radius: 4px; border: 1px solid var(--accent); background: var(--accent); color: #fff; cursor: pointer; }
.button:hover { text-decoration: none; }
button.secondary, .button.secondary { background: transparent; color: var(--accent); }
.toggle { display: inline-flex; border: 1px solid var(--accent); border-radius: 4px; overflow: hidden; }
.toggle a { padding: 6px 12px; color: var(--accent); font-weight: 500; } .toggle a.on { background: var(--accent); color: #fff; }
.toggle a:hover { text-decoration: none; }
button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
form.inline { display: inline-flex; gap: 8px; align-items: center; margin: 0; }
.formgrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; align-items: end; }
label { display: grid; gap: 4px; font-size: 12.5px; color: var(--muted); }
input[type=text], input[type=file], select { font: 14px var(--sans); padding: 6px 9px; border: 1px solid var(--rule-strong); border-radius: 4px; background: var(--surface); color: var(--ink); }
/* evidence view */
.evidence { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr); gap: 18px; align-items: start; }
.pagepane { position: sticky; top: 72px; max-height: calc(100vh - 90px); overflow: auto; background: var(--surface); border: 1px solid var(--rule); border-radius: 6px; }
.pg { position: relative; border-bottom: 1px solid var(--rule); } .pg:last-child { border-bottom: 0; }
.pg img { display: block; width: 100%; height: auto; background: #fff; }
.pg .pno { position: absolute; top: 6px; right: 8px; font-size: 11px; color: var(--muted); background: var(--surface); padding: 0 6px; border-radius: 3px; }
.box { position: absolute; pointer-events: none; display: none; border-radius: 2px; }
.box.extracted { border: 2.5px solid var(--extracted); background: color-mix(in srgb, var(--extracted) 14%, transparent); }
.box.golden { border: 2px dashed var(--golden); }
pre.bodytext { margin: 0; padding: 18px; white-space: pre-wrap; font: 13px/1.6 var(--mono); }
pre.bodytext mark { background: var(--mark); outline: 2px solid var(--extracted); }
.boxkey { display: flex; gap: 14px; font-size: 12px; color: var(--muted); align-items: center; }
.boxkey i { display: inline-block; width: 18px; height: 10px; margin-right: 5px; vertical-align: middle; }
.boxkey .e { border: 2.5px solid var(--extracted); } .boxkey .g { border: 2px dashed var(--golden); }
.group > h3 { font-size: 12px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); margin: 0; padding: 10px 18px; background: var(--bg); border-bottom: 1px solid var(--rule); }
.field { display: block; width: 100%; text-align: left; font: inherit; color: inherit; background: none; border: 0; border-bottom: 1px solid var(--rule); padding: 10px 18px; cursor: pointer; border-radius: 0; }
.field:hover { background: var(--accent-soft); }
.field.selected { background: var(--accent-soft); box-shadow: inset 3px 0 0 var(--accent); }
.field .top { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: baseline; }
.field .name { font-weight: 500; }
.field .value { font-size: 15px; font-weight: 600; margin-top: 1px; }
.field .value.none { font-weight: 400; color: var(--poor); }
.field .value.absent { font-weight: 400; color: var(--muted); }
.field .line { margin-top: 4px; font-size: 12.5px; color: var(--muted); display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.field .span { font-family: var(--mono); font-size: 12px; background: var(--mark); color: var(--ink); padding: 0 4px; white-space: pre-wrap; border-radius: 2px; }
.field .span.golden { background: color-mix(in srgb, var(--golden) 16%, transparent); }
.field .span.claimed { background: var(--bg); color: var(--faint); text-decoration: line-through; }
.field .flag { color: var(--fair); }
.verdict.ok { color: var(--good); font-weight: 600; } .verdict.no { color: var(--poor); font-weight: 600; }
.editor { height: calc(100vh - 140px); border: 1px solid var(--rule); border-radius: 6px; overflow: hidden; background: #fff; }
@media (max-width: 1000px) { .evidence { grid-template-columns: 1fr; } .pagepane { position: static; max-height: none; } .appbar .inner, .page-head, main { padding-left: 16px; padding-right: 16px; } }
'''

NAV = [('/', 'Documents'), ('/results', 'Results'), ('/scores', 'Accuracy'), ('/rules', 'Rules'), ('/upload', 'Upload')]

def _page(title: str, body: str, *, active: str = '/', heading: str = '', sub: str = '', actions: str = '',
          refresh: int | None = None, head: str = '') -> str:
    nav = ''.join(f'<a href="{href}" class="{"on" if href == active else ""}">{label}</a>' for href, label in NAV)
    heading_html = (f'<div class="page-head"><div><h1>{escape(heading)}</h1>{f"<p>{sub}</p>" if sub else ""}</div>'
                    f'<div class="actions">{actions}</div></div>' if heading else '')
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ''
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">' + meta +
            f'<title>{escape(title)} · {APP_NAME}</title>'
            '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">'
            f'<style>{STYLE}</style>{head}</head><body>'
            f'<div class="appbar"><div class="inner"><a class="brand" href="/"><span class="mark">A</span>{APP_NAME}</a>'
            f'<nav>{nav}</nav></div></div>{heading_html}<main>{body}</main></body></html>')

def _notice(text: str, error: bool = False) -> str:
    return f'<div class="notice{" error" if error else ""}">{text}</div>'

LEGEND = ('<div class="legend"><span>Compared with known correct answers, never model confidence.</span>'
          '<span class="score good">&gt; 95%</span><span class="score fair">80–95%</span><span class="score poor">&lt; 80%</span></div>')

def _doc_link(doc_id: str, field: str | None = None, run: str | None = None) -> str:
    params = {k: v for k, v in (('run', run), ('field', field)) if v}
    return f'/documents/{quote(doc_id)}' + (f'?{urlencode(params)}' if params else '')

# ---------- documents ----------

def render_index(conn) -> str:
    docs = documents(conn)
    index = golden_index()
    quotes, scored, rows = 0, [], []
    for d in docs:
        cells = []
        for s in d['segments']:
            quotes += 1
            detail = quote_detail(conn, s)
            sc = score_for(detail, index) if detail else None
            if sc:
                scored.append(sc)
            cells.append(f'<a href="{_doc_link(s)}" class="mono">{escape(s)}</a> {score_pill(sc["pct"] if sc else None)}')
        ocr = 'yes' if d['ocr'] else ('no' if d['ocr'] is False else '<span class="missing">—</span>')
        reason = escape(d['reason']) if d['reason'] else '<span class="missing">—</span>'
        rows.append(f'<tr><td class="mono">{escape(d["document_id"])}</td><td>{escape(d["kind"])}</td>'
                    f'<td><span class="tag {escape(d["status"])}">{escape(d["status"])}</span></td><td>{reason}</td><td>{ocr}</td>'
                    f'<td>{"<br>".join(cells) or "<span class=missing>—</span>"}</td>'
                    f'<td class="mono">{escape(Path(d["file_path"]).name) if d["file_path"] else "—"}</td>'
                    f'<td>{d["updated_at"]:%d %b %Y %H:%M}</td></tr>')
    overall = (sum(s['correct'] for s in scored) / sum(s['total'] for s in scored)) if scored else None
    kpis = ''.join(f'<div class="kpi"><b>{v}</b><span>{label}</span></div>' for v, label in (
        (len(docs), 'Documents'), (quotes, 'Quotes'), (sum(d['status'] == 'saved' for d in docs), 'Saved'),
        (sum(d['status'] == 'unparseable' for d in docs), 'Unparseable'), (score_pill(overall), 'Accuracy, scored quotes')))
    table = ('<div class="scroll"><table><thead><tr><th>Document</th><th>Kind</th><th>Status</th><th>Reason</th>'
             '<th>OCR</th><th>Quotes and accuracy</th><th>Source file</th><th>Updated</th></tr></thead><tbody>'
             + (''.join(rows) or '<tr><td colspan="8" class="missing">No documents yet. Upload a PDF, or drop an email into data/inbox with the monitor running.</td></tr>')
             + '</tbody></table></div>')
    return _page('Documents', f'<section class="panel"><div class="kpis">{kpis}</div></section>'
                 f'<section class="panel"><header><h2>Documents</h2>{LEGEND}</header>{table}</section>',
                 active='/', heading='Documents', sub='Everything processed and stored in Postgres.',
                 actions='<a class="button" href="/upload">Upload a PDF</a>')

# ---------- results (prompt 10) ----------

def list_runs() -> list[dict]:
    runs = []
    for path in REPORTS.glob('*.json'):
        if path.name == 'latest.json':
            continue
        try:
            r = json.loads(path.read_text())
        except ValueError:
            continue
        if 'fields' in r and not r.get('smoke_test'):
            runs.append({'run_id': r.get('run_id') or path.stem, 'path': path, 'extractor': r.get('extractor'),
                         'model': r.get('model') or (r.get('usage') or {}).get('model') or r.get('extractor'),
                         'parser': r.get('parser', 'docling'), 'generated_at': r.get('generated_at', '')})
    return sorted(runs, key=lambda r: r['generated_at'], reverse=True)

def load_run(run_id: str | None) -> dict | None:
    if not run_id:
        latest = REPORTS / 'latest.json'
        return json.loads(latest.read_text()) if latest.exists() else None
    run = next((r for r in list_runs() if r['run_id'] == run_id), None)
    if run is None:
        return None
    report = json.loads(run['path'].read_text())
    report.setdefault('run_id', run_id)
    return report

def _run_label(r: dict) -> str:
    return f"{r.get('model') or r.get('extractor')} · {r.get('parser') or 'docling'} · {str(r.get('generated_at', ''))[:16].replace('T', ' ')}"

def _delta(d: float | None) -> str:
    if d is None:
        return '<span class="muted">—</span>'
    points = d * 100
    if abs(points) < 0.05:
        return '0.0'
    sign = '<span class="sign up">+</span>' if points > 0 else '<span class="sign down">−</span>'
    return f'{sign}{abs(points):.1f}'

def render_results(args: dict, notice: str = '') -> str:
    """Renders the harness JSON as written; computes no metrics."""
    shown_id = args.get('show') or args.get('run')
    base = load_run(args.get('run'))
    if base is None:
        body = notice + _notice('No harness report yet. Start a run below, or run '
                                f'<span class="mono">uv run --env-file .env python -m eval.run --dataset {escape(golden_dataset())} --extractor current</span>.')
        return _page('Results', body + _run_form(), active='/results', heading='Results')
    compare = load_run(args['compare']) if args.get('compare') else None
    report = load_run(shown_id) if shown_id and shown_id != base.get('run_id') else base
    if report is None:
        report = base
    run_id = report.get('run_id')
    datasets = ', '.join(f"{d['name']} ({d['documents']})" for d in report.get('dataset_summary', [])) or ', '.join(report.get('datasets', []))
    runline = (f'<div class="runline">'
               f'<span><b>Dataset</b>{escape(datasets)}; {report.get("document_count")} documents</span>'
               f'<span><b>Labellers</b>{escape(", ".join(report.get("labellers", [])) or "—")}</span>'
               f'<span><b>Commit</b>{escape(report.get("git_commit") or "not under version control")}</span>'
               f'<span><b>Model</b>{escape(str(report.get("model") or report.get("extractor")))}</span>'
               f'<span><b>Parser</b>{escape(str(report.get("parser") or "docling"))}</span>'
               f'<span><b>Run</b>{escape(str(report.get("generated_at", "")).replace("T", " "))} '
               f'{f"({report["duration_s"]}s)" if report.get("duration_s") else ""}</span></div>'
               f'<div class="cmd" style="margin-top:10px">{escape(report.get("command") or "—")}</div>')
    order = list(base['fields'])  # identical row order for every run shown against this base
    rows = []
    for name in order + [n for n in report['fields'] if n not in order]:
        m = report['fields'].get(name)
        code = f'<span class="code">{escape(name)}</span>'
        if m is None:
            rows.append(f'<tr><td>{escape(_label(name.replace("[i]", "[0]")))}{code}</td><td colspan="6" class="muted">not in this run</td></tr>')
            continue
        worst = (f'<a class="mono" href="{_doc_link(m["worst_doc"], m.get("worst_doc_field"), run_id)}">{escape(m["worst_doc"])}</a>'
                 if m.get('worst_doc') else '<span class="muted">—</span>')
        rows.append(f'<tr><td>{escape(m.get("label") or _label(name.replace("[i]", "[0]")))}{code}</td>'
                    f'<td class="num">{score_pill(m["accuracy"])}</td>'
                    f'<td class="num">{_pct(m.get("grounded_rate"))}</td><td class="num">{_pct(m.get("ungrounded_rate"))}</td>'
                    f'<td class="num">{_pct(m.get("missing_rate"))}</td><td class="num">{_delta(m.get("delta"))}</td><td>{worst}</td></tr>')
    toggle = ''
    if compare:
        pair = [(base, 'A'), (compare, 'B')]
        toggle = '<div class="toggle">' + ''.join(
            f'<a class="{"on" if r.get("run_id") == run_id else ""}" href="/results?{urlencode({"run": base["run_id"], "compare": compare["run_id"], "show": r["run_id"]})}">'
            f'{letter}: {escape(_run_label(r))}</a>' for r, letter in pair) + '</div>'
    runs = list_runs()
    options = lambda selected: ''.join(f'<option value="{escape(r["run_id"])}" {"selected" if r["run_id"] == selected else ""}>{escape(_run_label(r))}</option>' for r in runs)
    picker = (f'<form method="get" action="/results" class="inline"><label>Run<select name="run">{options(base.get("run_id"))}</select></label>'
              f'<label>Compare with<select name="compare"><option value="">—</option>{options(compare.get("run_id") if compare else None)}</select></label>'
              '<button type="submit" class="secondary">Show</button></form>')
    table = ('<div class="scroll"><table><thead><tr><th>Field</th><th class="num">Accuracy</th><th class="num">Grounded</th>'
             '<th class="num">Ungrounded</th><th class="num">Missing</th><th class="num">Δ vs previous run</th><th>Worst doc</th></tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table></div>')
    edge = report.get('edge_case_summary') or []
    edge_html = ('<div class="scroll"><table><thead><tr><th>Case</th><th>What happened</th><th>Result</th></tr></thead><tbody>'
                 + ''.join(f'<tr><td>{escape(e["case"])}</td><td style="white-space:normal">{escape(e["what_happened"])}</td>'
                           f'<td><span class="tag {"pass" if e["pass"] else "fail"}">{"pass" if e["pass"] else "fail"}</span></td></tr>' for e in edge)
                 + '</tbody></table></div>') if edge else '<div class="body"><p class="note">This run has no edge-case block.</p></div>'
    previous = report.get('previous_run_id')
    body = (notice +
            f'<section class="panel"><div class="body">{runline}</div></section>'
            f'<section class="panel"><header><h2>Per field, worst first</h2><div class="actions">{toggle}{picker}</div></header>{table}'
            f'<div class="body"><p class="note">Δ is accuracy points against the previous run of the same configuration'
            f'{f" (<span class=mono>{escape(previous)}</span>)" if previous else ""}. Bands apply to accuracy only: '
            '<span class="score good">&gt; 95%</span> <span class="score fair">80–95%</span> <span class="score poor">&lt; 80%</span></p></div></section>'
            f'<section class="panel"><header><h2>Edge cases</h2></header>{edge_html}</section>' + _run_form())
    return _page('Results', body, active='/results', heading='Results', sub=f'Harness run <span class="mono">{escape(str(run_id))}</span>')

def _run_form() -> str:
    return ('<section class="panel"><header><h2>Run the harness</h2></header><div class="body">'
            '<form method="post" action="/results/run" class="inline">'
            '<label>Model<select name="extractor"><option value="current">gpt-4.1-nano</option><option value="frontier">gpt-4.1</option></select></label>'
            '<button type="submit">Start run</button></form>'
            f'<p class="note" style="margin-top:8px">Runs {", ".join(harness_datasets())} with parsing cached and extraction in parallel; '
            'the page updates when it finishes.</p></div></section>')

def start_harness(extractor: str) -> None:
    if HARNESS.get('proc') and HARNESS['proc'].poll() is None:
        return
    from temporal_app.activities import STATE_DIR
    log = Path(STATE_DIR) / 'harness.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, '-m', 'eval.run', *[x for d in harness_datasets() for x in ('--dataset', d)], '--extractor', extractor]
    HARNESS.update(proc=subprocess.Popen(cmd, cwd=ROOT, stdout=log.open('w'), stderr=subprocess.STDOUT),
                   started=time.time(), extractor=extractor)

def render_harness_progress() -> str | None:
    """A progress page while a run started here is going; None once it has finished."""
    proc = HARNESS.get('proc')
    if not proc:
        return None
    code = proc.poll()
    elapsed = int(time.time() - HARNESS['started'])
    if code is None:
        return _page('Running', _notice(f'Harness run in progress ({escape(HARNESS["extractor"])}): {elapsed}s elapsed. '
                                        'This page refreshes every 3 seconds and shows the new report when the run finishes.'),
                     active='/results', heading='Running the harness…', refresh=3)
    HARNESS.clear()
    if code != 0:
        return _page('Run failed', _notice(f'The harness run stopped with exit code {code} after {elapsed}s. '
                                           'See data/state/harness.log on the server.', True), active='/results', heading='Run failed')
    return None

# ---------- document evidence view (prompt 11) ----------

def _field_entry(d: dict, name: str, debug: bool) -> str:
    f = d['fields'].get(name)
    mark = (d['score'] or {}).get('fields', {}).get(name)
    key = _field_key(name)
    if f is None or (f['value'] is None and not f['reason'] and not f['grounded']):
        state, value_html = 'missing', '<div class="value absent">not in the document</div>'
    elif f['grounded']:
        state, value_html = 'grounded', f'<div class="value">{escape(_fmt(f["value"], key))}</div>'
    else:
        state, value_html = 'ungrounded', f'<div class="value none">ungrounded · {escape(f["reason"] or "")}</div>'
    lines = []
    if f and f['grounded'] and f.get('span'):
        lines.append(f'<div class="line">Source text <span class="span">{escape(f["span"])}</span></div>')
    if f and state == 'ungrounded' and f.get('claimed'):
        lines.append(f'<div class="line">Model claimed <span class="span claimed">{escape(f["claimed"])}</span></div>')
    if mark:
        verdict = '<span class="verdict ok">✓ matches</span>' if mark['correct'] else '<span class="verdict no">✗ differs</span>'
        lines.append(f'<div class="line">Known answer <b>{escape(_fmt(mark["expected"], key))}</b> '
                     f'<span class="span golden">{escape(mark.get("golden_span") or "")}</span> {verdict}</div>')
    for flag in (fl for fl in d['flags'] if fl.get('field') == key and not ITEM.fullmatch(name)):
        lines.append(f'<div class="line flag"><span class="tag review">{escape(flag["flag_code"])}</span> {escape(flag["message"])}</div>')
    if debug and f and f.get('confidence') is not None:
        lines.append(f'<div class="line">raw_confidence (uncalibrated, never used as a score): {f["confidence"]}</div>')
    attrs = {'data-name': name, 'data-page': (f or {}).get('page') or '', 'data-bbox': ','.join(map(str, (f or {}).get('bbox') or [])),
             'data-span': (f or {}).get('span') or '',
             'data-gpage': (mark or {}).get('golden_page') or '', 'data-gbbox': ','.join(map(str, (mark or {}).get('golden_bbox') or []))}
    attr_html = ' '.join(f'{k}="{escape(str(v))}"' for k, v in attrs.items())
    return (f'<button type="button" class="field" {attr_html}><div class="top"><div><span class="name">{escape(_label(name))}</span>'
            f'<span class="code">{escape(name)}</span></div><span class="tag {state}">{state}</span></div>{value_html}{"".join(lines)}</button>')

def render_document(conn, doc_id: str, args: dict, notice: str = '') -> str | None:
    d = evidence_detail(conn, doc_id, args.get('run'))
    if d is None:
        return None
    debug = 'debug' in args
    names = set(d['fields']) | set((d['score'] or {}).get('fields', {}))
    items = sorted({int(ITEM.fullmatch(n).group(1)) for n in names if ITEM.fullmatch(n)})
    header = ''.join(_field_entry(d, n, debug) for n in HEADER_FIELDS)
    item_groups = ''
    for i in items:
        pn = d['fields'].get(f'line_items[{i}].part_number_quoted') or {}
        title = f'Line item {i + 1}' + (f' · {escape(_fmt(pn.get("value")))}' if pn.get('value') else '')
        item_groups += (f'<div class="group"><h3>{title}</h3>'
                        + ''.join(_field_entry(d, f'line_items[{i}].{k}', debug) for k in ITEM_FIELDS
                                  if f'line_items[{i}].{k}' in names) + '</div>')
    pdf = _safe_file(d['file_path']) if d['kind'] == 'pdf' else None
    if pdf:
        sizes = page_sizes(pdf, d['page_numbers'])
        run_q = f'?run={quote(d["run_id"])}' if d['run_id'] else ''
        pages = ''.join(
            f'<div class="pg" data-page="{n}" data-w="{w}" data-h="{h}"><img loading="lazy" alt="Page {n}" '
            f'src="/documents/{quote(doc_id)}/page/{n}.png{run_q}"><span class="pno">page {n}</span></div>'
            for n, (w, h) in sizes.items())
        left = f'<div class="pagepane" id="pages">{pages}</div>'
    else:
        left = f'<div class="pagepane"><pre class="bodytext" id="bodytext">{escape(d.get("body_text") or "Email body not available.")}</pre></div>'
    sc = d['score']
    summary = []
    if sc:
        summary.append(f'<dt>Known answers</dt><dd>{score_pill(sc["pct"])} <span class="muted">{sc["correct"]} of {sc["total"]} fields match</span></dd>')
    else:
        summary.append('<dt>Known answers</dt><dd class="muted">none for this document, so it is not scored</dd>')
    flags = d['flags']
    summary.append('<dt>Flags</dt><dd>' + (' '.join(f'<span class="tag review" title="{escape(f["message"])}">{escape(f["flag_code"])}</span>' for f in flags)
                                            or '<span class="muted">none</span>') + '</dd>')
    summary.append(f'<dt>Model</dt><dd>{escape(str(d["model"] or "—"))}</dd>')
    summary.append(f'<dt>Source</dt><dd class="mono">{escape(Path(d["file_path"]).name) if d["file_path"] else "—"}'
                   f'{" · from email" if d.get("source_email") else ""}</dd>')
    if d['source'] == 'run':
        summary.append(f'<dt>From</dt><dd>harness run <a class="mono" href="/results?run={quote(d["run_id"])}">{escape(d["run_id"])}</a></dd>')
    right = (f'<section class="panel"><div class="body"><dl class="kv">{"".join(summary)}</dl>'
             '<div class="boxkey" style="margin-top:10px"><span><i class="e"></i>extracted</span><span><i class="g"></i>known answer</span>'
             '<span>Select a field to outline it on the page.</span></div></div></section>'
             f'<section class="panel"><div class="group"><h3>Header</h3>{header}</div>{item_groups}</section>')
    actions = ''
    if d['source'] == 'db':
        actions = (f'<form class="inline" method="post" action="/documents/{quote(doc_id)}/reprocess">'
                   '<button class="secondary" type="submit" title="Re-run grounding and rules on the stored extraction. No model call.">Re-check with current rules</button></form>'
                   f'<form class="inline" method="post" action="/documents/{quote(doc_id)}/resend">'
                   '<button type="submit" title="Test documents only: send the same email (or file) through the whole workflow again as a new document.">Resend</button></form>')
    script = '''<script>
(() => {
  const fields = [...document.querySelectorAll('.field')];
  const pane = document.getElementById('pages');
  const body = document.getElementById('bodytext');
  const original = body ? body.textContent : null;
  function place(kind, page, bbox) {
    let box = document.querySelector('.box.' + kind);
    if (!box) { box = document.createElement('div'); box.className = 'box ' + kind; }
    const pg = pane && page ? pane.querySelector('.pg[data-page="' + page + '"]') : null;
    if (!pg || bbox.length !== 4) { box.style.display = 'none'; return null; }
    const w = +pg.dataset.w, h = +pg.dataset.h, pad = 3;
    pg.appendChild(box);
    Object.assign(box.style, { display: 'block', left: ((bbox[0] - pad) / w * 100) + '%', top: ((bbox[1] - pad) / h * 100) + '%',
      width: ((bbox[2] - bbox[0] + 2 * pad) / w * 100) + '%', height: ((bbox[3] - bbox[1] + 2 * pad) / h * 100) + '%' });
    return box;
  }
  function select(el, scroll) {
    fields.forEach(f => f.classList.toggle('selected', f === el));
    const nums = s => s ? s.split(',').map(Number) : [];
    const ext = place('extracted', el.dataset.page, nums(el.dataset.bbox));
    const gold = place('golden', el.dataset.gpage, nums(el.dataset.gbbox));
    const target = ext || gold;
    if (target && scroll !== false) {
      const top = target.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop - 80;
      pane.scrollTo({ top, behavior: 'smooth' });
    }
    if (body) {
      body.textContent = original;
      const span = el.dataset.span, at = span ? original.indexOf(span) : -1;
      if (at >= 0) {
        const mark = document.createElement('mark');
        mark.textContent = span;
        body.textContent = original.slice(0, at);
        body.append(mark, original.slice(at + span.length));
        mark.scrollIntoView({ block: 'center' });
      }
    }
    const url = new URL(location.href); url.searchParams.set('field', el.dataset.name); history.replaceState(null, '', url);
  }
  fields.forEach(f => f.addEventListener('click', () => select(f)));
  const wanted = new URL(location.href).searchParams.get('field');
  const initial = fields.find(f => f.dataset.name === wanted);
  if (initial) {
    const go = () => { select(initial); initial.scrollIntoView({ block: 'nearest' }); };
    const imgs = pane ? [...pane.querySelectorAll('img')] : [];
    imgs.forEach(i => i.loading = 'eager');
    Promise.all(imgs.map(i => i.complete ? null : new Promise(r => { i.onload = i.onerror = r; }))).then(go);
  }
})();
</script>'''
    body_html = notice + f'<div class="evidence">{left}<div style="display:grid;gap:18px;min-width:0">{right}</div></div>{script}'
    title = f'Document {doc_id}'
    sub = f'<a href="/">Documents</a> / {escape(d["document_id"])}' if d['source'] == 'db' else f'<a href="/results?run={quote(d["run_id"])}">Results</a> / {escape(doc_id)}'
    return _page(title, body_html, active='/results' if d['source'] == 'run' else '/', heading=title, sub=sub, actions=actions)

# ---------- accuracy across stored documents ----------

def render_scores(conn) -> str:
    index = golden_index()
    scored = []
    for d in documents(conn):
        for s in d['segments']:
            detail = quote_detail(conn, s)
            sc = score_for(detail, index) if detail else None
            if sc:
                scored.append((s, sc))
    if not scored:
        return _page('Accuracy', _notice('No stored document has known answers yet. Process a golden quote (data/golden) or a synthetic one (data/synthetic).'),
                     active='/scores', heading='Accuracy')
    overall = sum(sc['correct'] for _, sc in scored) / sum(sc['total'] for _, sc in scored)
    fields = by_field([sc for _, sc in scored])
    field_rows = ''.join(f'<tr><td>{FIELD_LABELS.get(k, k)}<span class="code">{escape(k)}</span></td><td class="num">{v["correct"]} / {v["total"]}</td>'
                         f'<td class="num">{score_pill(v["pct"])}</td></tr>' for k, v in fields.items())
    doc_rows = ''.join(f'<tr><td><a class="mono" href="{_doc_link(s)}">{escape(s)}</a></td>'
                       f'<td class="num">{sc["correct"]} / {sc["total"]}</td><td class="num">{score_pill(sc["pct"])}</td></tr>'
                       for s, sc in sorted(scored, key=lambda x: x[1]['pct']))
    body = (f'<section class="panel"><div class="kpis"><div class="kpi"><b>{score_pill(overall)}</b><span>Overall, stored documents</span></div>'
            f'<div class="kpi"><b>{len(scored)}</b><span>Quotes with known answers</span></div>'
            f'<div class="kpi"><b>{sum(sc["total"] for _, sc in scored)}</b><span>Fields scored</span></div></div></section>'
            '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:18px">'
            '<section class="panel"><header><h2>By field</h2></header><div class="scroll"><table>'
            f'<thead><tr><th>Field</th><th class="num">Correct</th><th class="num">Score</th></tr></thead><tbody>{field_rows}</tbody></table></div></section>'
            '<section class="panel"><header><h2>By document, worst first</h2></header><div class="scroll"><table>'
            f'<thead><tr><th>Document</th><th class="num">Correct</th><th class="num">Score</th></tr></thead><tbody>{doc_rows}</tbody></table></div></section></div>')
    return _page('Accuracy', body, active='/scores', heading='Accuracy',
                 sub='Documents stored in Postgres, compared field by field with known correct answers. The harness view is under Results.', actions=LEGEND)

# ---------- rules: the GoRules editor ----------

def render_rules() -> str:
    from rules.engine import RULES_FILE
    graph = RULES_FILE.read_text()
    body = ('<div class="actions" style="justify-content:space-between"><p class="note">'
            'Edit the tables (open a node with <b>Edit Table</b>), then <b>Save</b>. The file is checked with zen-engine before it is written; '
            'new documents and resends use it immediately.</p>'
            '<div class="actions"><span id="status" class="note"></span><button id="save" type="button">Save</button></div></div>'
            '<div class="editor" id="editor"></div>'
            f'<script type="application/json" id="graph">{escape(graph, quote=False).replace("</", "<\\/")}</script>'
            '<script src="/static/jdm-editor.js"></script>'
            '''<script>
(() => {
  const status = document.getElementById('status');
  let graph = JSON.parse(document.getElementById('graph').textContent);
  let dirty = false, touched = false;
  const editor = document.getElementById('editor');
  // The editor reports a change when it first renders; only count changes after the user acts.
  ['pointerdown', 'keydown'].forEach(t => editor.addEventListener(t, () => { touched = true; }, true));
  window.mountJdmEditor(editor, graph, v => { graph = v; if (touched) { dirty = true; status.textContent = 'Unsaved changes'; } });
  document.getElementById('save').addEventListener('click', async () => {
    status.textContent = 'Saving…';
    try {
      const r = await fetch('/rules/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(graph) });
      const out = await r.json();
      status.textContent = out.ok ? 'Saved ' + new Date().toLocaleTimeString() : 'Not saved: ' + out.error;
      if (out.ok) dirty = false;
    } catch (e) { status.textContent = 'Not saved: the server did not answer'; }
  });
  addEventListener('beforeunload', e => { if (dirty) e.preventDefault(); });
})();
</script>''')
    return _page('Rules', body, active='/rules', heading='Rules',
                 sub='rules/quote_flags.json in the GoRules editor: supplier currency on file, and the quote flags.',
                 head='<link rel="stylesheet" href="/static/jdm-editor.css">')

def save_rules(body: bytes) -> dict:
    from rules.engine import RULES_FILE, validate
    try:
        graph = json.loads(body)
        content = json.dumps(graph, indent=2) + '\n'
        validate(content)
    except Exception as e:
        # Rule-structure errors only; no document content is involved here.
        return {'ok': False, 'error': f'{type(e).__name__}: {str(e)[:300]}'}
    history = RULES_FILE.parent / '.history'
    history.mkdir(exist_ok=True)
    (history / f'quote_flags-{time.strftime("%Y%m%d-%H%M%S")}.json').write_text(RULES_FILE.read_text())
    RULES_FILE.write_text(content)
    return {'ok': True}

# ---------- upload and workflow status ----------

def render_upload(notice: str = '') -> str:
    form = ('<form method="post" action="/upload" enctype="multipart/form-data" class="formgrid">'
            '<label>Quote PDF<input type="file" id="file" name="file" accept="application/pdf,.pdf" required></label>'
            '<label>Model<select id="extractor" name="extractor"><option value="current">gpt-4.1-nano</option>'
            '<option value="frontier">gpt-4.1</option></select></label>'
            '<div><button type="submit">Upload and process</button></div></form>')
    body = (notice + f'<section class="panel"><header><h2>Upload a quote</h2></header><div class="body">{form}'
            '<p class="note" style="margin-top:10px">The PDF goes through the same Temporal workflow as emailed quotes. '
            'Use synthetic or cleared documents only.</p></div></section>')
    return _page('Upload', body, active='/upload', heading='Upload a PDF', sub='Process a document without email.')

def _segments_of(result: dict) -> list[dict]:
    """Quote segments from a QuoteWorkflow or an EmailWorkflow result."""
    if 'documents' in result:
        return [s for doc in result['documents'] for s in doc.get('segments', [])]
    return result.get('segments', [])

def render_status(workflow_id: str) -> str:
    from ui.actions import status, temporal_link
    try:
        state, result = status(workflow_id)
    except Exception as e:
        return _page('Processing', _notice(f'Could not reach Temporal ({escape(type(e).__name__)}). Is the dev server running?', True), heading='Processing')
    link = f'<a href="{escape(temporal_link(workflow_id))}" target="_blank" rel="noopener">Open in Temporal UI</a>'
    if state == 'RUNNING':
        return _page('Processing', _notice(f'Processing <span class="mono">{escape(workflow_id)}</span>: intake → parse → split → extract → ground → rules → save. '
                                           f'This page refreshes every 3 seconds. {link}'), heading='Processing…', refresh=3)
    if state == 'COMPLETED' and result is not None:
        unparseable = [d for d in result.get('documents', [result]) if d.get('status') in ('unparseable', 'not_a_quote')]
        segments = _segments_of(result)
        items = ''.join(f'<li><a class="mono" href="{_doc_link(s["segment_id"])}">{escape(s["segment_id"])}</a> '
                        f'pages {s["pages"][0]}–{s["pages"][1]}, {s["grounded"]} fields grounded'
                        f'{", flags: " + ", ".join(s["flags"]) if s.get("flags") else ""}</li>' for s in segments)
        skipped = ''.join(f'<li>Skipped: {escape(s.get("reason", ""))}</li>' for s in result.get('skipped', []))
        rejected = ''.join(f'<li>{"Not a quote" if d.get("status") == "not_a_quote" else "Not parsed"}: {escape(d.get("reason", ""))}</li>'
                           for d in unparseable)
        return _page('Processed', _notice(f'Done. {link}') + f'<section class="panel"><div class="body"><ul>{items}{skipped}{rejected}</ul></div></section>',
                     heading='Processed')
    return _page('Processing', _notice(f'Workflow ended with status {escape(state)}. {link}', True), heading='Processing')

def _form(body: bytes) -> dict:
    return {k: v[0] for k, v in parse_qs(body.decode('utf-8', 'replace')).items()}

def save_upload(content_type: str, body: bytes) -> tuple[Path, str, str]:
    """Parse a multipart upload; store the PDF by content hash. Returns (path, document_id, extractor)."""
    from ingest.email_intake import safe_filename
    from temporal_app.activities import STATE_DIR
    message = BytesParser(policy=email_policy).parsebytes(b'Content-Type: ' + content_type.encode() + b'\r\n\r\n' + body)
    data, extractor, filename = None, 'current', None
    if message.is_multipart():
        for part in message.iter_parts():
            name = part.get_param('name', header='content-disposition')
            if name == 'file':
                data, filename = part.get_payload(decode=True), part.get_filename()
            elif name == 'extractor':
                extractor = (part.get_payload(decode=True) or b'').decode().strip()
    if not data or not data.startswith(b'%PDF'):
        raise ValueError('Please choose a PDF file.')
    if extractor not in ('current', 'frontier'):
        extractor = 'current'
    digest = hashlib.sha256(data).hexdigest()
    target = Path(STATE_DIR) / 'uploads' / digest / safe_filename(filename or '', 'upload.pdf')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target, digest[:16], extractor

# ---------- server ----------

class Handler(BaseHTTPRequestHandler):
    database_url: str | None = None
    access_key: str | None = None  # set when reachable from the network

    def _allowed(self, args: dict) -> bool:
        if not self.access_key:
            return True
        import hmac
        cookie = next((c.split('=', 1)[1] for c in (self.headers.get('Cookie') or '').split('; ') if c.startswith('viewer_key=')), '')
        return hmac.compare_digest(args.get('key', ''), self.access_key) or hmac.compare_digest(cookie, self.access_key)

    def _same_origin(self) -> bool:
        origin = self.headers.get('Origin') or self.headers.get('Referer')
        return origin is None or urlparse(origin).netloc == self.headers.get('Host')

    def do_GET(self):
        from storage.postgres import connect
        url = urlparse(self.path)
        args = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        if not self._allowed(args):
            return self._send(403, _page('Access key needed', _notice('Open the link printed when the app started (it ends in <code>?key=…</code>).', True)))
        try:
            if url.path.startswith('/static/'):
                return self._static(url.path[len('/static/'):])
            if url.path == '/results':
                if 'running' in args:
                    progress = render_harness_progress()
                    if progress:
                        return self._send(200, progress)
                    return self._redirect('/results')
                return self._send(200, render_results(args))
            if url.path == '/rules':
                return self._send(200, render_rules())
            if url.path == '/upload':
                return self._send(200, render_upload())
            if url.path == '/status' and 'wf' in args:
                return self._send(200, render_status(args['wf']))
            if url.path == '/quote' and 'id' in args:  # old links
                return self._redirect(_doc_link(args['id'], args.get('field')))
            with connect(self.database_url) as conn:
                if url.path == '/':
                    return self._send(200, render_index(conn))
                if url.path == '/scores':
                    return self._send(200, render_scores(conn))
                m = re.fullmatch(r'/documents/([\w.-]+)/page/(\d+)\.png', url.path)
                if m:
                    d = evidence_detail(conn, m.group(1), args.get('run'))
                    pdf = _safe_file(d['file_path']) if d else None
                    png = page_png(pdf, int(m.group(2))) if pdf else None
                    return self._send(200, png, 'image/png') if png else self._send(404, b'', 'image/png')
                m = re.fullmatch(r'/documents/([\w.-]+)', url.path)
                if m:
                    notice = _notice(escape(args['msg'])) if args.get('msg') else ''
                    page = render_document(conn, m.group(1), args, notice)
                    return self._send(200, page) if page else self._send(404, _page('Not found', _notice('No such document (or that harness run kept no per-document results).', True)))
        except Exception as e:
            # Fixed text only: an exception message could carry document content.
            return self._send(500, _page('Error', _notice(f'Could not load this page ({escape(type(e).__name__)}).', True)))
        self._send(404, _page('Not found', _notice('Page not found.', True)))

    def do_POST(self):
        url = urlparse(self.path)
        if not self._allowed({}) or not self._same_origin():
            return self._send(403, _page('Not allowed', _notice('This action is not allowed from here.', True)))
        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_UPLOAD:
            return self._send(413, _page('Too large', _notice('Files must be under 25 MB.', True)))
        body = self.rfile.read(length)
        try:
            if url.path == '/results/run':
                extractor = _form(body).get('extractor', 'current')
                start_harness(extractor if extractor in ('current', 'frontier') else 'current')
                return self._redirect('/results?running=1')
            if url.path == '/rules/save':
                return self._send(200, json.dumps(save_rules(body)), 'application/json')
            if url.path == '/upload':
                from ui.actions import start_quote
                try:
                    path, document_id, extractor = save_upload(self.headers.get('Content-Type', ''), body)
                except ValueError as e:
                    return self._send(400, render_upload(_notice(escape(str(e)), True)))
                return self._redirect('/status?wf=' + quote(start_quote(document_id, str(path), extractor)))
            m = re.fullmatch(r'/documents/([\w.-]+)/(resend|reprocess)', url.path)
            if m:
                return self._resend(m.group(1)) if m.group(2) == 'resend' else self._reprocess(m.group(1))
        except Exception as e:
            return self._send(500, _page('Error', _notice(f'The action failed ({escape(type(e).__name__)}). Is the Temporal worker running?', True)))
        self._send(404, _page('Not found', _notice('Page not found.', True)))

    def _resend(self, segment_id: str):
        """Test documents only: run the original email (or file) through the whole workflow again as a new document."""
        from storage.postgres import connect
        from ui.actions import resend_email, start_quote
        with connect(self.database_url) as conn:
            q = quote_detail(conn, segment_id)
        if q is None:
            return self._send(404, _page('Not found', _notice('No such document.', True)))
        email = _safe_file(q['source_email'], ('.eml',))
        if email:
            return self._redirect('/status?wf=' + quote(resend_email(str(email), q['extractor'] or 'current')))
        pdf = _safe_file(q['file_path'])
        if pdf is None:
            return self._send(400, _page('Cannot resend', _notice('The original file is no longer available.', True)))
        new_id = f'{q["document_id"].split("-r")[0]}-r{int(time.time()) % 1_000_000:06d}'
        return self._redirect('/status?wf=' + quote(start_quote(new_id, str(pdf), q['extractor'] or 'current')))

    def _reprocess(self, segment_id: str):
        from storage.postgres import connect
        from ui.actions import reprocess
        with connect(self.database_url) as conn:
            q = quote_detail(conn, segment_id)
            if q is None:
                return self._send(404, _page('Not found', _notice('No such document.', True)))
            before = score_for(q)
        workflow_id, result = reprocess(segment_id, q['document_id'], q['extractor'])
        with connect(self.database_url) as conn:
            after = score_for(quote_detail(conn, segment_id))
        change = f' Accuracy {_pct(before["pct"])} → {_pct(after["pct"])}.' if before and after else ''
        flags = f' Flags: {", ".join(result.get("flags", [])) or "none"}.' if 'flags' in result else ''
        message = (f'Re-checked with current rules: {result["grounded"]} fields grounded, {len(result["ungrounded"])} ungrounded.'
                   f'{change}{flags} Temporal workflow {workflow_id}.')
        return self._redirect(_doc_link(segment_id, 'currency') + '&msg=' + quote(message))

    def _static(self, name: str):
        types = {'.js': 'application/javascript', '.css': 'text/css'}
        path = (STATIC / name).resolve()
        if not path.is_relative_to(STATIC.resolve()) or not path.is_file() or path.suffix not in types:
            return self._send(404, b'', 'text/plain')
        return self._send(200, path.read_bytes(), types[path.suffix], cache=True)

    def _redirect(self, location: str):
        self.send_response(303)
        self.send_header('Location', location)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _send(self, status: int, body, content_type: str = 'text/html; charset=utf-8', cache: bool = False):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'max-age=3600' if cache else 'no-store')
        if self.access_key:
            self.send_header('Set-Cookie', f'viewer_key={self.access_key}; HttpOnly; SameSite=Strict; Path=/')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass

def main():
    from storage.postgres import database_url
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--host', default='127.0.0.1', help='0.0.0.0 to allow other computers on your network')
    a = ap.parse_args()
    if not database_url():
        raise SystemExit('DATABASE_URL is not set: run with --env-file .env')
    server = ThreadingHTTPServer((a.host, a.port), Handler)
    if a.host in ('127.0.0.1', 'localhost'):
        print(f'{APP_NAME}: http://127.0.0.1:{a.port}  (Ctrl+C to stop)')
    else:
        import secrets, socket
        Handler.access_key = secrets.token_urlsafe(16)
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(('192.0.2.1', 9))  # no packet is sent; this only picks the outgoing interface
            lan_ip = probe.getsockname()[0]
        except OSError:
            lan_ip = '<this-computer-ip>'
        finally:
            probe.close()
        print(f'{APP_NAME}, reachable from your network. Open this link on the other computer:')
        print(f'  http://{lan_ip}:{a.port}/?key={Handler.access_key}')
        print('The key changes every time the app starts. Ctrl+C to stop.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()
