"""Fail if any golden span cannot be found, or its bbox is not where the span is.

Run: uv run python -m tools.validate_golden data/golden/quotes-v1
     uv run python -m tools.validate_golden data/synthetic/edge-cases --expect 0
Edge-case golden files may name a source (.pdf/.eml), a segment, or expect: unparseable.
"""
import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ''):  # run as a script (`uv run tools/validate_golden.py`): make the repo root importable
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
import argparse, hashlib, json, re, sys
from datetime import date
from pathlib import Path
from eval.run import load_segments
from extract.errors import UnparseableDocument
from extract.ground import FIELD_LABELS, locate_span, span_elements

BBOX_TOLERANCE = 2.0  # points

def _inside(inner, outer) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return (outer[0] - BBOX_TOLERANCE <= cx <= outer[2] + BBOX_TOLERANCE
            and outer[1] - BBOX_TOLERANCE <= cy <= outer[3] + BBOX_TOLERANCE)

def _item_index(name: str) -> int | None:
    m = re.match(r'line_items\[(\d+)\]\.', name)
    return int(m.group(1)) if m else None

def check_document(obj: dict, parsed, fix: bool = False) -> list[str]:
    """Return problems as 'field: code' strings (never span text).

    With fix=True, a bbox that is not on the span's layout element is replaced by
    that element's bbox; value and span are never changed.
    """
    problems = []
    pages = {p.number: p for p in parsed.pages}
    rows = {}
    for field in obj['fields']:
        if field['name'].endswith('.part_number_quoted'):
            page = pages.get(field['page'])
            found = span_elements(field['verbatim_span'], page) if page else []
            if found and found[0].kind == 'cell':
                rows[_item_index(field['name'])] = (found[0].table, found[0].row)
    for field in obj['fields']:
        page = pages.get(field['page'])
        if not page or not locate_span(field['verbatim_span'], page):
            problems.append(f"{field['name']}: unlocated span")
            continue
        if page.number == 0:
            # Email body: no layout, so the bbox must be empty.
            if field['bbox'] is not None:
                problems.append(f"{field['name']}: email body field has a bbox")
            continue
        found = span_elements(field['verbatim_span'], page, rows.get(_item_index(field['name'])), FIELD_LABELS.get(field['name'].rsplit('.', 1)[-1], ()))
        if not found:
            problems.append(f"{field['name']}: no layout element")
        elif field['bbox'] is None:
            problems.append(f"{field['name']}: missing bbox")
        elif not any(_inside(field['bbox'], e.bbox) for e in found):
            if fix:
                b = found[0].bbox if len(found) == 1 else (min(e.bbox[0] for e in found), min(e.bbox[1] for e in found), max(e.bbox[2] for e in found), max(e.bbox[3] for e in found))
                field['bbox'] = list(b)
                field['bbox_corrected'] = f'{date.today().isoformat()} tools.validate_golden --fix-bboxes'
            else:
                problems.append(f"{field['name']}: bbox not on the span's layout element")
    return problems

def validate(folder: Path, fix: bool = False):
    labels = sorted(folder.glob('*.json'))
    if not labels:
        raise ValueError('No golden JSON files found')
    failures = []
    for label in labels:
        try:
            obj = json.loads(label.read_text())
            source = folder / obj.get('source', obj['document_id'] + '.pdf')
            if not source.exists(): raise ValueError('source document missing')
            if hashlib.sha256(source.read_bytes()).hexdigest() != obj['sha256']: raise ValueError('SHA mismatch')
            if obj.get('expect') == 'unparseable':
                try:
                    load_segments(source)
                    failures.append(f'{label.name}: expected unparseable, but it parsed')
                except UnparseableDocument as e:
                    if obj.get('reason') not in (None, e.reason):
                        failures.append(f"{label.name}: expected {obj['reason']}, got {e.reason}")
                continue
            segments = load_segments(source)
            if len(segments) != obj.get('segment_count', len(segments)):
                failures.append(f"{label.name}: expected {obj['segment_count']} quotes, split into {len(segments)}")
                continue
            before = json.dumps(obj)
            failures += [f'{label.name}: {p}' for p in check_document(obj, segments[obj.get('segment', 1) - 1], fix)]
            if fix and json.dumps(obj) != before:
                label.write_text(json.dumps(obj, indent=2) + '\n')
        except Exception as e:
            failures.append(f'{label.name}: {e}')
    return len(labels), failures

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('dataset', type=Path)
    ap.add_argument('--expect', type=int, default=0, help='required document count (default: any; task 01 uses 20)')
    ap.add_argument('--fix-bboxes', action='store_true', help="move misplaced golden bboxes onto the span's layout element")
    args = ap.parse_args()
    count, failures = validate(args.dataset, args.fix_bboxes)
    for f in failures: print('FAIL', f)
    print(f'Validated {count} documents, {len(failures)} failures')
    return 1 if failures or (args.expect and count != args.expect) else 0

if __name__ == '__main__': sys.exit(main())
