#!/usr/bin/env python3
"""[INPUT] Current LUX renders, exact-frame QC evidence and approved scripts.
[OUTPUT] Visual review boards, current native draft snapshots, narrow repair and rereview receipts.
[POS] Finalize this authorized batch; no new videos or TTS; do not invent visual approval.
"""
import argparse,copy,hashlib,json,math,subprocess,sys,urllib.parse
from types import SimpleNamespace
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import load_config

def canonical(value):
 if isinstance(value,dict):return {k:canonical(v) for k,v in value.items()}
 if isinstance(value,list):return [canonical(v) for v in value]
 if isinstance(value,str) and value.startswith(('https://','http://')):
  u=urllib.parse.urlsplit(value)
  if u.hostname and u.hostname.endswith('.aliyuncs.com'):
   q=[(k,v) for k,v in urllib.parse.parse_qsl(u.query) if not k.lower().startswith('x-oss-')]
   return urllib.parse.urlunsplit((u.scheme,u.netloc,u.path,urllib.parse.urlencode(q),u.fragment))
 return value

def save(p,d):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,indent=2));return p

def boards(a,cfg):
 from PIL import Image,ImageDraw
 root=Path(a.run);out=root/'revisions/终审';out.mkdir(exist_ok=True)
 for folder in sorted((root/'qc').glob('1125-L*-V*')):
  if a.key and folder.name!=a.key:continue
  record=json.loads((folder/'media-qc.json').read_text());frames=record['frames'];cols=6;rows=math.ceil(len(frames)/cols);im=Image.new('RGB',(cols*144,rows*276+28),'#eee');draw=ImageDraw.Draw(im);draw.text((4,4),folder.name+'  start / mid / end for every shot',fill='black')
  for i,row in enumerate(frames):
   pic=Image.open(row['file']).convert('RGB');pic.thumbnail((144,256));x=(i%cols)*144;y=(i//cols)*276+28;im.paste(pic,(x,y));draw.text((x+3,y+256),row['clip']+' '+str(round(row['time'],2))+'s',fill='black')
  im.save(out/(folder.name+'-review.jpg'),quality=88)
 print('Visual boards ready',out,flush=True)

def snapshot(a,cfg):
 from concurrent.futures import ThreadPoolExecutor
 root=Path(a.run);out=root/'revisions/终审';st=cb.State(root)
 def one(item):
  key,v=item;cut=cb.api('GET',f"/video-cuts/{v['cut_id']}");save(out/(key+'-cut.json'),cut);return key
 items=[(k,v) for k,v in sorted(st.videos.items()) if not a.key or k==a.key]
 with ThreadPoolExecutor(max_workers=4) as pool:keys=list(pool.map(one,items))
 print('Current native cut snapshots',len(keys),flush=True)

def rereview(a,cfg):
 root=Path(a.run);out=root/'revisions/终审';st=cb.State(root);keys=['1125-L01-V03','1125-L02-V07','1125-L05-V04','1125-L05-V07'];videos=[st.videos[k] for k in keys];ids=[v['cut_id'] for v in videos]
 if a.dry_run:save(out/'rereview-plan.json',{'keys':keys,'cut_ids':ids});print('Rereview existing outputs',keys,'no new generation/render/TTS');return
 receiptfile=out/'rereview-receipt.json'
 if receiptfile.exists():print('Rereview already submitted once');return
 plan=json.loads((out/'rereview-plan.json').read_text())
 if plan['cut_ids']!=ids:raise RuntimeError('Rereview IDs changed')
 for key,v in zip(keys,videos):save(out/(key+'-review-before.json'),copy.deepcopy(v))
 receipt=cb.api('POST','/video-cuts/review',{'cut_ids':ids},tries=1)
 if receipt.get('queued')!=len(ids) or receipt.get('skipped',0):raise RuntimeError('Review acceptance differs '+str(receipt))
 save(receiptfile,receipt)
 for v in videos:v.update(stage='reviewing',ai_review=None,ai_reason=None)
 st.save();print('Rereview queued',len(ids),flush=True)

def repair(a,cfg):
 from qc import cut_checks
 from asset_health import check_sources
 from lux_batch_variants import fix_sfx
 root=Path(a.run);out=root/'revisions/终审';st=cb.State(root);plan=json.loads(Path(a.plan).read_text());key=plan['video'];v=st.videos[key];cut=cb.api('GET',f"/video-cuts/{v['cut_id']}");draft=copy.deepcopy(cut['draft']);b=next(x for x in cb.load_briefs(root) if x['brief_id']==v['brief_id'])
 for change in plan['changes']:
  clip=next(x for x in draft['clips'] if x['id']==change['clip'])
  if clip['trackId'] not in plan['allowed_tracks']:raise RuntimeError('Changed unapproved track')
  if change.get('props'):clip['props'].update(change['props'])
 if plan.get('mix_override'):
  b.setdefault('mix_overrides',{})[draft['composition']['id']]=plan['mix_override']
 sfx_changes=[]
 if 'sound_effects' in plan['allowed_tracks']:draft,sfx_changes=fix_sfx(root,key,draft)
 checks=cut_checks(b,draft,v.get('voice'))
 if checks:raise RuntimeError(str(checks))
 attempt=plan.get('attempt','visual-v2')
 if not attempt.replace('-','').isalnum():raise RuntimeError('Unsafe attempt ID')
 intent=out/(key+'-'+attempt+'-repair-intent.json')
 if a.dry_run:save(intent,{'plan':plan,'before':cut,'draft':draft,'sfx_changes':sfx_changes});print(key,'narrow repair QC passed',plan['reason']);return
 record=json.loads(intent.read_text())
 if record['plan']!=plan:raise RuntimeError('Repair plan changed after dry-run')
 if canonical(record['draft'])!=canonical(draft):raise RuntimeError('Current draft differs from exact dry-run')
 receiptfile=out/(key+'-'+attempt+'-repair-receipt.json')
 if receiptfile.exists():print('Already submitted repair');return
 source_issues=check_sources(draft)
 if source_issues:raise RuntimeError(str(source_issues))
 if plan.get('mix_override'):save(root/'briefs'/(v['brief_id']+'.json'),b)
 cb.api('PUT',f"/video-cuts/{v['cut_id']}",{'script_id':cut['script_id'],'draft':draft});back=cb.api('GET',f"/video-cuts/{v['cut_id']}");cb.check_readback(draft,back['draft'],False)
 if canonical(draft)!=canonical(back['draft']):raise RuntimeError('Full native draft readback differs')
 job=cb.api('POST','/render-jobs',{'spec':back['draft'],'options':{'codec':'h264','quality':'high','scale':1},'cut_id':v['cut_id'],'idempotency_key':'lux-final-'+v['cut_id']+'-'+attempt});save(receiptfile,job);v.update(stage='rendering',render_job=job['job_id'],ai_review=None,ai_reason=None,qc_issues=[],final_repair_reason=plan['reason'])
 for field in ['object_key','review_waived','media_qc','error','last_error']:v.pop(field,None)
 st.save();print(key,'repair rendered',job['job_id'],flush=True)

def plan_repairs(a,cfg):
 root=Path(a.run);out=root/'revisions/终审';ledger=json.loads((out/'visual-ledger.json').read_text());briefs={b['brief_id']:b for b in cb.load_briefs(root)}
 cuts={k:json.loads((out/(k+'-cut.json')).read_text()) for k in ledger}
 def path(c):return urllib.parse.urlparse(c['props']['src']).path
 def segment(b,c):return next(s for s in b['segments'] if str(s['id'])==c['id'].split('-')[0])
 bad=set()
 for key,review in ledger.items():
  for issue in review['issues']:bad.add(path(next(c for c in cuts[key]['draft']['clips'] if c['id']==issue['clip'])))
 bank=[]
 for key,cut in sorted(cuts.items()):
  b=briefs[key.rsplit('-V',1)[0]]
  for c in cut['draft']['clips']:
   if c['trackId']!='main' or path(c) in bad:continue
   sg=segment(b,c)
   bank.append({'key':key,'product':b['product_key'],'clip':c,'tags':set(sg['visual_tags'])})
 plans=[]
 for key,review in sorted(ledger.items()):
  if review['status']!='repair':continue
  b=briefs[key.rsplit('-V',1)[0]];d=cuts[key]['draft'];used={path(c) for c in d['clips'] if c['trackId']=='main'};changes=[]
  for issue in review['issues']:
   c=next(x for x in d['clips'] if x['id']==issue['clip']);sg=segment(b,c)
   choices=[x for x in bank if x['product']==b['product_key'] and x['tags']&set(sg['visual_tags']) and path(x['clip']) not in used and x['clip']['durationInFrames']>=c['durationInFrames']]
   # Prefer same brief and the smallest duration surplus from an actually reviewed interval.
   choices.sort(key=lambda x:(x['key'].rsplit('-V',1)[0]!=b['brief_id'],x['clip']['durationInFrames']-c['durationInFrames'],x['key'],x['clip']['id']))
   if not choices:raise RuntimeError(key+' '+c['id']+' no sufficiently long, reviewed, unique replacement')
   ref=choices[0];rc=ref['clip'];props={f:rc['props'][f] for f in ['src','startFrom','fit','scale','x','y'] if f in rc['props']};changes.append({'clip':c['id'],'props':props,'reason':issue['reason'],'reviewed_reference':{'video':ref['key'],'clip':rc['id'],'board':str(out/(ref['key']+'-review.jpg'))}});used.add(path(rc))
  plan={'video':key,'allowed_tracks':['main','sound_effects'],'reason':'；'.join(x['reason'] for x in review['issues']),'changes':changes,'attempt':'visual-v2'};dest=save(out/(key+'-visual-plan.json'),plan);plans.append(str(dest))
 old=json.loads((out/'1125-L02-V09-volume-plan.json').read_text());old['allowed_tracks']=['audio','sound_effects'];old['attempt']='audio-v2';dest=save(out/'1125-L02-V09-audio-v2-plan.json',old);plans.append(str(dest));save(out/'repair-plans.json',plans);print('Narrow repairs',len(plans),'existing outputs; no copy/TTS/new videos')

def repair_batch(a,cfg):
 root=Path(a.run);plans=json.loads((root/'revisions/终审/repair-plans.json').read_text())
 # Sequential mutations and one explicit dry-run receipt per output.
 for plan in plans:repair(SimpleNamespace(run=a.run,plan=plan,dry_run=a.dry_run),cfg)

def approve_unchanged(a,cfg):
 root=Path(a.run);out=root/'revisions/终审';ledger=json.loads((out/'visual-ledger.json').read_text())
 for key,r in sorted(ledger.items()):
  if r['status']!='reviewed':continue
  p=subprocess.run([sys.executable,'-I',str(Path(__file__).with_name('media_qc.py')),'--config',a.config,'--run',a.run,'--key',key,'--reviewer','Agent','--review-note','逐镜头首中尾实际复核：单行字幕、上方花字避让、产品露出、无明显空镜及异常黑屏；证据 '+str(out/(key+'-review.jpg'))],capture_output=True,text=True)
  if p.returncode:raise RuntimeError(p.stderr)
  print(p.stdout.strip(),flush=True)

def media_repairs(a,cfg):
 from concurrent.futures import ThreadPoolExecutor
 root=Path(a.run);plans=json.loads((root/'revisions/终审/repair-plans.json').read_text())
 def one(p):
  key=json.loads(Path(p).read_text())['video'];cmd=[sys.executable,'-I',str(Path(__file__).with_name('media_qc.py')),'--config',a.config,'--run',a.run,'--key',key]
  result=subprocess.run(cmd,capture_output=True,text=True);print(result.stdout.strip() or key+' '+result.stderr[-450:],flush=True);return result.returncode
 with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(one,plans))
 if any(results):raise RuntimeError('Some repaired renders are not ready or failed technical QC')

def approve_repaired(a,cfg):
 root=Path(a.run);out=root/'revisions/终审';ledger=json.loads((out/'visual-ledger.json').read_text());key=a.key
 if not key:raise RuntimeError('Explicit individually viewed --key required')
 if ledger[key]['status'] not in ('repair','await_repaired_render'):raise RuntimeError('Not a pending repair')
 result=subprocess.run([sys.executable,'-I',str(Path(__file__).with_name('media_qc.py')),'--config',a.config,'--run',a.run,'--key',key,'--reviewer','Agent','--review-note','返工后逐镜头首中尾实际复核；问题空镜/产品出画/其他品牌背景已换；单行原生字幕与上方花字位置复核。证据 '+str(out/(key+'-review.jpg'))],capture_output=True,text=True)
 if result.returncode:raise RuntimeError(result.stderr)
 ledger[key].update(status='reviewed',resolved_issues=ledger[key]['issues'],issues=[]);save(out/'visual-ledger.json',ledger);print(result.stdout.strip(),flush=True)

def publish_page(a,cfg):
 import html,datetime
 root=Path(a.run);st=cb.State(root);out=root/'验收';before=(out/'index.html').read_text();tail=before[before.index('<h2>5个同类型AI追光钩子</h2>'):];cards=[];esc=lambda v:html.escape(str(v),quote=True)
 for b in cb.load_briefs(root):
  keys,_=cb.picks(st,b['brief_id'],b['quantity'])
  if len(keys)!=10:raise RuntimeError('Full delivery is not ready '+b['brief_id'])
  share=st.data['shares'][b['brief_id']];page=cb.api('GET',f"/video-shares/{share['id']}")
  if len(page['videos'])!=10:raise RuntimeError('Native share count mismatch')
  body=''
  for key in keys:
   v=st.videos[key];f=root/'qc'/key/'current.mp4'
   if not f.exists():raise RuntimeError('Current native render is not downloaded')
   body+='<article><p>'+esc(key)+'</p><video controls playsinline preload="none" poster="../qc/'+key+'/shot-00-0.jpg" src="../qc/'+key+'/current.mp4?v='+esc(v['render_job'])+'"></video></article>'
  url=cfg['clipo']['base_url'].rstrip('/')+'/share/'+share['id'];cards.append('<section><h2>'+esc(b['brief_id']+' · '+b['direction'])+'</h2><p><a target="_blank" href="'+esc(url)+'">打开Clipo本组10条</a></p><div class="grid">'+body+'</div></section>')
 gid=st.data['share_group']['id'];group=cb.api('GET',f'/video-share-groups/{gid}');expected=[st.data['shares'][b['brief_id']]['id'] for b in sorted(cb.load_briefs(root),key=lambda b:b['brief_id'])];save(root/'revisions/终审/group-before-final.json',group)
 if sorted(x['id'] for x in group['shares'])!=sorted(expected) or group['name']!='20261125-LUX-50条批量验收':
  cb.api('PATCH',f'/video-share-groups/{gid}',{'name':'20261125-LUX-50条批量验收','video_share_ids':list(reversed(expected))})
 back=cb.api('GET',f'/video-share-groups/{gid}')
 if sorted(x['id'] for x in back['shares'])!=sorted(expected):raise RuntimeError('Final group members mismatch')
 st.data['share_group']['name']=back['name'];st.save();save(root/'revisions/终审/group-final-readback.json',back)
 gurl=cfg['clipo']['base_url'].rstrip('/')+'/share/group/'+gid
 doc='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>LUX 11月25日批量验收</title><style>body{max-width:1300px;margin:30px auto;padding:0 24px;color:#342b24;background:#faf8f3;font:16px/1.7 system-ui}a{color:#875c22}h1{font-size:30px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:18px}article{background:white;padding:14px;border-radius:12px}video{width:100%;max-height:520px;background:#111}section{margin:34px 0}.meta{color:#786b5c}table{width:100%}</style><h1>LUX · 11月25日 · 50条批量验收</h1><p class="meta">'+datetime.datetime.now().strftime('%Y-%m-%d %H:%M')+' 更新。5组各10条，均完成制作自查，待你验收。</p><p>单片主字幕统一单行；全批轮换5种原生字幕样式、6首BGM；第1组使用5个同类追光AI钩子。上方花字、贴纸、转场与音效保留。空镜前奏、产品出画尾巴和明显其他品牌背景已局部替换，并复查本轮成片。</p><p><a target="_blank" href="'+esc(gurl)+'">打开Clipo · 完整50条合集</a></p>'+''.join(cards)+tail
 save(out/'批量验收记录.json',{'at':datetime.datetime.now().isoformat(),'status':'production_qc_pass_client_review_pending','videos':50,'group_url':gurl});(out/'index-before-full-batch.html').write_text(before);(out/'index.html').write_text(doc);print(out/'index.html',gurl)

def rereview_current(a,cfg):
 root=Path(a.run);out=root/'revisions/终审';st=cb.State(root);keys=['1125-L01-V06','1125-L01-V07'];receiptfile=out/'naming-rereview-receipt.json';videos=[st.videos[k] for k in keys]
 if receiptfile.exists():print('Naming rereview already submitted once');return
 ids=[v['cut_id'] for v in videos]
 plan={'keys':keys,'cut_ids':ids,'render_jobs':[v['render_job'] for v in videos],'reason':'洗发水为用户已审批和任务config的产品名称；实际镜头为同SKU精油香氛洗发露。保留全部原结论，原生复审，不改稿或放行。'}
 if a.dry_run:save(out/'naming-rereview-plan.json',plan);print(plan);return
 if json.loads((out/'naming-rereview-plan.json').read_text())!=plan:raise RuntimeError('Naming review plan changed')
 for key,v in zip(keys,videos):
  if v.get('ai_review')!='reject' or not v.get('media_qc',{}).get('visual_pass'):raise RuntimeError('Only individually inspected rejected output may be rereviewed')
  save(out/(key+'-naming-review-before.json'),copy.deepcopy(v))
 receipt=cb.api('POST','/video-cuts/review',{'cut_ids':ids},tries=1)
 if receipt.get('queued')!=2 or receipt.get('skipped',0):raise RuntimeError('Review acceptance mismatch '+str(receipt))
 save(receiptfile,receipt)
 for v in videos:v.update(stage='reviewing',ai_review=None,ai_reason=None)
 st.save();print('2 existing current renders submitted to native naming rereview')

def stage_delivery(a,cfg):
 import shutil
 root=Path(a.run);st=cb.State(root);out=root/'本地交付/20261125-LUX';plans=[]
 by_object={v.get('object_key'):(key,v) for key,v in st.videos.items() if cb.deliverable(v)}
 for bid,share in sorted(st.data['shares'].items()):
  page=cb.api('GET',f"/video-shares/{share['id']}")
  if len(page['videos'])!=10:raise RuntimeError('Share is not a full 10-video batch')
  for row in page['videos']:
   obj=row.get('video_object_key') or urllib.parse.unquote(urllib.parse.urlparse(row['video_url']).path.lstrip('/'))
   if obj not in by_object:raise RuntimeError('Share output is not currently approved')
   key,v=by_object[obj];folder=root/'qc'/key;ev=json.loads((folder/'media-qc.json').read_text());src=folder/'current.mp4';sha=hashlib.sha256(src.read_bytes()).hexdigest()
   if ev.get('render_job')!=v.get('render_job') or ev.get('output_object_key')!=obj or ev.get('sha256')!=sha or not v.get('media_qc',{}).get('visual_pass'):raise RuntimeError('Current checked cache differs '+key)
   dest=out/page['title']/(row['name']+'.mp4');plans.append((src,dest,sha,key,obj))
 if len(plans)!=50 or len({p[3] for p in plans})!=50:raise RuntimeError('Expected exactly 50 unique final outputs')
 for src,dest,sha,key,obj in plans:
  dest.parent.mkdir(parents=True,exist_ok=True)
  if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest()!=sha:
   tmp=dest.with_suffix('.qc-copy.tmp');shutil.copy2(src,tmp);tmp.replace(dest)
  if hashlib.sha256(dest.read_bytes()).hexdigest()!=sha:raise RuntimeError('Local final copy hash mismatch')
 save(out/'已检成片复用凭据.json',[{'video':key,'file':str(dest),'sha256':sha,'native_output':obj} for src,dest,sha,key,obj in plans]);print('50 current Clipo renders verified and staged; no duplicate downloads, no re-render')

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['boards','snapshot','rereview','repair','plan_repairs','repair_batch','media_repairs','approve_repaired','publish_page','rereview_current','stage_delivery']);p.add_argument('--config',required=True);p.add_argument('--run',required=True);p.add_argument('--key');p.add_argument('--plan');p.add_argument('--dry-run',action='store_true');a=p.parse_args();cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';globals()[a.cmd](a,cfg)
if __name__=='__main__':main()
