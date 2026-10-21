"""Show where an extracted value came from: page, bbox, verbatim span, and a PNG
of the page with the span outlined.

  # a document already processed by the workflow (segment id from its result)
  uv run python -m tools.where --segment quote_004 --field line_items[0].unit_price
  # or run parse + extract + ground on the spot
  uv run python -m tools.where data/golden/quotes-v1/quote_004.pdf --extractor current --field currency
  # list every field and whether it is grounded
  uv run python -m tools.where --segment quote_004 --list
  # read from Postgres (when local state was cleared)
  uv run --env-file .env python -m tools.where --segment quote_004 --field currency

Prints document text to your terminal only; nothing is logged.
"""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'out/where'

def from_state(segment_id: str):
    from extract.schemas import SupplierQuote
    from temporal_app.activities import STATE_DIR
    grounded = STATE_DIR / 'extractions' / f'{segment_id}.grounded.json'
    if not grounded.exists():
        return from_postgres(segment_id)
    source = None
    for status in (STATE_DIR / 'status').glob('*.json'):
        s = json.loads(status.read_text())
        if segment_id in s.get('segments', []):
            source = s.get('file_path')
    return SupplierQuote.model_validate_json(grounded.read_text()), source

def from_postgres(segment_id: str):
    from storage.postgres import connect, database_url, load_quote
    if not database_url():
        sys.exit(f'Segment {segment_id} is not in local state and DATABASE_URL is not set (run with --env-file .env)')
    with connect() as conn:
        stored = load_quote(conn, segment_id)
    if stored is None:
        sys.exit(f'Segment {segment_id} is not in local state or Postgres')
    return stored

def from_pdf(pdf: Path, extractor: str, segment: int):
    from extract.boundaries import split_quotes
    from extract.pipeline import extract_quote
    from parse.docling_parser import parse_pdf
    segments = split_quotes(parse_pdf(pdf))
    return extract_quote(segments[segment - 1], extractor), str(pdf)

def render_highlight(pdf: str, page_number: int, bbox, scale: float = 2.0):
    """The PDF page as a PIL image, with bbox (PDF points, top-left origin) outlined. bbox may be None."""
    import pypdfium2 as pdfium
    from PIL import ImageDraw
    doc = pdfium.PdfDocument(pdf)
    try:
        image = doc[page_number - 1].render(scale=scale).to_pil().convert('RGB')
    finally:
        doc.close()
    if bbox:
        draw = ImageDraw.Draw(image, 'RGBA')
        x0, y0, x1, y1 = (v * scale for v in bbox)
        pad = 3 * scale
        draw.rectangle((x0 - pad, y0 - pad, x1 + pad, y1 + pad), outline=(220, 30, 30, 255), width=int(2 * scale), fill=(255, 220, 0, 70))
    return image

def highlight(pdf: str, page_number: int, bbox, out: Path, scale: float = 2.0) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    render_highlight(pdf, page_number, bbox, scale).save(out)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf', nargs='?', type=Path)
    ap.add_argument('--segment', help='segment id from a workflow result')
    ap.add_argument('--extractor', default='current')
    ap.add_argument('--quote', type=int, default=1, help='quote number within the PDF (multi-quote PDFs)')
    ap.add_argument('--field')
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args()
    if bool(a.pdf) == bool(a.segment):
        ap.error('give either a PDF or --segment')
    quote, source = from_state(a.segment) if a.segment else from_pdf(a.pdf, a.extractor, a.quote)
    if a.list or not a.field:
        for name, f in quote.fields():
            mark = '✔ grounded' if f.grounded else (f'✘ {f.ungrounded_reason}' if f.ungrounded_reason else '· absent')
            print(f'{name:40} {mark:28} page {f.page}  value {f.value!r}')
        return
    fields = dict(quote.fields())
    if a.field not in fields:
        sys.exit(f'Unknown field {a.field}. Use --list to see field names.')
    f = fields[a.field]
    print(f'field:     {a.field}')
    if not f.grounded:
        print(f'value:     None (not stored)')
        print(f'grounded:  NO — {f.ungrounded_reason or "absent"}')
        print(f'model span (debug only): {f.ungrounded_span!r}')
        return
    print(f'value:     {f.value!r}')
    print(f'page:      {f.page}' + ('  (email body)' if f.page == 0 else ''))
    print(f'bbox:      {f.bbox}  (PDF points, top-left origin)')
    print(f'span:      {f.verbatim_span!r}')
    if f.page and f.bbox and source and source.endswith('.pdf'):
        name = (a.segment or Path(source).stem) + '-' + a.field.replace('[', '_').replace('].', '_') + '.png'
        print(f'image:     {highlight(source, f.page, f.bbox, OUT / name)}')

if __name__ == '__main__':
    main()
