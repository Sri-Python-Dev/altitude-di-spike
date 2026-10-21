"""Interactive golden labeling. Run: uv run python -m tools.label FILE.pdf --labeller NAME"""
import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ''):  # run as a script (`uv run tools/label.py`): make the repo root importable
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
import argparse, hashlib, json, re
from datetime import datetime, timezone
from pathlib import Path
from parse.docling_parser import parse_pdf
from extract.ground import FIELD_LABELS, locate_span, span_elements

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('pdf',type=Path)
    ap.add_argument('--labeller',required=True)
    ap.add_argument('--output',type=Path)
    args=ap.parse_args()
    parsed=parse_pdf(args.pdf)
    for p in parsed.pages:
        print(f'\n=== PAGE {p.number} ===')
        for n,line in enumerate(p.text.splitlines(),1): print(f'{n:04}: {line}')
    fields=[]
    rows={}
    print('Enter empty field name to finish. Spans must match the parsed text.')
    print('Label line_items[i].part_number_quoted before the other fields of that item.')
    while True:
        name=input('Field name: ').strip()
        if not name: break
        value=input('Value: ').strip()
        span=input('Exact verbatim span: ')
        candidates=[p for p in parsed.pages if locate_span(span,p)] if span else []
        if not candidates:
            print('REJECTED: span not present in parsed document')
            continue
        p=candidates[0]
        item=re.match(r'line_items\[(\d+)\]\.',name)
        found=span_elements(span,p,rows.get(item.group(1)) if item else None,FIELD_LABELS.get(name.rsplit('.',1)[-1],()))
        if not found:
            print('REJECTED: no matching Docling layout bbox')
            continue
        if item and name.endswith('.part_number_quoted') and found[0].kind=='cell':
            rows[item.group(1)]=(found[0].table,found[0].row)
        boxes=[e.bbox for e in found]
        bbox=(min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes))
        fields.append({'name':name,'value':value,'page':p.number,'bbox':bbox,'verbatim_span':span,'labeller':args.labeller,'labelled_at':datetime.now(timezone.utc).isoformat()})
    out=args.output or args.pdf.with_suffix('.json')
    payload={'document_id':args.pdf.stem,'sha256':hashlib.sha256(args.pdf.read_bytes()).hexdigest(),'doc_type':'quote','fields':fields}
    out.write_text(json.dumps(payload,indent=2)+'\n')
    print(f'Saved {out}')
if __name__=='__main__': main()
