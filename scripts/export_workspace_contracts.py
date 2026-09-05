"""Export only v2 schemas without modifying frozen core contracts."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from review_platform.main import create_app
from review_platform.contracts.workspace import SelfReviewRequest, SelfReviewEvent, ReviewAssistRequest, ReviewAssistEvent

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'specs/005-workspace-completion/contracts'

def main() -> None:
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    schema=create_app().openapi()
    schema['info']={'title':'Review Workspace Extension','version':'2.0.0'}
    schema['servers']=[{'url':'/api'}]
    schema['paths']={path.removeprefix('/api'):value for path,value in schema['paths'].items() if path.startswith('/api/v2/')}
    documents={'openapi.json':schema,'self-review-request.schema.json':SelfReviewRequest.model_json_schema(),'self-review-event.schema.json':SelfReviewEvent.model_json_schema(),'review-assist-request.schema.json':ReviewAssistRequest.model_json_schema(),'review-assist-event.schema.json':ReviewAssistEvent.model_json_schema()}
    manifest={}
    for name,value in documents.items():
        text=json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True)+'\n';manifest[name]=hashlib.sha256(text.encode()).hexdigest()
        path=DEST/name
        if args.check:
            if not path.exists() or path.read_text()!=text:raise SystemExit(f'stale: {name}')
        else:path.write_text(text)
    text=json.dumps({'version':'2.0.0','sha256':manifest},indent=2,sort_keys=True)+'\n'
    if args.check:
        if (DEST/'manifest.json').read_text()!=text:raise SystemExit('stale manifest')
    else:(DEST/'manifest.json').write_text(text)
    print('Workspace contracts verified' if args.check else 'Workspace contracts exported')

if __name__=='__main__':main()
