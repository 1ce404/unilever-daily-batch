#!/usr/bin/env python3
"""[INPUT] User Feishu BGM reference and the currently delivered Qingyang 30 native cuts.
[OUTPUT] Exact uploaded-track mapping and BGM-only revisions; all other tracks preserved.
[POS] No TTS/AIGC, no transition restoration, no shared resource mutation.
"""
import argparse,copy,hashlib,html,json,math,re,subprocess,sys,time,urllib.parse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parent))
import group_rework_apply as g
import clipo_batch as cb
from common import load_config

def norm(t):return re.sub(r'[^a-z0-9\u4e00-\u9fff]','',str(t).lower().removesuffix('.mp3'))
def inventory(a,cfg):
 root=Path(a.out);work=root/'bgm-reference';work.mkdir(exist_ok=True);doc=g.read(work/'feishu-document.json')['data']['document'];content=doc['content'];table=re.search(r'<table>.*?</table>',content,re.S).group();rows=re.findall(r'<tr>(.*?)</tr>',table,re.S);cats=[re.sub('<[^>]*>','',s) for s in re.findall(r'<td>(.*?)</td>',rows[0],re.S)];cells=re.findall(r'<td>(.*?)</td>',rows[1],re.S);refs=[]
 for cat,cell in zip(cats,cells):
  for name,token in re.findall(r'<source name="([^"]+)"[^>]*token="([^"]+)"',cell):refs.append({'category':cat,'name':html.unescape(name),'feishu_token':token})
 g.save(work/'reference-tracks.json',refs);g.save(work/'bgm-catalog.json',cb.api('GET','/resource/bgm/catalog'));library=[];skip=0
 while True:
  data=cb.api('GET',f'/resource/bgm?skip={skip}&limit=100');items=data if isinstance(data,list) else data['items'];library+=items
  if len(items)<100:break
  skip+=100
  if skip>3000:raise RuntimeError('BGM pagination unexpectedly large')
 g.save(work/'native-library.json',library);mapping=[]
 for ref in refs:
  n=norm(ref['name']);matched=[x for x in library if norm(x['name'])==n];related=[] if matched else [x for x in library if n in norm(x['name']) or norm(x['name']) in n]
  mapping.append({**ref,'exact':matched,'related':related})
 g.save(work/'reference-native-map.json',mapping)
 for cat in cats:
  print('REFERENCE',cat,[(x['name'],[(m['id'],m['name']) for m in x['exact']],[(m['id'],m['name']) for m in x['related'][:3]]) for x in mapping if x['category']==cat],flush=True)
 # Identify the actual current music files, rather than inferring from broad script categories.
 paths={urllib.parse.urlparse(x.get('url') or x.get('object_url') or '').path:x for x in library};current=[]
 for row in g.read(root/'video-manifest.json'):
  d=g.read(root/'plans'/(row['key']+'.json'))['draft'];c=next(c for c in d['clips'] if c['trackId']=='audio');p=g.srcpath(c);m=paths.get(p,{})
  current.append({'key':row['key'],'path':p,'name':m.get('name'),'id':m.get('id'),'categories':m.get('categories'),'volume':[z['props']['volume'] for z in d['clips'] if z['trackId']=='audio']})
 g.save(work/'current-music.json',current);print('Current actual music',[(x['key'],x['name'],x['categories'],x['volume']) for x in current],flush=True)

def samples(a,cfg):
 root=Path(a.out);work=root/'bgm-reference';refs=g.read(work/'reference-native-map.json');wanted=[x for x in refs if x['category']=='快节奏电商（快消）'];items=[]
 for ref in wanted:
  if len(ref['exact'])!=1:continue
  m=copy.deepcopy(ref['exact'][0]);m['reference_name']=ref['name'];m['feishu_token']=ref['feishu_token'];items.append(m)
 def one(m):
  out=work/'music';out.mkdir(exist_ok=True);f=out/(m['id']+'.mp3');url=m.get('url') or m.get('object_url')
  if not url:raise RuntimeError('Native music has no URL')
  if not f.exists():cb.fetch_file(url,f)
  info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-of','json',str(f)],text=True));m['file']=str(f);m['actual_duration']=float(info['format']['duration']);m['sha256']=hashlib.sha256(f.read_bytes()).hexdigest();m['actual_size']=f.stat().st_size;g.save(out/(m['id']+'.json'),m);return m
 with ThreadPoolExecutor(max_workers=5) as pool:done=list(pool.map(one,items))
 g.save(work/'verified-fast-tracks.json',done);print('Exact reference uploads physically verified',[(x['id'],x['name'],round(x['actual_duration'],2),x['actual_size']) for x in done],flush=True)

SELECTED=['满分男进行曲（Luxury）','所有人感受（Ready Go）','TAKE ME','柠檬叉','最初的记忆(DJ BGM版)','赵雨凡进行曲（天资）','Pretty Girl Rock']
def music_only(before,after):
 def locked(d):
  d=copy.deepcopy(d);d['clips']=[x for x in d['clips'] if x['trackId']!='audio'];return g.digest(d)
 if locked(before)!=locked(after):raise RuntimeError('Non-BGM source/audio/packaging/timing changed')
 def bgm_layout(d):
  clips=copy.deepcopy([x for x in d['clips'] if x['trackId']=='audio'])
  for c in clips:
   if c['component']!='AudioClip':raise RuntimeError('BGM must remain an audio clip')
   for k in ['src','startFrom','loop','volume','fadeIn','fadeOut']:c['props'].pop(k,None)
  return g.digest(clips)
 if bgm_layout(before)!=bgm_layout(after):raise RuntimeError('BGM timeline or track layout changed')
 if any(x.get('transitionIn') or x.get('transitionOut') for x in after['clips']):raise RuntimeError('Transitions restored')

def validate_mix(plan,root):
 """Gate renders on measured music presence and the actual time-aligned source mix."""
 rows=g.read(Path(root)/'bgm-reference/mix-measurements.json');m=next((x for x in rows if x['key']==plan['key']),None)
 if not m:raise RuntimeError('BGM source mix not measured '+plan['key'])
 body=next(c for c in plan['draft']['clips'] if c['id']=='bgm-body')
 if m.get('draft_fingerprint')!=g.digest(plan['draft']) or abs(body['props']['volume']-m['gain'])>.00001 or m['expected_music_vs_vo_db'] < -8 or m['predicted_source_peak']>=.96:raise RuntimeError('BGM mix stale, too quiet, or clipping '+plan['key'])

def measure_window(file,offset,n):
 p=subprocess.run(['ffmpeg','-hide_banner','-ss',str(offset),'-t',str(n),'-i',str(file),'-af','volumedetect','-f','null','-'],capture_output=True,text=True,check=True);mean=re.search(r'mean_volume: ([-\d.]+) dB',p.stderr);peak=re.search(r'max_volume: ([-\d.]+) dB',p.stderr);return float(mean.group(1)),float(peak.group(1))

def plan(a,cfg):
 import numpy as np
 root=Path(a.out);work=root/'bgm-reference';bank=g.read(work/'verified-fast-tracks.json');music={m['name']:m for m in bank};volevels=g.read(root/'resources/audio-levels.json');windows={};chosen=[music[n] for n in SELECTED]
 # Locate strong-energy source windows; this is a signal aid, not a listening/BPM claim.
 for m in chosen:
  raw=subprocess.check_output(['ffmpeg','-v','error','-i',m['file'],'-ac','1','-ar','8000','-f','f32le','-']);pcm=np.frombuffer(raw,dtype='<f4');bins=np.array([math.sqrt(float(np.mean(pcm[i:i+1600]**2))+1e-12) for i in range(0,len(pcm)-1600,1600)]);n=28;last=min(35,int(m['actual_duration']-n-1));candidates=[]
  for offset in range(0,last+1):
   part=bins[offset*5:(offset+n)*5];score=float(np.mean(part))+.2*float(np.mean(np.maximum(np.diff(part),0)));candidates.append((score,offset))
  offset=max(candidates)[1];mean,peak=measure_window(m['file'],offset,n);windows[m['id']]={'source_in_seconds':offset,'mean_db':mean,'peak_db':peak,'method':'highest average RMS/transient energy among valid 28-second windows within first35sec; not verified musical beats'}
 g.save(work/'selected-source-windows.json',windows);changes=[];rows=g.read(root/'video-manifest.json')
 for vi,row in enumerate(rows):
  key=row['key'];before=cb.api('GET','/video-cuts/'+row['cut_id']);prior=g.read(root/'plans'/(key+'.json'));rec=g.read(root/'renders'/(key+'.json'))
  if g.digest(before['draft'])!=g.digest(prior['draft']) or before.get('output_object_key')!=rec['job']['object_key']:raise RuntimeError('Current cut/output changed '+key)
  name=SELECTED[vi%7];m=music[name];w=windows[m['id']];d=copy.deepcopy(before['draft']);body=next(c for c in d['clips'] if c['trackId']=='voiceover');vo=volevels[g.srcpath(body)];target_gain=10**((vo['mean_db']-5-w['mean_db'])/20);gain=round(max(.025,min(.28,target_gain)),4);intro_gain=round(min(.045,gain*.45),4);offset=w['source_in_seconds']*30
  for c in d['clips']:
   if c['trackId']!='audio':continue
   c['props'].update(src=m['url'],startFrom=offset+c['from'],loop=False,volume=intro_gain if c['id']=='bgm-intro' else gain,fadeIn=0,fadeOut=9 if c['id']=='bgm-body' else 0)
   if c['props']['startFrom']+c['durationInFrames']>math.floor(m['actual_duration']*30)-1:raise RuntimeError('BGM source overrun')
  music_only(before['draft'],d);wanted=copy.deepcopy(prior);wanted['draft']=d;wanted['variant'].update(bgm_mix_checked=False,bgm_id=m['id'],bgm_name=name,bgm_gain=gain,bgm_reference='https://tezign.feishu.cn/wiki/AsVlwIVEpinrzvkGrfbcPZManbd');g.validate(g.read(root/'cuts'/(key+'.json'))['draft'],wanted);changes.append({'key':key,'before':before,'wanted':wanted,'music':{'id':m['id'],'name':name,'source_in_seconds':w['source_in_seconds'],'gain':gain,'intro_gain':intro_gain,'expected_music_vs_vo_mean_db':round(w['mean_db']+20*math.log10(gain)-vo['mean_db'],2)}})
 scripts=[]
 for sid in sorted({r['script_id'] for r in rows}):
  current=cb.api('GET','/video-scripts/'+sid);body={k:copy.deepcopy(current[k]) for k in ['product_id','title','video','config','vo_rewrite_prompt','segments']};body['config']['bgm']={'mode':'ids','volume':.2,'props':{'ids':[m['id'] for m in chosen],'categories':[]}};scripts.append({'id':sid,'before':current,'body':body})
 g.save(work/'revision-plan.json',{'at':time.time(),'instruction':'bgm对吗，这好平淡的bgm；参考飞书热门包装，系统我也上传了的','reference':'https://tezign.feishu.cn/wiki/AsVlwIVEpinrzvkGrfbcPZManbd','cuts':changes,'scripts':scripts,'render_count':30,'TTS':0,'AIGC':0});g.save(work/'batch-music-map.json',[{'key':x['key'],**x['music']} for x in changes]);print('PLANNED BGM-only existing renders 30, exact uploaded reference songs 7, no TTS/AIGC; all non-BGM tracks locked',flush=True)
 for x in changes:print(x['key'],x['music'],flush=True)

def prepare(a,cfg):
 root=Path(a.out);work=root/'bgm-reference';p=g.read(work/'revision-plan.json');archive=work/'before';marker=work/'prepared.json'
 if marker.exists():print('BGM-only revision already prepared');return
 if time.time()-p['at']>3600:raise RuntimeError('BGM plan expired')
 # Check all cuts before any platform mutation.
 def check_current(x):
  current=cb.api('GET','/video-cuts/'+x['before']['id'])
  if g.digest(current['draft'])!=g.digest(x['before']['draft']):raise RuntimeError('Concurrent cut change '+x['key'])
 with ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(check_current,p['cuts']))
 for s in p['scripts']:
  current=cb.api('GET','/video-scripts/'+s['id'])
  if any(g.canonical(current[k])!=g.canonical(s['before'][k]) for k in s['body']):raise RuntimeError('Concurrent script change')
  g.save(archive/'scripts'/(s['id']+'.json'),current);cb.api('PUT','/video-scripts/'+s['id'],s['body']);back=cb.api('GET','/video-scripts/'+s['id'])
  if any(g.canonical(back[k])!=g.canonical(s['body'][k]) for k in s['body']):raise RuntimeError('BGM configuration readback mismatch')
  g.save(work/'script-readbacks'/(s['id']+'.json'),back)
 for x in p['cuts']:
  key=x['key'];music_only(x['before']['draft'],x['wanted']['draft']);g.save(archive/'plans'/(key+'.json'),g.read(root/'plans'/(key+'.json')));g.save(archive/'cuts'/(key+'.json'),x['before']);g.save(root/'plans'/(key+'.json'),x['wanted'])
  for name in [key+'.json',key+'-intent.json']:
   f=root/'renders'/name
   if f.exists():dest=archive/'renders'/name;dest.parent.mkdir(parents=True,exist_ok=True);f.rename(dest)
 g.save(archive/'visual-ledger.json',g.read(root/'new-visual-ledger.json'));g.save(root/'new-visual-ledger.json',{});g.save(marker,{'at':time.time(),'videos':30,'tracks':7,'new_voices':0,'new_AIGC':0});print('4 script BGM pools read back with 7 exact reference IDs; 30 BGM-only drafts staged; previous delivery retained until current QC',flush=True)

def level_fix(a,cfg):
 import numpy as np
 root=Path(a.out);work=root/'bgm-reference';receipt=work/'mix-level-fix-prepared.json';intent=work/'mix-level-fix-plan.json';archive=work/'mix-level-before';bank={m['id']:m for m in g.read(work/'verified-fast-tracks.json')};volevels=g.read(root/'resources/audio-levels.json');windows=g.read(work/'selected-source-windows.json');sfx={urllib.parse.urlparse(x['url']).path:x['file'] for x in g.read(root/'resources/verified-sfx.json')};cache={};sr=48000;spf=1600
 def pcm(file):
  if file not in cache:cache[file]=np.frombuffer(subprocess.check_output(['ffmpeg','-v','error','-i',str(file),'-ac','2','-ar',str(sr),'-f','f32le','-']),dtype='<f4').reshape(-1,2)
  return cache[file]
 changes=[];measurements=[]
 for row in g.read(root/'video-manifest.json'):
  key=row['key'];before=g.read(root/'plans'/(key+'.json'));wanted=copy.deepcopy(before);d=wanted['draft'];v=next(c for c in d['clips'] if c['trackId']=='voiceover');at=v['from'];total=d['composition']['durationInFrames'];count=(total-at)*spf;base=np.zeros((count,2),dtype=np.float64)
  for c in d['clips']:
   if c['trackId'] not in ['voiceover','sound_effects']:continue
   path=g.srcpath(c);file=volevels[path]['file'] if c['trackId']=='voiceover' else sfx[path];src=pcm(file);srcstart=c['props'].get('startFrom',0)*spf;duration=c['durationInFrames']*spf;sample=src[srcstart:srcstart+duration].astype(np.float64)*c['props'].get('volume',1);fi=c['props'].get('fadeIn',0)*spf;fo=c['props'].get('fadeOut',0)*spf
   if fi:sample[:min(fi,len(sample))]*=np.linspace(0,1,min(fi,len(sample)))[:,None]
   if fo:sample[-min(fo,len(sample)):]*=np.linspace(1,0,min(fo,len(sample)))[:,None]
   dest=(c['from']-at)*spf;skip=max(0,-dest);dest=max(0,dest);n=min(len(sample)-skip,count-dest)
   if n>0:base[dest:dest+n]+=sample[skip:skip+n]
  mid=before['variant']['bgm_id'];m=bank[mid];w=windows[mid];offset=int(w['source_in_seconds']*sr+at*spf);music=pcm(m['file'])[offset:offset+count].astype(np.float64)
  if len(music)!=count:raise RuntimeError('Music window incomplete')
  music[-9*spf:]*=np.linspace(1,0,9*spf)[:,None];target=10**((volevels[g.srcpath(v)]['mean_db']-5-w['mean_db'])/20);positive=music>1e-5;negative=music< -1e-5;cap=min(float(np.min((.96-base[positive])/music[positive])) if np.any(positive) else 1,float(np.min((.96+base[negative])/(-music[negative]))) if np.any(negative) else 1);gain=round(min(.28,target,max(.025,cap*.97)),4);relative=w['mean_db']+20*math.log10(gain)-volevels[g.srcpath(v)]['mean_db']
  if relative < -8:raise RuntimeError('Music would be too far below voice; need a different source interval '+key+str(relative))
  for c in d['clips']:
   if c['trackId']=='audio':c['props']['volume']=gain if c['id']=='bgm-body' or at<=66 else round(min(.045,gain*.45),4)
  measurements.append({'key':key,'draft_fingerprint':g.digest(d),'gain':gain,'expected_music_vs_vo_db':round(relative,2),'predicted_source_peak':round(float(np.max(np.abs(base+music*gain))),4)})
  if g.digest(before['draft'])==g.digest(d):continue
  music_only(before['draft'],d);g.validate(g.read(root/'cuts'/(key+'.json'))['draft'],wanted);wanted['variant']['bgm_gain']=gain;wanted['variant']['mix_revision']='sample-accurate-source-mix';wanted['variant']['bgm_mix_checked']=True;changes.append({'key':key,'before':before,'wanted':wanted,'gain':gain,'expected_music_vs_vo_db':round(relative,2),'predicted_source_peak':round(float(np.max(np.abs(base+music*gain))),4)})
 g.save(work/'mix-measurements.json',measurements)
 if a.dry_run:g.save(intent,{'at':time.time(),'cuts':changes,'reason':'Use the actual time-aligned source waveform peaks; do not over-duck a whole song by adding unrelated peak maxima.'});g.save(work/'mix-level-fix-keys.json',[x['key'] for x in changes]);print('DRY-RUN exact-waveform BGM gain fixes',len(changes));print([(x['key'],x['gain'],x['expected_music_vs_vo_db'],x['predicted_source_peak']) for x in changes]);return
 if receipt.exists():print('Exact-waveform mix fix already staged');return
 old=g.read(intent)
 if old['cuts']!=changes or time.time()-old['at']>3600:raise RuntimeError('Mix correction plan changed/expired')
 for x in changes:
  key=x['key'];current=cb.api('GET','/video-cuts/'+x['before']['cut_id'])
  if g.digest(current['draft'])!=g.digest(x['before']['draft']):raise RuntimeError('Concurrent mix change')
  g.save(archive/'plans'/(key+'.json'),x['before']);g.save(root/'plans'/(key+'.json'),x['wanted'])
  for name in [key+'.json',key+'-intent.json']:
   f=root/'renders'/name
   if f.exists():dest=archive/'renders'/name;dest.parent.mkdir(parents=True,exist_ok=True);f.rename(dest)
 g.save(receipt,{'at':time.time(),'keys':[x['key'] for x in changes]});print('BGM gain corrections staged',len(changes))

def media(a,cfg):
 """Check the new audio and require identical decoded video before reusing visual QA."""
 root=Path(a.out);work=root/'bgm-reference';oldledger=g.read(work/'before/visual-ledger.json');rows=g.read(root/'video-manifest.json')
 def one(row):
  key=row['key'];rec=g.read(root/'renders'/(key+'.json'));plan=g.read(root/'plans'/(key+'.json'));before=g.read(work/'before/plans'/(key+'.json'));music_only(before['draft'],plan['draft'])
  validate_mix(plan,root)
  if rec['job']['status']!='success':raise RuntimeError('Render pending '+key)
  out=work/'qc'/key;out.mkdir(parents=True,exist_ok=True);f=out/'current.mp4';record=out/'media-qc.json'
  if record.exists():
   data=g.read(record)
   if data['fingerprint']!=rec['fingerprint']:raise RuntimeError('Stale BGM QC '+key)
   return data
  if not f.exists():cb.fetch_file(rec['job']['download_url'],f)
  def video_hash(file):return subprocess.check_output(['ffmpeg','-v','error','-threads','2','-i',str(file),'-map','0:v:0','-an','-c:v','rawvideo','-pix_fmt','yuv420p','-threads','2','-f','hash','-hash','sha256','-'],text=True).strip()
  old=root/'qc-new'/key/'current.mp4';h0=video_hash(old);h1=video_hash(f)
  identical=h0==h1
  log=subprocess.run(['ffmpeg','-hide_banner','-threads','2','-i',str(f),'-vf','scale=216:384,crop=168:330:24:18,blackframe=amount=98:threshold=32','-af','astats=metadata=1:reset=0','-f','null','-'],capture_output=True,text=True,check=True).stderr
  blacks=[{'frame':int(n),'pblack':float(p),'time':float(t)} for n,p,t in re.findall(r'frame:(\d+) pblack:([\d.]+).*? t:([\d.]+)',log)];peaks=re.findall(r'Peak level dB: ([-\d.inf]+)',log);peak=max((float(v) for v in peaks),default=-math.inf)
  oldqc=g.read(root/'qc-new'/key/'media-qc.json');data={'video':str(f),'fingerprint':rec['fingerprint'],'render_job':rec['job_id'],'sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'black_intervals':blacks,'peak_dbfs':peak if math.isfinite(peak) else None,'technical_pass':not blacks and math.isfinite(peak) and -60<peak<0,'visual_review':'decoded_video_identical_to_previously_inspected_output' if identical else 'pending_fresh_visual_inspection','decoded_video_sha256':h1,'baseline_decoded_video_sha256':h0,'frames':oldqc['frames'],'board':oldqc['board'],'scope':'Only BGM source/window/gain changed. Reuse baseline visual QA only for identical decoded frames; otherwise fresh actual frame review required. Subjective music approval remains with client.'}
  if oldledger.get(key,{}).get('status')!='pass':raise RuntimeError('No accepted baseline visual inspection '+key)
  if not identical:
   from PIL import Image,ImageDraw
   d=plan['draft'];frames=sorted({c['from']+max(1,c['durationInFrames']//2) for c in d['clips'] if c['trackId']=='main' or c['component']=='RemotionClip'}|{max(0,c['from']+delta) for c in d['clips'] if c['trackId']=='main' and c['from'] for delta in [-1,0,1]});expr='+'.join('eq(n,'+str(n)+')' for n in frames)
   for label,file in [('old',old),('new',f)]:subprocess.run(['ffmpeg','-v','error','-threads','2','-i',str(file),'-vf',"select='"+expr+"',scale=135:-2",'-fps_mode','vfr','-q:v','2',str(out/(label+'-%03d.jpg'))],check=True)
   board=Image.new('RGB',(810,math.ceil(len(frames)/3)*268+28),'#eee');draw=ImageDraw.Draw(board);draw.text((3,4),key+' BGM revision / old | new / fresh frame inspection',fill='black');data['frames']=[]
   for i,n in enumerate(frames):
    for j,label in enumerate(['old','new']):
     file=out/(label+'-%03d.jpg'%(i+1));pic=Image.open(file).convert('RGB');pic.thumbnail((135,240));x=(i%3)*270+j*135;y=(i//3)*268+28;board.paste(pic,(x,y));draw.text((x+2,y+240),label+' f'+str(n),fill='black')
     if label=='new':data['frames'].append({'frame':n,'time':n/30,'file':str(file)})
   bf=work/'boards'/(key+'.jpg');bf.parent.mkdir(exist_ok=True);board.save(bf,quality=90);data['board']=str(bf)
  g.save(record,data);print(key,'identical video',identical,'black',len(blacks),'audio peak',peak,flush=True);return data
 with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(one,rows))
 if any(not x['technical_pass'] for x in results):raise RuntimeError('Current BGM output technical failure')
 g.save(work/'media-checked.json',{'at':time.time(),'count':len(results),'identical_count':sum(x['decoded_video_sha256']==x['baseline_decoded_video_sha256'] for x in results),'fresh_review_required':[r['key'] for r,x in zip(rows,results) if x['decoded_video_sha256']!=x['baseline_decoded_video_sha256']],'black_frames':0,'clipped_audio':0})

def accept(a,cfg):
 root=Path(a.out);work=root/'bgm-reference'
 if not a.keys or not a.note:raise RuntimeError('Explicit actually inspected keys and note required')
 ledger=g.read(work/'visual-ledger.json') if (work/'visual-ledger.json').exists() else {}
 for key in a.keys.split(','):
  qc=g.read(work/'qc'/key/'media-qc.json');rec=g.read(root/'renders'/(key+'.json'))
  if not qc['technical_pass'] or rec['fingerprint']!=qc['fingerprint']:raise RuntimeError('Stale current media '+key)
  ledger[key]={'status':'pass','fingerprint':rec['fingerprint'],'board':qc['board'],'reviewer':'Agent','at':time.time(),'note':a.note}
 g.save(work/'visual-ledger.json',ledger)

def activate(a,cfg):
 import os
 root=Path(a.out);work=root/'bgm-reference';archive=work/'before';rows=g.read(root/'video-manifest.json');marker=work/'activated.json'
 if marker.exists():print('BGM QC already activated');return
 ledger={};mapping=[]
 for row in rows:
  key=row['key'];rec=g.read(root/'renders'/(key+'.json'));qc=g.read(work/'qc'/key/'media-qc.json');plan=g.read(root/'plans'/(key+'.json'))
  fresh=g.read(work/'visual-ledger.json') if (work/'visual-ledger.json').exists() else {}
  if not qc['technical_pass'] or qc['fingerprint']!=rec['fingerprint'] or (qc['decoded_video_sha256']!=qc['baseline_decoded_video_sha256'] and fresh.get(key,{}).get('fingerprint')!=rec['fingerprint']):raise RuntimeError('New BGM QC incomplete '+key)
  ledger[key]={'status':'pass','fingerprint':rec['fingerprint'],'board':qc['board'],'at':time.time(),'reviewer':'Agent','note':'Decoded video frame hash identical to previously actually inspected no-transition output. This revision checks exact uploaded BGM, source windows, gain and clipping; no claim of subjective listening approval.'}
  if qc['decoded_video_sha256']!=qc['baseline_decoded_video_sha256']:ledger[key]=fresh[key]
  clips=[{'id':c['id'],'gain':c['props']['volume'],'source_in_frames':c['props']['startFrom'],'duration':c['durationInFrames']} for c in plan['draft']['clips'] if c['trackId']=='audio'];mapping.append({'key':key,'id':plan['variant']['bgm_id'],'name':plan['variant']['bgm_name'],'clips':clips})
 os.replace(root/'qc-new',archive/'full-qc');os.replace(work/'qc',root/'qc-new')
 target=root/'本地交付/清扬30条'
 if target.exists():os.replace(target,archive/'local-delivery')
 # Prior visual boards are kept at their stable paths. Copy their exact inspection frame evidence into the new QC folders.
 import shutil
 for row in rows:
  key=row['key'];p=root/'qc-new'/key/'media-qc.json';data=g.read(p);data['video']=str(p.parent/'current.mp4');data['scope']='BGM-only revision. Identical decoded video uses previously actual visual inspection; nonidentical outputs were actually reviewed on new old/new frame boards. Subjective listening approval remains with client.';frames=[]
  for item in data['frames']:
   dest=p.parent/Path(item['file']).name
   if not dest.exists():shutil.copy2(archive/'full-qc'/key/dest.name,dest)
   frames.append(dict(item,file=str(dest)))
  data['frames']=frames;g.save(p,data)
 for key,entry in ledger.items():
  if key in fresh:
   board=Path(entry['board']);dest=root/'新版检查图'/(key+'.jpg');backup=archive/'boards'/dest.name;backup.parent.mkdir(exist_ok=True);shutil.copy2(dest,backup);shutil.copy2(board,dest);entry['board']=str(dest);qcfile=root/'qc-new'/key/'media-qc.json';qcdata=g.read(qcfile);qcdata['board']=str(dest);g.save(qcfile,qcdata)
 g.save(root/'new-visual-ledger.json',ledger);g.save(work/'batch-music-map.json',mapping);g.save(marker,{'at':time.time(),'videos':30,'tracks':len({x['id'] for x in mapping}),'visual_frames_identical_or_freshly_inspected':True});print('30 current BGM renders activated; exact group/share readback still required',flush=True)

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['inventory','samples','plan','prepare','level_fix','media','accept','activate']);p.add_argument('--out',required=True);p.add_argument('--dry-run',action='store_true');p.add_argument('--keys');p.add_argument('--note');a=p.parse_args();root=Path(a.out)
 if g.read(root/'inventory.json')['group_id']!='6b068349429cee6fdc0865db':raise RuntimeError('Wrong authorized group')
 cfg=load_config('qingyang');cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';globals()[a.cmd](a,cfg)
if __name__=='__main__':main()
