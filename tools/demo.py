"""Demo housekeeping (see docs/demo/run-sheet.md).

  uv run --env-file .env python -m tools.demo reset         # make demo-reset
  uv run --env-file .env python -m tools.demo check         # make demo-check
  uv run --env-file .env python -m tools.demo add-rule      # fallback if the rule editor will not save

reset: empties the inbox, clears processed state (data/state), clears the results database,
       and restores rules/quote_flags.json to the demo baseline (Lone Star Machining not on file).
       Harness reports in eval/reports are kept: the demo's delta column compares against them.
check: confirms the services the demo needs, then runs the harness once and fails unless it
       finishes in under two minutes with every edge case passing and the currency demo armed.
"""
import argparse, json, os, shutil, socket, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / 'rules/quote_flags.json'
BASELINE = ROOT / 'rules/quote_flags.baseline.json'
INBOX = Path(os.getenv('ALTITUDE_INBOX', ROOT / 'data/inbox'))
STATE = Path(os.getenv('ALTITUDE_STATE_DIR', ROOT / 'data/state'))
DATASETS = ['data/golden/quotes-v1', 'data/synthetic/edge-cases', 'data/synthetic']
DEMO_SUPPLIER = 'Lone Star Machining Inc'
LIMIT_S = 120

def reset():
    shutil.copyfile(BASELINE, RULES)
    print(f'rules      restored {RULES.relative_to(ROOT)} from the demo baseline')
    removed = 0
    if INBOX.exists():
        for item in INBOX.iterdir():
            shutil.rmtree(item) if item.is_dir() else item.unlink()
            removed += 1
    INBOX.mkdir(parents=True, exist_ok=True)
    print(f'inbox      emptied {INBOX} ({removed} entries); demo emails stay in data/synthetic/emails')
    if STATE.exists():
        shutil.rmtree(STATE)
    print(f'state      cleared {STATE} (dedupe records, stored files, extractions)')
    from storage.postgres import connect, database_url
    if database_url():
        with connect() as conn:
            conn.execute('truncate documents cascade')
        print('database   cleared documents, quotes, fields and flags')
    else:
        print('database   skipped: DATABASE_URL not set (run with --env-file .env)')
    print('Reset done. Restart the worker and the app if they were running.')

def _reachable(address: str) -> bool:
    host, _, port = address.partition(':')
    try:
        with socket.create_connection((host, int(port or 7233)), timeout=2):
            return True
    except OSError:
        return False

def check() -> int:
    problems = []
    def ok(label, condition, detail='', fix=''):
        note = detail if condition else (fix or detail)
        print(f"{'ok  ' if condition else 'FAIL'} {label}{(': ' + note) if note else ''}")
        if not condition:
            problems.append(label)
    ok('OpenAI key set', bool(os.getenv('OPENAI_API_KEY')), fix='run with --env-file .env')
    from storage.postgres import connect, database_url
    try:
        with connect() as conn:
            conn.execute('select 1')
        ok('Postgres reachable', True, database_url().rsplit('@', 1)[-1])
    except Exception as e:
        ok('Postgres reachable', False, type(e).__name__)
    address = os.getenv('TEMPORAL_ADDRESS', 'localhost:7233')
    ok('Temporal reachable', _reachable(address), address)
    from rules.engine import supplier_currency
    ok('Currency demo armed', supplier_currency(DEMO_SUPPLIER) is None, f'{DEMO_SUPPLIER} not on file', fix=f'{DEMO_SUPPLIER} is on file: run make demo-reset')
    ok('Demo emails present', (ROOT / 'data/synthetic/emails/08-dollar-prices.eml').exists(), fix='run uv run python -m tools.make_synthetic')
    if problems:
        print(f'\nNot running the harness: fix {", ".join(problems)} first.')
        return 1
    from eval.run import run
    print(f'\nRunning the harness on {", ".join(DATASETS)} …')
    started = time.perf_counter()
    report = run([ROOT / d for d in DATASETS], 'current', command='make demo-check')
    elapsed = time.perf_counter() - started
    ok(f'Harness under {LIMIT_S}s', elapsed < LIMIT_S, f'{elapsed:.1f}s for {report["document_count"]} documents')
    for case in report['edge_case_summary']:
        ok(f'Edge case: {case["case"]}', case['pass'], case['what_happened'])
    currency = report['fields'].get('currency', {})
    ok('Currency row shows the demo miss', currency.get('worst_doc') == 'quote-g-dollar',
       f'accuracy {currency.get("accuracy")}, worst doc {currency.get("worst_doc")}')
    print(f'\nRun {report["run_id"]} is now /results.')
    return 1 if problems else 0

def add_rule():
    """Fallback for demo step 3: add the Lone Star row to the supplier table without the editor."""
    from rules.engine import validate, supplier_currency
    graph = json.loads(RULES.read_text())
    table = next(n for n in graph['nodes'] if n['id'] == 'supplier_currency')
    rows = table['content']['rules']
    if not any('lone star machining' in r.get('s', '') for r in rows):
        rows.insert(0, {'_id': 'lone-star-machining', 's': '"lone star machining"', 'c': '"USD"'})
    content = json.dumps(graph, indent=2) + '\n'
    validate(content)
    RULES.write_text(content)
    print(f'Added: {DEMO_SUPPLIER} -> {supplier_currency(DEMO_SUPPLIER)}. Now resend the failing document.')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['reset', 'check', 'add-rule'])
    a = ap.parse_args()
    if a.action == 'reset':
        reset()
    elif a.action == 'add-rule':
        add_rule()
    else:
        sys.exit(check())

if __name__ == '__main__':
    main()
