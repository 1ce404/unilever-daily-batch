#!/usr/bin/env python3
"""[INPUT] Frozen legacy group, visually inspected sources, explicit user repair scope.
[OUTPUT] Exact narrow drafts, native render/review receipts and current-output QC.
[POS] Extension of group_quality_rework.py; no new AIGC/TTS or shared-library mutations.
"""
import argparse,copy,hashlib,json,math,subprocess,sys,time,urllib.parse
from collections import Counter,defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import load_config
from lux_final_qc import canonical,save
from group_quality_rework import EXCLUDED_ID,EXCLUDED_PATH
PILOTS=['B01-V01','B02-V01','B03-V01','B04-V01']
STYLES=['6ab0980646ba4a9c2877385c','6ab0980646ba4a9c2877385e','6ab0980646ba4a9c2877385d','6ae85bf753ee66572fe133b8','6ab0980646ba4a9c28773861']
FONTS=['DouyinSansBold','OPPOSans-H','阿里巴巴-普惠-bold','TaoBaoMaiCaiTi-Regular','douyinmeihao-bold']
TEMPLATES=['6afd05026af35ab34a0a5cb7','6afd05036af35ab34a0a5cc0','6afd05026af35ab34a0a5cbd','6afd05036af35ab34a0a5cc4']
def read(p):return json.loads(Path(p).read_text())
def srcpath(c):return urllib.parse.urlparse(c['props']['src']).path
def digest(d):
 def numeric(x):
  if isinstance(x,dict):return {k:numeric(v) for k,v in x.items()}
  if isinstance(x,list):return [numeric(v) for v in x]
  if isinstance(x,(float,int)) and not isinstance(x,bool):return float(x)
  return x
 return hashlib.sha256(json.dumps(numeric(canonical({k:v for k,v in d.items() if k!='cover_url'} if isinstance(d,dict) else d)),ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def selected(a,root):
 rows=read(root/'video-manifest.json')
 if a.keys and a.cmd in ['apply','status','review','media']:
  rows=[x for x in rows if x['key'] in a.keys.split(',')]
 if a.stage=='pilot':return [x for x in rows if x['key'] in PILOTS]
 if a.stage=='rest':return [x for x in rows if x['key'] not in PILOTS]
 return rows

def reviewed_pool(root):
 pools=read(root/'pool-candidates.json');windows=read(root/'pool-windows.json')
 indices={'pain':[4,5,6,7,8,11,12],'after':[1,2,3,4,5,6,7,8,10],'product':[1,4,5,7,9,10,12,14,15,16],'texture':[2,3,4,5,6,7,8,9],'cta':[1,3,4,6,11,16],'fresh':[1,2,3,4]}
 trims={'6ae4774d3f3e55b93ac64e24':(30,None),'6ae4772b3f3e55b93ac64de1':(12,None),'6ad5ce6c43d91170f3d225f4':(4,101),'6ad5ce6c43d91170f3d225f3':(4,72),'6ad5ce6c43d91170f3d225f1':(18,125),'6ae477943f3e55b93ac64e85':(8,None),'6ae4774c3f3e55b93ac64e23':(18,None),'6ad5b49a43d91170f3d225ee':(12,174),'6ae476a63f3e55b93ac64da5':(8,52)}
 out={}
 for role,nums in indices.items():
  out[role]=[]
  for n in nums:
   m=copy.deepcopy(pools[role][n-1]);p=urllib.parse.urlparse(m['object_url']).path;w=copy.deepcopy(windows[p]);start,end=trims.get(m['id'],(w['safe_start'],w['safe_end']));w['safe_start']=start;w['safe_end']=min(w['safe_end'],end or w['safe_end']);m['window']=w
   # Separate camera setups; do not infer a person's identity.
   m['setup']='black-shirt-curly' if role=='after' and n in [1,2,3] else m['id'];out[role].append(m)
 out['cta']+=copy.deepcopy(out['product'])
 save(root/'reviewed-source-pool.json',{'method':'Agent actually inspected 4 time points for each candidate; narrow windows exclude bottle exits, opaque wrapper lead-ins, excessive scalp and unsuitable claim/product shots.','pools':out,'banned_source':EXCLUDED_PATH});return out

def fonts(root):
 from PIL import ImageFont
 allfonts=read(root/'resources/fonts.json');wanted=[next(x for x in allfonts if x['name']==n) for n in FONTS];texts=''.join(c['props']['text'] for r in read(root/'video-manifest.json') for c in read(root/'cuts'/(r['key']+'.json'))['draft']['clips'] if c['component']=='SubtitleClip')
 for x in wanted:
  file=root/'resources'/('font-'+x['id']+'.ttf')
  if not file.exists():cb.fetch_file(x['url'],file)
  f=ImageFont.truetype(str(file),48);missing=bytes(f.getmask('\U0010ffff'));bad=[c for c in set(texts) if c.strip() and bytes(f.getmask(c))==missing]
  if bad:raise RuntimeError('Font glyphs absent '+x['name']+str(bad))
 save(root/'resources/verified-fonts.json',wanted);return wanted

def makeplan(a,cfg):
 root=Path(a.out);pool=reviewed_pool(root);ff=fonts(root);styles={x['id']:x for x in read(root/'resources/subtitle-styles.json')['items']};sounds=read(root/'resources/verified-sfx.json');stickers=read(root/'resources/verified-stickers.json');levels=read(root/'resources/audio-levels.json');usage=Counter();seconds=Counter();setupuse=Counter();assignments=[];known={urllib.parse.urlparse(x['object_url']).path:x for values in pool.values() for x in values}
 def pick(role,used,usedsetups,need,key,sid,index):
  eligible=[x for x in pool[role] if urllib.parse.urlparse(x['object_url']).path not in used and (role!='after' or x['setup'] not in usedsetups) and x['window']['safe_end']-x['window']['safe_start']>=need]
  if not eligible:raise RuntimeError('No safe distinct source '+str((key,role,need)))
  eligible.sort(key=lambda x:(seconds[urllib.parse.urlparse(x['object_url']).path],usage[urllib.parse.urlparse(x['object_url']).path],setupuse[x['setup']],hashlib.sha256(f"{key}:{sid}:{index}:{x['id']}".encode()).hexdigest()))
  m=eligible[0];p=urllib.parse.urlparse(m['object_url']).path;usage[p]+=1;seconds[p]+=need/30;setupuse[m['setup']]+=1;used.add(p);usedsetups.add(m['setup']);return m
 for vi,row in enumerate(read(root/'video-manifest.json')):
  key=row['key'];bn=int(key[1:3]);old=read(root/'cuts'/(key+'.json'));d=copy.deepcopy(old['draft']);total=d['composition']['durationInFrames'];shift=114 if bn>=3 else 0;d['composition']['durationInFrames']-=shift;total-=shift;main=[c for c in d['clips'] if c['trackId']=='main'];original_intro=copy.deepcopy(main[0]);introend=66 if shift else original_intro['durationInFrames'];body=[c for c in d['clips'] if c['trackId'] not in ['main','watermark','audio']]
  for c in body:c['from']-=shift
  d['clips']=body;used=set();usedsetups=set();spans=[];meta=[]
  if not shift:d['clips'].append(original_intro);used.add(srcpath(original_intro));spans.append((0,introend))
  else:
   # 3 distinct introductions for B03 and 2 for B04, from current product library.
   n=int(key[-2:])-1;idx=([0,2,3] if bn==3 else [1,2])[n];m=pool['fresh'][idx];p=urllib.parse.urlparse(m['object_url']).path;used.add(p);usage[p]+=1;seconds[p]+=introend/30
   c=copy.deepcopy(original_intro);c['durationInFrames']=introend;c['props'].update(src=m['object_url'],startFrom=8,volume=0,muted=True);d['clips'].append(c);spans.append((0,introend));meta.append({'clip':c['id'],'role':'fresh','source':m['id'],'path':p})
  bounds={}
  for sid in range(2,7):
   originals=[c for c in main if c['id'].split('-')[0]==str(sid)];start=min(c['from'] for c in originals)-shift;end=max(c['from']+c['durationInFrames'] for c in originals)-shift;length=end-start;bounds[sid]=(start,end);role={2:'pain' if bn<3 else 'after',3:'product',4:'texture',5:'after',6:'cta'}[sid]
   # Keep full product name on a continuous readable front, then change shot if needed.
   count=max(1,math.ceil(length/(55 if role in ['after','texture'] else 65)));sizes=[length//count+(1 if n<length%count else 0) for n in range(count)];at=start
   for j,n in enumerate(sizes):
    m=pick(role,used,usedsetups,n,key,sid,j);w=m['window'];p=urllib.parse.urlparse(m['object_url']).path;startfrom=w['safe_start'];slack=w['safe_end']-startfrom-n
    if role in ['pain','after','texture'] and slack>9:startfrom+=min(slack, (vi%3)*4)
    c=copy.deepcopy(originals[0]);c.update(id=f'{sid}-repair-{j}',**{'from':at},durationInFrames=n,transitionIn=None,transitionOut=None,motion=None);c['props'].update(src=m['object_url'],startFrom=startfrom,volume=0,muted=True,playbackRate=1,fit='cover',scale=1,x=0,y=0)
    if j==0 and sid in [3,5]:c['transitionIn']={'type':'hardCutFlash' if (vi+sid)%2 else 'radialZoomBlur','durationInFrames':4 if (vi+sid)%2 else 6}
    d['clips'].append(c);meta.append({'clip':c['id'],'role':role,'source':m['id'],'path':p,'start':startfrom,'end':startfrom+n,'physical_end':w['safe_end']});
    if m.get('ai_generated'):spans.append((at,at+n))
    at+=n
  native=styles[STYLES[vi%5]];font=ff[vi%5];mapping={'alignType':'align_type','backgroundColor':'background_color','bold':'bold','italic':'italic','underline':'underline','strokeColor':'stroke_color','strokeWidth':'stroke_width','textColor':'text_color','textShadow':'text_shadow'}
  for c in d['clips']:
   if c['component']=='SubtitleClip':
    p=c['props'];p.update({v:native.get(k) for k,v in mapping.items()});p.update(font_size=36,top=78,font_family=font['name'],font_url=font['url']);p['text_color']=p.get('text_color') or '#ffffff'
    if any(t in p['text'] for t in ['清扬','SCA','酰胺']):
     p['text_color']='#C8F751' if native.get('strokeColor') in ['#000000','#4a4238'] or native.get('backgroundColor') else '#087750'
  # Preserve existing music diversity; balance its loudness to each actual whole voice track.
  voice=next(c for c in d['clips'] if c['trackId']=='voiceover');bgm=copy.deepcopy(next(c for c in old['draft']['clips'] if c['trackId']=='audio'));vm=levels[srcpath(voice)]['mean_db'];bm=levels[srcpath(bgm)]['mean_db'];gain=max(.055,min(.14,10**((vm-10-bm)/20)))
  first=copy.deepcopy(bgm);first.update(id='bgm-intro',durationInFrames=introend);first['props'].update(volume=.035 if bn<3 else round(gain,3),fadeOut=0)
  second=copy.deepcopy(bgm);second.update(id='bgm-body',**{'from':introend},durationInFrames=total-introend);second['props'].update(startFrom=introend,volume=round(gain,3),fadeOut=9);d['clips'] += [first,second]
  # Native upper packaging uses small safe top placements; never a substitute for dialogue subtitles.
  hook={1:'见面先精神点',2:'发型也要在线',3:'清爽带点淡淡清香',4:'出门也要清爽'}[bn];selling='双重酰胺组合' if bn<3 else 'SCA控油蓬松技术';events=[(bounds[2][0]+4,min(62,bounds[2][1]-bounds[2][0]-4),hook),(bounds[4][0]+3,min(66,bounds[4][1]-bounds[4][0]-3),selling),(bounds[6][0]+3,min(60,bounds[6][1]-bounds[6][0]-3),'戳直播间看看')]
  for ei,(at,n,text) in enumerate(events):
   ti=(vi+ei)%4;tid=TEMPLATES[ti];tpl=read(root/'resources'/(tid+'.json'));params={**tpl['params'],'text':text,'accent':['#B6ED56','#67D3C1','#C8F751','#88CDE0'][vi%4],'position':'top_center','safeTop':.02,'safeBottom':.1,'maxWidth':780,'x':0,'y':0,'plate':False,'scale':[.72,.92,.92,1.02][ti]}
   if ti==3:params.update(safeTop=.006,y=-60)
   d['clips'].append({'id':f'upper-{ei}','component':'RemotionClip','trackId':'effects','from':at,'durationInFrames':n,'props':{'code':tpl['code'],'params':params,'x':0,'y':0,'scale':1}})
  cue=next(c for c in d['clips'] if c['component']=='SubtitleClip' and '戳直播间' in c['props']['text']);arrow=stickers[[0,1,5,6][vi%4]]
  d['clips'].append({'id':'cta-arrow','component':'StickerClip','trackId':'sticker','from':max(bounds[6][0],cue['from']-4),'durationInFrames':total-max(bounds[6][0],cue['from']-4),'props':{'src':arrow['url'],'alt':'戳直播间箭头','width':arrow['width'],'height':arrow['height'],'x':0,'y':0,'scale':1,'rotation':0,'opacity':.95,'z_index':1,'loop':True}})
  for si,(at,label) in enumerate([(bounds[3][0],'product'),(bounds[4][0],'selling'),(cue['from'],'cta')]):
   s=sounds[(vi+si)%len(sounds)];n=max(1,math.floor(s['actual_duration']*30));d['clips'].append({'id':'sfx-'+label,'component':'AudioClip','trackId':'sound_effects','from':at,'durationInFrames':min(n,total-at),'props':{'src':s['url'],'volume':.1 if si<2 else .13,'startFrom':0,'playbackRate':1,'loop':False,'fadeIn':0,'fadeOut':1 if n>3 else 0}})
  d['tracks']=[x for x in d['tracks'] if x['id']!='watermark']+[{'id':'effects','name':'原生上方花字','enabled':True},{'id':'sticker','name':'结尾箭头','enabled':True},{'id':'sound_effects','name':'动作与引导音效','enabled':True}]
  # Reuse the exact existing AI label source and restrict it to actual AI frames.
  wm=next(c for c in old['draft']['clips'] if c['trackId']=='watermark');d['tracks'].append({'id':'watermark','name':'AI标识','enabled':True})
  for i,(start,end) in enumerate(spans):c=copy.deepcopy(wm);c.update(id='compliance-watermark-'+str(i),**{'from':start},durationInFrames=end-start);d['clips'].append(c)
  for c in d['clips']:
   for field in ['transitionIn','transitionOut','motion']:c.setdefault(field,None)
  plan={'key':key,'cut_id':row['cut_id'],'script_id':row['script_id'],'shift':shift,'before_fingerprint':digest(old['draft']),'draft':d,'assignments':meta,'variant':{'style':native['id'],'font':font['id'],'upper_families':[(vi+i)%4 for i in range(3)],'arrow':arrow['id'],'bgm_gain':round(gain,3)},'reason':'撤掉用户指出源、纠正语义错配/香型文字；轮换原生包装并补结尾箭头音效；原口播及前25条开场原声保留。'}
  validate(old['draft'],plan);save(root/'plans'/(key+'.json'),plan);assignments.append({k:plan[k] for k in ['key','assignments','variant']})
 freq=[{'path':p,'count':usage[p],'seconds':round(seconds[p],2),'source':known[p]['id'],'name':known[p]['name']} for p in usage if p in known];freq.sort(key=lambda x:-x['seconds']);save(root/'source-frequency.json',freq);save(root/'all-assignments.json',assignments)
 if EXCLUDED_PATH in usage:raise RuntimeError('User-banned footage used')
 print('PLANNED existing cuts 30; TTS 0 / AIGC 0; 5 caption/font pairs; 4 upper families; every CTA arrow + sound. Distinct sources',len(freq),'max source appearances',max(usage.values()),flush=True)

def validate(old,plan):
 d=plan['draft'];shift=plan['shift'];clips=d['clips'];main=sorted([c for c in clips if c['trackId']=='main'],key=lambda c:c['from']);end=0;seen=set()
 for c in main:
  if c['from']!=end:raise RuntimeError('Main gap/overlap '+plan['key'])
  end+=c['durationInFrames'];p=srcpath(c)
  if p==EXCLUDED_PATH or p in seen:raise RuntimeError('Banned or duplicate source '+plan['key'])
  seen.add(p)
 if end!=d['composition']['durationInFrames']:raise RuntimeError('Main coverage')
 oldsubs=[(c['id'],c['from']-shift,c['durationInFrames'],c['props']['text']) for c in old['clips'] if c['component']=='SubtitleClip'];newsubs=[(c['id'],c['from'],c['durationInFrames'],c['props']['text']) for c in clips if c['component']=='SubtitleClip']
 if oldsubs!=newsubs:raise RuntimeError('Original subtitle text/timing changed')
 ovo=next(c for c in old['clips'] if c['trackId']=='voiceover');nvo=copy.deepcopy(next(c for c in clips if c['trackId']=='voiceover'));nvo['from']+=shift
 if canonical(ovo)!=canonical(nvo):raise RuntimeError('Original whole voice modified')
 if not shift:
  oi=next(c for c in old['clips'] if c['id']=='1-0')
  if canonical(oi)!=canonical(main[0]):raise RuntimeError('Protected original dialogue changed')
 if any(c['props']['font_size']!=36 or c['props']['top']!=78 or '\n' in c['props']['text'] or sum(1 if ord(t)>255 else .6 for t in c['props']['text'])>12 for c in clips if c['component']=='SubtitleClip'):raise RuntimeError('Caption line contract')
 uniform_fields=['font_family','font_size','top','stroke_color','stroke_width','background_color','bold','italic','text_shadow']
 if len({tuple(c['props'].get(f) for f in uniform_fields) for c in clips if c['component']=='SubtitleClip'})!=1:raise RuntimeError('Within-video native subtitle style differs')
 for c in clips:
  if c['from']<0 or c['durationInFrames']<=0 or c['from']+c['durationInFrames']>end:raise RuntimeError('Timeline bounds')
 for m in plan['assignments']:
  if 'end' in m and m['end']>m['physical_end']:raise RuntimeError('Source overrun')
 arrow=next(c for c in clips if c['id']=='cta-arrow');sfx=next(c for c in clips if c['id']=='sfx-cta');cue=next(c for c in clips if c['component']=='SubtitleClip' and '戳直播间' in c['props']['text'])
 if any(arrow['props'].get(k)!=v for k,v in {'x':0,'y':0,'scale':1,'rotation':0}.items()):raise RuntimeError('CTA must retain native Clipo right-avatar canvas placement')
 if sfx['from']!=cue['from'] or not arrow['from']<=cue['from']<arrow['from']+arrow['durationInFrames']:raise RuntimeError('CTA not aligned')


def apply(a,cfg):
 root=Path(a.out);rows=selected(a,root);keys=[r['key'] for r in rows];plans=[read(root/'plans'/(k+'.json')) for k in keys];budget=root/('budget-'+a.stage+'.json')
 for plan in plans:
  if plan.get('variant',{}).get('bgm_reference'):
   from group_bgm_rework import validate_mix
   validate_mix(plan,root)
 if a.dry_run:
  save(budget,{'at':time.time(),'keys':keys,'fingerprints':[digest(p['draft']) for p in plans],'renders':len(keys),'tts':0,'aigc':0});print('DRY-RUN',a.stage,'existing renders',len(keys),'new videos/TTS/AIGC 0',flush=True);return
 b=read(budget)
 if time.time()-b['at']>3600 or b['keys']!=keys or b['fingerprints']!=[digest(p['draft']) for p in plans]:raise RuntimeError('Expired or different paid-count receipt')
 if a.stage=='rest':
  ledger=read(root/'new-visual-ledger.json')
  if any(ledger.get(k,{}).get('status')!='pass' for k in PILOTS):raise RuntimeError('4 pilots must actually pass visual QC')
 for row,plan in zip(rows,plans):
  key=row['key'];receipt=root/'renders'/(key+'.json');intent=root/'renders'/(key+'-intent.json')
  if receipt.exists():print(key,'existing submission retained',flush=True);continue
  current=cb.api('GET',f"/video-cuts/{row['cut_id']}");old=read(root/'cuts'/(key+'.json'));validate(old['draft'],plan)
  if digest(current['draft']) not in [digest(old['draft']),digest(plan['draft'])]:
   previous=root/'readbacks'/(key+'.json')
   if not previous.exists() or digest(read(previous)['draft'])!=digest(current['draft']):raise RuntimeError('Concurrent native change '+key)
  if intent.exists():raise RuntimeError('Uncertain prior POST; reconcile receipt before retry '+key)
  if 'cover_url' in current['draft']:plan['draft']['cover_url']=current['draft']['cover_url'];save(root/'plans'/(key+'.json'),plan)
  if not (root/'backups'/(key+'.json')).exists():save(root/'backups'/(key+'.json'),current)
  if digest(current['draft'])!=digest(plan['draft']):cb.api('PUT',f"/video-cuts/{row['cut_id']}",{'script_id':row['script_id'],'draft':plan['draft']})
  back=cb.api('GET',f"/video-cuts/{row['cut_id']}")
  save(root/'readbacks'/(key+'.json'),back)
  if canonical(back['draft'])!=canonical(plan['draft']):
   def diff(x,y,path=''):
    if isinstance(x,dict) and isinstance(y,dict):
     for k in x.keys()|y.keys():
      if k not in x or k not in y:print('DIFF',path+'/'+k,'missing',k not in x,k not in y,flush=True)
      else:diff(x[k],y[k],path+'/'+k)
    elif isinstance(x,list) and isinstance(y,list):
     for i,(xx,yy) in enumerate(zip(x,y)):diff(xx,yy,path+'/'+str(i))
     if len(x)!=len(y):print('DIFF count',path,len(x),len(y),flush=True)
    elif x!=y:print('DIFF',path,str(x)[:70],str(y)[:70],flush=True)
   diff(canonical(plan['draft']),canonical(back['draft']))
   raise RuntimeError('Whole draft readback mismatch '+key)
  save(root/'readbacks'/(key+'.json'),back);payload={'spec':back['draft'],'options':{'codec':'h264','quality':'high','scale':1},'cut_id':row['cut_id'],'idempotency_key':'group-6b068349-'+row['cut_id']+'-'+digest(plan['draft'])[:16]};save(intent,{'state':'submitting','payload':payload});job=cb.api('POST','/render-jobs',payload,tries=1);save(receipt,{'key':key,'cut_id':row['cut_id'],'job_id':job['job_id'],'fingerprint':digest(plan['draft']),'job':job});save(intent,{'state':'submitted','job_id':job['job_id']});print(key,'native draft readback + render',job['job_id'],flush=True)


def status(a,cfg):
 from concurrent.futures import ThreadPoolExecutor
 root=Path(a.out);rows=selected(a,root)
 def one(row):
  key=row['key'];p=root/'renders'/(key+'.json')
  if not p.exists():return {'key':key,'status':'not_submitted'}
  rec=read(p);job=cb.api('GET',f"/render-jobs/{rec['job_id']}");rec['job']=job
  if job['status']=='success':
   cut=cb.api('GET',f"/video-cuts/{rec['cut_id']}");save(root/'current-cuts'/(key+'.json'),cut)
   if cut.get('output_object_key')!=job.get('object_key'):raise RuntimeError('Cut output changed '+key)
   plan=read(root/'plans'/(key+'.json'))
   if digest(cut['draft'])!=digest(plan['draft']):raise RuntimeError('Current draft changed '+key)
   rec.setdefault('submission_fingerprint',rec['fingerprint']);rec['fingerprint']=digest(plan['draft'])
   rec['review']={k:cut.get(k) for k in ['ai_review_status','ai_review_result','ai_review_reason']}
  save(p,rec);return {'key':key,'status':job['status'],'review':rec.get('review'),'error':(job.get('error_message') or '')[:200]}
 with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(one,rows))
 save(root/('status-'+a.stage+'.json'),results);print(json.dumps(results,ensure_ascii=False),flush=True)

def review(a,cfg):
 root=Path(a.out);rows=selected(a,root);ready=[]
 for row in rows:
  rec=read(root/'renders'/(row['key']+'.json'));job=rec['job']
  if job['status']!='success':raise RuntimeError('Not rendered '+row['key'])
  ready.append({'key':row['key'],'cut_id':row['cut_id'],'output':job['object_key']})
 p=root/('review-'+a.stage+'-'+hashlib.sha256(json.dumps(ready,sort_keys=True).encode()).hexdigest()[:12]+'.json')
 if p.exists():
  previous=read(p)
  if previous.get('state')!='submitted' or previous.get('videos')!=ready:raise RuntimeError('Uncertain review receipt; reconcile before retry')
  print('Exact-output review already submitted; use status');return
 if a.dry_run:print('DRY-RUN existing-output AI review',len(ready),'no generation');return
 save(p,{'state':'submitting','videos':ready});response=cb.api('POST','/video-cuts/review',{'cut_ids':[x['cut_id'] for x in ready]},tries=1);save(p,{'state':'submitted','videos':ready,'response':response})
 if response.get('queued')!=len(ready) or response.get('skipped',0):raise RuntimeError('Review count mismatch '+str(response))
 print('Current-output native review queued',len(ready),flush=True)


def media(a,cfg):
 from concurrent.futures import ThreadPoolExecutor
 from media_qc import inspect
 from PIL import Image,ImageDraw
 root=Path(a.out)
 def one(row):
  key=row['key'];rec=read(root/'renders'/(key+'.json'));job=rec['job']
  if job['status']!='success':raise RuntimeError('Not rendered '+key)
  d=read(root/'plans'/(key+'.json'))['draft'];out=root/'qc-new'/key;out.mkdir(parents=True,exist_ok=True);f=out/'current.mp4'
  if not f.exists():cb.fetch_file(job['download_url'],f)
  qc=out/'media-qc.json';result=read(qc) if qc.exists() else inspect(f,d,out,{'max_black_interval_seconds':.035})
  # Inspect packaging mid-animation as well as each actual shot's start/mid/end.
  frames=result['frames'][:]
  for c in d['clips']:
   if c['id'].startswith('upper-') or c['id']=='cta-arrow':
    t=(c['from']+min(15,c['durationInFrames']//2))/30;dest=out/(c['id']+'.jpg')
    if not dest.exists():subprocess.run(['ffmpeg','-v','error','-y','-ss',str(t),'-i',str(f),'-frames:v','1','-vf','scale=432:-2',str(dest)],check=True)
    frames.append({'file':str(dest),'clip':c['id'],'time':t})
  board=Image.new('RGB',(864,math.ceil(len(frames)/6)*276+28),'#eee');draw=ImageDraw.Draw(board);draw.text((4,4),key+' revised / every shot + upper / CTA',fill='black')
  for i,x in enumerate(frames):
   pic=Image.open(x['file']).convert('RGB');pic.thumbnail((144,256));xx=i%6*144;yy=i//6*276+28;board.paste(pic,(xx,yy));draw.text((xx+2,yy+256),x['clip']+' '+str(round(x['time'],2)),fill='black')
  boardpath=root/'新版检查图'/(key+'.jpg');boardpath.parent.mkdir(exist_ok=True);board.save(boardpath,quality=90);print(key,'technical',result['technical_pass'],'peak',result['peak_dbfs'],'black',result['black_intervals'],flush=True);return {'key':key,'fingerprint':rec['fingerprint'],'technical_pass':result['technical_pass'],'board':str(boardpath),'video':str(f)}
 with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(one,selected(a,root)))
 save(root/('new-media-'+a.stage+'.json'),results)


def accept(a,cfg):
 root=Path(a.out)
 if not a.keys or not a.note:raise RuntimeError('Explicit actually-viewed keys and review note required')
 ledger=read(root/'new-visual-ledger.json') if (root/'new-visual-ledger.json').exists() else {}
 for key in a.keys.split(','):
  rec=read(root/'renders'/(key+'.json'));qc=read(root/'qc-new'/key/'media-qc.json')
  if not qc['technical_pass']:raise RuntimeError('Technical failure '+key)
  ledger[key]={'status':'pass','reviewer':'Agent','fingerprint':rec['fingerprint'],'board':str(root/'新版检查图'/(key+'.jpg')),'note':a.note,'at':time.time()}
 save(root/'new-visual-ledger.json',ledger);print('Actual visual inspection recorded',a.keys)


def diagnose(a,cfg):
 root=Path(a.out);key=a.keys or 'B01-V01'
 if key.startswith('script-'):
  sid=key.split('-',1)[1];plan=read(root/'script-plans'/(sid+'.json'));back=cb.api('GET','/video-scripts/'+sid);expected=plan['body'];actual={k:back[k] for k in expected}
 else:
  plan=read(root/'plans'/(key+'.json'));back=cb.api('GET',f"/video-cuts/{plan['cut_id']}");expected=plan['draft'];actual=back['draft']
 save(root/'diagnostics'/(key+'.json'),back)
 def diff(x,y,path=''):
  if isinstance(x,dict) and isinstance(y,dict):
   for k in x.keys()|y.keys():
    if k not in x or k not in y:print('DIFF',path+'/'+k,'missing expected/actual',k not in x,k not in y)
    else:diff(x[k],y[k],path+'/'+k)
  elif isinstance(x,list) and isinstance(y,list):
   for i,(xx,yy) in enumerate(zip(x,y)):diff(xx,yy,path+'/'+str(i))
   if len(x)!=len(y):print('DIFF count',path,len(x),len(y))
  elif x!=y:print('DIFF',path,str(x)[:90],str(y)[:90])
 diff(canonical(expected),canonical(actual))

def configure(a,cfg):
 root=Path(a.out);pools=read(root/'reviewed-source-pool.json')['pools'];manifest=read(root/'video-manifest.json');nativefonts=read(root/'resources/verified-fonts.json');soundids=[x['id'] for x in read(root/'resources/verified-sfx.json')];library=read(next((root/'resources').glob('*-segments.json')));pathids={urllib.parse.urlparse(x.get('object_url','')).path:x['id'] for x in library if x.get('object_url')}
 for bn in range(1,5):
  rows=[x for x in manifest if x['key'].startswith(f'B{bn:02d}-')];sid=rows[0]['script_id'];original=read(root/'scripts'/(sid+'.json'));wanted=copy.deepcopy(original);wanted['config']['subtitle_style'].update(style_ids=STYLES,font_ids=[x['id'] for x in nativefonts],font_size=36,top=78);wanted['config']['bgm']['volume']=.09
  for seg in wanted['segments']:
   sn=int(seg['id']);role={2:'pain' if bn<3 else 'after',3:'product',4:'texture',5:'after',6:'cta'}.get(sn)
   if sn==1:
    if bn<3:ids=list(dict.fromkeys(pathids[srcpath(next(c for c in read(root/'cuts'/(x['key']+'.json'))['draft']['clips'] if c['id']=='1-0'))] for x in rows))
    else:ids=[pools['fresh'][i]['id'] for i in ([0,2,3] if bn==3 else [1,2])]
   else:ids=list(dict.fromkeys(x['id'] for x in pools[role]))
   seg['visual']={'mode':'material_segment_ids','props':{'segment_ids':ids}}
   if sn in [2,4,6]:
    i=[2,4,6].index(sn);tid=TEMPLATES[(bn-1+i)%4];tpl=read(root/'resources'/(tid+'.json'));text=({1:'见面先精神点',2:'发型也要在线',3:'清爽带点淡淡清香',4:'出门也要清爽'}[bn] if sn==2 else ('双重酰胺组合' if bn<3 else 'SCA控油蓬松技术') if sn==4 else '戳直播间看看');params=next(c['props']['params'] for c in read(root/'plans'/(rows[0]['key']+'.json'))['draft']['clips'] if c['id']=='upper-'+str(i));seg['effect']={'template_id':tid,'params':{**params,'text':text}}
   if sn in [3,4,6]:seg['sound_effects']={'mode':'ids','props':{'ids':soundids,'category':'字幕与动作点缀'}}
   if sn in [3,5]:seg['transition']=[{'name':'短闪切','type':'hardCutFlash','durationInFrames':4},{'name':'径向轻变焦','type':'radialZoomBlur','durationInFrames':6}]
   if sn==6:
    props=next(c['props'] for c in read(root/'plans'/(rows[0]['key']+'.json'))['draft']['clips'] if c['id']=='cta-arrow');seg['sticker']={'mode':'category','props':[{'category':'戳直播间头像-箭头','position':'center','x':props['x'],'y':props['y'],'scale':props['scale'],'rotation':0,'opacity':.95,'z_index':1,'play_mode':'segment'}]}
  body={k:wanted[k] for k in ['product_id','title','video','config','vo_rewrite_prompt','segments']};planpath=root/'script-plans'/(sid+'.json');backpath=root/'script-readbacks'/(sid+'.json')
  if a.dry_run:save(planpath,{'original':original,'body':body});print(f'B{bn:02d}','script packaging and reviewed source pools planned; TTS/words unchanged',flush=True);continue
  if backpath.exists():print('Parent script already read back',sid);continue
  plan=read(planpath)
  if plan['body']!=body:raise RuntimeError('Script plan changed')
  current=cb.api('GET','/video-scripts/'+sid)
  if all(canonical(current[k])==canonical(body[k]) for k in body):
   save(backpath,current);print(f'B{bn:02d}','exact configuration already persisted; readback saved',flush=True);continue
  if any(canonical(current[k])!=canonical(plan['original'][k]) for k in body):raise RuntimeError('Concurrent parent script change')
  save(root/'script-backups'/(sid+'.json'),current);cb.api('PUT','/video-scripts/'+sid,body);back=cb.api('GET','/video-scripts/'+sid)
  if any(canonical(body[k])!=canonical(back[k]) for k in body):raise RuntimeError('Parent configuration readback mismatch')
  save(backpath,back);print(f'B{bn:02d}','native script 5 styles / 5 fonts / reviewed pools / upper / CTA arrow+SFX READBACK',flush=True)

def finish(a,cfg):
 import datetime,html,os
 root=Path(a.out);rows=read(root/'video-manifest.json');ledger=read(root/'new-visual-ledger.json');final=[]
 for row in rows:
  key=row['key'];rec=read(root/'renders'/(key+'.json'));cut=cb.api('GET','/video-cuts/'+row['cut_id']);qc=read(root/'qc-new'/key/'media-qc.json');plan=read(root/'plans'/(key+'.json'));validate(read(root/'cuts'/(key+'.json'))['draft'],plan)
  if cut.get('output_status')!='success' or cut.get('output_object_key')!=rec['job'].get('object_key') or digest(cut['draft'])!=rec['fingerprint']:raise RuntimeError('Not current exact render '+key)
  if cut.get('ai_review_status')!='success' or cut.get('ai_review_result')!='pass':raise RuntimeError('Native review not pass '+key+str(cut.get('ai_review_reason')))
  if not qc['technical_pass'] or ledger.get(key,{}).get('status')!='pass' or ledger[key]['fingerprint']!=rec['fingerprint']:raise RuntimeError('New technical/actual visual inspection required '+key)
  file=root/'qc-new'/key/'current.mp4'
  if hashlib.sha256(file.read_bytes()).hexdigest()!=qc['sha256']:raise RuntimeError('Media changed '+key)
  final.append({**row,'new_output_key':cut['output_object_key'],'render_job':rec['job_id'],'technical_pass':True,'visual_pass':True,'ai_review':'pass'})
 # Legacy shares read each current cut output dynamically. Reuse all four original share IDs.
 group=cb.api('GET','/video-share-groups/'+read(root/'inventory.json')['group_id']);original=read(root/'original-group.json')
 if [x['id'] for x in group['shares']]!=[x['id'] for x in original['shares']]:raise RuntimeError('Group membership changed')
 for sid in {r['share_id'] for r in rows}:
  current=cb.api('GET','/video-shares/'+sid);expected=[r for r in final if r['share_id']==sid]
  if [v['cut_id'] for v in current['videos']]!=[r['cut_id'] for r in expected]:raise RuntimeError('Share count/order changed')
  for v,r in zip(current['videos'],expected):
   if urllib.parse.unquote(urllib.parse.urlparse(v['video_url']).path).lstrip('/')!=r['new_output_key']:raise RuntimeError('Share contains stale output')
  save(root/'final-shares'/(sid+'.json'),current)
 save(root/'final-group.json',group);save(root/'final-manifest.json',final)
 dest=root/'本地交付/清扬30条';dest.mkdir(parents=True,exist_ok=True)
 for row in final:
  src=root/'qc-new'/row['key']/'current.mp4';target=dest/(row['key']+'.mp4')
  if not target.exists():os.link(src,target)
 url=cfg['clipo']['base_url'].rstrip('/')+'/share/group/'+group['id'];out=root/'修改后验收';out.mkdir(exist_ok=True);cards=[];esc=html.escape
 for bn in range(1,5):
  entries=[r for r in final if r['key'].startswith(f'B{bn:02d}-')];content=''.join('<article><b>'+r['key']+'</b><video controls playsinline preload="none" src="../qc-new/'+r['key']+'/current.mp4"></video><p><a href="../新版检查图/'+r['key']+'.jpg">逐镜检查图</a> · <a href="../qc-original/'+r['key']+'/original.mp4">原片对照</a></p></article>' for r in entries);cards.append('<h2>Brief '+str(bn)+' · '+str(len(entries))+'条</h2><div class="grid">'+content+'</div>')
 examples=[('重复男士/洗后展示','B01-V02',20.07,20.07),('成分改为质地实拍','B01-V02',16.73,16.73),('清爽氛围开场','B03-V01',3,.9),('产品完整正面出场','B03-V01',10.89,7.09)]
 comparison=''
 for i,(label,key,tbefore,tafter) in enumerate(examples):
  for phase,t,f in [('before',tbefore,root/'qc-original'/key/'original.mp4'),('after',tafter,root/'qc-new'/key/'current.mp4')]:subprocess.run(['ffmpeg','-v','error','-y','-ss',str(t),'-i',str(f),'-frames:v','1','-vf','scale=324:-2',str(out/(f'{i}-{phase}.jpg'))],check=True)
  comparison+='<article><h3>'+label+'</h3><div class="pair"><figure><img src="'+str(i)+'-before.jpg"><figcaption>修改前</figcaption></figure><figure><img src="'+str(i)+'-after.jpg"><figcaption>修改后</figcaption></figure></div></article>'
 doc='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>清扬30条 · 修改后验收</title><style>body{max-width:1300px;margin:30px auto;padding:0 24px;font:16px/1.7 system-ui;color:#153b31;background:#f1f6f3}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(225px,1fr));gap:18px}article{background:white;border-radius:12px;padding:15px}video{width:100%;max-height:500px;background:#111}a{color:#117b56}.pair{display:flex;gap:8px}figure{margin:0;flex:1}img{width:100%}</style><h1>清扬30条 · 修改后验收</h1><p>'+datetime.datetime.now().strftime('%Y-%m-%d %H:%M')+'。4组10/15/3/2条，原片保留。30条均通过新版原生审核、黑帧/峰值检查和实际逐镜检查。</p><p><a href="'+esc(url)+'">打开原Clipo合集（已回读新版30条）</a> · <a href="../问题清单/index.html">原片问题与证据</a></p><p>被指出的男士源全部撤下；产品正面、质地及洗后画面对位；5套单行主字幕、4类上方花字轮换；保留原有多音色、多首BGM，重配音乐音量；每条结尾均有原生箭头和短音效。前25条开场原声保留，按用户选择暂不补对白字幕。</p><h2>修改前后对照</h2><div class="grid">'+comparison+'</div>'+''.join(cards)+'</html>'
 if (root/'bgm-reference/activated.json').exists():
  doc=doc.replace('保留原有多音色、多首BGM，重配音乐音量','保留已审配音；按用户飞书快消参考更换为系统已上传的7首BGM并重配混音').replace('30条均通过新版原生审核、黑帧/峰值检查和实际逐镜检查。','30条均通过当前原生审核、单帧黑屏和音频峰值检查；画面逐帧相同的沿用前版实际复核，其余已重看检查图。配乐听感待客户验收。')
 (out/'index.html').write_text(doc);save(root/'delivery-receipt.json',{'time':datetime.datetime.now().isoformat(),'group':url,'videos':30,'counts':[10,15,3,2],'status':'production_qc_pass_client_review_pending','page':str(out/'index.html'),'local':str(dest),'deferred':'前25条开场对白字幕按用户选择暂不补'});print('DELIVERY VERIFIED',url,'30/30',out/'index.html',flush=True)

def native_cta(a,cfg):
 root=Path(a.out);rows=read(root/'video-manifest.json');changes={}
 for row in rows:
  key=row['key'];plan=read(root/'plans'/(key+'.json'));arrow=next(c for c in plan['draft']['clips'] if c['id']=='cta-arrow');changes[key]={'before':{k:arrow['props'][k] for k in ['x','y','scale','rotation']},'after':{'x':0,'y':0,'scale':1,'rotation':0},'fingerprint':digest(plan['draft'])}
 intent=root/'native-cta-plan.json';prepared=root/'native-cta-prepared.json';archive=root/'attempts/native-cta-before'
 if a.dry_run:save(intent,{'changes':changes,'instruction':'直播间在哪里你知道吗哥，你现在戳错了吧','native_product_prompt':'戳直播间头像-箭头分类，默认position:center、scale:1、rotation:0、x:0、y:0','rerender_existing':4,'unrendered_plans':26,'TTS':0,'AIGC':0});print('DRY-RUN restore right-side avatar CTA using original Clipo canvas: 4 existing pilot rerenders; 26 unrendered plans; TTS/AIGC 0');return
 if prepared.exists():print('Native CTA already restored');return
 record=read(intent)
 if record['changes']!=changes:raise RuntimeError('Native CTA plan changed')
 archive.mkdir(parents=True,exist_ok=True)
 for row in rows:
  key=row['key'];path=root/'plans'/(key+'.json');plan=read(path);save(archive/'plans'/(key+'.json'),plan);next(c for c in plan['draft']['clips'] if c['id']=='cta-arrow')['props'].update(x=0,y=0,scale=1,rotation=0);validate(read(root/'cuts'/(key+'.json'))['draft'],plan);save(path,plan)
  for name in [key+'.json',key+'-intent.json']:
   p=root/'renders'/name
   if p.exists():p.rename(archive/name)
  p=root/'qc-new'/key
  if p.exists():p.rename(archive/(key+'-qc'))
 if (root/'new-visual-ledger.json').exists():save(archive/'visual-ledger.json',read(root/'new-visual-ledger.json'));save(root/'new-visual-ledger.json',{})
 for row in read(root/'inventory.json')['shares']:
  sid=row['script_id'];current=cb.api('GET','/video-scripts/'+sid);save(archive/(sid+'-script-before.json'),current);body={k:copy.deepcopy(current[k]) for k in ['product_id','title','video','config','vo_rewrite_prompt','segments']};props=next(s for s in body['segments'] if s['id']=='6')['sticker']['props'][0];props.update(position='center',x=0,y=0,scale=1,rotation=0);cb.api('PUT','/video-scripts/'+sid,body);back=cb.api('GET','/video-scripts/'+sid)
  if any(canonical(body[k])!=canonical(back[k]) for k in body):raise RuntimeError('Native CTA parent readback mismatch')
  save(root/'script-native-cta-readbacks'/(sid+'.json'),back)
 save(prepared,record);print('Native CTA restored in all 30 plans and four parent configs; previous renders/evidence retained; four pilots await rerender',flush=True)

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['plan','apply','status','review','media','accept','diagnose','configure','finish','native_cta']);p.add_argument('--out',required=True);p.add_argument('--config',default='qingyang');p.add_argument('--stage',choices=['pilot','rest','all'],default='pilot');p.add_argument('--dry-run',action='store_true');p.add_argument('--keys');p.add_argument('--note');a=p.parse_args();inv=read(Path(a.out)/'inventory.json')
 if inv.get('group_id')!='6b068349429cee6fdc0865db' or sorted(x['count'] for x in inv['shares'])!=[2,3,10,15]:raise RuntimeError('This apply command is scoped to the authorized 30-cut group only')
 cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';(makeplan if a.cmd=='plan' else globals()[a.cmd])(a,cfg)
if __name__=='__main__':main()
