"""Microsoft Graph mailbox reader: new messages from one folder, as raw MIME (.eml).

Read-only (application permission Mail.Read on one mailbox). Standard library only: a client-
credentials token and three Graph endpoints. Nothing here logs subjects, senders or content.

Configuration (.env):
  GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET   app registration
  GRAPH_MAILBOX         the mailbox to read, e.g. rfq-test@company.com
  GRAPH_FOLDER          folder to watch (default Inbox); a dedicated folder is recommended
  GRAPH_ALLOWED_SENDERS optional comma list of addresses or @domains; others are skipped
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

GRAPH = 'https://graph.microsoft.com/v1.0'
LOGIN = 'https://login.microsoftonline.com'
WELL_KNOWN = {'inbox', 'archive', 'junkemail'}

@dataclass
class GraphConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    mailbox: str
    folder: str = 'Inbox'
    allowed_senders: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> 'GraphConfig | None':
        keys = ('GRAPH_TENANT_ID', 'GRAPH_CLIENT_ID', 'GRAPH_CLIENT_SECRET', 'GRAPH_MAILBOX')
        values = [os.getenv(k, '').strip() for k in keys]
        if not all(values):
            return None
        senders = [s.strip().lower() for s in os.getenv('GRAPH_ALLOWED_SENDERS', '').split(',') if s.strip()]
        return cls(*values, folder=os.getenv('GRAPH_FOLDER', 'Inbox').strip() or 'Inbox', allowed_senders=senders)

    def sender_allowed(self, address: str | None) -> bool:
        if not self.allowed_senders:
            return True
        address = (address or '').lower()
        return any(address == s or (s.startswith('@') and address.endswith(s)) for s in self.allowed_senders)

class GraphError(Exception):
    """Graph or sign-in failure. The message carries the HTTP status and Graph's error code only."""

def _urllib_http(method: str, url: str, headers: dict, data: bytes | None = None, timeout: int = 30):
    # urllib honours HTTPS_PROXY / HTTP_PROXY from the environment (corporate proxies).
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()

class GraphMailbox:
    def __init__(self, config: GraphConfig, http=_urllib_http):
        self.config = config
        self.http = http
        self._token = None
        self._expires = 0.0
        self._folder = None

    def _access_token(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        body = urllib.parse.urlencode({'client_id': self.config.client_id, 'client_secret': self.config.client_secret,
                                       'grant_type': 'client_credentials', 'scope': 'https://graph.microsoft.com/.default'}).encode()
        status, _, raw = self.http('POST', f'{LOGIN}/{self.config.tenant_id}/oauth2/v2.0/token',
                                   {'Content-Type': 'application/x-www-form-urlencoded'}, body)
        data = json.loads(raw or b'{}')
        if status != 200 or 'access_token' not in data:
            raise GraphError(f'sign-in failed: HTTP {status} {data.get("error", "")}')
        self._token, self._expires = data['access_token'], time.time() + int(data.get('expires_in', 3600))
        return self._token

    def _request(self, url: str, accept: str = 'application/json') -> bytes:
        for attempt in range(4):
            status, headers, raw = self.http('GET', url, {'Authorization': f'Bearer {self._access_token()}', 'Accept': accept})
            if status == 429 or status >= 500:  # throttled or transient: wait and retry
                time.sleep(min(int(headers.get('Retry-After', 2 ** attempt)), 30))
                continue
            if status == 401 and attempt == 0:
                self._token = None  # expired token: sign in again once
                continue
            if status != 200:
                code = ''
                try:
                    code = json.loads(raw).get('error', {}).get('code', '')
                except (ValueError, AttributeError):
                    pass
                raise GraphError(f'Graph request failed: HTTP {status} {code}')
            return raw
        raise GraphError('Graph request failed after retries')

    def _user(self) -> str:
        return f'{GRAPH}/users/{urllib.parse.quote(self.config.mailbox)}'

    def folder_id(self) -> str:
        if self._folder:
            return self._folder
        name = self.config.folder
        if name.lower() in WELL_KNOWN:
            self._folder = name.lower()
        else:
            query = urllib.parse.quote(f"displayName eq '{name}'")
            found = json.loads(self._request(f'{self._user()}/mailFolders?$filter={query}')).get('value', [])
            if not found:
                raise GraphError(f'folder not found: {name}')
            self._folder = found[0]['id']
        return self._folder

    def changes(self, delta_link: str | None = None) -> tuple[list[dict], str]:
        """New or changed messages since delta_link (all messages on the first call), and the next delta link."""
        url = delta_link or (f'{self._user()}/mailFolders/{self.folder_id()}/messages/delta'
                             '?$select=id,receivedDateTime,from,hasAttachments')
        messages = []
        while True:
            page = json.loads(self._request(url))
            messages += [m for m in page.get('value', []) if '@removed' not in m]
            if '@odata.nextLink' in page:
                url = page['@odata.nextLink']
            else:
                return messages, page['@odata.deltaLink']

    def mime(self, message_id: str) -> bytes:
        """The full message as RFC 822 MIME, the same format as a saved .eml file."""
        return self._request(f'{self._user()}/messages/{urllib.parse.quote(message_id)}/$value', accept='message/rfc822')

def sender_address(message: dict) -> str | None:
    return ((message.get('from') or {}).get('emailAddress') or {}).get('address')
