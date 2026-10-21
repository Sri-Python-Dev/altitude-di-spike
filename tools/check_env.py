"""Check a machine (e.g. the Windows VDI) for everything the spike needs, and say what is blocked.

  uv run --env-file .env python tools/check_env.py

Read-only: it makes HEAD requests to public endpoints (no data sent), connects to Postgres and
Temporal, and imports the libraries. It never prints secret values.
"""
import importlib
import os
import platform
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
results = []

def check(group, name, ok, detail='', fix=''):
    results.append((group, name, ok, detail if ok else (fix or detail)))

def reachable(url: str) -> tuple[bool, str]:
    """Any HTTP answer (even 401/404) means the network path works; honours HTTPS_PROXY."""
    try:
        urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=8)
        return True, 'reachable'
    except urllib.error.HTTPError as e:
        return True, f'reachable (HTTP {e.code})'
    except urllib.error.URLError as e:
        reason = str(e.reason)
        if 'CERTIFICATE' in reason.upper():
            return False, 'TLS certificate rejected: corporate proxy? set SSL_CERT_FILE to the company root CA'
        return False, f'blocked or offline ({reason[:80]})'
    except Exception as e:
        return False, f'blocked ({type(e).__name__})'

def port_open(address: str) -> bool:
    host, _, port = address.partition(':')
    try:
        with socket.create_connection((host or 'localhost', int(port or 7233)), timeout=3):
            return True
    except OSError:
        return False

def main():
    check('System', 'Python', sys.version_info[:2] == (3, 12), f'{platform.python_version()} on {platform.system()}',
          'Python 3.12 required: uv python install 3.12 && uv sync --python 3.12')
    if platform.system() == 'Windows':
        check('System', 'UTF-8 console', os.getenv('PYTHONUTF8') == '1' or sys.flags.utf8_mode == 1, 'on',
              'add PYTHONUTF8=1 to .env (from .env.example)')
    for module in ('pydantic', 'docling', 'pypdfium2', 'onnxruntime', 'psycopg', 'temporalio', 'zen', 'openai', 'yaml'):
        try:
            importlib.import_module(module)
            check('Libraries', module, True, 'installed')
        except Exception as e:
            check('Libraries', module, False, '', f'not importable ({type(e).__name__}): run uv sync')

    for name, url, needed_for in [
        ('PyPI', 'https://pypi.org/simple/', 'installing packages (uv sync)'),
        ('Hugging Face', 'https://huggingface.co/', "Docling's layout/OCR models, first run only"),
        ('OpenAI API', 'https://api.openai.com/v1/models', 'extraction with the current models'),
        ('Microsoft sign-in', 'https://login.microsoftonline.com/', 'Graph mail monitor and Azure'),
        ('Microsoft Graph', 'https://graph.microsoft.com/v1.0/', 'Graph mail monitor'),
        ('Google Fonts', 'https://fonts.googleapis.com/', 'app typography (falls back to system fonts)'),
    ]:
        ok, detail = reachable(url)
        check('Network', name, ok, f'{detail} — {needed_for}', f'{detail} — needed for {needed_for}')

    hf_home = Path(os.getenv('HF_HOME', Path.home() / '.cache' / 'huggingface'))
    models = list(hf_home.glob('hub/models--*docling*')) + list(hf_home.glob('hub/models--ds4sd*'))
    check('Models', 'Docling models cached', bool(models), f'{len(models)} model folder(s) in {hf_home}',
          f'none in {hf_home}: downloaded on first parse (needs Hugging Face), or copy the folder from another machine')

    check('Secrets', 'OPENAI_API_KEY', bool(os.getenv('OPENAI_API_KEY')), 'set', 'not set in .env: model calls will fail')
    url = os.getenv('DATABASE_URL')
    if url:
        try:
            from storage.postgres import connect
            with connect(url) as conn:
                conn.execute('select 1')
            check('Services', 'Postgres (DATABASE_URL)', True, 'connected, tables ready')
        except Exception as e:
            check('Services', 'Postgres (DATABASE_URL)', False, '', f'cannot connect ({type(e).__name__}): is Postgres running, is the database created?')
    else:
        check('Services', 'Postgres (DATABASE_URL)', False, '', 'DATABASE_URL not set in .env')
    address = os.getenv('TEMPORAL_ADDRESS', 'localhost:7233')
    check('Services', f'Temporal ({address})', port_open(address), 'listening', 'not running: temporal server start-dev')

    enabled = os.getenv('MAIL_MONITOR_ENABLED', '').lower() == 'true'
    graph_keys = all(os.getenv(k) for k in ('GRAPH_TENANT_ID', 'GRAPH_CLIENT_ID', 'GRAPH_CLIENT_SECRET', 'GRAPH_MAILBOX'))
    check('Mail', 'Graph mail monitor', True,
          'enabled and configured' if enabled and graph_keys else
          'configured but OFF (MAIL_MONITOR_ENABLED=false)' if graph_keys else
          'not configured (optional; the local inbox folder works without it)')

    width = max(len(n) for _, n, _, _ in results)
    group = None
    for g, name, ok, detail in results:
        if g != group:
            print(f'\n{g}')
            group = g
        print(f"  {'ok  ' if ok else 'FAIL'}  {name:<{width}}  {detail}")
    failed = [n for _, n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)} ok, {len(failed)} to fix" + (f": {', '.join(failed)}" if failed else ''))
    return 1 if failed else 0

if __name__ == '__main__':
    sys.exit(main())
