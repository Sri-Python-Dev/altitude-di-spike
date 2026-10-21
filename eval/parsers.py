"""Parser measurements against the golden set (prompt 06, step 2).

  uv run python -m eval.parsers --parser docling
  uv run --env-file .env python -m eval.parsers --parser azure_di      # needs AZURE_DI_ENDPOINT / AZURE_DI_KEY

Per document: character error rate against the golden spans, share of golden spans found
exactly, table cells recovered, bbox IoU for located spans, and parse latency (uncached).
Writes eval/reports/parsers-<parser>-<timestamp>.{json,md}. Step 3 (full pipeline per parser)
is the harness with --parser.
"""
import argparse, json, re, statistics, sys, time
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]

ITEM = re.compile(r'line_items\[(\d+)\]\.')
TABLE_COLUMNS = 6  # the golden quotes' line-item tables: part, qty, unit, extended, lead days, MOQ

def levenshtein(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]

def span_errors(span: str, text: str) -> int:
    """Edits needed to turn the closest stretch of text into the span (0 when it appears exactly)."""
    if span in text:
        return 0
    best = len(span)
    for length in {max(1, len(span) - 2), len(span), len(span) + 2}:
        for start in range(0, max(1, len(text) - length + 1)):
            window = text[start:start + length]
            if window[:1] != span[:1] and window[-1:] != span[-1:]:
                continue  # cheap prefilter: closest stretches share an end
            best = min(best, levenshtein(span, window))
            if best == 0:
                return 0
    return best

def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0

def measure_document(golden: dict, segment, parse_seconds: float) -> dict:
    from extract.ground import FIELD_LABELS, span_elements
    pages = {p.number: p for p in segment.pages}
    spans = [f for f in golden['fields'] if f.get('page')]
    exact = errors = chars = 0
    ious, rows = [], {}
    for field in sorted(spans, key=lambda f: not f['name'].endswith('part_number_quoted')):
        page = pages.get(field['page'])
        text = page.text if page else ''
        span = field['verbatim_span']
        exact += span in text
        errors += span_errors(span, text)
        chars += len(span)
        if page and span in text and field.get('bbox'):
            item = ITEM.match(field['name'])
            found = span_elements(span, page, rows.get(item.group(1)) if item else None, FIELD_LABELS.get(field['name'].rsplit('.', 1)[-1], ()))
            if found:
                if item and field['name'].endswith('part_number_quoted') and found[0].kind == 'cell':
                    rows[item.group(1)] = (found[0].table, found[0].row)
                box = (min(e.bbox[0] for e in found), min(e.bbox[1] for e in found), max(e.bbox[2] for e in found), max(e.bbox[3] for e in found))
                ious.append(iou(box, field['bbox']))
    line_items = len({m.group(1) for f in golden['fields'] if (m := ITEM.match(f['name']))})
    cells = sum(1 for p in segment.pages for e in p.elements if e.kind == 'cell')
    return {'document_id': golden['document_id'], 'spans': len(spans), 'exact_rate': round(exact / len(spans), 4) if spans else None,
            'cer': round(errors / chars, 4) if chars else None, 'cells_recovered': cells,
            'cells_expected': (line_items + 1) * TABLE_COLUMNS if line_items else 0,
            'mean_iou': round(statistics.mean(ious), 4) if ious else None, 'parse_s': round(parse_seconds, 2), 'ocr': segment.ocr}

def run(parser: str, out_dir: Path = ROOT / 'eval/reports') -> dict:
    from eval.run import parse_with
    from extract.boundaries import split_quotes
    from eval.datasets import golden_dataset, path
    golden = path(golden_dataset())
    rows, parsed_cache = [], {}
    for dataset in [golden, ROOT / 'data/synthetic/edge-cases']:
        for label in sorted(dataset.glob('*.json')):
            g = json.loads(label.read_text())
            source = dataset / g.get('source', g['document_id'] + '.pdf')
            if g.get('expect') or source.suffix != '.pdf' or not g.get('fields'):
                continue
            if source not in parsed_cache:
                started = time.perf_counter()
                parsed_cache[source] = (split_quotes(parse_with(parser, source)), time.perf_counter() - started)
            segments, seconds = parsed_cache[source]
            rows.append({**measure_document(g, segments[g.get('segment', 1) - 1], seconds), 'dataset': dataset.name})
    def mean(key, only=None):
        values = [r[key] for r in rows if r[key] is not None and (only is None or only(r))]
        return round(statistics.mean(values), 4) if values else None
    total_spans = sum(r['spans'] for r in rows)
    summary = {
        'documents': len(rows), 'spans': total_spans,
        'exact_rate': round(sum(r['exact_rate'] * r['spans'] for r in rows) / total_spans, 4) if total_spans else None,
        'cer': mean('cer'), 'cer_scanned': mean('cer', lambda r: r['ocr']),
        'cells_recovered': sum(r['cells_recovered'] for r in rows if r['dataset'] == golden.name),
        'cells_expected': sum(r['cells_expected'] for r in rows if r['dataset'] == golden.name),
        'mean_iou': mean('mean_iou'),
        'parse_p50_s': round(statistics.median(s for _, s in parsed_cache.values()), 2),
        'parse_max_s': round(max(s for _, s in parsed_cache.values()), 2),
    }
    stamp = datetime.now(timezone.utc)
    report = {'parser': parser, 'generated_at': stamp.isoformat(timespec='seconds'), 'summary': summary, 'documents': rows}
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f'parsers-{parser}-{stamp:%Y%m%dT%H%M%SZ}'
    base.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    lines = [f'# Parser measurements — {parser}', '', f"{summary['documents']} documents, {summary['spans']} golden spans.", '',
             '| Document | Spans found exactly | CER | Cells (found/expected) | Mean bbox IoU | Parse (s) |', '|---|---:|---:|---:|---:|---:|']
    lines += [f"| {r['document_id']} | {r['exact_rate']:.1%} | {r['cer']:.3f} | {r['cells_recovered']}/{r['cells_expected']} | "
              f"{r['mean_iou'] if r['mean_iou'] is not None else '—'} | {r['parse_s']} |" for r in rows]
    base.with_suffix('.md').write_text('\n'.join(lines) + '\n')
    return report

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--parser', choices=['docling', 'azure_di'], default='docling')
    a = ap.parse_args()
    if a.parser == 'azure_di':
        from parse.azure_di import configured
        if not configured():
            raise SystemExit('Azure Document Intelligence is not configured: set AZURE_DI_ENDPOINT and AZURE_DI_KEY in .env')
    print(json.dumps(run(a.parser)['summary'], indent=2))

if __name__ == '__main__':
    main()
