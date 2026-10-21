import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def _flag_codes():
    graph = json.loads((ROOT / 'rules/quote_flags.json').read_text())
    table = next(n for n in graph['nodes'] if n['id'] == 'quote_flags')
    return {json.loads(rule['fc']) for rule in table['content']['rules']}

def test_every_flag_in_the_table_is_documented():
    readme = (ROOT / 'rules/README.md').read_text()
    for code in _flag_codes():
        assert re.search(rf'\|\s*{code}\s*\|', readme), f'{code} missing from rules/README.md'

def test_table_compiles_and_runs():
    from rules.engine import RULES_FILE, validate
    validate(RULES_FILE.read_text())
    assert _flag_codes() == {'CURRENCY_MISMATCH', 'CURRENCY_UNRESOLVED', 'TOTAL_MISMATCH'}
