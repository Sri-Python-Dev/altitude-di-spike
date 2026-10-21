"""Accuracy against known answers (golden files), never against the model's confidence.

A stored quote is scored only when a golden file exists for it, matched by the source
file's SHA-256 and the quote's position in the document, or by document ID.
"""
import json
import re
from pathlib import Path
from eval.metrics import match_line_items, normalize

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOTS = (ROOT / 'data/golden', ROOT / 'data/synthetic')
ITEM = re.compile(r'line_items\[(\d+)\]\.(.+)')
from eval.metrics import FIELD_LABELS  # noqa: E402  (one list of plain-English field names)

def band(pct: float | None) -> str:
    """good above 95%, fair 80–95%, poor below 80% (prompt 10)."""
    if pct is None:
        return 'none'
    return 'good' if pct > 0.95 else 'fair' if pct >= 0.8 else 'poor'

def golden_index() -> dict:
    """Golden files by document_id and by (sha256, segment). Rebuilt per call, so new files are seen."""
    index = {}
    for root in GOLDEN_ROOTS:
        for label in root.rglob('*.json'):
            if label.name == 'schema.json':
                continue
            try:
                g = json.loads(label.read_text())
                if g.get('expect') or not g.get('fields'):
                    continue
                index[g['document_id']] = g
                for sha in (g.get('sha256'), g.get('body_sha256')):
                    if sha:
                        index[(sha, g.get('segment', 1))] = g
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
    return index

def segment_number(segment_id: str) -> int:
    m = re.search(r'-quote-(\d+)$', segment_id)
    return int(m.group(1)) if m else 1

def find_golden(index: dict, document_id: str, segment_id: str, source_sha256: str | None) -> dict | None:
    return (index.get((source_sha256, segment_number(segment_id))) if source_sha256 else None) \
        or index.get(segment_id) or index.get(document_id)

def _split(fields: dict[str, dict]) -> tuple[dict, dict]:
    header, items = {}, {}
    for name, f in fields.items():
        m = ITEM.fullmatch(name)
        if m:
            items.setdefault(int(m.group(1)), {})[m.group(2)] = f
        else:
            header[name] = f
    return header, items

def score_quote(stored: dict[str, dict], golden: dict) -> dict:
    """Per-field correctness of stored values (name -> {'value'...}) against one golden file.

    Returns {'fields': {stored_or_golden_name: {...}}, 'correct', 'total', 'pct'}.
    A field counts as correct only if a grounded value was stored and it equals the known answer.
    """
    gold_fields = {f['name']: f for f in golden['fields']}
    gh, gi = _split(gold_fields)
    ph, pi = _split(stored)
    mapping = match_line_items(gi, {i: {k: {'value': v['value']} for k, v in item.items()} for i, item in pi.items()})
    results = {}

    def judge(gf: dict, pf: dict | None, display_name: str, field: str):
        got = pf['value'] if pf and pf.get('grounded') else None
        correct = got is not None and normalize(got) == normalize(gf['value'])
        results[display_name] = {'field': field, 'correct': correct, 'expected': gf['value'], 'got': got,
                                 'stored': got is not None, 'golden_span': gf.get('verbatim_span'),
                                 'golden_page': gf.get('page'), 'golden_bbox': gf.get('bbox')}

    for name, gf in gh.items():
        judge(gf, ph.get(name), name, name)
    for i, item in gi.items():
        j = mapping.get(i)
        for attr, gf in item.items():
            pf = pi.get(j, {}).get(attr) if j is not None else None
            # Show the mark on the stored line item the golden one was matched to.
            judge(gf, pf, f'line_items[{j if j is not None else i}].{attr}', attr)
    total = len(results)
    correct = sum(r['correct'] for r in results.values())
    return {'fields': results, 'correct': correct, 'total': total, 'pct': correct / total if total else None}

def by_field(scores: list[dict]) -> dict[str, dict]:
    """Accuracy per field type (part number, quantity, …) across several scored quotes."""
    out: dict[str, dict] = {}
    for s in scores:
        for r in s['fields'].values():
            entry = out.setdefault(r['field'], {'correct': 0, 'total': 0})
            entry['correct'] += r['correct']
            entry['total'] += 1
    for entry in out.values():
        entry['pct'] = entry['correct'] / entry['total'] if entry['total'] else None
    return dict(sorted(out.items(), key=lambda kv: (kv[1]['pct'] if kv[1]['pct'] is not None else 2, kv[0])))

def latest_reports(reports_dir: Path = ROOT / 'eval/reports') -> list[dict]:
    """The newest evaluation report per real extractor (smoke tests excluded)."""
    newest = {}
    for path in sorted(reports_dir.glob('*.json')):
        try:
            report = json.loads(path.read_text())
        except ValueError:
            continue
        if report.get('smoke_test') or 'fields' not in report:
            continue
        newest[report['extractor']] = report
    return list(newest.values())
