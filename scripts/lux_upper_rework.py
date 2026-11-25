#!/usr/bin/env python3
"""Native upper-title-only revision for the approved LUX brief4 batch; protect all other tracks."""
import argparse,copy,hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import load_config
from lux_final_qc import canonical,save
BID='1125-L04'
def inventory(a,cfg):
 root=Path(a.run);out=root/'revisions/brief4花字返工';st=cb.State(root)
 catalog=cb.api('GET','/resource/remotion-effect-template?limit=100');save(out/'catalog.json',catalog);rows=catalog if isinstance(catalog,list) else catalog['items']
 for x in rows:print(x['id'],x.get('name'),x.get('category'),str(x.get('description',''))[:100],flush=True)
 for key,v in sorted(st.videos.items()):
  if v['brief_id']!=BID:continue
  cut=cb.api('GET',f"/video-cuts/{v['cut_id']}");save(out/(key+'-before.json'),{'video':copy.deepcopy(v),'cut':cut})
 sid=st.data['briefs'][BID]['script_id'];save(out/'script-before.json',cb.api('GET',f'/video-scripts/{sid}'))
 print('Read-only inventory and current brief4 snapshots saved',flush=True)
def choices(a,cfg):
 import subprocess
 root=Path(a.run);out=root/'revisions/brief4花字返工';catalog=json.loads((out/'catalog.json').read_text());rows=catalog if isinstance(catalog,list) else catalog['items'];names=['网格错版','贴纸爆点','金环扫光','点阵标注','星尘缓渡','悬浮光晕','封面文案02','卖点三连']
 from PIL import Image,ImageDraw
 sheet=Image.new('RGB',(144*4,280*2),'#222');draw=ImageDraw.Draw(sheet)
 for i,name in enumerate(names):
  meta=next(x for x in rows if x['name']==name);native=cb.api('GET','/resource/remotion-effect-template/'+meta['id']);save(out/(meta['id']+'.json'),native);url=meta.get('previewUrl');file=out/(meta['id']+'-preview');cb.fetch_file(url,file);pic=out/(meta['id']+'-preview.jpg');subprocess.run(['ffmpeg','-v','error','-y','-ss','0' if meta.get('durationInFrames')==1 else '0.7','-i',str(file),'-frames:v','1',str(pic)],check=True);im=Image.open(pic).convert('RGB');im.thumbnail((144,256));x=i%4*144;y=i//4*280;sheet.paste(im,(x,y));draw.text((x+2,y+258),str(i+1)+' '+meta['id'][-4:],fill='white');print(i+1,name,native.get('params'),flush=True)
 sheet.save(out/'native-choice-board.jpg',quality=90)

FAMILIES=[('标注','6afd05026af35ab34a0a5cb7','#DFC0CF',.72,.020,0),('柔光轻框','6afd05036af35ab34a0a5cc0','#C5B3D5',.92,.020,0),('金色轻弹','6afd05026af35ab34a0a5cbd','#E1C28B',.92,.018,0),('光晕胶囊','6afd05036af35ab34a0a5cc4','#CCB6D7',1.02,.006,-60)]
def upper(c):return c.get('id','').startswith('upper-')
def validate(before,after,sfx_changes=()):
 for change in sfx_changes:
  old=next(c for c in before['clips'] if c['id']==change['clip']);new=next(c for c in after['clips'] if c['id']==change['clip'])
  if old['trackId']!='sound_effects' or new['trackId']!='sound_effects' or old['from']!=new['from'] or old['component']!=new['component']:raise RuntimeError('Only unavailable SFX may be repaired at the original cue')
  allowed={'src','startFrom','volume','playbackRate'}
  if {k:v for k,v in old['props'].items() if k not in allowed}!={k:v for k,v in new['props'].items() if k not in allowed}:raise RuntimeError('SFX repair changed other properties')
 protected=lambda d:{**d,'clips':[x for x in d['clips'] if not upper(x) and x['id'] not in {y['clip'] for y in sfx_changes}]}
 if canonical(protected(before))!=canonical(protected(after)):raise RuntimeError('Non-title content changed')
 old=[c for c in before['clips'] if upper(c)];new=[c for c in after['clips'] if upper(c)]
 if len(old)!=3 or len(new)!=3:raise RuntimeError('Upper title count changed')
 for a,b in zip(old,new):
  if {k:v for k,v in a.items() if k!='props'}!={k:v for k,v in b.items() if k!='props'} or a['props']['params']['text']!=b['props']['params']['text']:raise RuntimeError('Upper text/timing/track changed')
  if b['props']['params']['position']!='top_center' or not 0<=b['props']['params']['safeTop']<=.055:raise RuntimeError('Unsafe upper position')

def plan(a,cfg):
 from lux_batch_variants import fix_sfx
 from qc import cut_checks
 root=Path(a.run);out=root/'revisions/brief4花字返工';plans=[]
 for n in range(1,11):
  key=f'{BID}-V{n:02d}';before=json.loads((out/(key+'-before.json')).read_text());d=copy.deepcopy(before['cut']['draft']);family,tid,color,scale,top,y=FAMILIES[(n-1)%4];native=json.loads((out/(tid+'.json')).read_text())
  for c in d['clips']:
   if upper(c):
    params={**native.get('params',{}),**c['props']['params'],'accent':color,'position':'top_center','safeTop':top,'safeBottom':.1,'maxWidth':780,'scale':scale,'x':0,'y':y,'plate':False};c['props']['code']=native['code'];c['props']['params']=params
  d,changes=fix_sfx(root,key,d);validate(before['cut']['draft'],d,changes);brief=next(b for b in cb.load_briefs(root) if b['brief_id']==BID);issues=cut_checks(brief,d,before['video'].get('voice'))
  if issues:raise RuntimeError(str(issues))
  save(out/(key+'-plan.json'),{'before':before,'draft':d,'family':family,'template_id':tid,'sfx_repairs':changes});plans.append({'video':key,'family':family,'template_id':tid,'expired_sfx_refs':len(changes)})
 old=json.loads((out/'script-before.json').read_text());wanted=copy.deepcopy(old)
 for i,seg in enumerate([x for x in wanted['segments'] if x.get('effect')]):
  tid=FAMILIES[i%3][1];nat=json.loads((out/(tid+'.json')).read_text());_,_,accent,scale,top,y=FAMILIES[i%3];seg['effect']={'template_id':tid,'params':{**nat.get('params',{}),**seg['effect']['params'],'accent':accent,'safeTop':top,'scale':scale,'y':y}}
 save(out/'script-plan.json',{'before':old,'wanted':wanted});save(out/'budget.json',{'count':10,'new_videos':0,'tts':0,'scope':BID,'plans':plans});print('DRY RUN: 10 existing brief4 renders; 4 native upper-title families; no new copy/TTS/footage/BGM.');print(json.dumps(plans,ensure_ascii=False),flush=True)

def submit(a,cfg):
 from asset_health import check_sources
 from qc import cut_checks
 root=Path(a.run);out=root/'revisions/brief4花字返工';budget=json.loads((out/'budget.json').read_text());st=cb.State(root)
 if budget['count']!=10 or budget['new_videos'] or budget['tts']:raise RuntimeError('Invalid narrow revision budget')
 sp=json.loads((out/'script-plan.json').read_text());wanted=sp['wanted'];current=cb.api('GET',f"/video-scripts/{wanted['id']}")
 if canonical(current)!=canonical(sp['before']) and canonical(current)!=canonical(wanted):raise RuntimeError('Script changed since plan')
 body={k:wanted[k] for k in ['product_id','title','video','config','vo_rewrite_prompt','segments']};cb.api('PUT',f"/video-scripts/{wanted['id']}",body);back=cb.api('GET',f"/video-scripts/{wanted['id']}")
 if any(back[k]!=body[k] for k in body):raise RuntimeError('Script readback mismatch')
 save(out/'script-after.json',back)
 bp=root/'briefs'/(BID+'.json');b=json.loads(bp.read_text());b['upper_title_variation']={'min_actual_sets':4,'native_template_ids':[x[1] for x in FAMILIES],'note':'用户：brief4的上方花字也是一样的样式，不OK，改一下。只改上方花字；底部字幕、文案、配音、画面和BGM保留。'};save(bp,b)
 for item in budget['plans']:
  key=item['video'];v=st.videos[key];receipt=out/(key+'-render.json')
  if receipt.exists():print(key,'already submitted');continue
  record=json.loads((out/(key+'-plan.json')).read_text());before=record['before'];draft=record['draft'];current=cb.api('GET',f"/video-cuts/{v['cut_id']}")
  if canonical(current['draft'])!=canonical(before['cut']['draft']):raise RuntimeError('Current cut differs from plan '+key)
  validate(current['draft'],draft,record['sfx_repairs']);issues=cut_checks(b,draft,v.get('voice'))+check_sources(draft)
  if issues:raise RuntimeError(str(issues))
  cb.api('PUT',f"/video-cuts/{v['cut_id']}",{'script_id':current['script_id'],'draft':draft});readback=cb.api('GET',f"/video-cuts/{v['cut_id']}")
  if canonical(readback['draft'])!=canonical(draft):raise RuntimeError('Full draft readback mismatch')
  save(out/(key+'-after.json'),readback);job=cb.api('POST','/render-jobs',{'spec':readback['draft'],'options':{'codec':'h264','quality':'high','scale':1},'cut_id':v['cut_id'],'idempotency_key':'lux-upper4-'+v['cut_id']},tries=1);save(receipt,job)
  v.update(stage='rendering',render_job=job['job_id'],ai_review=None,ai_reason=None,qc_issues=[],upper_title_family=record['family'],upper_template_id=record['template_id'])
  for field in ['media_qc','object_key','review_waived','error','last_error']:v.pop(field,None)
  st.save();print(key,record['family'],'native render',job['job_id'],flush=True)

def media(a,cfg):
 from concurrent.futures import ThreadPoolExecutor
 import subprocess,time
 root=Path(a.run)
 def one(n):
  key=f'{BID}-V{n:02d}';deadline=time.time()+480
  while True:
   v=cb.State(root).videos[key]
   if v.get('stage')=='done' and v.get('object_key'):break
   if v.get('stage')=='failed' or time.time()>deadline:raise RuntimeError(key+' current native render not ready')
   time.sleep(2)
  cmd=[sys.executable,'-I',str(Path(__file__).with_name('media_qc.py')),'--config',a.config,'--run',a.run,'--key',key];r=subprocess.run(cmd,capture_output=True,text=True)
  print(r.stdout.strip() or r.stderr[-400:],flush=True)
  if r.returncode:raise RuntimeError(key+' technical check failed')
 with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(one,[2,3,6,7,10] if a.round==2 else range(1,11)))

def boards(a,cfg):
 import subprocess
 from PIL import Image,ImageDraw
 root=Path(a.run);out=root/'revisions/brief4花字返工';st=cb.State(root);sheets=[Image.new('RGB',(1080,440),'#eee') for _ in range(3)]
 for n in range(1,11):
  key=f'{BID}-V{n:02d}';v=st.videos[key];suffix='-r2' if (out/(key+'-r2-render.json')).exists() else '';record=json.loads((out/(key+suffix+'-plan.json')).read_text());job=json.loads((out/(key+suffix+'-render.json')).read_text())
  if v['render_job']!=job['job_id'] or not v.get('media_qc',{}).get('technical_pass'):raise RuntimeError('Cannot review stale/unmeasured output '+key)
  fps=record['draft']['composition']['fps'];clips=sorted([c for c in record['draft']['clips'] if upper(c)],key=lambda c:c['from'])
  for i,c in enumerate(clips):
   t=(c['from']+min(c['durationInFrames']*.6,30))/fps;dest=out/(key+f'-upper-{i}.jpg');subprocess.run(['ffmpeg','-v','error','-y','-ss',str(t),'-i',str(root/'qc'/key/'current.mp4'),'-frames:v','1','-vf','scale=432:-2',str(dest)],check=True);im=Image.open(dest).convert('RGB');im=im.crop((0,0,432,400));im=im.resize((216,200));x=(n-1)%5*216;y=(n-1)//5*220;sheets[i].paste(im,(x,y));ImageDraw.Draw(sheets[i]).text((x+4,y+202),key+' / '+str((n-1)%4+1),fill='black')
 for i,im in enumerate(sheets):im.save(out/f'upper-event-{i+1}-10-videos.jpg',quality=92)
 print('3 actual rendered upper-event boards, each covers all10 current outputs',flush=True)

def case(a,cfg):
 import subprocess
 root=Path(a.run);out=root/'revisions/brief4花字返工';key=BID+'-V06'
 for t in [12.4,12.8]:subprocess.run(['ffmpeg','-v','error','-y','-ss',str(t),'-i',str(root/'qc'/key/'current.mp4'),'-frames:v','1','-vf','scale=432:-2',str(out/(key+'-case-'+str(t)+'.jpg'))],check=True)

def rereview(a,cfg):
 root=Path(a.run);out=root/'revisions/brief4花字返工';st=cb.State(root);key=BID+'-V06';v=st.videos[key];receipt=out/'V06-rereview.json'
 if receipt.exists():print('One scoped rereview already submitted');return
 job=json.loads((out/(key+'-render.json')).read_text())
 if v.get('ai_review')!='reject' or v['render_job']!=job['job_id'] or not v.get('media_qc',{}).get('technical_pass'):raise RuntimeError('Current rereview candidate mismatch')
 save(out/'V06-ai-rejection-before.json',copy.deepcopy(v));result=cb.api('POST','/video-cuts/review',{'cut_ids':[v['cut_id']]},tries=1)
 if result.get('queued')!=1 or result.get('skipped',0):raise RuntimeError('Rereview acceptance mismatch')
 save(receipt,result);v.update(stage='reviewing',ai_review=None,ai_reason=None);st.save();print('One native rereview queued; original result preserved; no new render or waiver')

def compact_choices(a,cfg):
 root=Path(a.run);out=root/'revisions/brief4花字返工';catalog=json.loads((out/'catalog.json').read_text());rows=catalog if isinstance(catalog,list) else catalog['items']
 for name in ['液体填充','磁吸按钮','呼吸按钮','霓虹闪烁']:
  meta=next(x for x in rows if x['name']==name);native=cb.api('GET','/resource/remotion-effect-template/'+meta['id']);save(out/(meta['id']+'.json'),native);print(name,meta['id'],native.get('params'));s=native['code'];start=s.rfind('export const');print(s[start:start+1000],s[-1800:])

R2=[2,3,6,7,10]
def r2_plan(a,cfg):
 root=Path(a.run);out=root/'revisions/brief4花字返工';st=cb.State(root)
 for n in R2:
  key=f'{BID}-V{n:02d}';v=st.videos[key];cut=cb.api('GET',f"/video-cuts/{v['cut_id']}");draft=copy.deepcopy(cut['draft']);family,tid,color,scale,top,y=FAMILIES[(n-1)%4];native=json.loads((out/(tid+'.json')).read_text())
  for c in draft['clips']:
   if upper(c):c['props'].update(code=native['code'],params={**c['props']['params'],'accent':color,'scale':scale,'safeTop':top,'y':y})
  validate(cut['draft'],draft);save(out/(key+'-r2-plan.json'),{'before':{'cut':cut,'video':copy.deepcopy(v)},'draft':draft,'family':family,'template_id':tid,'sfx_repairs':[]})
 save(out/'r2-budget.json',{'keys':[f'{BID}-V{n:02d}' for n in R2],'renders':5,'new_videos':0,'tts':0,'reason':'实际新成片中圆环/网格跨人物发顶，改为紧凑的原生柔光框/金色轻弹，主画面和声音不改。'});print('DRY RUN 5 upper-title occupancy corrections; no new videos/TTS/audio changes')

def r2_submit(a,cfg):
 from asset_health import check_sources
 from qc import cut_checks
 root=Path(a.run);out=root/'revisions/brief4花字返工';st=cb.State(root);budget=json.loads((out/'r2-budget.json').read_text());b=next(x for x in cb.load_briefs(root) if x['brief_id']==BID)
 if budget['renders']!=5 or budget['tts'] or budget['new_videos']:raise RuntimeError('Wrong narrow round2 budget')
 sid=st.data['briefs'][BID]['script_id'];script=cb.api('GET',f'/video-scripts/{sid}');save(out/'script-before-r2.json',script);wanted=copy.deepcopy(script)
 for i,seg in enumerate([s for s in wanted['segments'] if s.get('effect')]):
  family,tid,color,scale,top,y=FAMILIES[i%3];seg['effect']['template_id']=tid;seg['effect']['params'].update(accent=color,scale=scale,safeTop=top,y=y)
 body={k:wanted[k] for k in ['product_id','title','video','config','vo_rewrite_prompt','segments']};cb.api('PUT',f'/video-scripts/{sid}',body);readback=cb.api('GET',f'/video-scripts/{sid}')
 if any(body[k]!=readback[k] for k in body):raise RuntimeError('Parent script round2 readback differs')
 save(out/'script-after-r2.json',readback);b['upper_title_variation']['native_template_ids']=[x[1] for x in FAMILIES];save(root/'briefs'/(BID+'.json'),b)
 for key in budget['keys']:
  receipt=out/(key+'-r2-render.json')
  if receipt.exists():print(key,'round2 already submitted');continue
  record=json.loads((out/(key+'-r2-plan.json')).read_text());v=st.videos[key];current=cb.api('GET',f"/video-cuts/{v['cut_id']}");draft=record['draft']
  if canonical(current['draft'])!=canonical(record['before']['cut']['draft']):raise RuntimeError('Round2 plan stale')
  validate(current['draft'],draft);issues=cut_checks(b,draft,v.get('voice'))+check_sources(draft)
  if issues:raise RuntimeError(str(issues))
  cb.api('PUT',f"/video-cuts/{v['cut_id']}",{'script_id':current['script_id'],'draft':draft});back=cb.api('GET',f"/video-cuts/{v['cut_id']}")
  if canonical(back['draft'])!=canonical(draft):raise RuntimeError('Round2 full readback differs')
  job=cb.api('POST','/render-jobs',{'spec':back['draft'],'options':{'codec':'h264','quality':'high','scale':1},'cut_id':v['cut_id'],'idempotency_key':'lux-upper4-safe-'+v['cut_id']},tries=1);save(receipt,job);v.update(stage='rendering',render_job=job['job_id'],ai_review=None,ai_reason=None,qc_issues=[],upper_title_family=record['family'],upper_template_id=record['template_id'])
  for f in ['media_qc','object_key','review_waived']:v.pop(f,None)
  st.save();print(key,record['family'],'safe upper native render',flush=True)

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['inventory','choices','plan','submit','media','boards','case','rereview','compact_choices','r2_plan','r2_submit']);p.add_argument('--round',type=int,choices=[1,2],default=1);p.add_argument('--config',required=True);p.add_argument('--run',required=True);a=p.parse_args();cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';globals()[a.cmd](a,cfg)
if __name__=='__main__':main()
