"""Intake for saved .eml files (synthetic only; nothing here reads a real mailbox).

Returns work items holding IDs and file paths only, so they are safe to pass
through Temporal. Attachment bytes and bodies are written to a local
content-addressed store under the state directory.
"""
import hashlib
import re
import time
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from pathlib import Path
from extract.dedupe import DedupeRegistry

QUOTE_HINT = re.compile(r'(?i)\b(quote|quotation)\b')

def safe_filename(name: str, default: str = 'document.pdf') -> str:
    """A filesystem-safe version of an attachment's filename, ending in .pdf. For display only."""
    stem = re.sub(r'[^A-Za-z0-9._ -]+', '_', Path(name or '').name).strip(' ._')[:80] or Path(default).stem
    return stem if stem.lower().endswith('.pdf') else f'{stem}.pdf'


@dataclass
class EmailMessage:
    message_id: str
    body: str
    body_is_html: bool
    attachments: list[tuple[str, bytes]] = field(default_factory=list)

def read_eml(path: str | Path) -> EmailMessage:
    raw = Path(path).read_bytes()
    message = BytesParser(policy=policy.default).parsebytes(raw)
    # No Message-ID: fall back to a hash of the raw message so a re-send of the same file still dedupes.
    message_id = (message['Message-ID'] or '').strip() or f'<sha256-{hashlib.sha256(raw).hexdigest()}@missing>'
    # HTML first: a plain alternative is often a stub, and the HTML holds the quote table.
    body_part = message.get_body(preferencelist=('html', 'plain'))
    body = body_part.get_content() if body_part else ''
    attachments = []
    for part in message.iter_attachments():
        filename = part.get_filename() or ''
        if part.get_content_type() == 'application/pdf' or filename.lower().endswith('.pdf'):
            attachments.append((filename, part.get_payload(decode=True) or b''))
    return EmailMessage(message_id, body, bool(body_part and body_part.get_content_subtype() == 'html'), attachments)

def intake_email(eml_path: str | Path, state_dir: str | Path, resend: bool = False) -> dict:
    """Dedupe and store an email's quote documents.

    resend=True is the test-only "resend" action: dedupe is bypassed on purpose and every
    document gets a new ID, so a rule change can be shown end to end on the same email.
    """
    state_dir = Path(state_dir)
    blobs = state_dir / 'blobs'
    blobs.mkdir(parents=True, exist_ok=True)
    message = read_eml(eml_path)
    registry = DedupeRegistry(state_dir / 'dedupe.sqlite')
    new_email = registry.record_email(message.message_id)
    suffix = f'-r{int(time.time()) % 1_000_000:06d}' if resend else ''
    items, skipped = [], []
    for filename, content in message.attachments:
        digest = hashlib.sha256(content).hexdigest()
        if not registry.record_attachment(content, message.message_id, filename) and not resend:
            skipped.append({'sha256': digest, 'reason': 'duplicate_attachment'})
            continue
        # Stored by content hash; the original filename is kept only so people can recognise it.
        target = blobs / digest / safe_filename(filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        items.append({'document_id': digest[:16] + suffix, 'file_path': str(target), 'kind': 'pdf', 'source_email': str(eml_path)})
    if not message.attachments and QUOTE_HINT.search(message.body):
        doc_id = 'body-' + hashlib.sha256(message.message_id.encode()).hexdigest()[:16] + suffix
        if new_email or resend:
            target = blobs / f"{doc_id}.{'html' if message.body_is_html else 'txt'}"
            target.write_text(message.body)
            items.append({'document_id': doc_id, 'file_path': str(target), 'kind': 'body', 'source_email': str(eml_path)})
        else:
            skipped.append({'document_id': doc_id, 'reason': 'duplicate_email'})
    # Message-ID is not content, but hash it anyway so workflow history carries no addresses.
    return {'email_key': hashlib.sha256(message.message_id.encode()).hexdigest()[:16],
            'new_email': new_email, 'items': items, 'skipped': skipped}
