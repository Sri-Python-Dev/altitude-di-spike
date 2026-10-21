"""Azure Document Intelligence (prebuilt-layout) adapter, producing the same ParsedDocument as
parse/docling_parser.py: pages with text, layout elements (headings, text, table cells with
row/column) and bboxes in PDF points, top-left origin.

Dev resource only; endpoint and key from AZURE_DI_ENDPOINT / AZURE_DI_KEY. Only files under
data/golden/ or data/synthetic/ are ever sent.
"""
import hashlib
import os
from pathlib import Path
from parse.models import LayoutElement, Page, ParsedDocument, render_text

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = (ROOT / 'data/golden', ROOT / 'data/synthetic')
HEADING_ROLES = {'title', 'sectionHeading'}

def configured() -> bool:
    return bool(os.getenv('AZURE_DI_ENDPOINT') and os.getenv('AZURE_DI_KEY'))

def _bbox(polygon, factor: float):
    """Azure polygons are flat [x1, y1, x2, y2, …] lists, top-left origin, in the page's unit."""
    if not polygon:
        return None
    xs, ys = polygon[0::2], polygon[1::2]
    return (round(min(xs) * factor, 2), round(min(ys) * factor, 2), round(max(xs) * factor, 2), round(max(ys) * factor, 2))

def _region(item):
    regions = getattr(item, 'bounding_regions', None) or []
    return regions[0] if regions else None

def to_parsed_document(result, document_id: str, source_sha256: str | None = None) -> ParsedDocument:
    """Convert an AnalyzeResult (prebuilt-layout) into our ParsedDocument. Pure; no network."""
    factors, pages = {}, {}
    for p in result.pages or []:
        factors[p.page_number] = 72.0 if (p.unit or '').lower() == 'inch' else 1.0  # PDFs come back in inches
        pages[p.page_number] = Page(number=p.page_number, text='', width=round((p.width or 0) * factors[p.page_number], 2) or None,
                                    height=round((p.height or 0) * factors[p.page_number], 2) or None)
    # Paragraphs that are table cells belong to their table, not to the running text.
    in_table: dict[int, int] = {}
    for t_index, table in enumerate(result.tables or []):
        for cell in table.cells or []:
            for ref in cell.elements or []:
                if ref.startswith('/paragraphs/'):
                    in_table[int(ref.rsplit('/', 1)[1])] = t_index
    emitted, tables = set(), []

    def emit_table(t_index: int):
        table = result.tables[t_index]
        cells = sorted(table.cells or [], key=lambda c: (c.row_index, c.column_index))
        first = _region(cells[0]) if cells else _region(table)
        page_no = first.page_number if first else 1
        tables.append({'page': page_no, 'rows': table.row_count, 'cols': table.column_count, 'cells': len(cells)})
        for cell in cells:
            region = _region(cell)
            n = region.page_number if region else page_no
            pages[n].elements.append(LayoutElement(text=cell.content or '', bbox=_bbox(region.polygon, factors.get(n, 1)) if region else None,
                                                   kind='cell', table=len(tables) - 1, row=cell.row_index, col=cell.column_index))

    for index, paragraph in enumerate(result.paragraphs or []):
        if index in in_table:
            if in_table[index] not in emitted:
                emitted.add(in_table[index])
                emit_table(in_table[index])
            continue
        region = _region(paragraph)
        if region is None or region.page_number not in pages:
            continue
        kind = 'heading' if getattr(paragraph, 'role', None) in HEADING_ROLES else 'text'
        pages[region.page_number].elements.append(
            LayoutElement(text=paragraph.content, bbox=_bbox(region.polygon, factors[region.page_number]), kind=kind))
    for t_index in range(len(result.tables or [])):
        if t_index not in emitted:  # tables with no paragraph references
            emit_table(t_index)
    for page in pages.values():
        page.text = render_text(page.elements)
    # Azure reads every page as an image, so treat it as OCR.
    return ParsedDocument(document_id=document_id, pages=sorted(pages.values(), key=lambda p: p.number), ocr=True,
                          tables=tables, source_sha256=source_sha256)

def parse_azure(path: str | Path, document_id: str | None = None) -> ParsedDocument:
    from parse.docling_parser import check_pdf
    path = Path(path).resolve()
    # Never upload outside the cleared golden set or the synthetic documents.
    if not any(path.is_relative_to(a.resolve()) for a in ALLOWED):
        raise PermissionError('Document is outside approved test directories')
    if not configured():
        raise RuntimeError('Azure Document Intelligence is not configured (AZURE_DI_ENDPOINT, AZURE_DI_KEY)')
    check_pdf(path)  # typed errors for password-protected and corrupt PDFs, before anything is sent
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.core.credentials import AzureKeyCredential
    client = DocumentIntelligenceClient(endpoint=os.environ['AZURE_DI_ENDPOINT'], credential=AzureKeyCredential(os.environ['AZURE_DI_KEY']))
    data = path.read_bytes()
    result = client.begin_analyze_document('prebuilt-layout', body=data, content_type='application/octet-stream').result()
    return to_parsed_document(result, document_id or path.stem, hashlib.sha256(data).hexdigest())
