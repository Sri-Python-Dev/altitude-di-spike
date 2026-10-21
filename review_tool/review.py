"""Golden-set review tool: a person checks every known answer against the PDF and signs it off.

Standalone. Nothing in the pipeline imports this folder, and it imports nothing from the
pipeline: it reads and writes the golden JSON files and renders PDF pages (pypdfium2, Pillow).
Delete review_tool/ and the pipeline is unaffected.

  uv run python review_tool/review.py data/golden/quotes-v1 --reviewer "Srini"
  then open http://127.0.0.1:8770

For each quote: click a field to see its box on the page, correct the value if it is wrong,
tick it as checked (Enter does both and moves on), then "Save review". Saving records who
checked it and when, and logs every change inside the golden file itself.
"""
import argparse
import io
import json
import re
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

SCALE = 1.6
ITEM = re.compile(r'line_items\[(\d+)\]\.(.+)')
LABELS = {
    'supplier_name': 'Supplier', 'quote_reference': 'Quote reference', 'quote_date': 'Quote date',
    'valid_until': 'Valid until', 'currency': 'Currency (ISO code)', 'incoterms': 'Incoterms',
    'payment_terms': 'Payment terms', 'part_number_quoted': 'Part number', 'quantity': 'Quantity',
    'unit_price': 'Unit price', 'extended_price': 'Extended price', 'lead_time_days': 'Lead time (days)', 'moq': 'MOQ',
}
CHECKS = [
    'Right field: the quote date is not the valid-until date; the quote reference is not the RFQ number.',
    'Right number: quantities, prices and amounts exactly as printed.',
    'Units: lead time in days ("2 weeks" = 14). Currency as an ISO code (USD, not "$").',
    'Nothing missing or extra: every line item on the page has its fields; nothing the page does not state.',
]

# ---------- data ----------

def label_files(folder: Path) -> list[Path]:
    """Golden files that have answers to review (skips schema and expected-unparseable entries)."""
    out = []
    for path in sorted(folder.glob('*.json')):
        try:
            data = json.loads(path.read_text())
        except ValueError:
            continue
        if isinstance(data, dict) and data.get('fields') and not data.get('expect'):
            out.append(path)
    return out

def source_pdf(path: Path, data: dict) -> Path:
    return path.parent / data.get('source', data['document_id'] + '.pdf')

def is_reviewed(data: dict) -> bool:
    return bool((data.get('metadata') or {}).get('human_reviewed'))

def _coerce(original, text: str):
    """Keep numbers as numbers when the reviewer types a number."""
    text = text.strip()
    if isinstance(original, (int, float)) and not isinstance(original, bool):
        try:
            number = float(text.replace(',', ''))
            return int(number) if number.is_integer() and isinstance(original, int) else number
        except ValueError:
            return text
    return text

def apply_review(path: Path, reviewer: str, decisions: list[dict], notes: str = '', now: str | None = None) -> dict:
    """Write a review into the golden file.

    decisions: one per field, {'name', 'value' (as typed), 'remove': bool, 'checked': bool}.
    Every remaining field must be checked. Changed values and removed fields are logged in
    metadata.review_changes. Returns a summary.
    """
    data = json.loads(path.read_text())
    now = now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    by_name = {d['name']: d for d in decisions}
    fields = data['fields']
    missing = [f['name'] for f in fields if f['name'] not in by_name]
    unchecked = [d['name'] for d in decisions if not d.get('remove') and not d.get('checked')]
    if missing or unchecked:
        raise ValueError('Every field must be checked before saving: ' + ', '.join((missing + unchecked)[:5]))
    changes, kept = [], []
    for field in fields:
        decision = by_name[field['name']]
        if decision.get('remove'):
            changes.append({'field': field['name'], 'change': 'removed', 'from': field['value']})
            continue
        new_value = _coerce(field['value'], str(decision.get('value', '')))
        if new_value != field['value'] and str(new_value) != str(field['value']):
            changes.append({'field': field['name'], 'change': 'value', 'from': field['value'], 'to': new_value})
            field['value'] = new_value
        field['labeller'] = reviewer
        field['labelled_at'] = now
        kept.append(field)
    data['fields'] = kept
    meta = data.setdefault('metadata', {})
    previous = {f.get('labeller') for f in json.loads(path.read_text())['fields']} - {reviewer}
    if previous and 'original_labeller' not in meta:
        meta['original_labeller'] = sorted(previous)[0] if len(previous) == 1 else sorted(previous)
    meta['human_reviewed'] = True
    meta['reviewed_by'] = reviewer
    meta['reviewed_at'] = now
    meta.setdefault('review_changes', []).extend({**c, 'by': reviewer, 'at': now} for c in changes)
    if notes.strip():
        meta.setdefault('review_notes', []).append({'by': reviewer, 'at': now, 'note': notes.strip()})
    path.write_text(json.dumps(data, indent=2) + '\n')
    return {'changed': sum(c['change'] == 'value' for c in changes), 'removed': sum(c['change'] == 'removed' for c in changes),
            'fields': len(kept)}

def page_png(pdf: Path, number: int) -> bytes:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    try:
        image = doc[number - 1].render(scale=SCALE).to_pil().convert('RGB')
    finally:
        doc.close()
    out = io.BytesIO()
    image.save(out, 'PNG')
    return out.getvalue()

def page_sizes(pdf: Path) -> list[tuple[float, float]]:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    try:
        return [tuple(p.get_size()) for p in doc]
    finally:
        doc.close()

# ---------- pages ----------

STYLE = '''
:root { --bg:#f3f5f8; --surface:#fff; --ink:#14202e; --muted:#5a6675; --rule:#dfe4ea; --accent:#1d5c96; --soft:#e6eef7;
  --good:#146c43; --good-soft:#dff3e8; --warn:#9a3412; --warn-soft:#fdebe2; --box:#1c7ed6;
  --sans:"IBM Plex Sans","Helvetica Neue",Arial,sans-serif; --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace; }
@media (prefers-color-scheme: dark) { :root { --bg:#0e141b; --surface:#151d27; --ink:#e4e9ef; --muted:#98a4b2; --rule:#253141;
  --accent:#7fb2e5; --soft:#1b2b3d; --good:#5fd39a; --good-soft:#12301f; --warn:#ff9b73; --warn-soft:#3a2018; --box:#4dabf7; color-scheme:dark; } }
* { box-sizing:border-box } body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.5 var(--sans) }
a { color:var(--accent); text-decoration:none } a:hover { text-decoration:underline }
header { background:var(--surface); border-bottom:1px solid var(--rule); padding:12px 24px; display:flex; gap:16px; align-items:center; flex-wrap:wrap; position:sticky; top:0; z-index:5 }
header h1 { font-size:16px; margin:0 } .muted { color:var(--muted) } .mono { font-family:var(--mono); font-size:12.5px }
main { padding:18px 24px 48px; max-width:1500px; margin:0 auto }
table { border-collapse:collapse; width:100%; background:var(--surface); border:1px solid var(--rule) }
th, td { text-align:left; padding:8px 12px; border-bottom:1px solid var(--rule) } th { font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em }
.tag { display:inline-block; padding:1px 8px; border-radius:3px; font-size:12px; font-weight:500 }
.tag.ok { background:var(--good-soft); color:var(--good) } .tag.todo { background:var(--warn-soft); color:var(--warn) }
.layout { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:18px; align-items:start }
.pages { position:sticky; top:70px; max-height:calc(100vh - 90px); overflow:auto; background:var(--surface); border:1px solid var(--rule); border-radius:6px }
.pg { position:relative } .pg img { display:block; width:100% }
.box { position:absolute; border:2.5px solid var(--box); background:color-mix(in srgb, var(--box) 15%, transparent); display:none; pointer-events:none }
.panel { background:var(--surface); border:1px solid var(--rule); border-radius:6px }
.checks { margin:0; padding:12px 18px 12px 34px; color:var(--muted); font-size:12.5px; border-bottom:1px solid var(--rule) }
.row { display:grid; grid-template-columns:22px 150px minmax(0,1fr) auto; gap:10px; align-items:center; padding:8px 14px; border-bottom:1px solid var(--rule); cursor:pointer }
.row.active { background:var(--soft); box-shadow:inset 3px 0 0 var(--accent) } .row.removed { opacity:.45 }
.row .lab { font-weight:500 } .row .lab small { display:block; font:11px var(--mono); color:var(--muted); overflow-wrap:anywhere }
.row input[type=text] { width:100%; font:14px var(--sans); padding:5px 8px; border:1px solid var(--rule); border-radius:4px; background:var(--surface); color:var(--ink) }
.row .span { grid-column:3 / 5; font:12px var(--mono); color:var(--muted) } .row .span b { background:#fff0a3; color:#14202e; font-weight:400; padding:0 3px }
.row.changed input[type=text] { border-color:var(--warn); background:var(--warn-soft) }
.group { padding:8px 14px; background:var(--bg); border-bottom:1px solid var(--rule); font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted) }
footer.bar { display:flex; gap:10px; align-items:center; padding:12px 14px; flex-wrap:wrap }
button { font:500 13px var(--sans); padding:7px 14px; border-radius:4px; border:1px solid var(--accent); background:var(--accent); color:#fff; cursor:pointer }
button.secondary { background:transparent; color:var(--accent) } button:disabled { opacity:.5; cursor:not-allowed }
textarea { width:100%; font:13px var(--sans); padding:6px 8px; border:1px solid var(--rule); border-radius:4px; background:var(--surface); color:var(--ink) }
.notice { padding:10px 14px; border-radius:4px; background:var(--soft); margin-bottom:14px } .notice.error { background:var(--warn-soft) }
@media (max-width:1000px) { .layout { grid-template-columns:1fr } .pages { position:static; max-height:none } }
'''

def _page(title: str, body: str, reviewer: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{escape(title)} · Golden review</title><style>{STYLE}</style></head><body>'
            f'<header><h1><a href="/">Golden review</a></h1><span class="muted">Reviewer: <b>{escape(reviewer)}</b></span></header>'
            f'<main>{body}</main></body></html>')

def render_index(folder: Path, reviewer: str, message: str = '') -> str:
    rows, done = [], 0
    for path in label_files(folder):
        data = json.loads(path.read_text())
        meta = data.get('metadata') or {}
        reviewed = is_reviewed(data)
        done += reviewed
        state = (f'<span class="tag ok">reviewed by {escape(str(meta.get("reviewed_by")))}</span>' if reviewed
                 else '<span class="tag todo">to review</span>')
        changes = len(meta.get('review_changes', []))
        rows.append(f'<tr><td><a href="/quote?id={quote(path.stem)}">{escape(data["document_id"])}</a></td>'
                    f'<td>{len(data["fields"])}</td><td>{state}</td><td>{changes or ""}</td></tr>')
    notice = f'<div class="notice">{escape(message)}</div>' if message else ''
    body = (notice + f'<p>{done} of {len(rows)} quotes reviewed in <span class="mono">{escape(str(folder))}</span>.</p>'
            '<table><thead><tr><th>Quote</th><th>Fields</th><th>Status</th><th>Changes made</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')
    return _page('Quotes', body, reviewer)

def render_quote(folder: Path, reviewer: str, stem: str, message: str = '') -> str | None:
    path = folder / f'{stem}.json'
    if not re.fullmatch(r'[\w.-]+', stem) or path not in label_files(folder):
        return None
    data = json.loads(path.read_text())
    pdf = source_pdf(path, data)
    sizes = page_sizes(pdf) if pdf.suffix == '.pdf' and pdf.exists() else []
    pages = ''.join(f'<div class="pg" data-page="{i}" data-w="{w}" data-h="{h}"><img alt="Page {i}" src="/page.png?id={quote(stem)}&n={i}"></div>'
                    for i, (w, h) in enumerate(sizes, 1)) or '<p class="muted" style="padding:16px">No PDF page to show (email body).</p>'
    def row(f):
        m = ITEM.fullmatch(f['name'])
        label = LABELS.get(m.group(2) if m else f['name'], f['name'])
        bbox = ','.join(map(str, f.get('bbox') or []))
        return (f'<div class="row" data-name="{escape(f["name"])}" data-page="{f.get("page") or ""}" data-bbox="{bbox}" data-original="{escape(str(f["value"]))}">'
                f'<input type="checkbox" class="ok" title="Checked against the page" aria-label="Checked">'
                f'<div class="lab">{escape(label)}<small>{escape(f["name"])}</small></div>'
                f'<input type="text" class="val" value="{escape(str(f["value"]))}" aria-label="Value">'
                f'<label class="muted" style="font-size:12px"><input type="checkbox" class="rm"> not on page</label>'
                f'<div class="span">from <b>{escape(f.get("verbatim_span") or "")}</b> · page {f.get("page")}</div></div>')
    header = ''.join(row(f) for f in data['fields'] if not ITEM.fullmatch(f['name']))
    items: dict[int, list] = {}
    for f in data['fields']:
        m = ITEM.fullmatch(f['name'])
        if m:
            items.setdefault(int(m.group(1)), []).append(f)
    item_html = ''.join(f'<div class="group">Line item {i + 1}</div>' + ''.join(row(f) for f in fs) for i, fs in sorted(items.items()))
    meta = data.get('metadata') or {}
    status = (f'Reviewed by {escape(str(meta.get("reviewed_by")))} at {escape(str(meta.get("reviewed_at")))}. You can review it again.'
              if is_reviewed(data) else 'Not reviewed yet.')
    files = label_files(folder)
    index = files.index(path)
    nxt = f'<a href="/quote?id={quote(files[index + 1].stem)}">Next quote →</a>' if index + 1 < len(files) else ''
    notice = f'<div class="notice{" error" if message.startswith("Not saved") else ""}">{escape(message)}</div>' if message else ''
    body = (notice + f'<p><a href="/">All quotes</a> · <b>{escape(data["document_id"])}</b> · {status} {nxt}</p>'
            f'<div class="layout"><div class="pages" id="pages">{pages}</div><div class="panel">'
            f'<ul class="checks">{"".join(f"<li>{escape(c)}</li>" for c in CHECKS)}</ul>'
            f'<div class="group">Header</div>{header}{item_html}'
            '<div style="padding:12px 14px"><textarea id="notes" rows="2" placeholder="Notes (optional): anything a second reviewer should know"></textarea></div>'
            f'<footer class="bar"><button id="save" disabled>Save review</button><span class="muted" id="count"></span></footer></div></div>'
            f'<form id="form" method="post" action="/save?id={quote(stem)}"><input type="hidden" name="payload" id="payload"></form>'
            '''<script>
(() => {
  const rows = [...document.querySelectorAll('.row')], pane = document.getElementById('pages');
  const box = document.createElement('div'); box.className = 'box';
  const save = document.getElementById('save'), count = document.getElementById('count');
  function refresh() {
    const left = rows.filter(r => !r.querySelector('.rm').checked && !r.querySelector('.ok').checked).length;
    save.disabled = left > 0;
    count.textContent = left ? left + ' field(s) still to check' : 'All fields checked';
    rows.forEach(r => { r.classList.toggle('removed', r.querySelector('.rm').checked);
      r.classList.toggle('changed', r.querySelector('.val').value.trim() !== r.dataset.original); });
  }
  function show(r) {
    rows.forEach(x => x.classList.toggle('active', x === r));
    const pg = pane.querySelector('.pg[data-page="' + r.dataset.page + '"]'), b = r.dataset.bbox.split(',').map(Number);
    if (!pg || b.length !== 4) { box.style.display = 'none'; return; }
    const w = +pg.dataset.w, h = +pg.dataset.h, pad = 3; pg.appendChild(box);
    Object.assign(box.style, { display: 'block', left: (b[0]-pad)/w*100 + '%', top: (b[1]-pad)/h*100 + '%',
      width: (b[2]-b[0]+2*pad)/w*100 + '%', height: (b[3]-b[1]+2*pad)/h*100 + '%' });
    const top = box.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop - 120;
    pane.scrollTo({ top, behavior: 'smooth' });
  }
  rows.forEach((r, i) => {
    r.addEventListener('click', e => { if (e.target.tagName !== 'INPUT') r.querySelector('.val').focus(); show(r); });
    r.querySelector('.val').addEventListener('focus', () => show(r));
    r.querySelector('.val').addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); r.querySelector('.ok').checked = true; refresh();
        const next = rows[i + 1]; if (next) next.querySelector('.val').focus(); else save.focus(); }
    });
    r.querySelectorAll('input').forEach(x => x.addEventListener('input', refresh));
  });
  save.addEventListener('click', () => {
    document.getElementById('payload').value = JSON.stringify({ notes: document.getElementById('notes').value,
      decisions: rows.map(r => ({ name: r.dataset.name, value: r.querySelector('.val').value,
        remove: r.querySelector('.rm').checked, checked: r.querySelector('.ok').checked })) });
    document.getElementById('form').submit();
  });
  refresh(); if (rows[0]) rows[0].querySelector('.val').focus();
})();
</script>''')
    return _page(data['document_id'], body, reviewer)

# ---------- server ----------

def make_handler(folder: Path, reviewer: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            args = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == '/':
                return self._send(200, render_index(folder, reviewer, args.get('msg', '')))
            if url.path == '/quote':
                page = render_quote(folder, reviewer, args.get('id', ''), args.get('msg', ''))
                return self._send(200, page) if page else self._send(404, 'No such quote.')
            if url.path == '/page.png':
                stem = args.get('id', '')
                path = folder / f'{stem}.json'
                if re.fullmatch(r'[\w.-]+', stem) and path in label_files(folder) and args.get('n', '').isdigit():
                    pdf = source_pdf(path, json.loads(path.read_text()))
                    if pdf.suffix == '.pdf' and pdf.exists():
                        return self._send(200, page_png(pdf, int(args['n'])), 'image/png')
                return self._send(404, b'', 'image/png')
            self._send(404, 'Not found.')

        def do_POST(self):
            url = urlparse(self.path)
            origin = self.headers.get('Origin')
            if origin and urlparse(origin).netloc != self.headers.get('Host'):
                return self._send(403, 'Not allowed.')
            stem = parse_qs(url.query).get('id', [''])[0]
            path = folder / f'{stem}.json'
            if url.path != '/save' or not re.fullmatch(r'[\w.-]+', stem) or path not in label_files(folder):
                return self._send(404, 'Not found.')
            body = self.rfile.read(int(self.headers.get('Content-Length') or 0)).decode()
            try:
                payload = json.loads(parse_qs(body)['payload'][0])
                summary = apply_review(path, reviewer, payload['decisions'], payload.get('notes', ''))
            except (ValueError, KeyError) as e:
                return self._redirect(f'/quote?id={quote(stem)}&msg=' + quote(f'Not saved: {e}'))
            message = (f'{stem} saved: {summary["fields"]} fields signed off by {reviewer}; '
                       f'{summary["changed"]} value(s) corrected, {summary["removed"]} removed.')
            files = label_files(folder)
            todo = [p for p in files if not is_reviewed(json.loads(p.read_text()))]
            target = f'/quote?id={quote(todo[0].stem)}' if todo else '/'
            self._redirect(target + ('&' if '?' in target else '?') + 'msg=' + quote(message))

        def _redirect(self, location):
            self.send_response(303)
            self.send_header('Location', location)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def _send(self, status, body, content_type='text/html; charset=utf-8'):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):
            pass
    return Handler

def main():
    ap = argparse.ArgumentParser(description='Review golden answers against the PDFs.')
    ap.add_argument('folder', type=Path, help='a golden folder, e.g. data/golden/quotes-v1')
    ap.add_argument('--reviewer', required=True, help='your name, recorded on every field you sign off')
    ap.add_argument('--port', type=int, default=8770)
    a = ap.parse_args()
    if not label_files(a.folder):
        raise SystemExit(f'No golden files with answers in {a.folder}')
    server = ThreadingHTTPServer(('127.0.0.1', a.port), make_handler(a.folder.resolve(), a.reviewer.strip()))
    print(f'Golden review for {a.folder} as {a.reviewer}: http://127.0.0.1:{a.port}  (Ctrl+C to stop)')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()
