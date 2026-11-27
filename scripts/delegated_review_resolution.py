#!/usr/bin/env python3
"""Resolve an individually evidenced platform false positive under delegated batch QC.
Preserves raw AI verdict and customer approval fields; no platform writes or generation.
"""
import argparse,hashlib,json
from pathlib import Path

def evidence(ref):
 p=Path(ref['path'])
 return p.is_absolute() and p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==ref['sha256']

def valid(v):
 try:
  ref=v['delegated_quality_resolution'];q=v['media_qc'];p=Path(ref['path'])
  if not evidence(ref):return False
  d=json.loads(p.read_text())
  return (v.get('ai_review')=='reject' and d['decision']=='platform_false_positive' and d['reviewer']=='Agent'
   and bool(d['delegated_user_instruction']) and d['render_job']==v['render_job']==q['render_job']
   and d['object_key']==v['object_key']==q['output_object_key'] and d['video_sha256']==q['sha256']
   and d['raw_platform_reason']==v['ai_reason'] and q['technical_pass'] is True and q['visual_pass'] is True
   and isinstance(d['finding'],str) and bool(d['finding'].strip()) and bool(d['evidence'])
   and all(evidence(x) for x in d['evidence']))
 except (KeyError,TypeError,ValueError,OSError):return False

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--run',required=True);p.add_argument('--plan',required=True);p.add_argument('--dry-run',action='store_true');a=p.parse_args()
 import clipo_batch as cb
 from common import load_config
 from checked_batch_authorization import permits_checked_batch
 cb.CFG=load_config(a.config);st=cb.State(a.run);briefs={b['brief_id']:b for b in cb.load_briefs(a.run)};rows=json.loads(Path(a.plan).read_text())
 if not isinstance(rows,list) or not rows or len({x['key'] for x in rows})!=len(rows):raise RuntimeError('Unique exact resolution list required')
 checked=[]
 for row in rows:
  key=row['key'];v=st.videos[key];b=briefs[v['brief_id']];auth=b.get('checked_batch_authorization') or {};path=Path(row['report']);d=json.loads(path.read_text())
  if not permits_checked_batch(b) or key not in auth.get('exact_video_keys',[]) or d['key']!=key or d['delegated_user_instruction']!=auth['instruction']:raise RuntimeError('No matching current delegated batch inspection authorization')
  ref={'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()};candidate=dict(v,delegated_quality_resolution=ref)
  if not valid(candidate):raise RuntimeError('Incomplete, changed or unreviewed false-positive evidence '+key)
  checked.append((key,ref))
 if not a.dry_run:
  for key,ref in checked:st.videos[key]['delegated_quality_resolution']=ref
  st.save()
 print('DRY-RUN' if a.dry_run else 'RECORDED',len(checked),'exact delegated resolutions; raw AI results unchanged, no customer approval added')

if __name__=='__main__':main()
