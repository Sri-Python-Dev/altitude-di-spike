"""Field-by-field evaluation against golden answers.

Run: uv run python -m eval.run --dataset data/golden/quotes-v1 --extractor current
     (repeat --dataset to include data/synthetic/edge-cases and data/synthetic)
Writes eval/reports/<run_id>.{json,md}, eval/reports/latest.json (real runs only) and the
per-document results under eval/reports/runs/<run_id>/.

Optional golden keys for edge cases (see data/golden/schema.json):
  source   file in the same folder to parse (.pdf or .eml); default <document_id>.pdf
  segment  1-based quote index when the source holds several quotes
  expect   "unparseable" with "reason" for documents that must be rejected
"""
import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ''):  # run as a script (`uv run eval/run.py`): make the repo root importable
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
import argparse, hashlib, json, os, shlex, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from eval.metrics import calculate
from extract.boundaries import split_quotes
from extract.errors import CorruptDocument, PasswordProtectedDocument, UnparseableDocument
from extract.extractors import MODEL_TASKS
from extract.ground import ground
from extract.pipeline import extract_raw
from llm.router import model_prices, track_usage

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / 'eval/reports'
CACHE_DIR = ROOT / 'eval/cache/parsed'
PARSE_CACHE_VERSION = 'v1'  # bump when the parser or splitter changes, so cached parses are redone
WORKERS = int(os.getenv('EVAL_WORKERS', '8'))
SMOKE_TEST_EXTRACTORS = {'golden-echo'}
ERRORS = {cls.reason: cls for cls in (PasswordProtectedDocument, CorruptDocument, UnparseableDocument)}

def parse_with(parser: str, source: Path):
    """A PDF through the named parser: docling (local) or azure_di (dev resource; needs AZURE_DI_*)."""
    if parser == 'azure_di':
        from parse.azure_di import parse_azure
        return parse_azure(source)
    from parse.docling_parser import parse_pdf
    return parse_pdf(source)

def _parse(source: Path, parser: str = 'docling') -> list:
    if source.suffix == '.eml':
        from ingest.email_intake import read_eml
        from extract.body import parse_email_body
        message = read_eml(source)
        parsed = parse_email_body(source.stem, message.body, html=message.body_is_html)
    else:
        parsed = parse_with(parser, source)
    return split_quotes(parsed)

def load_segments(source: Path, cache_dir: Path | None = CACHE_DIR, parser: str = 'docling') -> list:
    """Parsed quote segments for a source file, cached by content hash so re-runs skip parsing.
    Raises UnparseableDocument (also from the cache) for rejected files."""
    from parse.models import ParsedDocument
    suffix = '' if parser == 'docling' else f'-{parser}'
    cached = cache_dir / f'{hashlib.sha256(source.read_bytes()).hexdigest()}-{PARSE_CACHE_VERSION}{suffix}.json' if cache_dir else None
    if cached and cached.exists():
        data = json.loads(cached.read_text())
        if 'unparseable' in data:
            raise ERRORS.get(data['unparseable'], UnparseableDocument)()
        return [ParsedDocument.model_validate(s) for s in data['segments']]
    try:
        segments = _parse(source, parser)
    except UnparseableDocument as e:
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps({'unparseable': e.reason}))
        raise
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps({'segments': [s.model_dump(mode='json') for s in segments]}))
    return segments

def _git_commit() -> str | None:
    try:
        out = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None

def _parser_name(parser: str = 'docling') -> str:
    if parser == 'azure_di':
        return 'azure-di prebuilt-layout'
    try:
        from importlib.metadata import version
        return f"docling {version('docling')}"
    except Exception:
        return 'docling'

def _previous_run(out_dir: Path, extractor: str, datasets: list[str], before: str, parser: str | None = None) -> dict | None:
    """The latest earlier report for the same extractor, parser and datasets."""
    best = None
    for path in out_dir.glob('*.json'):
        if path.name == 'latest.json':
            continue
        try:
            r = json.loads(path.read_text())
        except ValueError:
            continue
        same_parser = parser is None or r.get('parser', _parser_name()).split(' ')[0] == parser.split(' ')[0]
        if r.get('extractor') == extractor and r.get('datasets') == datasets and r.get('generated_at', '') < before and same_parser \
                and 'fields' in r and (best is None or r['generated_at'] > best['generated_at']):
            best = {**r, 'run_id': r.get('run_id') or path.stem}
    return best

def duplicate_resend_check() -> dict:
    """Intake the dedupe demo emails into a scratch state: the renamed PDF in a new email must be skipped,
    a new PDF re-sent under the same Message-ID must be processed. No model calls."""
    from ingest.email_intake import intake_email
    emails = ROOT / 'data/synthetic/emails'
    names = ['01-original.eml', '02-same-pdf-renamed.eml', '03-resent-with-new-pdf.eml']
    if not all((emails / n).exists() for n in names):
        return {'case': 'Duplicate resend', 'what_happened': 'Demo emails missing (run tools.make_synthetic)', 'pass': False}
    with tempfile.TemporaryDirectory() as state:
        first, renamed, resent = (intake_email(emails / n, state) for n in names)
    skipped = not renamed['items'] and renamed['skipped'][:1] and renamed['skipped'][0]['reason'] == 'duplicate_attachment'
    processed = len(resent['items']) == 1 and not resent['new_email']
    return {'case': 'Duplicate resend',
            'what_happened': ('Same PDF renamed in a new email: skipped (duplicate attachment). '
                              'New PDF re-sent with the same Message-ID: processed.') if skipped and processed
                             else f"Renamed duplicate {'skipped' if skipped else 'NOT skipped'}; re-sent new PDF {'processed' if processed else 'NOT processed'}.",
            'pass': bool(skipped and processed and len(first['items']) == 1)}

def edge_case_summary(rows: list[dict]) -> list[dict]:
    """One row per demo edge case, built from the per-document edge results."""
    def group(match):
        return [r for r in rows if match(r)]
    def summary(case, matched, describe):
        if not matched:
            return {'case': case, 'what_happened': 'Not in this run (add data/synthetic/edge-cases)', 'pass': False}
        return {'case': case, 'what_happened': describe(matched), 'pass': all(r['pass'] for r in matched)}
    multi = group(lambda r: r['case'].startswith('multi-quote'))
    body = group(lambda r: 'email body' in r['case'])
    scanned = group(lambda r: r['case'].startswith('scanned'))
    locked = group(lambda r: r.get('expected') == 'password_protected')
    out = [
        summary('Multi-quote PDF', multi, lambda m: '; '.join(sorted({f"{r['source']}: {r['got']} ({r['case'].split(', ')[-1]})" for r in m}))),
        summary('Body-only quote', body, lambda m: '; '.join(f"{r['case'].replace('quote in ', '')}: {r['grounded_fields']}/{r['fields']} fields grounded" for r in m)),
        summary('Scanned PDF', scanned, lambda m: '; '.join(f"No text layer, OCR used; {r['grounded_fields']}/{r['fields']} fields grounded" for r in m)),
        summary('Password-protected PDF', locked, lambda m: '; '.join(f"Rejected as {r['got']}, no retry" for r in m)),
    ]
    out.append(duplicate_resend_check())
    return out

def run(datasets: list[Path], extractor: str, out_dir: Path = REPORTS, command: str | None = None,
        cache_dir: Path | None = CACHE_DIR, workers: int = WORKERS, parser: str = 'docling'):
    run_started = time.perf_counter()
    now = datetime.now(timezone.utc)
    config = extractor if parser == 'docling' else f'{extractor}-{parser}'
    run_id = f'{config}-{now:%Y%m%dT%H%M%SZ}'  # eval/reports/<extractor>-<timestamp>.{md,json} (prompt 02)
    entries = []
    for dataset in datasets:
        for label in sorted(dataset.glob('*.json')):
            g = json.loads(label.read_text())
            source = dataset / g.get('source', g['document_id'] + '.pdf')
            if not source.is_file(): raise FileNotFoundError(source)
            entries.append((dataset, g, source))
    # Parse first (cached by sha256), then extract concurrently: model calls dominate the time.
    loaded: dict[Path, object] = {}
    for _, _, source in entries:
        if source not in loaded:
            try:
                loaded[source] = load_segments(source, cache_dir, parser)
            except UnparseableDocument as e:
                loaded[source] = e
    edge_cases = []
    work = []
    for dataset, g, source in entries:
        result = loaded[source]
        if g.get('expect') == 'unparseable':
            rejected = isinstance(result, UnparseableDocument)
            expected = g.get('reason')
            edge_cases.append({'document_id': g['document_id'], 'case': g.get('case', ''), 'source': source.name,
                               'expected': expected or 'unparseable', 'got': result.reason if rejected else 'parsed',
                               'pass': rejected and (expected is None or result.reason == expected)})
            continue
        if isinstance(result, UnparseableDocument):
            raise RuntimeError(f"{g['document_id']}: unexpectedly unparseable ({result.reason})")
        work.append((g, source, result))

    def extract_one(item):
        g, source, segments = item
        segment = segments[g.get('segment', 1) - 1].model_copy(update={'document_id': g['document_id']})
        started = time.perf_counter()
        with track_usage() as doc_calls:
            raw = extract_raw(segment, extractor)
            quote = ground(raw, segment)
        # The model's own claims, kept for confidence calibration (grounding empties rejected values).
        claimed = {name: field.value for name, field in raw.fields()}
        return g, source, segments, segment, quote, time.perf_counter() - started, doc_calls, claimed

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(extract_one, work))

    gold, pred, latencies, calls = [], [], [], []
    run_dir = out_dir / 'runs' / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    from rules.engine import flags_for_quote
    for g, source, segments, segment, quote, elapsed, doc_calls, claimed in results:
        # Per-document result, so the results page can open the exact extraction it scored.
        (run_dir / f"{g['document_id']}.json").write_text(json.dumps({
            'document_id': g['document_id'], 'run_id': run_id, 'extractor': extractor,
            'source_path': str(source.resolve()), 'kind': 'body' if source.suffix == '.eml' else 'pdf',
            'segment': g.get('segment', 1), 'page_numbers': sorted({p.number for p in segment.pages}),
            'body_text': segment.pages[0].text if source.suffix == '.eml' else None,
            'golden': g, 'flags': flags_for_quote(quote),
            'fields': [{'name': name, **field.model_dump(mode='json')} for name, field in quote.fields()]}, indent=1))
        gold.append(g)
        pred.append({'fields': [{'name': name, **field.model_dump(), 'claimed_value': claimed.get(name)} for name, field in quote.fields()]})
        latencies.append(elapsed)
        calls += doc_calls
        if 'case' in g:
            edge_cases.append({'document_id': g['document_id'], 'case': g['case'], 'source': source.name,
                               'expected': f"{g.get('segment_count', len(segments))} quote(s)",
                               'got': f'{len(segments)} quote(s){", ocr" if segment.ocr else ""}',
                               'pass': len(segments) == g.get('segment_count', len(segments)) and segment.ocr == g.get('ocr', segment.ocr),
                               # Fields the extractor claimed a value for; absent fields are neither grounded nor rejected.
                               'grounded_fields': sum(f.grounded for _, f in quote.fields()),
                               'fields': sum(1 for _, f in quote.fields() if f.grounded or f.ungrounded_reason)})
    report = calculate(gold, pred, latencies)
    report['usage'] = usage_summary(calls, len(gold), extractor)
    dataset_names = [str(d) for d in datasets]
    generated_at = now.isoformat(timespec='seconds')
    previous = _previous_run(out_dir, extractor, dataset_names, generated_at, _parser_name(parser))
    for name, m in report['fields'].items():
        before = (previous or {}).get('fields', {}).get(name, {}).get('accuracy')
        m['previous_accuracy'] = before
        m['delta'] = round(m['accuracy'] - before, 4) if before is not None else None
    configured_model = model_prices(MODEL_TASKS[extractor])[0] if extractor in MODEL_TASKS else extractor
    report.update({
        'run_id': run_id, 'previous_run_id': previous['run_id'] if previous else None,
        'extractor': extractor, 'model': (report['usage'] or {}).get('model') or configured_model, 'parser': _parser_name(parser),
        'datasets': dataset_names,
        'dataset_summary': [{'name': d.name, 'path': str(d), 'documents': sum(1 for e in entries if e[0] == d)} for d in datasets],
        'labellers': sorted({f.get('labeller') for _, g, _ in entries for f in g.get('fields', []) if f.get('labeller')}),
        'git_commit': _git_commit(),
        'command': command or 'uv run python -m eval.run ' + ' '.join(f'--dataset {shlex.quote(d)}' for d in dataset_names) + f' --extractor {extractor}',
        'edge_cases': edge_cases, 'edge_case_summary': edge_case_summary(edge_cases),
        'smoke_test': extractor in SMOKE_TEST_EXTRACTORS,
        'generated_at': generated_at, 'duration_s': round(time.perf_counter() - run_started, 1)})
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f'{run_id}.json').write_text(json.dumps(report, indent=2) + '\n')
    (out_dir / f'{run_id}.md').write_text(markdown(report))
    if not report['smoke_test']:
        # The results page renders latest.json; smoke tests never replace a real run there.
        (out_dir / 'latest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report

def usage_summary(calls: list[dict], documents: int, extractor: str) -> dict | None:
    if not calls or not documents:
        return None
    tokens_in = sum(c['input_tokens'] or 0 for c in calls)
    tokens_out = sum(c['output_tokens'] or 0 for c in calls)
    summary = {'model': calls[0]['model'], 'calls': len(calls), 'input_tokens': tokens_in, 'output_tokens': tokens_out,
               'input_tokens_per_doc': round(tokens_in / documents), 'output_tokens_per_doc': round(tokens_out / documents),
               'usd_per_100_docs': None}
    if extractor in MODEL_TASKS:
        _, price_in, price_out = model_prices(MODEL_TASKS[extractor])
        if price_in is not None and price_out is not None:
            summary['usd_per_100_docs'] = round((tokens_in * price_in + tokens_out * price_out) / 1e6 / documents * 100, 4)
    return summary

def markdown(report: dict) -> str:
    lines = [f"# Supplier quote extraction — `{report['extractor']}`", '']
    if report['smoke_test']:
        lines += ['> **SMOKE TEST — not a measurement.** This extractor copies the golden answers, so accuracy is 100% by construction.',
                  '> It checks that parsing, the Ground step and this harness work end to end. Use `current` or `frontier` for real numbers.', '']
    lines += [f"Generated {report['generated_at']} over {', '.join(report['datasets'])}.", '',
              f"- **Documents:** {report['document_count']}",
              f"- **Latency per document:** p50 {report['p50_latency_s']}s, p95 {report['p95_latency_s']}s",
              f"- **Worst fields:** {', '.join(report['worst_fields'])}"]
    if report.get('usage'):
        u = report['usage']
        cost = f"${u['usd_per_100_docs']:.4f}" if u['usd_per_100_docs'] is not None else 'n/a (no price in llm/config.yaml)'
        lines += [f"- **Model:** {u['model']}, {u['calls']} calls, {u['input_tokens_per_doc']} input + {u['output_tokens_per_doc']} output tokens per document",
                  f"- **Cost per 100 documents:** {cost} (list prices in llm/config.yaml)"]
    lines += ['',
              '`Accuracy` = value correct after normalisation. `Traced to source` = the Ground step found the span on the stated page and attached its location.',
              '`Evidence match` = traced to the same spot as the golden answer: the span covers ≥80% of the golden span, on the same page, and the golden bbox lies inside the extracted bbox.',
              '`Ungrounded` = a value was claimed but rejected by the Ground step.', '',
              '| Field | Accuracy | Δ vs previous | Traced to source | Evidence match | Ungrounded | Missing | n |',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name, m in report['fields'].items():
        delta = f"{m['delta'] * 100:+.1f}" if m.get('delta') is not None else '—'
        lines.append(f"| {name} | {m['accuracy']:.1%} | {delta} | {m['grounded_rate']:.1%} | {m['evidence_match_rate']:.1%} | {m['ungrounded_rate']:.1%} | {m['missing_rate']:.1%} | {m['count']} |")
    lines += calibration_markdown(report)
    ungrounded = {n: m for n, m in report['fields'].items() if m['ungrounded_count']}
    lines += ['', '## Ungrounded fields', '']
    if ungrounded:
        for name, m in ungrounded.items():
            reasons = ', '.join(f'{r} ×{c}' for r, c in m['ungrounded_reasons'].items())
            lines.append(f"- {name}: {m['ungrounded_count']} ({reasons})")
    else:
        lines.append('None.')
    if report['edge_cases']:
        lines += ['', '## Edge cases', '', '| Document | Case | Expected | Got | Grounded fields | Pass |', '|---|---|---|---|---:|---|']
        for e in report['edge_cases']:
            grounded = f"{e['grounded_fields']}/{e['fields']}" if 'fields' in e else '—'
            lines.append(f"| {e['document_id']} | {e['case']} | {e['expected']} | {e['got']} | {grounded} | {'✅' if e['pass'] else '❌'} |")
    return '\n'.join(lines) + '\n'

def calibration_markdown(report: dict) -> list[str]:
    """Self-reported confidence against correctness. Analysis only: plain 0-1 numbers, never percentages,
    never a rule input or score (prompt 04)."""
    c = report.get('calibration')
    out = ['', '## Confidence calibration (analysis only)', '',
           "The model's self-reported confidence (0-1) against whether its claimed value was right, before grounding.",
           'It never decides anything: not an input to rules or scores, and not shown in the app.', '']
    if not c:
        return out + ['No confidence values in this run (the extractor did not report any).']
    auroc = c['auroc'] if c['auroc'] is not None else 'n/a (no wrong answers to compare)'
    out += [f"- **Fields with a confidence:** {c['pairs']} ({c['right']} right, {c['wrong']} wrong)",
            f"- **Expected calibration error:** {c['ece']} (0 = confidence matches accuracy)",
            f"- **Brier score:** {c['brier']} (lower is better)",
            f"- **Separates right from wrong (AUROC):** {auroc}",
            f"- **Mean confidence:** right {c['mean_confidence_right']}, wrong {c['mean_confidence_wrong']}",
            f"- **Wrong answers with confidence ≥ 0.9:** {c['overconfident_errors']}", '',
            '| Confidence | Fields | Mean confidence | Share right |', '|---|---:|---:|---:|']
    out += [f"| {b['from']:.1f}–{b['to']:.1f} | {b['count']} | {b['mean_confidence']} | {b['accuracy']} |" for b in c['reliability']]
    if c['wrong'] < 10:
        out += ['', f"With {c['wrong']} wrong answer(s), these numbers cannot show whether confidence predicts errors; "
                'that needs a harder golden set.']
    out += ['', '| Field | Coverage | ECE | Mean conf. right | Mean conf. wrong |', '|---|---:|---:|---:|---:|']
    for name, m in report['fields'].items():
        fc = m.get('calibration')
        if fc:
            out.append(f"| {name} | {m['confidence_coverage']} | {fc['ece']} | {fc['mean_confidence_right']} | {fc['mean_confidence_wrong'] if fc['mean_confidence_wrong'] is not None else '—'} |")
    return out

def write_baseline(report: dict, path: Path = ROOT / 'eval/baseline.json'):
    if report['smoke_test']:
        raise ValueError('A smoke-test extractor cannot set the baseline')
    baseline = {'extractor': report['extractor'], 'generated_at': report['generated_at'], 'datasets': report['datasets'],
                'fields': {n: {'accuracy': m['accuracy']} for n, m in report['fields'].items()}}
    path.write_text(json.dumps(baseline, indent=2) + '\n')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', type=Path, action='append', required=True)
    ap.add_argument('--extractor', required=True)
    ap.add_argument('--write-baseline', action='store_true', help='record this run as eval/baseline.json')
    ap.add_argument('--workers', type=int, default=WORKERS, help='concurrent extractions')
    ap.add_argument('--no-cache', action='store_true', help='parse every document again')
    ap.add_argument('--parser', choices=['docling', 'azure_di'], default='docling')
    a = ap.parse_args()
    command = 'uv run python -m eval.run ' + ' '.join(shlex.quote(x) for x in sys.argv[1:])
    report = run(a.dataset, a.extractor, command=command, cache_dir=None if a.no_cache else CACHE_DIR, workers=a.workers,
                 parser=a.parser)
    if a.write_baseline:
        write_baseline(report)
    print(markdown(report))

if __name__ == '__main__': main()
