#!/usr/bin/env python3
"""[INPUT] User corrections: each batch needs multiple native subtitle styles, BGM and AI hook sources.
[OUTPUT] Read-backed five script configurations plus variant assignments for new/existing batch cuts.
[POS] Only this authorized LUX batch; never modifies shared resource definitions.
"""
import argparse,copy,hashlib,json,sys,urllib.parse,math,subprocess
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import load_config
STYLES=['6ab0980646ba4a9c28773861','6ab0980646ba4a9c2877385c','6aded5007024270fd6c450be','6ab0980646ba4a9c28773857','6ab0980646ba4a9c2877385d']
MUSIC=['6ac72f6b4fc86c540004c80a','6aafcfe4f406342b4dc429c5','6ac72f6a4fc86c540004c808','6ac72f694fc86c540004c807','6ac72f6b4fc86c540004c809','6aafcff1f406342b4dc42a88']
AI=['6b058a9a429cee6fdc08603a','6b064799429cee6fdc086073','6b0646b6429cee6fdc086070','6b0646c0429cee6fdc086071','6b0646ac429cee6fdc08606f']
def save(p,d):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,indent=2));return p

def configure(a,cfg):
 root=Path(a.run);out=root/'revisions/批量资源纠正';out.mkdir(exist_ok=True);st=cb.State(root);pools=json.loads((root/'revisions/切片去重/expanded-pools.json').read_text());library=json.loads((root/'revisions/切片去重/library-jingyou.json').read_text());byid={x['id']:x for x in library};sources=set();segplan={};b1=json.loads((root/'briefs/1125-L01.json').read_text())
 for seg in b1['segments']:
  sid=seg['id'];tag=seg['visual_tags'][0]
  if sid=='1':picked=[byid[x] for x in AI]
  else:
   count=16 if tag=='头发顺滑' else 12;rows=[x for x in library if tag in x.get('tags',[]) and x.get('review_result')=='pass' and not any(str(t).startswith('AI-') for t in x.get('tags',[])) and x.get('object_url') and x['object_url'].split('?')[0].rsplit('/',1)[-1] not in (cfg.get('banned_footage_files') or {}) and not any(w in (x.get('description') or '') for w in cb.banned_words_for('jingyou'))];rows.sort(key=lambda x:hashlib.sha256(('1125-L01|'+sid+'|'+x['id']).encode()).hexdigest());picked=[]
   for x in rows:
    path=urllib.parse.urlparse(x['object_url']).path
    if path in sources:continue
    picked.append(x);sources.add(path)
    if len(picked)==count:break
   if len(picked)!=count:raise RuntimeError('Insufficient unique L1 real footage')
  segplan[sid]={'tag':tag,'candidates':picked}
 pools['1125-L01']={'product_key':'jingyou','script_id':st.data['briefs']['1125-L01']['script_id'],'segments':segplan}
 for bid in ['1125-L01','1125-L02','1125-L03','1125-L04','1125-L05']:
  sid=st.data['briefs'][bid]['script_id'];before=cb.api('GET',f'/video-scripts/{sid}');wanted=copy.deepcopy(before);wanted['config']['subtitle_style']['style_ids']=STYLES;wanted['config']['bgm']={'mode':'ids','volume':before['config']['bgm']['volume'],'props':{'ids':MUSIC,'categories':[]}}
  for seg in wanted['segments']:seg['visual']={'mode':'material_segment_ids','props':{'segment_ids':[x['id'] for x in pools[bid]['segments'][seg['id']]['candidates']]}}
  if a.dry_run:save(out/(bid+'-config-plan.json'),{'before':before,'wanted':wanted});print(bid,'5 native styles / 6 BGM / AI hooks',5 if bid=='1125-L01' else 0,flush=True);continue
  plan=json.loads((out/(bid+'-config-plan.json')).read_text())
  if before['config']!=plan['before']['config'] or before['segments']!=plan['before']['segments']:raise RuntimeError('Script changed after dry-run '+bid)
  save(out/(bid+'-config-before.json'),before);body={k:wanted[k] for k in ['product_id','title','video','config','vo_rewrite_prompt','segments']};cb.api('PUT',f'/video-scripts/{sid}',body);back=cb.api('GET',f'/video-scripts/{sid}')
  if any(body[k]!=back[k] for k in body):raise RuntimeError('Configuration readback mismatch')
  save(out/(bid+'-config-after.json'),back);print(bid,'READBACK styles',len(back['config']['subtitle_style']['style_ids']),'BGM',len(back['config']['bgm']['props']['ids']),'hook pool',len(back['segments'][0]['visual']['props']['segment_ids']),flush=True)
 if not a.dry_run:
  save(root/'revisions/切片去重/expanded-pools.json',pools);b1['hook_repeat_ok']=True;b1['hook_repeat_note']='本次brief1量产沿用已展示5个同类追光钩子，10条按每个2条轮换；单条片内不重复。用户此前要求至少5个同类钩子，本轮明确“1也可以批量了”。';b1['segments'][0]['visual_tags']=['AI-1125-发丝追光','AI-1125-追光灯备选'];b1['aigc_plan']['selected_segment_ids']=AI;b1['aigc_plan']['batch_rotation_note']=b1['hook_repeat_note'];save(root/'briefs/1125-L01.json',b1)
  cpath=root/'config/lux.json';raw=json.loads(cpath.read_text());raw['lux_bulk_approved_briefs']=['1125-L01','1125-L02','1125-L03','1125-L04','1125-L05'];raw['batch_native_variants']={'subtitle_style_ids':STYLES,'bgm_ids':MUSIC,'AI_hook_ids':AI,'uniform_within_each_video':True,'rotation':'每条片内统一；跨成片轮换，AI5款各2条'};save(cpath,raw)

def apply_variant(root,bid,key,d,cfg):
 n=int(key.rsplit('-V',1)[1]);bn=int(bid[-2:]);settings=cfg['batch_native_variants'];styleid=settings['subtitle_style_ids'][(n+bn-4)%len(settings['subtitle_style_ids'])];catalog=json.loads((Path(root)/'clipo/resources/subtitle-styles.json').read_text());rows=catalog if isinstance(catalog,list) else catalog['items'];native=next(x for x in rows if x['id']==styleid);mapping={'alignType':'align_type','backgroundColor':'background_color','bold':'bold','italic':'italic','underline':'underline','strokeColor':'stroke_color','strokeWidth':'stroke_width','textColor':'text_color','textShadow':'text_shadow'}
 for x in d['clips']:
  if x['trackId']=='overlay':
   props=x['props'];props.update({v:native.get(k) for k,v in mapping.items()});props['font_size']=36;props['top']=78
   hi=cfg['native_caption_highlights'];text=props['text']
   # Highlight colors remain legible with every native outline / shadow option.
   if styleid!='6ab0980646ba4a9c2877385d':
    if any(w in text for w in hi['product_keywords']):props['text_color']=hi['product_color']
    elif any(w in text for w in hi['selling_keywords']):props['text_color']=hi['selling_color']
 musicid=settings['bgm_ids'][(n+bn-4)%len(settings['bgm_ids'])];m=json.loads((Path(root)/'revisions/声音节奏与人物重选/music'/(musicid+'.json')).read_text());duration=d['composition']['durationInFrames']/d['composition']['fps'];safe=max(0,math.floor(m['duration_seconds']-duration-1));offset=min(safe,6+(n%3)*4)
 for x in d['clips']:
  if x['trackId']=='audio':x['id']='bgm-variant-'+musicid;x['props'].update(src=m['url'],startFrom=offset*d['composition']['fps'],loop=False,volume=.16 if musicid=='6aafcfe4f406342b4dc429c5' else .23)
 # Per-video mix is checked from its explicit native variant evidence, not a single batch-level volume.
 d,changes=fix_sfx(root,key,d)
 return d,{'sfx_replacements':changes,'subtitle_style_id':styleid,'subtitle_style_name':native['name'],'bgm_id':musicid,'bgm_name':m['name'],'bgm_volume':.16 if musicid=='6aafcfe4f406342b4dc429c5' else .23,'bgm_offset_seconds':offset}

def rework(a,cfg):
 from qc import cut_checks
 root=Path(a.run);out=root/'revisions/批量资源纠正';st=cb.State(root);briefs={b['brief_id']:b for b in cb.load_briefs(root)};count=0
 for key,v in sorted(st.videos.items()):
  if key.endswith('-V01') or v['brief_id']=='1125-L01':continue
  if v.get('copy_issues'):print(key,'hold copy defect',v['copy_issues'],flush=True);continue
  cut=cb.api('GET',f"/video-cuts/{v['cut_id']}");draft,variant=apply_variant(root,v['brief_id'],key,copy.deepcopy(cut['draft']),cfg);issues=cut_checks(briefs[v['brief_id']],draft,v.get('voice'))
  if issues:raise RuntimeError(key+' '+str(issues))
  count+=1
  if a.dry_run:save(out/(key+'-variant-plan.json'),{'before':cut,'draft':draft,'variant':variant});continue
  receipt=out/(key+'-render-receipt.json')
  if receipt.exists():continue
  intended=json.loads((out/(key+'-variant-plan.json')).read_text());unchanged=lambda d:[x for x in d['clips'] if x['trackId'] not in ['audio','overlay']]
  if unchanged(cut['draft'])!=unchanged(draft):raise RuntimeError('Non-subtitle/BGM tracks changed')
  cb.api('PUT',f"/video-cuts/{v['cut_id']}",{'script_id':cut['script_id'],'draft':draft});back=cb.api('GET',f"/video-cuts/{v['cut_id']}");cb.check_readback(draft,back['draft'],False)
  if [{k:z for k,z in x['props'].items() if k!='font_url'} for x in back['draft']['clips'] if x['trackId']=='overlay']!=[{k:z for k,z in x['props'].items() if k!='font_url'} for x in draft['clips'] if x['trackId']=='overlay']:raise RuntimeError('Subtitle style readback differs')
  job=cb.api('POST','/render-jobs',{'spec':back['draft'],'options':{'codec':'h264','quality':'high','scale':1},'cut_id':v['cut_id'],'idempotency_key':'lux-multi-variant-'+v['cut_id']});save(receipt,job);save(out/(key+'-variant-after.json'),back);v.update(render_job=job['job_id'],stage='rendering',ai_review=None,ai_reason=None,qc_issues=[],native_variant=variant,native_subtitle_style=variant['subtitle_style_id'])
  for field in ['object_key','review_waived','layout_review_note','user_review_pending']:v.pop(field,None)
  st.save();print(key,'rerender',variant['subtitle_style_name'],variant['bgm_name'],flush=True)
 print('DRY-RUN' if a.dry_run else 'SUBMITTED','existing video rerenders',count,'new generations 0',flush=True)

def setup_brief1(a,cfg):
 import time
 root=Path(a.run);out=root/'revisions/切片去重';path=root/'revisions/声音节奏与人物重选/voice-intent.json';intents=json.loads(path.read_text());demo=json.loads((out/'1125-L01-V01-before.json').read_text())['cut'];rows=demo['random_result']['vo_rewrite'];spoken=''.join(x['content'] for x in rows);body={'text':'[#像有审美的时髦女生分享发丝的小心机，开头俏皮带笑，有停顿和重点，清亮知性，品牌名清楚，轻快自然，不播音，不拖腔，不喊卖。]\n'+spoken,'voice':{'platform':'volcengine','voice_type':'zh_female_zhixingnv_uranus_bigtts','resource_id':'seed-tts-2.0','speed_ratio':1.5},'force':False};intents['1125-L01']={'body':body,'spoken':spoken,'segments':rows};save(path,intents)
 if not a.dry_run:raise RuntimeError('setup-brief1 is a paid-count dry-run only')
 save(out/'bulk-voice-budget-L01.json',{'at':time.time(),'briefs':['1125-L01'],'new_videos':9,'max_continuous_tts_calls':9,'speech_rate':1.5,'note':'本轮用户明确brief1可以量产；保持其已认可demo的1.5速度，5音色候选轮换。'})
 old=json.loads((out/'bulk-voice-budget.json').read_text());old.update(at=time.time(),briefs=['1125-L02','1125-L03','1125-L04','1125-L05'],max_continuous_tts_calls=37,note='原36份已合成；另授权修正L02-V07漏词，整篇重配1份，其他不重做配音。');save(out/'bulk-voice-budget.json',old)
 print('DRY-RUN: brief1 new videos 9 / whole TTS 9; existing L02 copy repair whole TTS 1; packaging-only rerenders 35 use existing voices',flush=True)

def repair_copy(a,cfg):
 import shutil
 from qc import cut_checks
 from lint_copy import lint_script,lint_text
 from common import voiced
 root=Path(a.run);out=root/'revisions/批量资源纠正';st=cb.State(root);key='1125-L02-V07';v=st.videos[key];b=next(x for x in cb.load_briefs(root) if x['brief_id']==v['brief_id']);cut=cb.api('GET',f"/video-cuts/{v['cut_id']}");rows=[{'id':x['id'],'content':x['text']} for x in b['segments']];prod=cfg['products'][b['product_key']];got=[x['content'] for x in rows];issues=[m for t in got for l,m in lint_text(t,cfg,prod) if l=='error']+[m for l,m in lint_script(got,voiced(b['segments']),b,cfg,prod) if l=='error']
 if issues:raise RuntimeError('Approved copy lint '+str(issues))
 plan={'cut_id':v['cut_id'],'approved_rows':rows,'instruction':'恢复已经用户批准的主版口播，确保鲜花精油和4D玻尿酸逐字保留，不创作新文案。'};planpath=out/(key+'-copy-repair-plan.json')
 if a.dry_run:save(planpath,plan);print(key,'DRY-RUN: restore approved copy, 1 continuous TTS, 1 rerender');return
 if not planpath.exists() or json.loads(planpath.read_text())!=plan:raise RuntimeError('Missing matching copy repair dry-run')
 receipt=out/(key+'-copy-repair-receipt.json')
 if receipt.exists():print(key,'copy repair already submitted');return
 save(out/(key+'-copy-before.json'),{'cut':cut,'state_video':v});work=root/'revisions/切片去重/bulk-native'/key;archive=out/(key+'-old-voice')
 if work.exists() and not archive.exists():shutil.move(str(work),str(archive))
 cut['random_result']['vo_rewrite']=rows;v.update(texts=got,copy_issues=[]);draft,spans=cb.prepare_draft(st,b,v,key,cut);qc=cut_checks(b,draft,v.get('voice'))
 if qc:raise RuntimeError(str(qc))
 cb.api('PUT',f"/video-cuts/{v['cut_id']}",{'script_id':cut['script_id'],'draft':draft});back=cb.api('GET',f"/video-cuts/{v['cut_id']}");cb.check_readback(draft,back['draft'],spans);job=cb.api('POST','/render-jobs',{'spec':back['draft'],'options':{'codec':'h264','quality':'high','scale':1},'cut_id':v['cut_id'],'idempotency_key':'lux-approved-copy-repair-'+v['cut_id']});save(receipt,job);v.update(render_job=job['job_id'],stage='rendering',ai_review=None,ai_reason=None,qc_issues=[],copy_repair=plan)
 for field in ['error','last_error','object_key','review_waived']:v.pop(field,None)
 st.save();print(key,'approved copy restored; native whole speech and render submitted',flush=True)

def diagnose(a,cfg):
 import re
 root=Path(a.run);st=cb.State(root);report=[]
 for key,v in sorted(st.videos.items()):
  if v.get('stage')!='failed' or not v.get('render_job'):continue
  job=cb.api('GET',f"/render-jobs/{v['render_job']}");message=job.get('error_message') or '';safe=re.sub(r'https?://[^\s]+',lambda m:urllib.parse.urlparse(m.group()).path,message);report.append({'video':key,'job_id':v['render_job'],'message':safe});print(key,safe[:1800],flush=True)
  if len(report)>=3:break
 save(root/'revisions/批量资源纠正/download-failures.json',report)

def sound_inventory(a,cfg):
 root=Path(a.run);d=cb.api('GET','/resource/sound-effect?limit=100');save(root/'revisions/批量资源纠正/sound-library.json',d);rows=d if isinstance(d,list) else d.get('items',[])
 print('sound effect rows',len(rows),'total',d.get('total') if isinstance(d,dict) else len(rows),flush=True)
 print([(x.get('id'),x.get('name'),list(x)) for x in rows[:70]],flush=True)

def sound_bank(a,cfg):
 import urllib.request,urllib.error
 root=Path(a.run);out=root/'revisions/批量资源纠正';d=json.loads((out/'sound-library.json').read_text());rows=d if isinstance(d,list) else d['items'];names=['卡通眨眼音效','啵一声','卡通气泡叮声1','综艺冒泡','可爱biu'];bank=[];bad=[]
 for x in rows:
  if x['name'] not in names:continue
  p=out/'sound-cache'/(x['id']+Path(urllib.parse.urlparse(x['url']).path).suffix);p.parent.mkdir(exist_ok=True)
  try:
   cb.fetch_file(x['url'],p)
   duration=float(json.loads(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(p)],capture_output=True,text=True,check=True).stdout)['format']['duration']);bank.append({**x,'actual_duration':duration,'file':str(p)})
  except Exception as e:print('unavailable',x['name'],type(e).__name__,flush=True)
 if len(bank)<3:raise RuntimeError('Fewer than 3 verified short Clipo sound effects')
 sources={}
 for n in range(1,6):
  demo=json.loads((root/'revisions/切片去重'/f'1125-L{n:02d}-V01-before.json').read_text())['cut']['draft']
  for x in demo['clips']:
   if x['trackId']=='sound_effects':sources[urllib.parse.urlparse(x['props']['src']).path]=x['props']['src']
 for path,url in sources.items():
  try:
   req=urllib.request.Request(url,headers={'Range':'bytes=0-0'})
   with urllib.request.urlopen(req,timeout=30) as r:r.read(1)
  except (urllib.error.URLError,OSError,TimeoutError):bad.append(path)
 save(out/'sound-bank.json',{'verified':bank,'bad_paths':bad});print('Verified Clipo SFX',[(x['id'],x['name']) for x in bank],'unavailable old source files',len(bad),flush=True)

def fix_sfx(root,key,d):
 bankpath=Path(root)/'revisions/批量资源纠正/sound-bank.json'
 if not bankpath.exists():return d,[]
 data=json.loads(bankpath.read_text());bank=data['verified'];changes=[]
 for i,x in enumerate(d['clips']):
  if x['trackId']!='sound_effects' or urllib.parse.urlparse(x['props'].get('src','')).path not in data['bad_paths']:continue
  choice=bank[(int(hashlib.sha256((key+'|'+x['id']).encode()).hexdigest()[:8],16))%len(bank)];old=x['props']['src'];x['props'].update(src=choice['url'],startFrom=0,playbackRate=1,volume=.40);x['durationInFrames']=min(x['durationInFrames'],max(2,math.floor(choice['actual_duration']*d['composition']['fps'])));changes.append({'clip':x['id'],'before_source':urllib.parse.urlparse(old).path,'replacement_id':choice['id'],'replacement_name':choice['name']})
 return d,changes

def retry_sfx(a,cfg):
 from qc import cut_checks
 root=Path(a.run);out=root/'revisions/批量资源纠正';st=cb.State(root);briefs={b['brief_id']:b for b in cb.load_briefs(root)};count=0
 for key,v in sorted(st.videos.items()):
  if v.get('stage')!='failed' or 'Error while downloading' not in v.get('error',''):continue
  receipt=out/(key+'-sfx-retry-receipt.json')
  if receipt.exists():continue
  cut=cb.api('GET',f"/video-cuts/{v['cut_id']}");draft,changes=fix_sfx(root,key,copy.deepcopy(cut['draft']))
  if not changes:continue
  issues=cut_checks(briefs[v['brief_id']],draft,v.get('voice'))
  if issues:raise RuntimeError(key+' '+str(issues))
  count+=1;save(out/(key+'-sfx-repair-plan.json'),{'before':cut,'draft':draft,'changes':changes})
  if a.dry_run:continue
  cb.api('PUT',f"/video-cuts/{v['cut_id']}",{'script_id':cut['script_id'],'draft':draft});back=cb.api('GET',f"/video-cuts/{v['cut_id']}");cb.check_readback(draft,back['draft'],False);job=cb.api('POST','/render-jobs',{'spec':back['draft'],'options':{'codec':'h264','quality':'high','scale':1},'cut_id':v['cut_id'],'idempotency_key':'lux-live-sfx-'+v['cut_id']});save(receipt,job);v.update(render_job=job['job_id'],stage='rendering',ai_review=None,ai_reason=None,qc_issues=[],sfx_replacements=changes)
  for field in ['error','object_key','review_waived']:v.pop(field,None)
  st.save();print(key,'SFX repaired',len(changes),flush=True)
 print('DRY-RUN' if a.dry_run else 'SUBMITTED','sound source repairs',count,'new videos 0 / new TTS 0',flush=True)

def sound_config(a,cfg):
 root=Path(a.run);out=root/'revisions/批量资源纠正';data=json.loads((out/'sound-bank.json').read_text());ids=[x['id'] for x in data['verified']];st=cb.State(root)
 for bid,row in sorted(st.data['briefs'].items()):
  if bid not in ['1125-L01','1125-L02','1125-L03','1125-L04','1125-L05']:continue
  sid=row['script_id'];script=cb.api('GET',f'/video-scripts/{sid}');save(out/(bid+'-sfx-config-before.json'),script)
  for seg in script['segments']:
   if seg.get('sound_effects'):seg['sound_effects']={'mode':'ids','props':{'ids':ids,'category':(seg['sound_effects'].get('props') or {}).get('category') or '字幕与动作点缀'}}
  body={k:script[k] for k in ['product_id','title','video','config','vo_rewrite_prompt','segments']};cb.api('PUT',f'/video-scripts/{sid}',body);back=cb.api('GET',f'/video-scripts/{sid}')
  if back['segments']!=body['segments']:raise RuntimeError('SFX config readback differs')
  save(out/(bid+'-sfx-config-after.json'),back);print(bid,'verified SFX candidates',len(ids),'readback confirmed',flush=True)

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['configure','rework','setup-brief1','repair-copy','diagnose','sound-inventory','sound-bank','retry-sfx','sound-config']);p.add_argument('--config',required=True);p.add_argument('--run',required=True);p.add_argument('--dry-run',action='store_true');a=p.parse_args();cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';{'configure':configure,'rework':rework,'setup-brief1':setup_brief1,'repair-copy':repair_copy,'diagnose':diagnose,'sound-inventory':sound_inventory,'sound-bank':sound_bank,'retry-sfx':retry_sfx,'sound-config':sound_config}[a.cmd](a,cfg)
if __name__=='__main__':main()
