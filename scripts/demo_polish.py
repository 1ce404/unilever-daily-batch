#!/usr/bin/env python3
"""[INPUT] Existing Clipo cuts and user-authorized demo revisions.
[OUTPUT] One continuous TTS, native-timed captions, restrained sticker accents and same-cut rerenders.
[POS] LUX demo revision only; never touches brief1 or shared product prompts.
"""
import argparse,copy,hashlib,json,math,re,sys,subprocess
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import load_config

def save(p,d):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,indent=2));return p

def tts(args,cfg):
 st=cb.State(args.run);v=st.videos['1125-L02-V01'];c=cb.api('GET',f"/video-cuts/{v['cut_id']}")
 rows=c['random_result']['vo_rewrite'];text='\n'.join(x['content'] for x in rows)
 body={'text':text,'voice':{'platform':'volcengine','voice_type':c['random_result']['tts_speaker_id'],'resource_id':'seed-tts-2.0','speed_ratio':1.05},'force':False}
 out=Path(args.run)/'revisions/brief2连贯配音';out.mkdir(parents=True,exist_ok=True);receipt=out/'tts-intent.json'
 stamp={'body':body,'digest':hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),'count':1}
 if args.dry_run:save(receipt,stamp);print('dry-run: brief2 整篇连续配音1条，亲切女声，语速1.05；文案不变；其他组不重新合成');return
 if not receipt.exists() or json.loads(receipt.read_text())!=stamp:raise SystemExit('Missing exact TTS dry-run receipt')
 dest=out/'tts.json'
 if dest.exists():result=json.loads(dest.read_text())
 else:
  result=cb.api('POST','/tts',body,tries=1);save(dest,result)
 if re.sub(r'\s','',result['synthesized_text'])!=re.sub(r'\s','',text):raise SystemExit('TTS copy mismatch')
 if not result.get('timeline',{}).get('items'):raise SystemExit('No native timing; refusing guessed alignment')
 cb.fetch_file(result['audio_url'],out/'brief2-整篇配音.mp3');save(out/'segments.json',rows)
 print(json.dumps({'duration_ms':result['audio']['duration_ms'],'voice':body['voice']['voice_type'],'speed':1.05,'words':len(result['timeline']['items']),'first':result['timeline']['items'][:5]},ensure_ascii=False))

def norm(s):return re.sub(r'[^\w\u4e00-\u9fff]','',s)

def probe(p):
 return json.loads(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(p)],text=True,capture_output=True,check=True).stdout)['format']['duration']

def retime_brief2(args,cfg,cut,borrow):
 root=Path(args.run)/'revisions/brief2连贯配音';t=json.loads((root/'tts.json').read_text());rows=json.loads((root/'segments.json').read_text());d=copy.deepcopy(cut['draft']);items=t['timeline']['items'];fps=d['composition']['fps'];total=math.ceil(t['audio']['duration_ms']*fps/1000)
 tokens=[];pos=0
 for item in items:
  n=norm(item['text'])
  if not n:continue
  tokens.append({**item,'a':pos,'b':pos+len(n)});pos+=len(n)
 whole=''.join(norm(x['text']) for x in items)
 if whole!=''.join(norm(x['content']) for x in rows):raise SystemExit('Native timing text does not match demo copy')
 oldcues=sorted([x for x in d['clips'] if x['trackId']=='overlay'],key=lambda x:x['from'])
 if ''.join(norm(x['props']['text']) for x in oldcues)!=whole:raise SystemExit('Old caption copy differs; no silent rewrite allowed')
 cues=[];pos=0;last_end=0
 for old in oldcues:
  x=copy.deepcopy(old);a=pos;b=a+len(norm(x['props']['text']));hits=[z for z in tokens if z['b']>a and z['a']<b];pos=b
  first=math.floor(hits[0]['start_ms']*fps/1000);end=math.floor(hits[-1]['end_ms']*fps/1000)
  if first<last_end:raise SystemExit('Caption boundary bisects a native word; manual alignment required')
  x.update(**{'from':first,'durationInFrames':end-first});x['motion']=None;cues.append(x);last_end=end
 newbounds=[];pos=0
 for row in rows:
  end=pos+len(norm(row['content']));hits=[z for z in tokens if z['b']>pos and z['a']<end];pos=end;newbounds.append(math.floor(hits[0]['start_ms']*fps/1000))
 newbounds[0]=0;newbounds.append(total)
 voices=sorted([x for x in d['clips'] if x['trackId']=='voiceover'],key=lambda x:x['from']);oldbounds=[x['from'] for x in voices]+[d['composition']['durationInFrames']]
 def tf(frame):
  for i in range(6):
   if oldbounds[i]<=frame<oldbounds[i+1]:return newbounds[i]+round((frame-oldbounds[i])/(oldbounds[i+1]-oldbounds[i])*(newbounds[i+1]-newbounds[i]))
  return total
 oldmain={x['id']:x for x in d['clips'] if x['trackId']=='main'}
 fresh=json.loads(sorted((root/'新选发丝').glob('segments*.json'))[-1].read_text())['records'];fresh={x['segment']['id']:x for x in fresh}
 def freshclip(sid):
  x=copy.deepcopy(oldmain['1-0']);x['props'].update(src=fresh[sid]['segment']['object_url'],startFrom=0,alt=fresh[sid]['segment'].get('description'));return x
 sources=[[(freshclip('6acde24c1d9d7854729952df'),3)],[(oldmain['2-0'],3),(freshclip('6acde24c1d9d7854729952da'),3)],[(oldmain['3-0'],3),(oldmain['1-0'],3)],[(oldmain['4-0'],12),(oldmain['4-1'],9)],[(oldmain['5-0'],27),(oldmain['5-1'],12)],[(oldmain['6-0'],0),(borrow,borrow['props'].get('startFrom',0))]]
 main=[];duration_evidence={};media=root/'retime-sources';media.mkdir(exist_ok=True)
 for i,pairs in enumerate(sources):
  a,b=newbounds[i:i+2];n=b-a
  lengths=[n] if len(pairs)==1 else ([min(108,n-30),n-min(108,n-30)] if i==3 else [n//2,n-n//2])
  cursor=a
  for j,((old,start),length) in enumerate(zip(pairs,lengths)):
   x=copy.deepcopy(old);x.update(id=f'{i+1}-{j}',**{'from':cursor,'durationInFrames':length,'transitionIn':None,'transitionOut':None});x['props'].update(startFrom=start,playbackRate=1.0);cursor+=length
   source=x['props']['src'];path=media/(hashlib.sha256(source.split('?')[0].encode()).hexdigest()[:16]+'.mp4')
   if not path.exists():cb.fetch_file(source,path)
   duration=float(probe(path));duration_evidence[source.split('?')[0]]=duration
   if (start+length)/fps>duration-.025:raise SystemExit(f'{x["id"]} unsafe source window')
   main.append(x)
 others=[]
 for old in d['clips']:
  if old['trackId'] in ['main','voiceover','overlay'] or old['id']=='cover':continue
  x=copy.deepcopy(old)
  if x['trackId']=='audio':x.update(**{'from':0,'durationInFrames':total})
  else:
   x['from']=tf(x['from']);x['durationInFrames']=min(x['durationInFrames'],total-x['from'])
  others.append(x)
 voice=copy.deepcopy(voices[0]);voice.update(id='voice-continuous',**{'from':0,'durationInFrames':total});voice['props'].update(src=t['audio_url'],startFrom=0,playbackRate=1.0)
 d['clips']=main+cues+[voice]+others;d['composition']['durationInFrames']=total
 save(root/'timing-evidence.json',{'audio_duration_ms':t['audio']['duration_ms'],'segment_frames':newbounds,'native_word_count':len(tokens),'source_durations':duration_evidence,'voice_requests':1,'playbackRate':1.0})
 return d

def decorate(draft,bid):
 d=copy.deepcopy(draft);n=int(bid[-2:]);palette={2:('#8C6AA5','#FCF6FF','tab','lens'),3:('#B9944E','#FFFAEC','pill','petal'),4:('#8D738B','#FFF6F5','tab','flow'),5:('#5C9690','#F0FFFA','pill','mist')}[n];accent,pale,shape,kind=palette
 code=(Path(__file__).resolve().parent.parent/'templates/lux-effects/SingleLineAccents.tsx').read_text()
 for x in d['clips']:
  if x['trackId']=='main' and x.get('transitionIn',{}):x['transitionIn']=None
 d['clips']=[x for x in d['clips'] if x['id']!='cover']
 overlayidx=next(i for i,x in enumerate(d['tracks']) if x['id']=='overlay')
 if not any(x['id']=='caption-decor' for x in d['tracks']):d['tracks'].insert(overlayidx,{'id':'caption-decor','name':'单行花字底纹','enabled':True})
 if not any(x['id']=='brand-stickers' for x in d['tracks']):d['tracks'].append({'id':'brand-stickers','name':'品牌贴纸','enabled':True})
 cues=sorted([x for x in d['clips'] if x['trackId']=='overlay'],key=lambda x:x['from']);added=[];events=[]
 words={2:['花香','氛围感','力士','又顺又亮','直播间'],3:['发型','力士','光泽','直播间'],4:['毛躁','力士','玻尿酸','柔顺','直播间'],5:['讲究','雾','不粘','光','直播间']}[n]
 for i,x in enumerate(cues):
  text=x['props']['text']
  if not any(k in text for k in words):continue
  x['props'].update(font_size=min(36,x['props'].get('font_size',36)),text_color=accent,background_color=None,stroke_color=None,stroke_width=None,text_shadow='0px 1px 1px rgba(255,255,255,.7)',bold=True)
  clip={'id':f'caption-mat-{i}','trackId':'caption-decor','from':x['from'],'durationInFrames':x['durationInFrames'],'component':'RemotionClip','transitionIn':None,'transitionOut':None,'motion':None,'props':{'code':code,'params':{'kind':'mat','accent':accent,'pale':pale,'shape':shape,'chars':len(text),'durationFrames':x['durationInFrames']},'x':0,'y':0,'scale':1}}
  added.append(clip)
  if len(events)<4:
   icon=copy.deepcopy(clip);icon.update(id=f'brand-sticker-{i}',trackId='brand-stickers',durationInFrames=min(42,x['durationInFrames']));icon['props']['params'].update(kind=kind if len(events)%2==0 else 'sparkle',side='left' if n in [3,5] else 'right',durationFrames=icon['durationInFrames']);added.append(icon)
  events.append({'text':text,'frame':x['from'],'duration':x['durationInFrames']})
 d['clips']+=added;return d,events

def plan(args,cfg):
 st=cb.State(args.run);out=Path(args.run)/'revisions/单行花字与连贯配音';out.mkdir(exist_ok=True)
 ref=cb.api('GET',f"/video-cuts/{st.videos['1125-L01-V01']['cut_id']}");save(out/'brief1-protected.json',ref)
 borrow=next(x for x in ref['draft']['clips'] if x['trackId']=='main' and x['id'].startswith('6-'))
 for key,v in sorted(st.videos.items()):
  if key=='1125-L01-V01':continue
  c=cb.api('GET',f"/video-cuts/{v['cut_id']}");d=retime_brief2(args,cfg,c,borrow) if key=='1125-L02-V01' else c['draft'];d,events=decorate(d,v['brief_id'])
  if key!='1125-L02-V01':
   for track in ['voiceover','overlay','audio','sound_effects']:
    a=[x for x in c['draft']['clips'] if x['trackId']==track];b=[x for x in d['clips'] if x['trackId']==track]
    if track!='overlay' and a!=b:raise SystemExit('Unexpected audio change '+key)
    if track=='overlay' and [(x['from'],x['durationInFrames'],x['props']['text']) for x in a]!=[(x['from'],x['durationInFrames'],x['props']['text']) for x in b]:raise SystemExit('Caption copy/timing changed '+key)
  cues=sorted([x for x in d['clips'] if x['trackId']=='overlay'],key=lambda x:x['from'])
  if any(a['from']+a['durationInFrames']>b['from'] for a,b in zip(cues,cues[1:])):raise SystemExit('Subtitle overlap')
  save(out/f'{key}-plan.json',{'cut_id':v['cut_id'],'before':c,'draft':d,'caption_events':events,'reason':'用户要求brief2朗读连贯；恢复各组贴纸花字但字幕单行并避让人物；喷雾剔除空镜、抖动及露黑底转场。'})
  print(key,'planned',round(d['composition']['durationInFrames']/30,2),'seconds, highlights',len(events),'sticker events',min(4,len(events)),flush=True)

def apply(args,cfg):
 import footage_qc as fq
 from qc import cut_checks
 st=cb.State(args.run);out=Path(args.run)/'revisions/单行花字与连贯配音'
 for path in sorted(out.glob('*-plan.json')):
  p=json.loads(path.read_text());key=path.name[:-10];v=st.videos[key]
  if v.get('polish_revision')=='singleline-stickers-01':print(key,'already submitted');continue
  current=cb.api('GET',f"/video-cuts/{p['cut_id']}")
  if fq.stable_media(current['draft'])!=fq.stable_media(p['before']['draft']):raise SystemExit('Draft changed after plan')
  b=next(b for b in cb.load_briefs(args.run) if b['brief_id']==v['brief_id']);issues=cut_checks(b,p['draft'],v.get('voice'))
  if issues:raise SystemExit(str(issues))
  if args.dry_run:print(key,'will render one revised demo; QC passed');continue
  cb.api('PUT',f"/video-cuts/{p['cut_id']}",{'script_id':current['script_id'],'draft':p['draft']});after=cb.api('GET',f"/video-cuts/{p['cut_id']}")
  if fq.stable_media(after['draft'])!=fq.stable_media(p['draft']):raise SystemExit('Readback mismatch')
  save(out/f'{key}-after.json',after)
  r=cb.api('POST','/render-jobs',{'spec':after['draft'],'options':{'codec':'h264','quality':'high','scale':1},'cut_id':p['cut_id'],'idempotency_key':'udb-'+p['cut_id']+'-singleline-stickers-01'},tries=1)
  save(out/f'{key}-render.json',r);v.update(render_job=r['job_id'],stage='rendering',ai_review=None,ai_reason=None,polish_revision='singleline-stickers-01',duration_s=round(after['draft']['composition']['durationInFrames']/30,2),qc_issues=issues);v.pop('object_key',None);st.save();print(key,'render',r['job_id'],flush=True)
 ref=json.loads((out/'brief1-protected.json').read_text());now=cb.api('GET',f"/video-cuts/{ref['id']}")
 if fq.stable_media(ref['draft'])!=fq.stable_media(now['draft']) or ref.get('output_object_key')!=now.get('output_object_key'):raise SystemExit('Brief1 changed unexpectedly')
 print('brief1 unchanged, including output object')

def fix_mats(args,cfg):
 import footage_qc as fq
 st=cb.State(args.run);out=Path(args.run)/'revisions/单行花字与连贯配音/底纹位置校正';out.mkdir(exist_ok=True)
 code=(Path(__file__).resolve().parent.parent/'templates/lux-effects/SingleLineAccents.tsx').read_text()
 for key,v in sorted(st.videos.items()):
  if key=='1125-L01-V01' or v.get('caption_mat_revision')==2:continue
  before=cb.api('GET',f"/video-cuts/{v['cut_id']}");draft=copy.deepcopy(before['draft'])
  for x in draft['clips']:
   if x['trackId']=='caption-decor':
    x['props']['code']=code
    if key=='1125-L03-V01':x['props']['params']['shape']='brush'
    if key=='1125-L04-V01':x['props']['params']['shape']='brackets'
   if x['trackId']=='overlay' and x['props'].get('bold') and key=='1125-L04-V01':x['props'].update(text_color='#FFF6F0',stroke_color='#574C48',stroke_width=3,text_shadow=None)
  protected=lambda d:[x for x in d['clips'] if x['trackId'] not in ['caption-decor','overlay']]
  if protected(draft)!=protected(before['draft']):raise SystemExit('Unexpected non-mat change')
  captions=lambda d:[(x['id'],x['from'],x['durationInFrames'],x['props']['text']) for x in d['clips'] if x['trackId']=='overlay']
  if captions(draft)!=captions(before['draft']):raise SystemExit('Caption copy/timing changed')
  if args.dry_run:print(key,'move caption backplate down45px; text/audio/footage unchanged');continue
  save(out/f'{key}-before.json',before)
  cb.api('PUT',f"/video-cuts/{v['cut_id']}",{'script_id':before['script_id'],'draft':draft});after=cb.api('GET',f"/video-cuts/{v['cut_id']}")
  if fq.stable_media(after['draft'])!=fq.stable_media(draft):raise SystemExit('Mat readback mismatch')
  save(out/f'{key}-after.json',after)
  job=cb.api('POST','/render-jobs',{'spec':after['draft'],'options':{'codec':'h264','quality':'high','scale':1},'cut_id':v['cut_id'],'idempotency_key':'udb-'+v['cut_id']+'-captionmat02'},tries=1)
  save(out/f'{key}-render.json',job);v.update(render_job=job['job_id'],stage='rendering',ai_review=None,ai_reason=None,caption_mat_revision=2);v.pop('object_key',None);st.save();print(key,'mat corrected, rerender',job['job_id'],flush=True)

def tts14(args,cfg):
 st=cb.State(args.run);v=st.videos['1125-L02-V01'];c=cb.api('GET',f"/video-cuts/{v['cut_id']}");rows=c['random_result']['vo_rewrite']
 body={'text':'\n'.join(x['content'] for x in rows),'voice':{'platform':'volcengine','voice_type':c['random_result']['tts_speaker_id'],'resource_id':'seed-tts-2.0','speed_ratio':1.4},'force':False}
 out=Path(args.run)/'revisions/brief2-原生统一字幕-1.4';out.mkdir(exist_ok=True);receipt=out/'tts-intent.json'
 if args.dry_run:save(receipt,body);print('dry-run: 仅brief2，Clipo整篇TTS 1条，语速1.4；原口播文字不变。');return
 if not receipt.exists() or json.loads(receipt.read_text())!=body:raise SystemExit('Missing exact TTS14 receipt')
 dest=out/'tts.json'
 if dest.exists():t=json.loads(dest.read_text())
 else:t=cb.api('POST','/tts',body,tries=1);save(dest,t)
 if norm(t['synthesized_text'])!=norm(body['text']) or not (t.get('timeline') or {}).get('items'):raise SystemExit('Native TTS text/timing unavailable')
 cb.fetch_file(t['audio_url'],out/'brief2-1.4-整篇配音.mp3');save(out/'segments.json',rows)
 print(json.dumps({'speed_ratio':1.4,'duration_ms':t['audio']['duration_ms'],'voice':body['voice']['voice_type'],'native_timed_tokens':len(t['timeline']['items'])},ensure_ascii=False))

def plan14(args,cfg):
 st=cb.State(args.run);root=Path(args.run);out=root/'revisions/brief2-原生统一字幕-1.4';t=json.loads((out/'tts.json').read_text());rows=json.loads((out/'segments.json').read_text());v=st.videos['1125-L02-V01'];c=cb.api('GET',f"/video-cuts/{v['cut_id']}");d=copy.deepcopy(c['draft']);fps=d['composition']['fps'];total=math.ceil(t['audio']['duration_ms']*fps/1000)
 items=[];pos=0
 for token in t['timeline']['items']:
  n=norm(token['text'])
  if n:items.append({**token,'a':pos,'b':pos+len(n)});pos+=len(n)
 text=''.join(norm(z['text']) for z in t['timeline']['items']);cues=sorted([x for x in d['clips'] if x['trackId']=='overlay'],key=lambda x:x['from'])
 if ''.join(norm(x['props']['text']) for x in cues)!=text:raise SystemExit('Native speech/caption copy differs')
 original=json.loads((root/'revisions/brief2连贯配音/cut-6b06460c429cee6fdc086062.json').read_text());style=copy.deepcopy(next(x['props'] for x in original['draft']['clips'] if x['trackId']=='overlay'));style.pop('text');style['font_size']=36
 oldbounds=json.loads((root/'revisions/brief2连贯配音/timing-evidence.json').read_text())['segment_frames'];newbounds=[];pos=0
 for row in rows:
  end=pos+len(norm(row['content']));hits=[z for z in items if z['b']>pos and z['a']<end];pos=end;newbounds.append(math.floor(hits[0]['start_ms']*fps/1000))
 newbounds[0]=0;newbounds.append(total)
 def tf(frame):
  if frame>=oldbounds[-1]:return total
  for i in range(6):
   if oldbounds[i]<=frame<oldbounds[i+1]:return newbounds[i]+round((frame-oldbounds[i])/(oldbounds[i+1]-oldbounds[i])*(newbounds[i+1]-newbounds[i]))
  raise SystemExit('Invalid frame mapping')
 aligned=[];pos=0;previous_end=0;cue_map={}
 for old in cues:
  x=copy.deepcopy(old);a=pos;b=a+len(norm(x['props']['text']));hits=[z for z in items if z['b']>a and z['a']<b];pos=b;start=math.floor(hits[0]['start_ms']*fps/1000);end=math.floor(hits[-1]['end_ms']*fps/1000)
  if start<previous_end or end<=start:raise SystemExit('Native caption boundaries overlap or empty')
  x.update(**{'from':start,'durationInFrames':end-start,'motion':None});x['props']={'text':old['props']['text'],**style};aligned.append(x);cue_map[old['id']]=x;previous_end=end
 clips=[]
 for old in d['clips']:
  if old['trackId'] in ['caption-decor','overlay']:continue
  x=copy.deepcopy(old)
  if x['trackId']=='voiceover':x.update(**{'from':0,'durationInFrames':total});x['props'].update(src=t['audio_url'],startFrom=0,playbackRate=1.0)
  elif x['trackId']=='audio':x.update(**{'from':0,'durationInFrames':total})
  elif x['trackId']=='main':
   start=tf(x['from']);end=tf(x['from']+x['durationInFrames']);x.update(**{'from':start,'durationInFrames':end-start})
   if x['durationInFrames']>old['durationInFrames']:raise SystemExit('Source window extension not authorized')
  else:
   start=tf(x['from'])
   if x['id'].startswith('brand-sticker-'):
    i=int(x['id'].rsplit('-',1)[1]);start=aligned[i]['from']
   x['from']=start;x['durationInFrames']=min(x['durationInFrames'],total-start)
   if 'durationFrames' in x.get('props',{}).get('params',{}):x['props']['params']['durationFrames']=x['durationInFrames']
  clips.append(x)
 d['clips']=clips+aligned;d['tracks']=[x for x in d['tracks'] if x['id']!='caption-decor'];d['composition']['durationInFrames']=total
 main=sorted([x for x in clips if x['trackId']=='main'],key=lambda x:x['from']);end=0
 for x in main:
  if x['from']!=end or x['durationInFrames']<22:raise SystemExit('Picture gaps/flash cuts')
  end=x['from']+x['durationInFrames']
 if end!=total:raise SystemExit('Picture does not cover full video')
 protected={}
 for key,row in st.videos.items():
  if key=='1125-L02-V01':continue
  other=cb.api('GET',f"/video-cuts/{row['cut_id']}");protected[key]=other
 script=cb.api('GET',f"/video-scripts/{c['script_id']}");intended=copy.deepcopy(script);intended['config']['tts']['speech_rate']=1.4;intended['config']['subtitle_style'].update(style_ids=['6ab0980646ba4a9c28773860'],font_size=36)
 for seg in intended['segments']:
  if seg.get('tts'):seg['tts']['speech_rate']=1.4
 if len(intended['config']['tts']['voices'])<3:raise SystemExit('Do not restrict global voice pool')
 save(out/'plan.json',{'before':c,'draft':d,'native_subtitle_style_id':'6ab0980646ba4a9c28773860','uniform_style':style,'speed_ratio':1.4,'native_audio_duration_ms':t['audio']['duration_ms'],'segment_frames':newbounds,'script_before':script,'script_intended':intended,'other_cuts_protected':protected})
 print('brief2 plan:',round(total/fps,2),'seconds; 14 cues same Clipo native style; one continuous TTS at1.4; other4 protected')

def apply14(args,cfg):
 import footage_qc as fq
 from qc import cut_checks
 root=Path(args.run);out=root/'revisions/brief2-原生统一字幕-1.4';plan=json.loads((out/'plan.json').read_text());st=cb.State(root);v=st.videos['1125-L02-V01']
 if v.get('brief2_native14_revision')==1:print('Already submitted native14; no duplicate render');return
 current=cb.api('GET',f"/video-cuts/{v['cut_id']}")
 if fq.stable_media(current['draft'])!=fq.stable_media(plan['before']['draft']):raise SystemExit('Draft changed after plan')
 b=next(b for b in cb.load_briefs(root) if b['brief_id']==v['brief_id']);issues=cut_checks(b,plan['draft'],v.get('voice'))
 if issues:raise SystemExit(str(issues))
 styles=[{k:z for k,z in x['props'].items() if k!='text'} for x in plan['draft']['clips'] if x['trackId']=='overlay']
 if not all(fq.stable_media(x)==fq.stable_media(styles[0]) for x in styles):raise SystemExit('Subtitle styles differ')
 if args.dry_run:print('dry-run: 仅brief2重渲1条；原生字幕样式全程一致；语速1.4；闸门通过');return
 script=plan['script_intended'];body={k:script[k] for k in ['product_id','title','video','config','vo_rewrite_prompt','segments']};cb.api('PUT',f"/video-scripts/{script['id']}",body);back=cb.api('GET',f"/video-scripts/{script['id']}")
 if any(back[k]!=body[k] for k in body):raise SystemExit('System script readback mismatch')
 save(out/'script-after.json',back)
 cb.api('PUT',f"/video-cuts/{v['cut_id']}",{'script_id':current['script_id'],'draft':plan['draft']});after=cb.api('GET',f"/video-cuts/{v['cut_id']}")
 if fq.stable_media(after['draft'])!=fq.stable_media(plan['draft']):raise SystemExit('Cut readback mismatch')
 save(out/'cut-after.json',after)
 job=cb.api('POST','/render-jobs',{'spec':after['draft'],'options':{'codec':'h264','quality':'high','scale':1},'cut_id':v['cut_id'],'idempotency_key':'udb-'+v['cut_id']+'-native14-01'},tries=1);save(out/'render.json',job)
 v.update(render_job=job['job_id'],stage='rendering',ai_review=None,ai_reason=None,brief2_native14_revision=1,duration_s=round(after['draft']['composition']['durationInFrames']/30,2),qc_issues=issues);v.pop('object_key',None);st.save()
 for key,old in plan['other_cuts_protected'].items():
  now=cb.api('GET',f"/video-cuts/{old['id']}")
  if fq.stable_media(now['draft'])!=fq.stable_media(old['draft']) or now.get('output_object_key')!=old.get('output_object_key'):raise SystemExit('Other cut changed: '+key)
 print('brief2 render:',job['job_id'],'; other4 draft and outputs unchanged')

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['tts','plan','apply','fix-mats','tts14','plan14','apply14']);p.add_argument('--config',required=True);p.add_argument('--run',required=True);p.add_argument('--dry-run',action='store_true');a=p.parse_args();cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';{'tts':tts,'plan':plan,'apply':apply,'fix-mats':fix_mats,'tts14':tts14,'plan14':plan14,'apply14':apply14}[a.cmd](a,cfg)
if __name__=='__main__':main()
