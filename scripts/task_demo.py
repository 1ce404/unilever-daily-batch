#!/usr/bin/env python3
"""Task-owned approved demo finishing: whole native TTS, word-timed native captions, same-cut render.
Never expands quota, changes approved copy, bypasses AI review, or uses local final-video rendering.
"""
import argparse,copy,hashlib,json,math,re,sys,time,random,subprocess,urllib.request
from pathlib import Path
from urllib.parse import urlparse
sys.path.insert(0,str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import load_config,approval_problem,aigc_problem
from product_scene import validate_code

def save(p,x):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);q=p.with_suffix('.pending');q.write_text(json.dumps(x,ensure_ascii=False,indent=2));q.replace(p)
def norm(s):return re.sub(r'[^\w\u4e00-\u9fff]','',s)
def digest(x):return hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def load(p):return json.loads(Path(p).read_text())

def effect_template(eid,run):
 try:return cb.api('GET','/resource/remotion-effect-template/'+eid)
 except RuntimeError as exc:
  if 'remotion_effect_template_not_found' not in str(exc):raise
  cache=Path(run)/'clipo/task-inventory'/('effect-'+eid+'.json')
  if not cache.exists():raise
  item=load(cache)
  if item.get('id')!=eid or not item.get('code'):raise RuntimeError('Native effect cache identity mismatch')
  validate_code(item['code'])
  save(Path(run)/'clipo/effect-cache-reuse'/('effect-'+eid+'.json'),{'id':eid,'cache':str(cache.resolve()),'sha256':hashlib.sha256(cache.read_bytes()).hexdigest(),'reason':'native template removed; exact previously fetched code retained','at':time.time()})
  return item

def fit_source_count(ordered,used,length,minimum,cfg,fps):
 """Fit reviewed, original-speed windows by adding genuine shots, never looping a short source."""
 for count in range(max(1,minimum),len(ordered)+1):
  chosen=set(used)
  for j in range(count):
   frames=length//count+(j<length%count);pick=None
   for m in ordered:
    path=urlparse(m['object_url']).path;available=math.floor((m['end_ms']-m['begin_ms'])*fps/1000);window=(cfg.get('source_highlight_windows') or {}).get(path.rsplit('/',1)[-1],{});start=math.ceil(window.get('start_seconds',0)*fps);end=min(available,math.floor(window.get('end_seconds',available/fps)*fps))
    if path not in chosen and end-start>=frames+4:pick=path;break
   if pick is None:break
   chosen.add(pick)
  else:return count
 raise RuntimeError('Reviewed unique original-speed windows cannot cover segment')

def timing(tts,rows,fps):
 whole=''.join(norm(s['text']) for s in rows);tokens=[];pos=0
 for t in tts.get('timeline',{}).get('items',[]):
  txt=norm(t['text'])
  if txt:tokens.append({**t,'a':pos,'b':pos+len(txt)});pos+=len(txt)
 if ''.join(norm(t['text']) for t in tokens).casefold()!=whole.casefold():raise RuntimeError('Native word timing text incomplete; no guessed word times')
 atomic=[]
 for name in ['力士精油香氛洗发水','力士护发精油喷雾','4D玻尿酸','鲜花精油']:
  atomic += [(m.start(),m.end()) for m in re.finditer(re.escape(name),whole)]
 bounds=[];cues=[];pos=0
 for row in rows:
  stop=pos+len(norm(row['text']));hits=[t for t in tokens if t['b']>pos and t['a']<stop];bounds.append(math.floor(hits[0]['start_ms']*fps/1000));cursor=pos
  preferred=[];hintpos=pos
  for part in row.get('caption_chunks',[]):hintpos+=len(norm(part));preferred.append(hintpos)
  if preferred and hintpos!=stop:raise RuntimeError('Caption plan differs from approved copy')
  while cursor<stop:
   ends=[t['b'] for t in hits if cursor<t['b']<=min(stop,cursor+12) and not any(a<t['b']<b for a,b in atomic)]
   if not ends:raise RuntimeError('Cannot split a native token within single-line width')
   semantic=[z for z in preferred if z in ends]
   end=min(semantic) if semantic else max(ends)
   if stop-end==1 and len(ends)>1:end=ends[-2]
   ts=[t for t in tokens if t['b']>cursor and t['a']<end]
   if ts[0]['a']<cursor or ts[-1]['b']>end:raise RuntimeError('Cue bisects native word')
   cues.append({'text':whole[cursor:end],'start':math.floor(ts[0]['start_ms']*fps/1000),'end':math.floor(ts[-1]['end_ms']*fps/1000),'segment':row['id']});cursor=end
  pos=stop
 total=math.ceil(tts['audio']['duration_ms']*fps/1000);bounds[0]=0;bounds.append(total)
 for x,y in zip(cues,cues[1:]):x['end']=min(x['end'],y['start'])
 if any(x['end']<=x['start'] for x in cues):raise RuntimeError('Invalid native cue interval')
 return total,bounds,cues

def build(b,cut,tts,script,library,cfg,key,allocation=None,authorization_brief=None):
 d=copy.deepcopy(cut['draft']);fps=d['composition']['fps'];total,bounds,cues=timing(tts,b['segments'],fps);original=sorted([x for x in d['clips'] if x['trackId']=='main'],key=lambda x:x['from']);oldbounds=[];groups={}
 for s in b['segments']:
  group=[x for x in original if x['id'].split('-')[0]==s['id']]
  if not group:raise RuntimeError('No native main shots for segment '+s['id'])
  groups[s['id']]=group;oldbounds.append(group[0]['from'])
 oldbounds.append(d['composition']['durationInFrames']);by_path={urlparse(x.get('object_url') or '').path:x for x in library};used=set();main=[];sources=[];rng=random.Random(key)
 for i,s in enumerate(b['segments']):
  begin,end=bounds[i:i+2];length=end-begin;tag=s['visual_tags'];base=groups[s['id']];ai_role=bool(tag) and all(t.startswith('AI-') for t in tag);approved_ai=set((cfg.get('approved_ai_hook_ids') or {}).get(b['brief_id'],[]))
  from demo_authorization import preview_ai_ids
  approved_ai.update(preview_ai_ids(b,cfg,key))
  material_brief=authorization_brief or b
  if authorization_brief is not None:
   from checked_batch_authorization import permits_checked_batch
   from common import aigc_tags
   a=authorization_brief.get('checked_batch_authorization') or {}
   if not permits_checked_batch(authorization_brief) or key not in a.get('exact_video_keys',[]) or any(b.get(k)!=authorization_brief.get(k) for k in ['brief_id','product_key']) or aigc_tags(b)!=aigc_tags(authorization_brief):raise RuntimeError('Checked batch source permission mismatch')
  if ai_role and (i!=0 or not approved_ai or aigc_problem(material_brief)):raise RuntimeError('AI source needs human approval or exact delegated production permission')
  meta=[x for x in library if set(tag)&set(x.get('tags',[])) and ((x.get('ai_generated') and x['id'] in approved_ai) if ai_role else (not x.get('ai_generated') and x.get('review_result')=='pass')) and x.get('object_url') and x['object_url'].split('?')[0].rsplit('/',1)[-1] not in (cfg.get('banned_footage_files') or {}) and not any(w in x.get('description','') for w in cb.banned_words_for(b['product_key']))];rng.shuffle(meta)
  valid={x['id'] for x in meta}
  if allocation is not None:
   chosen=allocation.get(s['id']) or []
   if not chosen or any(x not in valid for x in chosen):raise RuntimeError('Reviewed source allocation missing or incompatible '+s['id'])
   indexed={x['id']:x for x in meta};ordered=[indexed[x] for x in chosen]
  else:
   preferred=[by_path.get(urlparse(x['props']['src']).path) for x in base];ordered=[x for x in preferred if x and x['id'] in valid]+meta
  count=1 if ai_role else max(len(base),math.ceil(length/60));cursor=begin
  if allocation is not None:count=fit_source_count(ordered,used,length,count,cfg,fps)
  for j in range(count):
   frames=length//count+(j<length%count);pick=None
   for m in ordered:
    path=urlparse(m['object_url']).path;available=math.floor((m['end_ms']-m['begin_ms'])*fps/1000)
    window=(cfg.get('source_highlight_windows') or {}).get(path.rsplit('/',1)[-1],{});safe_start=math.ceil(window.get('start_seconds',0)*fps);safe_end=min(available,math.floor(window.get('end_seconds',available/fps)*fps))
    if path in used or safe_end-safe_start<frames+4:continue
    pick=m;break
   if not pick:raise RuntimeError('Insufficient original-speed unique source windows '+s['id'])
   path=urlparse(pick['object_url']).path;used.add(path);x=copy.deepcopy(base[min(j,len(base)-1)]);x.update(id=s['id']+'-'+str(j),**{'from':cursor,'durationInFrames':frames,'motion':None,'transitionIn':base[0].get('transitionIn') if j==0 else None,'transitionOut':None})
   if (x.get('transitionIn') or {}).get('type') in cfg.get('task_remove_black_reveal_transitions',[]):x['transitionIn']=None
   native_start=next((z['props'].get('startFrom',0) for z in base if urlparse(z['props']['src']).path==path),0);native_start=max(safe_start,min(native_start,max(safe_start,safe_end-frames-2)));x['props'].update(src=pick['object_url'],startFrom=native_start,playbackRate=1,volume=0,muted=True,fit='cover',scale=cfg.get('task_ai_footer_crop_scale',1) if ai_role else 1,x=0,y=0,alt=pick.get('description',''));x['props'].pop('loop',None);main.append(x);sources.append({'clip':x['id'],'source_segment_id':pick['id'],'source_path':path,'source_available_frames':available,'used_frames':frames,'startFrom':native_start});cursor+=frames
 caption=next(x for x in d['clips'] if x['trackId']=='overlay');style=copy.deepcopy(caption['props']);style.pop('text',None);style.update(font_size=36,top=78);native=[]
 for i,c in enumerate(cues):
  props={**style,'text':c['text']};hi=cfg.get('caption_highlights') or {}
  for rule in hi.get('rules',[]):
   if any(word in c['text'] for word in rule['terms']):props['text_color']=rule['color'];break
  native.append({**copy.deepcopy(caption),'id':'s'+c['segment']+'-'+str(i),'from':c['start'],'durationInFrames':c['end']-c['start'],'transitionIn':None,'transitionOut':None,'motion':None,'props':props})
 def tf(frame):
  i=min(len(b['segments'])-1,max(0,max((i for i,z in enumerate(oldbounds[:-1]) if z<=frame),default=0)));return min(total,bounds[i]+round((frame-oldbounds[i])*(bounds[i+1]-bounds[i])/max(1,oldbounds[i+1]-oldbounds[i])))
 extras=[]
 for old in d['clips']:
  if old['trackId'] in ['main','overlay','voiceover','watermark'] or old['id']=='cover':continue
  x=copy.deepcopy(old)
  if x['trackId']=='audio':x.update(**{'from':0,'durationInFrames':total});x['props']['volume']=script['config']['bgm']['volume'];x['props']['loop']=True
  else:
   x['from']=tf(old['from']);x['durationInFrames']=max(1,min(total-x['from'],tf(old['from']+old['durationInFrames'])-x['from']))
   if x['trackId']=='sticker' and x['from']<bounds[-2]:x['durationInFrames']=min(x['durationInFrames'],round(.9*fps))
   if x['trackId']=='sticker' and x['from']>=bounds[-2]:x['from']=bounds[-2];x['durationInFrames']=total-bounds[-2]
   if x['component']=='RemotionClip':validate_code(x['props']['code']);x['durationInFrames']=min(x['durationInFrames'],round(2.2*fps))
  if x['durationInFrames']>0:extras.append(x)
 voice=copy.deepcopy(next(x for x in d['clips'] if x['trackId']=='voiceover'));voice.update(id='voice-continuous',**{'from':0,'durationInFrames':total});voice['props'].update(src=tts['audio_url'],startFrom=0,playbackRate=1,volume=1)
 d['clips']=main+native+[voice]+extras;d['composition']['durationInFrames']=total
 if (cfg.get('watermark') or {}).get('add',True) and any(all(t.startswith('AI-') for t in s['visual_tags']) for s in b['segments']):
  from footage_qc import with_watermark
  d=with_watermark(d,[(bounds[i],bounds[i+1]-bounds[i]) for i,s in enumerate(b['segments']) if s['visual_tags'] and all(t.startswith('AI-') for t in s['visual_tags'])])
 return d,{'segment_frames':bounds,'source_windows':sources,'native_word_cues':cues,'whole_voice_tracks':1}

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['voice','gain','prepare','patch','render']);p.add_argument('--config',required=True);p.add_argument('--run',required=True);p.add_argument('--brief',required=True);p.add_argument('--patch-plan');p.add_argument('--revision',default='');p.add_argument('--voice-index',type=int);p.add_argument('--dry-run',action='store_true');a=p.parse_args();cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';r=Path(a.run);st=cb.State(r);b=next(x for x in cb.load_briefs(r) if x['brief_id']==a.brief);key=a.brief+'-V01';out=r/'demo-native'/key
 if a.revision:
  if not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}',a.revision):raise RuntimeError('Invalid revision name')
  out=out/a.revision
 out.mkdir(parents=True,exist_ok=True)
 if approval_problem(b) or aigc_problem(b):raise RuntimeError(approval_problem(b) or aigc_problem(b))
 if key not in st.videos:raise RuntimeError('Native cut not collected yet')
 v=st.videos[key];cut=cb.api('GET','/video-cuts/'+v['cut_id']);script=cb.api('GET','/video-scripts/'+cut['script_id']);save(out/'native-cut.json',cut)
 if a.cmd=='voice':
  voice=script['config']['tts']['voices'][a.voice_index if a.voice_index is not None else (3 if a.brief.endswith('02') else 6)];spoken=''.join(s['text'] for s in b['segments']);body={'text':spoken,'voice':{'platform':voice['platform'],'voice_type':voice['voice_type'],'resource_id':voice['config']['resource_id'],'speed_ratio':1.4},'force':False};receipt=out/'voice-budget.json'
  if a.dry_run:save(receipt,{'body':body,'at':time.time(),'count':1});print('DRY-RUN',key,'1 continuous native TTS',voice['voice_type'],'speed1.4');return
  if (out/'tts.json').exists():
   if load(out/'voice-intent.json')!=body:raise RuntimeError('Voice changed; use a new explicit revision')
   print(key,'reuse continuous TTS');return
  budget=load(receipt)
  if budget['body']!=body or time.time()-budget['at']>3600:raise RuntimeError('Missing exact recent TTS dry-run')
  intent=out/'voice-intent.json'
  if intent.exists():raise RuntimeError('Uncertain TTS outcome; no duplicate submit')
  save(intent,body);tts=cb.api('POST','/tts',body,tries=1);save(out/'tts.json',tts)
  if norm(tts['synthesized_text']).casefold()!=norm(spoken).casefold():raise RuntimeError('Native TTS copy mismatch')
  cb.fetch_file(tts['audio_url'],out/'voice.mp3');print(key,'continuous TTS ready',tts['audio']['duration_ms']);return
 if a.cmd=='gain':
  import numpy as np
  source=out/'voice.mp3';budget={'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'target_gain_db':4,'peak_ceiling':.9,'uploads':1,'TTS':0};receipt=out/'gain-budget.json'
  if a.dry_run:save(receipt,{**budget,'at':time.time()});print('DRY-RUN',key,'1 original-voice gain upload, 0 TTS; pitch/speed/timing unchanged');return
  prior=load(receipt)
  if {k:prior[k] for k in budget}!=budget or time.time()-prior['at']>3600:raise RuntimeError('Missing gain dry-run')
  asset=out/'gain-upload.json'
  if asset.exists():print(key,'reuse original-voice gain upload');return
  raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(source),'-ac','1','-f','f32le','-']);peak=float(np.max(np.abs(np.frombuffer(raw,dtype='<f4'))));gain=min(4,20*math.log10(.9/peak));dest=out/'voice-plus-gain.wav'
  if gain<=0:raise RuntimeError('No peak headroom for lossless linear gain')
  subprocess.run(['ffmpeg','-v','error','-y','-i',str(source),'-af',f'volume={gain}dB','-c:a','pcm_s16le',str(dest)],check=True)
  tok=cb.api('POST','/storage/upload-tokens',{'filename':key+'-original-voice-gain.wav','content_type':'audio/wav','content_length':dest.stat().st_size,'purpose':'material'},tries=1)
  request=urllib.request.Request(tok['upload_url'],data=dest.read_bytes(),method='PUT',headers=dict(tok.get('headers') or {},**{'Content-Type':'audio/wav'}));urllib.request.urlopen(request,timeout=120).read();save(asset,{'object_key':tok['object_key'],'gain_db':gain,'source_peak':peak,'source_sha256':budget['source_sha256'],'TTS':0});print(key,'original voice gain uploaded',round(gain,2),'dB');return
 if a.cmd=='patch':
  plan=load(a.patch_plan);entry=plan['videos'][key]
  if not plan.get('authorization'):raise RuntimeError('Missing user revision authorization')
  draft=copy.deepcopy(cut['draft']);save(out/('patch-before-'+digest(draft)[:12]+'.json'),cut)
  for x in draft['clips']:
   if x['trackId']=='overlay':
    for rule in (cfg.get('caption_highlights') or {}).get('rules',[]):
     if any(term in x['props']['text'] for term in rule['terms']):x['props']['text_color']=rule['color'];break
   if x['trackId']=='voiceover':
    x['props']['volume']=entry['voice_volume']
    if entry.get('original_voice_gain'):
     asset=load(out/'gain-upload.json');x['props']['src']=cb.api('POST','/storage/download-tokens',{'object_key':asset['object_key']})['download_url']
   if x['id'] in entry.get('source_starts',{}):
    change=entry['source_starts'][x['id']]
    if urlparse(x['props']['src']).path!=change['source_path']:raise RuntimeError('Changed source identity')
    if change.get('replacement_src'):
     x['props']['src']=change['replacement_src'];x['props']['alt']=change['replacement_description']
    x['props']['startFrom']=change['startFrom']
    if x['props']['startFrom']+x['durationInFrames']>change['available_frames']:raise RuntimeError('Source overrun')
  from qc import cut_checks
  issues=cut_checks(b,draft,load(out/'voice-intent.json')['voice']['voice_type']);save(out/'draft.json',draft);save(out/'preflight.json',{'issues':issues,'draft_hash':digest(draft),'patch_plan':plan})
  if issues:raise RuntimeError('Patch gate: '+str(issues))
  print(key,'prepared volume/source-window patch; voice, speed, copy and all timing preserved');return
 if a.cmd=='prepare':
  b=copy.deepcopy(b)
  for sg in b['segments']:
   hints=(cfg.get('demo_caption_chunks',{}).get(b['brief_id'],{})).get(sg['id'])
   if hints:sg['caption_chunks']=hints
  tts=load(out/'tts.json')
  if (out/'gain-upload.json').exists():tts['audio_url']=cb.api('POST','/storage/download-tokens',{'object_key':load(out/'gain-upload.json')['object_key']})['download_url']
  library=cb.list_segments(cfg['products'][b['product_key']]['clipo_product_id']);draft,evidence=build(b,cut,tts,script,library,cfg,key)
  # Native assembly can omit an effect node; materialize every configured product/texture title.
  existing={x['id'].split('-')[0] for x in draft['clips'] if x['trackId']=='effect'}
  for x in draft['clips']:
   if x['trackId']=='effect':
    parent=next((s for s in script['segments'] if s['id']==x['id'].split('-')[0]),None)
    if parent and parent.get('effect'):x['props']['params']=copy.deepcopy(parent['effect'].get('params',{}))
  for i,sg in enumerate(script['segments']):
   if not sg.get('effect') or sg['id'] in existing:continue
   fx=sg['effect'];template=effect_template(fx['template_id'],r);validate_code(template['code']);start,end=evidence['segment_frames'][i:i+2];draft['clips'].append({'id':sg['id']+'-x0','trackId':'effect','from':start,'durationInFrames':min(end-start,66),'component':'RemotionClip','props':{'code':template['code'],'params':fx.get('params',{}),'x':0,'y':0,'scale':1}})
  from quality_contract import contract
  if contract(cfg).get('require_title_entry_sound'):
   from title_audio import from_inventory
   draft=from_inventory(draft,r,key)
  save(out/'draft.json',draft);save(out/'timing-and-sources.json',evidence)
  from qc import cut_checks
  issues=cut_checks(b,draft,load(out/'voice-intent.json')['voice']['voice_type']);save(out/'preflight.json',{'issues':issues,'draft_hash':digest(draft)})
  if issues:raise RuntimeError('Pre-render gate: '+str(issues))
  print(key,'prepared native demo',draft['composition']['durationInFrames']/draft['composition']['fps'],'seconds');return
 draft=load(out/'draft.json');check=load(out/'preflight.json')
 if check['issues'] or check['draft_hash']!=digest(draft):raise RuntimeError('Prepared draft changed or failed')
 if a.dry_run:save(out/'render-budget.json',{'hash':digest(draft),'at':time.time(),'count':1});print('DRY-RUN',key,'1 same-cut cloud render');return
 prior=load(out/'render-budget.json')
 if prior['hash']!=digest(draft) or time.time()-prior['at']>3600:raise RuntimeError('Missing render dry-run')
 receipt=out/('render-'+digest(draft)[:16]+'.json')
 if receipt.exists():job=load(receipt)
 else:
  save(out/'before-write.json',cut);cb.api('PUT','/video-cuts/'+v['cut_id'],{'script_id':cut['script_id'],'draft':draft},tries=1);back=cb.api('GET','/video-cuts/'+v['cut_id']);cb.check_readback(draft,back['draft'],False);save(out/'after-write.json',back);job=cb.api('POST','/render-jobs',{'spec':back['draft'],'cut_id':v['cut_id'],'options':{'codec':'h264','quality':'high','scale':1},'idempotency_key':'demo-whole-'+v['cut_id']+'-'+digest(draft)[:12]},tries=1);save(receipt,job)
 save(out/'render.json',job)
 v.update(render_job=job['job_id'],stage='rendering',duration_s=draft['composition']['durationInFrames']/draft['composition']['fps'],voice=load(out/'voice-intent.json')['voice']['voice_type'],texts=[s['text'] for s in b['segments']],voice_cmd=False,qc_issues=[],copy_issues=[],ai_review=None,ai_reason=None)
 for f in ['error','last_error','object_key','bad_voice','watermark_missing']:v.pop(f,None)
 st.save();print(key,'rendering',job['job_id'])
if __name__=='__main__':main()
