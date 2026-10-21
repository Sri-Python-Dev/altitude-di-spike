import hashlib
import re
from html import unescape
from parse.models import Page, LayoutElement, ParsedDocument, render_text

def html_to_text(body: str) -> str:
    body = re.sub(r'(?is)<(script|style)\b.*?</\1\s*>', ' ', body)
    body = re.sub(r'(?i)<\s*br\s*/?\s*>|</\s*(?:p|div|tr|li|h\d)\s*>', '\n', body)
    body = re.sub(r'(?i)</\s*t[dh]\s*>', ' ', body)
    body = re.sub(r'<[^>]+>', ' ', body)
    return '\n'.join(' '.join(line.split()) for line in unescape(body).splitlines())

def parse_email_body(document_id: str, body: str, html: bool = False) -> ParsedDocument:
    """An email body as page 0. Each line is an element; there is no layout, so no bbox."""
    if html:
        body = html_to_text(body)
    elements = [LayoutElement(text=line.strip()) for line in body.splitlines() if line.strip()]
    # Body content is kept in the activity process; never passed to workflows.
    return ParsedDocument(document_id=document_id,
        pages=[Page(number=0, text=render_text(elements), elements=elements)],
        source_sha256=hashlib.sha256(body.encode()).hexdigest())
