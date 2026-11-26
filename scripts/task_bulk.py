#!/usr/bin/env python3
"""Approved task bulk callback: bounded whole TTS, native variation, actual subtitle highlights.
No shared-resource writes, human approval bypass, fixed real-footage pool or quota expansion.
"""
import argparse,copy,hashlib,json,math,subprocess,sys,time,urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
import clipo_batch as cb,task_demo as td
from common import load_config,demo_problem
from task_configuration import pages

def save(p,x):return td.save(p,x)
def read(p):return td.load(p)
def validate_budget(plan,key,now):
 if key not in plan['keys'] or now-plan['at']>7200:raise RuntimeError('Missing current matching bulk TTS budget')

def hold(a,cfg):
 st=cb.State(a.run);plan=read(a.plan);out=Path(a.run)/'revisions/customer-bulk-hold';out.mkdir(parents=True,exist_ok=True)
 if not plan.get('authorization') or not plan.get('reason') or not plan.get('keys'):raise RuntimeError('Missing explicit user rejection')
 for key in plan['keys']:
  if key not in st.videos or st.videos[key]['brief_id']!=a.brief:raise RuntimeError('Hold scope mismatch')
 if a.dry_run:save(out/'plan.json',plan);print('DRY-RUN hold',len(plan['keys']),'owned videos; preserve demo, raw files and old share for audit');return
 from sync_skill import assert_current
 assert_current(Path(__file__).resolve().parent.parent)
 if read(out/'plan.json')!=plan:raise RuntimeError('Hold dry-run mismatch')
 receipt=out/'applied.json'
 if receipt.exists():
  if any(st.videos[k].get('customer_rework_required')!=plan['reason'] for k in plan['keys']):raise RuntimeError('Previously held videos changed; do not reapply automatically')
  st.data.setdefault('briefs',{}).setdefault(a.brief,{})['customer_rework_hold']=plan['reason'];st.save();print('Hold already applied; production stop verified');return
 save(out/'before.json',st.data)
 for key in plan['keys']:st.videos[key]['customer_rework_required']=plan['reason']
 st.data.setdefault('briefs',{}).setdefault(a.brief,{})['customer_rework_hold']=plan['reason']
 group=st.data.get('share_group');share=st.data.get('shares',{}).get(a.brief)
 if share and group:
  before=cb.api('GET','/video-share-groups/'+group['id']);save(out/'group-before.json',before)
  wanted=[x['id'] for x in before['shares'] if x['id']!=share['id']];cb.api('PATCH','/video-share-groups/'+group['id'],{'video_share_ids':wanted},tries=1)
  after=cb.api('GET','/video-share-groups/'+group['id']);save(out/'group-after.json',after)
  if sorted(x['id'] for x in after['shares'])!=sorted(wanted):raise RuntimeError('Group withdrawal readback mismatch')
  st.data.setdefault('withdrawn_shares',[]).append({**share,'reason':plan['reason']});st.data['shares'].pop(a.brief,None)
 st.save();cb.write_delivery(st,{b['brief_id']:b for b in cb.load_briefs(a.run)},a.run);save(receipt,plan);print('Held',len(plan['keys']),'videos; batch share removed from delivery group, original demo retained')

def repair(a,cfg):
 from footage_qc import stable_media
 from qc import cut_checks
 st=cb.State(a.run);plan=read(a.plan);key=plan['key'];v=st.videos[key];b=next(x for x in cb.load_briefs(a.run) if x['brief_id']==v['brief_id'])
 if not plan.get('authorization') or b['brief_id']!=a.brief:raise RuntimeError('Repair authorization or task mismatch')
 cut=cb.api('GET','/video-cuts/'+v['cut_id']);draft=copy.deepcopy(cut['draft']);library={x['id']:x for x in cb.list_segments(cfg['products'][b['product_key']]['clipo_product_id'])};fps=draft['composition']['fps']
 for c in draft['clips']:
  if c['id'] not in plan['clips']:continue
  change=plan['clips'][c['id']];m=library[change['segment_id']];start=change['startFrom']
  if c['trackId']!='main' or m.get('ai_generated') or m.get('review_result')!='pass' or start+c['durationInFrames']>math.floor((m['end_ms']-m['begin_ms'])*fps/1000):raise RuntimeError('Unsafe real source replacement')
  c['props'].update(src=m['object_url'],startFrom=start,alt=m.get('description',''))
 if [x for x in cut['draft']['clips'] if x['trackId']!='main']!=[x for x in draft['clips'] if x['trackId']!='main']:raise RuntimeError('Protected tracks changed')
 issues=cut_checks(b,draft,v.get('voice'))
 if issues:raise RuntimeError('Repair gate '+str(issues))
 out=Path(a.run)/'task-bulk'/key/'source-repair';out.mkdir(exist_ok=True);sig=td.digest(stable_media(draft));budgetfile=out/'budget.json'
 if a.dry_run:save(budgetfile,{'at':time.time(),'hash':sig,'plan':plan});print('DRY-RUN',key,'1 source-only native rerender; 0 TTS, timing/captions/packaging/audio preserved');return
 rec=read(budgetfile)
 if rec['hash']!=sig or rec['plan']!=plan or time.time()-rec['at']>3600:raise RuntimeError('Missing exact source repair dry-run')
 receipt=out/(sig+'.json')
 if receipt.exists():job=read(receipt)
 else:
  save(out/'before.json',cut);cb.api('PUT','/video-cuts/'+v['cut_id'],{'script_id':cut['script_id'],'draft':draft},tries=1);back=cb.api('GET','/video-cuts/'+v['cut_id'])
  if stable_media(back['draft'])!=stable_media(draft):raise RuntimeError('Repair readback differs')
  save(out/'after.json',back);job=cb.api('POST','/render-jobs',{'spec':back['draft'],'cut_id':v['cut_id'],'options':{'codec':'h264','quality':'high','scale':1},'idempotency_key':'task-source-repair-'+v['cut_id']+'-'+sig[:16]},tries=1);save(receipt,job)
 v.update(render_job=job['job_id'],stage='rendering',ai_review=None,ai_reason=None,qc_issues=[])
 for k in ['error','last_error','object_key','bad_footage']:v.pop(k,None)
 st.save();print(key,'source-only repair rendering',job['job_id'])

def budget(a,cfg):
 if not a.dry_run:raise RuntimeError('Budget requires --dry-run; no paid operations here')
 st=cb.State(a.run);b=next(x for x in cb.load_briefs(a.run) if x['brief_id']==a.brief)
 if demo_problem(b):raise RuntimeError(demo_problem(b))
 sid=st.data['briefs'][a.brief]['script_id'];script=cb.api('GET','/video-scripts/'+sid);root=Path(a.run)/'task-bulk';root.mkdir(exist_ok=True)
 save(root/(a.brief+'-script.json'),script);save(root/(a.brief+'-library.json'),cb.list_segments(script['product_id']));save(root/'styles.json',pages('/resource/subtitle-style'))
 music=read(Path(a.run)/'clipo/task-inventory/bgm.json');save(root/'music.json',music)
 ids=['6afd05026af35ab34a0a5cb4','6afd05026af35ab34a0a5cb5','6afd05026af35ab34a0a5cb7','6afd05026af35ab34a0a5cb9']
 with ThreadPoolExecutor(max_workers=4) as pool:effects=list(pool.map(lambda x:cb.api('GET','/resource/remotion-effect-template/'+x),ids))
 save(root/'effects.json',effects)
 n=a.quantity
 if n!=b['quantity']-1:raise RuntimeError('Only remaining quota after one approved demo')
 offset=max([int(k.rsplit('-V',1)[1]) for k in st.videos if k.startswith(a.brief+'-V')],default=1)
 keys=[a.brief+f'-V{i:02d}' for i in range(offset+1,offset+n+1)]
 save(root/(a.brief+'-budget.json'),{'at':time.time(),'keys':keys,'quantity':n,'whole_tts':n,'speed':1.4,'authorization':b['demo_approval'],'source':'full current tag libraries, no fixed real pool'})
 print('DRY-RUN',a.brief,'new videos',n,'whole TTS',n,'native voice/style/music choices',len(script['config']['tts']['voices']),len(script['config']['subtitle_style']['style_ids']),len(script['config']['bgm']['props']['ids']),flush=True)

def allocate(a,cfg):
 """Register a visually reviewed whole-batch source plan; no paid calls or state fabrication."""
 plan=read(a.plan);root=Path(a.run)/'task-bulk';budget=read(root/(a.brief+'-budget.json'));b=next(x for x in cb.load_briefs(a.run) if x['brief_id']==a.brief)
 if not plan.get('reviewer') or not plan.get('evidence') or not plan.get('authorization'):raise RuntimeError('Visual grouping evidence and task authorization required')
 if set(plan.get('videos',{}))!=set(budget['keys']):raise RuntimeError('Allocation must cover the exact remaining video keys')
 library={x['id']:x for x in read(root/(a.brief+'-library.json'))};groups=plan.get('source_groups',{});counts={}
 for key,item in plan['videos'].items():
  used=set();people=set()
  for s in b['segments']:
   ids=item['segments'].get(s['id'],[])
   if not ids:raise RuntimeError('Missing role allocation '+key+'/'+s['id'])
   for sid in ids:
    m=library[sid];path=td.urlparse(m['object_url']).path
    if m.get('ai_generated') or m.get('review_result')!='pass' or not set(m.get('tags',[]))&set(s['visual_tags']) or path in used:raise RuntimeError('Invalid/duplicate source in allocation')
    used.add(path)
    if '头发顺滑' in s['visual_tags']:
     if not groups.get(sid):raise RuntimeError('Hair footage lacks reviewed person/appearance grouping')
     people.add(groups[sid])
  for person in people:counts[person]=counts.get(person,0)+1
 cap=plan.get('max_videos_per_person_group')
 if not isinstance(cap,int) or cap<1 or cap>=len(budget['keys']):raise RuntimeError('A real cross-video person cap is required')
 if any(n>cap for n in counts.values()):raise RuntimeError('Reviewed person-group frequency cap exceeded')
 target=root/(a.brief+'-source-allocation.json')
 if not a.dry_run:
  save(target,{'plan':plan,'counts':counts,'budget_at':budget['at']});st=cb.State(a.run);st.data.setdefault('briefs',{}).setdefault(a.brief,{}).pop('customer_rework_hold',None);st.save()
 print('DRY-RUN' if a.dry_run else 'SAVED','reviewed source allocation',len(plan['videos']),'videos; person groups',counts)

def copies(a,cfg):
 """Export actual native rewrites and actual used-source counts, never count planned resources as used."""
 st=cb.State(a.run);root=Path(a.run)/'task-bulk';budget=read(root/(a.brief+'-budget.json'));allocation=read(root/(a.brief+'-source-allocation.json'));groups=allocation['plan']['source_groups'];rows=[];people={};sources={}
 for key in budget['keys']:
  v=st.videos.get(key)
  if not v:continue
  text=''.join(v.get('texts') or []);prepared=root/key/'prepared.json';used=[]
  if prepared.exists():
   evidence=read(prepared)['evidence']['source_windows'];seen=set()
   for x in evidence:
    sid=x['source_segment_id'];sources.setdefault(sid,{'videos':set(),'shots':0,'frames':0});sources[sid]['videos'].add(key);sources[sid]['shots']+=1;sources[sid]['frames']+=x['used_frames']
    if sid in groups:seen.add(groups[sid])
   used=sorted(seen)
   for g in used:people[g]=people.get(g,0)+1
  rows.append({'key':key,'cut_id':v.get('cut_id'),'stage':v.get('stage'),'copy':text,'copy_hash':hashlib.sha256(td.norm(text).casefold().encode()).hexdigest(),'actual_person_groups':used,'last_error':v.get('last_error'),'native_copy_source':'Clipo random_result.vo_rewrite → collected video texts'})
 serial={k:{**v,'videos':sorted(v['videos'])} for k,v in sources.items()};out=Path(a.run)/'revisions/新批量文案与人物分配';out.mkdir(parents=True,exist_ok=True);save(out/(a.brief+'-实际改写与用料.json'),{'rows':rows,'unique_actual_copy':len({x['copy_hash'] for x in rows if x['copy']}),'actual_person_groups':people,'actual_source_frequency':serial})
 (out/(a.brief+'-实际文案.md')).write_text('\n\n'.join('### '+x['key']+'\n\n'+x['copy'] for x in rows))
 print(a.brief,'collected',len(rows),'unique actual copy',len({x['copy_hash'] for x in rows if x['copy']}),'prepared',sum(bool(x['actual_person_groups']) for x in rows),'actual groups',people,'report',str(out/(a.brief+'-实际文案.md')))

def prepare(api_module,st,b,v,key,cut,d):
 td.cb=api_module;cb.CFG=api_module.CFG;cb.BASE=api_module.BASE;cfg=api_module.CFG;root=st.path.parent.parent;assets=root/'task-bulk';plan=read(assets/(b['brief_id']+'-budget.json'))
 validate_budget(plan,key,time.time())
 script=read(assets/(b['brief_id']+'-script.json'));library=read(assets/(b['brief_id']+'-library.json'));work=assets/key;work.mkdir(exist_ok=True);i=int(key.rsplit('-V',1)[1])-2
 got=v.get('texts') or []
 if len(got)!=len(b['segments']):raise RuntimeError('Missing actual per-video native rewrite')
 spoken=''.join(got)
 fingerprint=lambda texts:td.norm(''.join(texts)).casefold()
 for other,item in st.videos.items():
  if other!=key and item.get('brief_id')==b['brief_id'] and not item.get('customer_rework_required') and item.get('texts') and fingerprint(item['texts'])==fingerprint(got):raise RuntimeError('Duplicate batch copy before whole TTS: '+other)
 allocation=read(assets/(b['brief_id']+'-source-allocation.json'))
 if allocation['budget_at']!=plan['at'] or key not in allocation['plan']['videos']:raise RuntimeError('整组人物/造型与源频次分配尚未实际复核；禁止无分配直接随机生产')
 cfg={**cfg,'source_highlight_windows':{**cfg.get('source_highlight_windows',{}),**allocation['plan'].get('source_windows',{})}}
 voice=script['config']['tts']['voices'][i%len(script['config']['tts']['voices'])];body={'text':spoken,'voice':{'platform':voice['platform'],'voice_type':voice['voice_type'],'resource_id':voice['config']['resource_id'],'speed_ratio':1.4},'force':False};receipt=work/'tts.json';intent=work/'tts-intent.json'
 if receipt.exists():
  if read(intent)!=body:raise RuntimeError('Bulk TTS cached input changed')
  tts=read(receipt)
 else:
  if intent.exists():raise RuntimeError('Uncertain TTS outcome; no duplicate submit')
  if sum((assets/k/'tts-intent.json').exists() for k in plan['keys'])>=plan['whole_tts']:raise RuntimeError('TTS budget exhausted')
  save(intent,body);tts=api_module.api('POST','/tts',body,tries=1);save(receipt,tts)
 if td.norm(tts['synthesized_text']).casefold()!=td.norm(spoken).casefold():raise RuntimeError('TTS copy mismatch')
 source=work/'voice.mp3';wav=work/'voice.wav';assetfile=work/'voice-upload.json'
 if not assetfile.exists():
  if not source.exists():api_module.fetch_file(tts['audio_url'],source)
  pcm=np.frombuffer(subprocess.check_output(['ffmpeg','-v','error','-i',str(source),'-ac','1','-f','f32le','-']),dtype='<f4');peak=float(np.max(np.abs(pcm)));gain=min(6,20*math.log10(.88/max(.001,peak)))
  subprocess.run(['ffmpeg','-v','error','-y','-i',str(source),'-af',f'volume={gain}dB','-c:a','pcm_s16le',str(wav)],check=True)
  tok=api_module.api('POST','/storage/upload-tokens',{'filename':key+'-continuous.wav','content_type':'audio/wav','content_length':wav.stat().st_size,'purpose':'material'},tries=1);req=urllib.request.Request(tok['upload_url'],data=wav.read_bytes(),method='PUT',headers=dict(tok.get('headers') or {},**{'Content-Type':'audio/wav'}));urllib.request.urlopen(req,timeout=120).read();save(assetfile,{'object_key':tok['object_key'],'gain_db':gain,'source_peak':peak})
 asset=read(assetfile);tts=copy.deepcopy(tts);tts['audio_url']=api_module.api('POST','/storage/download-tokens',{'object_key':asset['object_key']})['download_url']
 rows=copy.deepcopy(b)
 for s,text in zip(rows['segments'],got):s['text']=text;s.pop('caption_chunks',None)
 draft,evidence=td.build(rows,{**cut,'draft':d},tts,script,library,cfg,key,allocation=allocation['plan']['videos'][key]['segments'])
 styles=read(assets/'styles.json');styleid=script['config']['subtitle_style']['style_ids'][i%len(script['config']['subtitle_style']['style_ids'])];style=next(x for x in styles if x['id']==styleid)
 fieldmap={'alignType':'align_type','backgroundColor':'background_color','strokeColor':'stroke_color','strokeWidth':'stroke_width','textColor':'text_color','textShadow':'text_shadow','bold':'bold','italic':'italic','underline':'underline'}
 for x in draft['clips']:
  if x['trackId']=='overlay':
   highlighted=next((rule['color'] for rule in (cfg.get('caption_highlights') or {}).get('rules',[]) if any(term in x['props']['text'] for term in rule['terms'])),None)
   x['props'].update({dst:style.get(src) for src,dst in fieldmap.items()})
   if highlighted:
    if style.get('textColor')=='#000000':highlighted='#8F5700' if highlighted=='#FFD36A' else '#944868'
    x['props']['text_color']=highlighted
 musicid=script['config']['bgm']['props']['ids'][i%len(script['config']['bgm']['props']['ids'])];music=next(x for x in read(assets/'music.json') if x['id']==musicid)
 for x in draft['clips']:
  if x['trackId']=='audio':x['props']['src']=music['url']
 effects=read(assets/'effects.json');patterns=[(1,2,3),(2,3,1),(3,1,2),(0,2,1),(1,0,3),(3,2,0),(0,1,2),(2,0,3),(3,0,1)];fxrows=[(j,s) for j,s in enumerate(script['segments']) if s.get('effect')];draft['clips']=[x for x in draft['clips'] if x['trackId']!='effect']
 for j,(segment_index,s) in enumerate(fxrows):
  template=effects[patterns[i%len(patterns)][j%3]];td.validate_code(template['code']);start,end=evidence['segment_frames'][segment_index:segment_index+2];draft['clips'].append({'id':s['id']+'-x0','trackId':'effect','from':start,'durationInFrames':min(end-start,66),'component':'RemotionClip','props':{'code':template['code'],'params':s['effect']['params'],'x':0,'y':0,'scale':1}})
 from lint_copy import lint_text,lint_script
 from common import voiced
 prod=cfg['products'][b['product_key']];issues=[m for t in got for level,m in lint_text(t,cfg,prod) if level=='error']+[m for level,m in lint_script(got,voiced(b['segments']),b,cfg,prod) if level=='error']
 if issues:raise RuntimeError('Approved copy invalid '+str(issues))
 v.update(voice=voice['voice_type'],texts=got,copy_issues=[],voice_cmd=False,duration_s=draft['composition']['durationInFrames']/draft['composition']['fps'],native_variant={'subtitle_style_id':styleid,'bgm_id':musicid,'upper_templates':[effects[z]['id'] for z in patterns[i%len(patterns)]],'voice_gain_db':asset['gain_db']})
 save(work/'prepared.json',{'draft':draft,'evidence':evidence,'variant':v['native_variant'],'voice':v['voice']});return draft

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['budget','repair','hold','allocate','copies']);p.add_argument('--config',required=True);p.add_argument('--run',required=True);p.add_argument('--brief',required=True);p.add_argument('--quantity',type=int,default=9);p.add_argument('--plan');p.add_argument('--dry-run',action='store_true');a=p.parse_args();cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';globals()[a.cmd](a,cfg)
if __name__=='__main__':main()
