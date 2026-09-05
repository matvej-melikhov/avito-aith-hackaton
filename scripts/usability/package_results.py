#!/usr/bin/env python3
"""Package this study's reports and evidence without cookies or signed URL queries."""
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit, urlunsplit

ROOT = Path.cwd()
SOURCE = Path('/tmp/usability-reports')
DEST = ROOT / 'docs/usability-study'
RESULTS = DEST / 'results'
EVIDENCE = DEST / 'evidence'
RESULTS.mkdir(exist_ok=True)
EVIDENCE.mkdir(exist_ok=True)
copied = {}

def strip_url(match):
    value=match.group(0)
    parsed=urlsplit(value)
    if any(term in parsed.query.lower() for term in ('signature','x-amz','token','credential')):
        return urlunsplit((parsed.scheme,parsed.netloc,parsed.path,'[redacted]',parsed.fragment))
    return value

def evidence_path(raw):
    source=Path(raw)
    if not source.is_file():
        return raw
    if source in copied:
        return copied[source]
    target=EVIDENCE / f'{source.parent.name}-{source.name}'
    if source.suffix.lower()=='.png':
        shutil.copyfile(source,target)
    elif source.suffix.lower()=='.json':
        target.write_text(json.dumps(clean(json.loads(source.read_text())),ensure_ascii=False,indent=2)+'\n')
    elif source.suffix.lower()=='.csv':
        target.write_text(source.read_text(encoding='utf-8-sig'))
    else:
        return raw
    result='../evidence/'+target.name
    copied[source]=result
    return result

def clean(value):
    if isinstance(value,dict):
        return {key:clean(item) for key,item in value.items() if key.lower() not in {'cookies','authorization','set-cookie'}}
    if isinstance(value,list):
        return [clean(item) for item in value]
    if isinstance(value,str):
        value=re.sub(r'https?://[^\s<>"\]]+',strip_url,value)
        value=re.sub(r'(?:/Users/vmgubin/Documents/avito-aith-hackaton/\.cache/usability/browser|/tmp/usability-reports/copy-browser)/[^\s)]+\.(?:png|json|csv)',lambda m:evidence_path(m.group(0)),value)
        return value
    return value

reports=sorted(SOURCE.glob('before-*.md'))+sorted(SOURCE.glob('after-*.md'))+[SOURCE/'copy-audit.md']
for source in reports:
    (RESULTS/source.name).write_text(clean(source.read_text()))
audit=clean(json.loads((SOURCE/'copy-audit.json').read_text()))
(RESULTS/'copy-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
for name in ['outcomes-before-group','outcomes-before-c2','outcomes-after-group','outcomes-after-valid-group','outcomes-after-c2']:
    source=ROOT/'.cache/usability'/f'{name}.json'
    (RESULTS/f'{name}.json').write_text(json.dumps(clean(json.loads(source.read_text())),ensure_ascii=False,indent=2)+'\n')
for name in ['final-layout.json','final-layout-320.png','final-layout-390.png','final-layout-1440.png','final-layout-1600.png']:
    evidence_path(str(ROOT/'.cache/usability'/name))
source=Path('/tmp/usability-copy-adjudication.json')
if source.exists():
    (RESULTS/'copy-applied-locations.json').write_text(source.read_text())
manifest={
    'before_commit':'4632ad5',
    'before_tag':'workspace-ux-before-20260905',
    'after_tag':'workspace-ux-after-20260905',
    'model':'gpt-6-astra', 'reasoning_effort':'low',
    'scenario_runs':15, 'copy_audit_agents':1,
    'operator_change':{'invalidated_run':'after-s1','retest':'after-s1-valid','change':'Positional control ids replaced with persistent per-element ids; removed controls fail instead of selecting another element.'},
    'limits':['Synthetic agent study, not human research','App AI used a local fixture, not a real model','One after S1 run was invalidated by an operator control-ID mistake','Intermediate after R1 and C1 findings were corrected and rerun'],
    'files':[{'path':str(p.relative_to(DEST)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for folder in (RESULTS,EVIDENCE) for p in sorted(folder.iterdir()) if p.is_file()],
}
(DEST/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'reports':len(reports),'evidence':len(copied),'files':len(manifest['files'])}))
