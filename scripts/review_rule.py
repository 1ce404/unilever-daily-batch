#!/usr/bin/env python3
"""Apply one explicitly authorized product-review clause, with exact backups and readback.
No generation, review override, or change to any other clause. See scripts/CLAUDE.md.
"""
import argparse,json,sys,time,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import load_config

def revise(prompt,old,new):
 if not old or not new or prompt.count(old)!=1:raise RuntimeError('Expected exactly one unchanged review clause')
 return prompt.replace(old,new,1)

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--plan',required=True);p.add_argument('--out',required=True);p.add_argument('--dry-run',action='store_true');a=p.parse_args()
 cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';plan=json.loads(Path(a.plan).read_text());auth=plan.get('authorization') or {}
 if not auth.get('by') or not auth.get('quote'):raise RuntimeError('Explicit shared-review-rule authorization required')
 if plan['product_id'] not in [x['clipo_product_id'] for x in cfg['products'].values()] or plan['prompt_type']!='video_cut_review':raise RuntimeError('Wrong product or prompt type')
 path='/products/'+plan['product_id']+'/prompts';rows=cb.api('GET',path);current=next(x for x in rows if x['prompt_type']==plan['prompt_type']);out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 save=lambda name,x:(out/name).write_text(json.dumps(x,ensure_ascii=False,indent=2))
 budget=out/'budget.json';request=out/'intent.json';receipt=out/'after.json'
 if receipt.exists():
  after=json.loads(receipt.read_text())
  if after['prompt']!=current['prompt']:raise RuntimeError('Review prompt changed after this revision')
  print('Already applied and currently verified');return
 wanted=revise(current['prompt'],plan['old_clause'],plan['proposed_clause']);sig=hashlib.sha256(json.dumps({'before':current['prompt'],'after':wanted,'plan':plan},sort_keys=True).encode()).hexdigest()
 if a.dry_run:
  save('before.json',current);save('proposed.json',{'prompt':wanted});save('budget.json',{'at':time.time(),'hash':sig});print('DRY-RUN 1 authorized review clause; no generation/render; other prompt text preserved');return
 prior=json.loads(budget.read_text())
 if prior['hash']!=sig or time.time()-prior['at']>3600:raise RuntimeError('Missing exact recent clause dry-run')
 from sync_skill import assert_current
 assert_current(Path(__file__).resolve().parent.parent)
 if request.exists():raise RuntimeError('Prior prompt write uncertain; reconcile before retry')
 save('intent.json',{'hash':sig,'authorization':auth,'prompt':wanted});cb.api('PUT',path+'/'+plan['prompt_type'],{'prompt':wanted},tries=1)
 after=next(x for x in cb.api('GET',path) if x['prompt_type']==plan['prompt_type'])
 if after['prompt']!=wanted:raise RuntimeError('Full prompt readback differs')
 save('after.json',after);print('Authorized clause applied; full prompt readback matches')
if __name__=='__main__':main()
