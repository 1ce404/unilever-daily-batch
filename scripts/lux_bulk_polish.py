#!/usr/bin/env python3
"""[INPUT] Approved LUX demos, disjoint checked material pools and bulk authorization.
[OUTPUT] One continuous native 1.4 TTS per new video, approved decoration, no repeated main source.
[POS] Task-scoped pre-render callback; paid calls limited by explicit dry-run budget; no shared prompt edits.
"""
import argparse,copy,hashlib,json,math,re,subprocess,sys,time,urllib.parse,urllib.request
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import load_config
from lux_creative_rework import norm
VOICES=['zh_female_shuangkuaisisi_uranus_bigtts','ICL_uranus_zh_female_kailangtingting_tob','zh_female_zhishuaiyingzi_uranus_bigtts','ICL_uranus_zh_female_tiexinguimi_tob']
BIDS=['1125-L01','1125-L02','1125-L03','1125-L04','1125-L05']
def save(p,d):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,indent=2));return p

def budget(a,cfg):
 data={'briefs':[x for x in BIDS if x!='1125-L01'],'new_videos':36,'max_continuous_tts_calls':36,'speed_ratio':1.4,'voices':VOICES,'note':'用户授权brief2–5量产，demo计入每组10条；brief1不量产。'}
 p=Path(a.run)/'revisions/切片去重/bulk-voice-budget.json'
 if a.dry_run:save(p,{'at':time.time(),**data});print(json.dumps(data,ensure_ascii=False));return
 raise RuntimeError('budget only supports --dry-run')

def subtitle_chunks(s):
 parts=[x for x in re.split(r'[，,。！？!?；;、：:\n]+',s) if x.strip()];out=[]
 for part in parts:
  part=part.strip()
  while len(part)>12:
   k=12
   if len(part)-k==1:k-=1
   # Preserve a full product name on one cue.
   for name in ['力士精油香氛洗发水','力士护发精油喷雾','4D玻尿酸','鲜花精油']:
    p=part.find(name)
    if 0<=p<k<p+len(name):k=p if p>=2 else p+len(name)
   out.append(part[:k]);part=part[k:]
  if part:out.append(part)
 return out

def prepare(cb,st,b,v,key,cut,d):
 cfg=cb.CFG;root=st.path.parent.parent;out=root/'revisions/切片去重';work=out/'bulk-native'/key;work.mkdir(parents=True,exist_ok=True)
 if b['brief_id'] not in BIDS or key.endswith('-V01'):return d
 receipt=json.loads((out/('bulk-voice-budget-L01.json' if b['brief_id']=='1125-L01' else 'bulk-voice-budget.json')).read_text())
 if b['brief_id'] not in receipt['briefs']:raise RuntimeError('Missing matching brief voice budget')
 n=int(key.rsplit('-V',1)[1]);bid=b['brief_id'];bn=int(bid[-2:]);rows=copy.deepcopy(cut['random_result']['vo_rewrite'])
 for row in rows:row['content']=re.sub(r'\[#[^\]]*\]','',row['content']).strip()
 spoken=''.join(row['content'] for row in rows);voice=(['zh_female_zhixingnv_uranus_bigtts']+VOICES)[(n-2)%5] if bn==1 else VOICES[(n-2+bn-2)%len(VOICES)];speech_rate=1.5 if bn==1 else 1.4
 intents=json.loads((root/'revisions/声音节奏与人物重选/voice-intent.json').read_text());instruction=intents[bid]['body']['text'].split(']\n',1)[0]+']\n'
 body={'text':instruction+spoken,'voice':{'platform':'volcengine','voice_type':voice,'resource_id':'seed-tts-2.0','speed_ratio':speech_rate},'force':False}
 tpath=work/'tts.json';ipath=work/'tts-intent.json'
 if tpath.exists():
  if json.loads(ipath.read_text())!=body:raise RuntimeError('TTS cached input differs')
  tts=json.loads(tpath.read_text())
 else:
  if time.time()-receipt['at']>7200:raise RuntimeError('Bulk TTS budget expired before new request')
  if ipath.exists():raise RuntimeError('Unresolved TTS intent; reconcile before retrying')
  count=sum(1 for p in (out/'bulk-native').glob('*/tts-intent.json') if p.parent.name.rsplit('-V',1)[0] in receipt['briefs'])
  if count>=receipt['max_continuous_tts_calls']:raise RuntimeError('Continuous TTS count exceeds budget')
  save(ipath,body);tts=cb.api('POST','/tts',body,tries=1);save(tpath,tts)
 if norm(tts['synthesized_text']).casefold()!=norm(spoken).casefold():raise RuntimeError('TTS speech text differs from rewrite')
 upload=work/'voice-upload.json'
 if upload.exists():asset=json.loads(upload.read_text())
 else:
  mp3=work/'voice.mp3';wav=work/'voice.wav'
  if not mp3.exists():cb.fetch_file(tts['audio_url'],mp3)
  pcm=subprocess.run(['ffmpeg','-v','error','-i',str(mp3),'-f','f32le','-ac','1','-'],capture_output=True,check=True).stdout;peak=float(np.max(np.abs(np.frombuffer(pcm,dtype='<f4'))));gain=min(5.5 if voice==VOICES[2] else 2.5,20*math.log10(.88/max(peak,.001)))
  subprocess.run(['ffmpeg','-v','error','-y','-i',str(mp3),'-af',f'volume={gain}dB','-c:a','pcm_s16le',str(wav)],check=True)
  tok=cb.api('POST','/storage/upload-tokens',{'filename':key+'-continuous.wav','content_type':'audio/wav','content_length':wav.stat().st_size,'purpose':'material'})
  req=urllib.request.Request(tok['upload_url'],data=wav.read_bytes(),method='PUT',headers=dict(tok.get('headers') or {},**{'Content-Type':'audio/wav'}));urllib.request.urlopen(req,timeout=120).read()
  asset={'src':'https://'+urllib.parse.urlparse(tts['audio_url']).netloc+'/'+tok['object_key'],'gain_db':gain,'source_peak':peak};save(upload,asset)
 demo=json.loads((out/(bid+'-V01-before.json')).read_text())['cut']['draft'];fps=d['composition']['fps'];total=math.ceil(tts['audio']['duration_ms']*fps/1000)
 tokens=[];pos=0
 for z in tts['timeline']['items']:
  text=norm(z['text'])
  if text:tokens.append({**z,'a':pos,'b':pos+len(text)});pos+=len(text)
 if ''.join(norm(z['text']) for z in tokens).casefold()!=norm(spoken).casefold():raise RuntimeError('Word timeline differs from spoken text')
 style=copy.deepcopy(next(x['props'] for x in demo['clips'] if x['trackId']=='overlay'));style.pop('text',None);style['text_color']='#f9f3c4';style['font_size']=36
 cues=[];bounds=[];pos=0;last=0
 for row in rows:
  hits=[z for z in tokens if z['b']>pos and z['a']<pos+len(norm(row['content']))];bounds.append(math.floor(hits[0]['start_ms']*fps/1000))
  for j,text in enumerate(subtitle_chunks(row['content'])):
   end=pos+len(norm(text));hits=[z for z in tokens if z['b']>pos and z['a']<end];start=max(last,math.floor(hits[0]['start_ms']*fps/1000));finish=math.floor(hits[-1]['end_ms']*fps/1000);pos=end
   if finish<=start:raise RuntimeError('Invalid word cue')
   props={**style,'text':text};hi=cfg['native_caption_highlights']
   if any(w in text for w in hi['product_keywords']):props['text_color']=hi['product_color']
   elif any(w in text for w in hi['selling_keywords']):props['text_color']=hi['selling_color']
   cue=copy.deepcopy(next(x for x in demo['clips'] if x['trackId']=='overlay'));cue.update(id='s'+row['id']+'-'+str(j),**{'from':start,'durationInFrames':finish-start,'motion':None,'transitionIn':None,'transitionOut':None,'props':props});cues.append(cue);last=finish
 bounds[0]=0;bounds.append(total)
 pools=json.loads((out/'expanded-pools.json').read_text())[bid]['segments'];windows=json.loads((out/'pool-windows.json').read_text());used=set();used_materials=set();main=[];evidence=[]
 original=next(x for x in demo['clips'] if x['trackId']=='main');demo_opener=urllib.parse.urlparse(min([x for x in demo['clips'] if x['trackId']=='main'],key=lambda x:x['from'])['props']['src']).path
 for i,row in enumerate(rows):
  sid=row['id'];start,end=bounds[i:i+2];length=end-start;count=1 if bn==1 and i==0 else max(1,math.ceil(length/48));pool=pools[sid]['candidates']
  if i==0 and bn!=1:pool=[x for x in pool if urllib.parse.urlparse(x['object_url']).path!=demo_opener]
  offset=(n-1 if i==0 and bn==1 else n-2 if i==0 else (n-2)*3+i)%len(pool);ordered=pool[offset:]+pool[:offset]
  cursor=start
  for j in range(count):
   frames=length//count+(j<length%count);selected=None
   for relaxed in [False,True]:
    for meta in ordered:
     path=urllib.parse.urlparse(meta['object_url']).path;w=windows[path]
     if path in used or (not relaxed and meta['material_id'] in used_materials) or w['safe_end']-w['safe_start']<frames:continue
     selected=(meta,path,w);break
    if selected:break
   if not selected:raise RuntimeError('Insufficient non-repeating safe windows '+key+' segment'+sid)
   meta,path,w=selected;used.add(path);used_materials.add(meta['material_id']);x=copy.deepcopy(original);x.update(id=sid+'-'+str(j),**{'from':cursor,'durationInFrames':frames,'motion':None,'transitionIn':None,'transitionOut':None});x['props'].update(src=meta['object_url'],startFrom=w['safe_start'],playbackRate=1,volume=0,muted=True,scale=1.0,x=0,y=0,alt=meta.get('description',''));x['props'].pop('loop',None);main.append(x);cursor+=frames;evidence.append({'clip':x['id'],'segment_id':meta['id'],'source':path,'safe_start':w['safe_start'],'safe_end':w['safe_end'],'frames':frames})
 if bn==1:
  oldbounds=[x['from'] for x in sorted([x for x in demo['clips'] if x['trackId']=='voiceover'],key=lambda x:x['from'])]+[demo['composition']['durationInFrames']]
 else:
  oldtts=json.loads((root/'revisions/声音节奏与人物重选'/(bid+'-tts.json')).read_text());oldrows=intents[bid]['segments'];oldbounds=[];pos=0;oldtokens=[]
  for z in oldtts['timeline']['items']:
   t=norm(z['text'])
   if t:oldtokens.append({**z,'a':pos,'b':pos+len(t)});pos+=len(t)
  pos=0
  for row in oldrows:
   hits=[z for z in oldtokens if z['b']>pos and z['a']<pos+len(norm(row['content']))];oldbounds.append(math.floor(hits[0]['start_ms']*fps/1000));pos+=len(norm(row['content']))
  oldbounds[0]=0;oldbounds.append(demo['composition']['durationInFrames'])
 def tf(frame):
  k=min(len(rows)-1,max(i for i,z in enumerate(oldbounds[:-1]) if z<=frame));return min(total-1,bounds[k]+round((frame-oldbounds[k])*(bounds[k+1]-bounds[k])/max(1,oldbounds[k+1]-oldbounds[k])))
 extras=[]
 for old in demo['clips']:
  if old['trackId'] in ['main','overlay','voiceover','audio','watermark'] or old['id']=='cover':continue
  x=copy.deepcopy(old);x['from']=tf(x['from']);x['durationInFrames']=min(x['durationInFrames'],total-x['from'])
  if old['id'].startswith('upper-'):
   k=int(old['id'].split('-')[1])-1;x['from']=bounds[k];x['durationInFrames']=min(x['durationInFrames'],bounds[k+1]-bounds[k])
  if x['durationInFrames']>0:extras.append(x)
 voiceclip=copy.deepcopy(next(x for x in demo['clips'] if x['trackId']=='voiceover'));voiceclip.update(id='voice-continuous',**{'from':0,'durationInFrames':total});voiceclip['props'].update(src=asset['src'],startFrom=0,playbackRate=1,volume=1)
 music=copy.deepcopy(next(x for x in demo['clips'] if x['trackId']=='audio'));music.update(**{'from':0,'durationInFrames':total});music['props']['volume']=b['mix']['bgm_volume'];music['props']['startFrom']+=((n-2)%3)*fps;music['props']['loop']=True
 d['composition']['durationInFrames']=total;d['clips']=main+cues+[voiceclip,music]+extras;d['tracks']=copy.deepcopy(demo['tracks']);v.update(voice=voice,voice_cmd=False,duration_s=round(total/fps,2),native_subtitle_style='6ab0980646ba4a9c28773861',bulk_polish=True,hook_clip=evidence[0]['segment_id']);v.pop('bad_voice',None);cut['random_result']['tts_speaker_id']=voice
 from lux_batch_variants import apply_variant
 d,variant=apply_variant(root,bid,key,d,cfg);v.update(native_variant=variant,native_subtitle_style=variant['subtitle_style_id'])
 save(work/'prepared.json',{'draft':d,'native_voice':voice,'speed_ratio':speech_rate,'variant':variant,'segment_frames':bounds,'source_windows':evidence,'tts_synthesis_id':tts.get('synthesis_id')});return d

def selfcheck(a,cfg):
 import shutil
 from types import SimpleNamespace
 root=Path(a.run);out=root/'revisions/切片去重';test_bids=[x for x in BIDS if x!='1125-L01'];fixture=out/'preflight-fixture';(fixture/'clipo').mkdir(parents=True,exist_ok=True)
 for rel in ['revisions/切片去重/expanded-pools.json','revisions/切片去重/pool-windows.json','revisions/声音节奏与人物重选/voice-intent.json']+[f'revisions/切片去重/{bid}-V01-before.json' for bid in test_bids]+[f'revisions/声音节奏与人物重选/{bid}-tts.json' for bid in test_bids]+['clipo/resources/subtitle-styles.json']+[str(p.relative_to(root)) for p in (root/'revisions/声音节奏与人物重选/music').glob('*.json')]:
  dest=fixture/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/rel,dest)
 save(fixture/'revisions/切片去重/bulk-voice-budget.json',{'at':time.time(),'briefs':test_bids,'new_videos':36,'max_continuous_tts_calls':36})
 def forbidden(*args,**kwargs):raise RuntimeError('Self-check must never call Clipo')
 cb=SimpleNamespace(CFG=cfg,api=forbidden,fetch_file=forbidden);st=SimpleNamespace(path=fixture/'clipo/state.json');intents=json.loads((fixture/'revisions/声音节奏与人物重选/voice-intent.json').read_text());result=[]
 for bid in test_bids:
  b=json.loads((root/'briefs'/(bid+'.json')).read_text());base=json.loads((fixture/'revisions/切片去重'/(bid+'-V01-before.json')).read_text())['cut'];base['random_result']['vo_rewrite']=intents[bid]['segments'];tts=json.loads((fixture/'revisions/声音节奏与人物重选'/(bid+'-tts.json')).read_text());openers=[]
  for n in range(2,11):
   key=bid+f'-V{n:02d}';work=fixture/'revisions/切片去重/bulk-native'/key;voice=VOICES[(n-2+int(bid[-2:])-2)%4];spoken=''.join(x['content'] for x in intents[bid]['segments']);instruction=intents[bid]['body']['text'].split(']\n',1)[0]+']\n';body={'text':instruction+spoken,'voice':{'platform':'volcengine','voice_type':voice,'resource_id':'seed-tts-2.0','speed_ratio':1.4},'force':False}
   save(work/'tts-intent.json',body);save(work/'tts.json',tts);save(work/'voice-upload.json',{'src':'https://selfcheck.invalid/voice.wav'});v={};d=prepare(cb,st,b,v,key,copy.deepcopy(base),copy.deepcopy(base['draft']));main=sorted([x for x in d['clips'] if x['trackId']=='main'],key=lambda x:x['from']);paths=[urllib.parse.urlparse(x['props']['src']).path for x in main]
   if len(paths)!=len(set(paths)):raise RuntimeError('Within-video duplicate')
   cursor=0
   for x in main:
    if x['from']!=cursor or x['durationInFrames']<20:raise RuntimeError('Gap or flash cut')
    cursor+=x['durationInFrames']
   if cursor!=d['composition']['durationInFrames']:raise RuntimeError('Main timeline incomplete')
   if any('\n' in x['props']['text'] or len(x['props']['text'])>12 or x['props']['font_size']!=36 for x in d['clips'] if x['trackId']=='overlay'):raise RuntimeError('Single-row subtitle check failed')
   if len([x for x in d['clips'] if x['id'].startswith('upper-')])!=3:raise RuntimeError('Upper titles missing')
   openers.append(paths[0])
  if len(set(openers))!=9:raise RuntimeError('Bulk opener repeats '+bid)
  result.append({'brief':bid,'simulated_videos':9,'unique_openers':9,'repeat_main_sources':0,'continuous_voice':True,'native_caption_size':36,'upper_titles_per_video':3})
 save(out/'bulk-selfcheck.json',result);print(json.dumps(result,ensure_ascii=False))

def release_layout(cb,st,b,v,key,cut):
 if b['brief_id'] not in BIDS or not b.get('demo_approval') or v.get('ai_review')!='reject':return False
 why=v.get('ai_reason') or ''
 if not ('字幕' in why and '两行' in why) or any(w in why for w in ['黑屏','抖动','露脐','暴露','违禁','广告法','产品错误','产品不符','非力士','模糊','马赛克','其他问题']):return False
 from qc import cut_checks
 issues=cut_checks(b,cut['draft'],v.get('voice'));cues=[x for x in cut['draft']['clips'] if x['trackId']=='overlay'];upper=[x for x in cut['draft']['clips'] if x['id'].startswith('upper-')]
 if issues or len(upper)!=3 or not cues:return False
 styles=[{k:z for k,z in x['props'].items() if k not in ['text','text_color','font_url']} for x in cues]
 if not all(x==styles[0] for x in styles) or any('\n' in x['props']['text'] or x['props'].get('font_size')!=36 for x in cues):return False
 note='用户明确要求统一原生单行字幕、配置独立上方花字；本轮明确“除了brief1别的都可以批量了”。仅对该已确认版式的上下文争议放行，保留AI原驳回。'
 record={'video':key,'cut_id':v['cut_id'],'output_object_key':v.get('object_key'),'ai_review':'reject','ai_reason':why,'native_primary_caption_single_row':True,'independent_upper_titles':3,'mechanical_qc':issues,'user_approval':b['demo_approval'],'scope':'仅已确认版式；不是AI通过，不豁免其他内容问题'}
 save(st.path.parent.parent/'revisions/切片去重/layout-reviews'/(key+'.json'),record);v.update(review_waived=note,qc_issues=[],content_review_status='client_approved_layout',layout_review_note=record);v.pop('user_review_pending',None);return True

def release_current(a,cfg):
 import clipo_batch as cb
 cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';st=cb.State(a.run);briefs={b['brief_id']:b for b in cb.load_briefs(a.run)}
 for key,v in sorted(st.videos.items()):
  if v['brief_id'] not in BIDS or v.get('ai_review')!='reject':continue
  cut=cb.api('GET',f"/video-cuts/{v['cut_id']}");ok=release_layout(cb,st,briefs[v['brief_id']],v,key,cut);print(key,'approved-layout conflict only' if ok else 'held: '+str(v.get('ai_reason')),flush=True)
 st.save()

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['budget','selfcheck','release-layout']);p.add_argument('--run',required=True);p.add_argument('--config',required=True);p.add_argument('--dry-run',action='store_true');a=p.parse_args();{'budget':budget,'selfcheck':selfcheck,'release-layout':release_current}[a.cmd](a,load_config(a.config))
if __name__=='__main__':main()
