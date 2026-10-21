import hashlib
from functools import lru_cache
from pathlib import Path
from parse.models import ParsedDocument, Page, LayoutElement, render_text
from extract.errors import CorruptDocument, PasswordProtectedDocument

class ParseFailed(RuntimeError):
    def __str__(self):
        return 'Docling conversion did not succeed'

# A page with fewer extractable characters than this is treated as an image.
MIN_TEXT_LAYER_CHARS = 20

def check_pdf(path: str | Path) -> bool:
    """Raise a typed error for encrypted or corrupt PDFs; return True if it has a text layer."""
    import pypdfium2 as pdfium
    try:
        pdf = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as e:
        # Only the PDFium error class is inspected; nothing from the document is kept.
        if 'password' in str(e).lower():
            raise PasswordProtectedDocument() from None
        raise CorruptDocument() from None
    try:
        chars = 0
        for page in pdf:
            chars += len(page.get_textpage().get_text_range().strip())
        return chars >= MIN_TEXT_LAYER_CHARS
    finally:
        pdf.close()

@lru_cache(maxsize=2)
def _converter(ocr: bool):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import OcrMode, PdfPipelineOptions, RapidOcrOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    options = PdfPipelineOptions(do_ocr=ocr)
    if ocr:
        options.ocr_options = RapidOcrOptions(mode=OcrMode.FULL_PAGE)
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})

def parse_pdf(path: str | Path, document_id: str | None = None) -> ParsedDocument:
    path = Path(path)
    has_text = check_pdf(path)
    from docling.datamodel.base_models import ConversionStatus
    # The PDF already opened cleanly in check_pdf, so a failure here is a pipeline
    # fault (e.g. a missing OCR engine): raise it, retryable, rather than call the document corrupt.
    result = _converter(not has_text).convert(str(path), raises_on_error=False)
    if result.status != ConversionStatus.SUCCESS:
        raise ParseFailed()
    document = result.document
    pages = {int(n): Page(number=int(n), text='', width=p.size.width, height=p.size.height)
             for n, p in document.pages.items()}
    tables = []
    for item, _ in document.iterate_items():
        prov = getattr(item, 'prov', None) or []
        if not prov:
            continue
        n = int(prov[0].page_no)
        page = pages.setdefault(n, Page(number=n, text=''))
        if type(item).__name__ == 'TableItem':
            index = len(tables)
            cells = item.data.table_cells
            tables.append({'page': n, 'rows': item.data.num_rows, 'cols': item.data.num_cols, 'cells': len(cells)})
            rows: dict[int, list] = {}
            for cell in cells:
                rows.setdefault(cell.start_row_offset_idx, []).append(cell)
            for r in sorted(rows):
                row = sorted(rows[r], key=lambda c: c.start_col_offset_idx)
                for cell in row:
                    page.elements.append(LayoutElement(
                        text=cell.text, bbox=_top_left(cell.bbox, page.height), kind='cell',
                        table=index, row=r, col=cell.start_col_offset_idx))
            continue
        text = getattr(item, 'text', None)
        if not text:
            continue
        label = str(getattr(item, 'label', '')).lower()
        kind = 'heading' if 'header' in label or 'title' in label else 'text'
        for entry in prov:
            target = pages.setdefault(int(entry.page_no), Page(number=int(entry.page_no), text=''))
            target.elements.append(LayoutElement(text=text, bbox=_top_left(entry.bbox, target.height), kind=kind))
    for page in pages.values():
        page.text = render_text(page.elements)
    return ParsedDocument(document_id=document_id or path.stem,
                          pages=sorted(pages.values(), key=lambda p: p.number),
                          ocr=not has_text, tables=tables,
                          source_sha256=hashlib.sha256(path.read_bytes()).hexdigest())

def _top_left(bbox, page_height):
    if bbox is None:
        return None
    if page_height is not None:
        bbox = bbox.to_top_left_origin(page_height)
    return (round(bbox.l, 2), round(bbox.t, 2), round(bbox.r, 2), round(bbox.b, 2))
