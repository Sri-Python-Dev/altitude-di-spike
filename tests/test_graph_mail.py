"""Graph mail monitor against a fake Graph (no network, no credentials)."""
import json
from pathlib import Path
import pytest
import email_monitor
from ingest.graph_mail import GraphConfig, GraphError, GraphMailbox

EML = (Path(__file__).resolve().parents[1] / 'data/synthetic/emails/01-original.eml')

class FakeGraph:
    """Answers the token, folder, delta and $value endpoints; records calls."""
    def __init__(self, messages, folders=None, throttle_once=False, expire_once=False):
        self.messages, self.folders = messages, folders or []
        self.calls, self.tokens = [], 0
        self.throttle_once, self.expire_once = throttle_once, expire_once
    def __call__(self, method, url, headers, data=None, timeout=30):
        self.calls.append((method, url))
        if 'oauth2/v2.0/token' in url:
            self.tokens += 1
            return 200, {}, json.dumps({'access_token': f't{self.tokens}', 'expires_in': 3600}).encode()
        if self.throttle_once:
            self.throttle_once = False
            return 429, {'Retry-After': '0'}, b''
        if self.expire_once:
            self.expire_once = False
            return 401, {}, b'{"error": {"code": "InvalidAuthenticationToken"}}'
        if '/mailFolders?$filter=' in url:
            return 200, {}, json.dumps({'value': self.folders}).encode()
        if '/messages/delta' in url and 'page=2' not in url and 'deltatoken' not in url:
            return 200, {}, json.dumps({'value': self.messages[:1], '@odata.nextLink': url + '&page=2'}).encode()
        if 'page=2' in url:
            return 200, {}, json.dumps({'value': self.messages[1:] + [{'id': 'gone', '@removed': {}}],
                                        '@odata.deltaLink': 'https://graph/delta?deltatoken=1'}).encode()
        if 'deltatoken' in url:
            return 200, {}, json.dumps({'value': [], '@odata.deltaLink': 'https://graph/delta?deltatoken=2'}).encode()
        if url.endswith('/$value'):
            return 200, {}, EML.read_bytes()
        return 404, {}, b'{"error": {"code": "NotFound"}}'

def config(**kw):
    return GraphConfig('tenant', 'client', 'secret', 'rfq-test@altitude.example', **kw)

def msg(id_, when, sender='sales@supplier.example'):
    return {'id': id_, 'receivedDateTime': when, 'from': {'emailAddress': {'address': sender}}, 'hasAttachments': True}

def test_delta_pages_and_removed_messages():
    graph = FakeGraph([msg('a', '2026-10-21T10:00:00Z'), msg('b', '2026-10-21T10:01:00Z')])
    messages, link = GraphMailbox(config(), graph).changes()
    assert [m['id'] for m in messages] == ['a', 'b'] and link.endswith('deltatoken=1')
    assert graph.tokens == 1  # token reused across pages

def test_named_folder_is_looked_up_and_missing_folder_fails():
    graph = FakeGraph([], folders=[{'id': 'F123', 'displayName': 'Altitude Test'}])
    assert GraphMailbox(config(folder='Altitude Test'), graph).folder_id() == 'F123'
    with pytest.raises(GraphError, match='folder not found'):
        GraphMailbox(config(folder='Nope'), FakeGraph([])).folder_id()

def test_throttling_and_expired_token_are_retried():
    assert GraphMailbox(config(), FakeGraph([], throttle_once=True)).changes()[1]
    graph = FakeGraph([], expire_once=True)
    GraphMailbox(config(), graph).changes()
    assert graph.tokens == 2  # signed in again after the 401

def test_sender_allowlist():
    c = config(allowed_senders=['sales@supplier.example', '@trusted.example'])
    assert c.sender_allowed('SALES@supplier.example') and c.sender_allowed('bob@trusted.example')
    assert not c.sender_allowed('someone@else.example') and config().sender_allowed('anyone@x.example')

def test_fetcher_takes_only_new_allowed_mail_once(tmp_path):
    graph = FakeGraph([msg('old', '2020-01-01T00:00:00Z'), msg('new', '2999-01-01T00:00:00Z'),
                       msg('other', '2999-01-01T00:00:00Z', sender='spam@else.example')])
    mailbox = GraphMailbox(config(allowed_senders=['@supplier.example']), graph)
    fetcher = email_monitor.GraphFetcher(mailbox, tmp_path, tmp_path / 'state.json')
    assert fetcher.fetch() == {'new': 1, 'skipped_sender': 1, 'skipped_old': 1}
    emails = list(tmp_path.glob('graph-*.eml'))
    assert len(emails) == 1 and emails[0].read_bytes() == EML.read_bytes() and not list(tmp_path.glob('*.part'))
    # Restart: the saved delta link and seen IDs mean nothing is fetched twice.
    again = email_monitor.GraphFetcher(GraphMailbox(config(), graph), tmp_path, tmp_path / 'state.json')
    assert again.fetch() == {'new': 0, 'skipped_sender': 0, 'skipped_old': 0}
    assert json.loads((tmp_path / 'state.json').read_text())['delta_link'].endswith('deltatoken=2')

def test_monitor_is_off_unless_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv('MAIL_MONITOR_ENABLED', raising=False)
    with pytest.raises(SystemExit, match='CLAUDE.md'):
        email_monitor.graph_fetcher(tmp_path, backfill=False)
    monkeypatch.setenv('MAIL_MONITOR_ENABLED', 'true')
    for key in ('GRAPH_TENANT_ID', 'GRAPH_CLIENT_ID', 'GRAPH_CLIENT_SECRET', 'GRAPH_MAILBOX'):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemExit, match='GRAPH_TENANT_ID'):
        email_monitor.graph_fetcher(tmp_path, backfill=False)

def test_errors_carry_no_content():
    def failing(method, url, headers, data=None, timeout=30):
        if 'token' in url:
            return 200, {}, b'{"access_token": "t", "expires_in": 3600}'
        return 403, {}, b'{"error": {"code": "ErrorAccessDenied", "message": "secret mailbox details"}}'
    with pytest.raises(GraphError) as e:
        GraphMailbox(config(), failing).changes()
    assert str(e.value) == 'Graph request failed: HTTP 403 ErrorAccessDenied'
