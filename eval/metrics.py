from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import re

from extract.normalise import UNAMBIGUOUS as SYMBOLS  # no global "$" alias: "$" alone is not a currency
ITEM = re.compile(r'line_items\[(\d+)\]\.(.+)')
FIELD_LABELS = {
    'supplier_name': 'Supplier', 'quote_reference': 'Quote reference', 'quote_date': 'Quote date',
    'valid_until': 'Valid until', 'currency': 'Currency', 'incoterms': 'Incoterms', 'payment_terms': 'Payment terms',
    'part_number_quoted': 'Part number', 'quantity': 'Quantity', 'unit_price': 'Unit price',
    'extended_price': 'Extended price', 'lead_time_days': 'Lead time (days)', 'moq': 'MOQ', 'stated_total': 'Stated total',
}

def normalize(value):
    if value is None: return None
    s = str(value).strip()
    if s in SYMBOLS: return SYMBOLS[s].casefold()
    s = s.casefold()
    try: return str(Decimal(s.replace(',', '')).normalize())
    except InvalidOperation: pass
    try: return date.fromisoformat(s).isoformat()
    except ValueError: pass
    return ' '.join(s.split())

def _flat(s: str) -> str:
    return ' '.join(s.replace('|', ' ').split())

def overlap(extracted, golden):
    """Spans are equal, or the extracted span covers at least 80% of the golden span's characters.

    Coverage, not similarity: "Currency: USD" covers the golden "USD" fully.
    """
    if not extracted or not golden: return False
    a, b = _flat(extracted), _flat(golden)
    if a == b or b in a: return True
    from difflib import SequenceMatcher
    covered = sum(block.size for block in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
    return covered >= 0.8 * len(b)

def same_place(pf: dict, gf: dict) -> bool:
    """Same page, and the golden bbox centre lies inside the extracted bbox (when both have one)."""
    if pf.get('page') is not None and gf.get('page') is not None and pf['page'] != gf['page']:
        return False
    pb, gb = pf.get('bbox'), gf.get('bbox')
    if not pb or not gb:
        return True
    cx, cy = (gb[0] + gb[2]) / 2, (gb[1] + gb[3]) / 2
    return pb[0] - 2 <= cx <= pb[2] + 2 and pb[1] - 2 <= cy <= pb[3] + 2

def _split(fields: list[dict]) -> tuple[dict, dict[int, dict]]:
    header, items = {}, defaultdict(dict)
    for f in fields:
        m = ITEM.fullmatch(f['name'])
        if m: items[int(m.group(1))][m.group(2)] = f
        else: header[f['name']] = f
    return header, dict(items)

def match_line_items(gold: dict[int, dict], pred: dict[int, dict]) -> dict[int, int]:
    """Golden item index -> predicted item index: by part number first, then position."""
    pn = lambda f: normalize((f.get('part_number_quoted') or {}).get('value'))
    by_part = {}
    for j, item in pred.items():
        if pn(item) is not None: by_part.setdefault(pn(item), j)
    mapping, used = {}, set()
    for i, item in gold.items():
        j = by_part.get(pn(item))
        if j is not None and j not in used:
            mapping[i] = j; used.add(j)
    free = [j for j in sorted(pred) if j not in used]
    for i in sorted(gold):
        if i in mapping: continue
        j = i if i in free else (free[0] if free else None)
        if j is not None:
            mapping[i] = j; free.remove(j)
    return mapping

def _score(gf: dict, pf: dict | None) -> dict:
    missing = pf is None or pf.get('value') is None
    grounded = bool(pf and pf.get('grounded') and pf.get('value') is not None)
    return {
        'correct': not missing and normalize(pf['value']) == normalize(gf['value']),
        # Traced to a real spot: the Ground step found the span on the page and attached a location.
        'grounded': grounded,
        # Traced to the same spot as the golden answer: span covers it and the location agrees.
        'evidence': grounded and overlap(pf.get('verbatim_span'), gf['verbatim_span']) and same_place(pf, gf),
        # The model claimed a value/span but the Ground step rejected it.
        'ungrounded': bool(pf) and not grounded and bool(pf.get('ungrounded_reason') or pf.get('ungrounded_span') or pf.get('verbatim_span')),
        'missing': missing,
        'reason': (pf or {}).get('ungrounded_reason'),
    }

def calculate(golden: list[dict], predicted: list[dict], latencies: list[float] | None = None):
    grouped = defaultdict(list)
    for gdoc, pdoc in zip(golden, predicted, strict=True):
        doc = gdoc.get('document_id')
        gh, gi = _split(gdoc['fields'])
        ph, pi = _split(pdoc.get('fields', []))
        for name, gf in gh.items():
            grouped[name].append({**_score(gf, ph.get(name)), 'doc': doc, 'name': name})
        mapping = match_line_items(gi, pi)
        for i, item in gi.items():
            j = mapping.get(i)
            pred_item = pi.get(j, {})
            for attr, gf in item.items():
                # 'name' is the stored field to open in the document view.
                grouped[f'line_items[i].{attr}'].append({**_score(gf, pred_item.get(attr)), 'doc': doc,
                                                         'name': f'line_items[{j if j is not None else i}].{attr}'})
    fields = {}
    for name, rows in grouped.items():
        n = len(rows)
        rate = lambda key: round(sum(r[key] for r in rows) / n, 4)
        reasons = defaultdict(int)
        for r in rows:
            if r['ungrounded'] and r['reason']: reasons[r['reason']] += 1
        # Worst document: the one where this field is most often wrong (None when it is never wrong).
        per_doc = defaultdict(list)
        for r in rows:
            per_doc[r['doc']].append(r)
        worst_doc, worst_rate = None, 1.0
        for doc, doc_rows in per_doc.items():
            doc_rate = sum(r['correct'] for r in doc_rows) / len(doc_rows)
            if doc is not None and doc_rate < worst_rate:
                worst_doc, worst_rate = doc, doc_rate
        worst_field = next((r['name'] for r in per_doc.get(worst_doc, []) if not r['correct']), None)
        key = name.split('.', 1)[-1] if name.startswith('line_items') else name
        fields[name] = {'label': FIELD_LABELS.get(key, key), 'accuracy': rate('correct'), 'grounded_rate': rate('grounded'),
                        'evidence_match_rate': rate('evidence'), 'ungrounded_rate': rate('ungrounded'),
                        'missing_rate': rate('missing'), 'count': n,
                        'ungrounded_count': sum(r['ungrounded'] for r in rows),
                        'ungrounded_reasons': dict(reasons),
                        'worst_doc': worst_doc, 'worst_doc_accuracy': round(worst_rate, 4) if worst_doc else None,
                        'worst_doc_field': worst_field}
    times = sorted(latencies or [])
    def percentile(p):
        if not times: return None
        return round(times[min(len(times)-1, max(0, int((len(times)-1)*p + 0.5)))], 3)
    ordered = dict(sorted(fields.items(), key=lambda kv: (kv[1]['accuracy'], kv[1]['grounded_rate'], kv[0])))
    return {'document_count': len(golden), 'fields': ordered,
            'worst_fields': list(ordered)[:3],
            'p50_latency_s': percentile(.5), 'p95_latency_s': percentile(.95)}
