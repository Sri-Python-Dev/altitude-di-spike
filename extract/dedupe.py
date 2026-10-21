"""Dedupe on RFC Internet Message-ID and attachment SHA-256, never on filename.

Backed by SQLite so a worker restart keeps what it has seen; pass no path for an
in-memory registry (tests). Filenames are recorded for audit only.
"""
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = '''
create table if not exists emails (message_id text primary key, first_seen text not null);
create table if not exists attachments (sha256 text primary key, first_message_id text, first_filename text, first_seen text not null);
create table if not exists sightings (message_id text, sha256 text, filename text, duplicate integer, seen_at text not null);
'''

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

class DedupeRegistry:
    def __init__(self, path: str | Path | None = None):
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path) if path else ':memory:')
        self.db.executescript(SCHEMA)

    def record_email(self, internet_message_id: str) -> bool:
        """True if this Message-ID is new."""
        with self.db:
            cur = self.db.execute('insert or ignore into emails values (?, ?)', (internet_message_id, _now()))
        return cur.rowcount == 1

    def record_attachment(self, content: bytes, message_id: str | None = None, filename: str | None = None) -> bool:
        """True if these bytes have never been seen, whatever the filename or email."""
        digest = hashlib.sha256(content).hexdigest()
        with self.db:
            cur = self.db.execute('insert or ignore into attachments values (?, ?, ?, ?)', (digest, message_id, filename, _now()))
            new = cur.rowcount == 1
            self.db.execute('insert into sightings values (?, ?, ?, ?, ?)', (message_id, digest, filename, int(not new), _now()))
        return new

    def ingest(self, message_id: str, attachments: list[bytes]) -> list[bool]:
        # Even if the message ID repeats, newly attached PDFs are still processed.
        self.record_email(message_id)
        return [self.record_attachment(content, message_id) for content in attachments]
