from pydantic import BaseModel, Field

# All bboxes are (x0, y0, x1, y1) in PDF points with a TOP-LEFT origin
# (y grows downwards), so they can be drawn directly on a rendered page.
BBox = tuple[float, float, float, float]

class LayoutElement(BaseModel):
    text: str
    bbox: BBox | None = None
    kind: str = 'text'  # text | heading | cell
    table: int | None = None  # table index within the document, for cells
    row: int | None = None
    col: int | None = None

class Page(BaseModel):
    number: int  # 1-based for PDFs; 0 means an email body
    text: str
    elements: list[LayoutElement] = Field(default_factory=list)
    width: float | None = None
    height: float | None = None

class ParsedDocument(BaseModel):
    document_id: str
    pages: list[Page]
    ocr: bool = False
    tables: list[dict] = Field(default_factory=list)
    source_sha256: str | None = None  # of the PDF, or of the body text for email bodies
    segment: int | None = None  # 1-based quote index after split_quotes

def render_text(elements: list[LayoutElement]) -> str:
    """Page text in reading order; cells of one table row are joined with ' | '."""
    lines, row_key, row = [], None, []
    for e in elements:
        key = (e.table, e.row) if e.kind == 'cell' else None
        if row and key != row_key:
            lines.append(' | '.join(row)); row = []
        if key is None:
            lines.append(e.text)
        else:
            row.append(e.text)
        row_key = key
    if row:
        lines.append(' | '.join(row))
    return ''.join(line + '\n' for line in lines)
