from pathlib import Path

def test_llm_sdk_only_in_router():
    root=Path(__file__).resolve().parents[1]
    for file in root.rglob('*.py'):
        if file in (root/'llm/router.py', root/'tests/test_import_policy.py'): continue
        if '.venv' in file.parts: continue
        text=file.read_text()
        assert 'import openai' not in text.lower() and 'from openai import' not in text.lower(), file
