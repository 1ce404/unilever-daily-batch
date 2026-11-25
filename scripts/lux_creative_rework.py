#!/usr/bin/env python3
"""[INPUT] LUX user feedback, existing native cuts, Clipo voice/BGM/material libraries.
[OUTPUT] Read-backed native revisions: brief1 vocal gain only; other4 lively full TTS, BGM and disjoint visual groups.
[POS] Current demo revision; no bulk or shared prompt modification.
"""
import argparse,copy,json,math,re,subprocess,sys,hashlib,urllib.parse,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import load_config

def save(p,d):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,indent=2));return p

def norm(s):return re.sub(r'[^\w\u4e00-\u9fff]','',s)

def inventory(a,cfg):
 out=Path(a.run)/'revisions/声音节奏与人物重选';out.mkdir(exist_ok=True)
 for name in ['美妆时尚','卡点','爵士','Beauty','轻快','节奏感3.0']:
  d=cb.api('GET','/resource/bgm?limit=100&catalog='+urllib.parse.quote(name));save(out/('bgm-'+name+'.json'),d);rows=d.get('items',[]) if isinstance(d,dict) else d
  print('BGM',name,[(x['id'],x.get('name'),x.get('duration_ms')) for x in rows],flush=True)
 voices=['zh_female_shuangkuaisisi_uranus_bigtts','ICL_uranus_zh_female_kailangtingting_tob','zh_female_zhishuaiyingzi_uranus_bigtts','ICL_uranus_zh_female_tiexinguimi_tob']
 d=cb.api('POST','/tts/speakers/lookup',{'voices':[{'platform':'volcengine','voice_type':v} for v in voices]});save(out/'voices.json',d);print('voices verified response saved',flush=True)
 for pk in ['jingyou','spray']:
  rows=cb.list_segments(cfg['products'][pk]['clipo_product_id']);rows=[s for s in rows if '头发顺滑' in s.get('tags',[]) and s.get('review_result')!='reject'];save(out/f'hair-{pk}.json',rows)
  selected=[s for s in rows if any(w in (s.get('description') or '') for w in ['酒红','香槟','浴巾','紫色蕾丝'])]
  print('HAIR',pk,[(x['id'],x.get('name')) for x in selected],flush=True)
 st=cb.State(a.run);c=cb.api('GET',f"/video-cuts/{st.videos['1125-L01-V01']['cut_id']}");save(out/'brief1-before.json',c)
 media=out/'brief1-voice';media.mkdir(exist_ok=True)
 for x in c['draft']['clips']:
  if x['trackId']!='voiceover':continue
  p=media/(x['id']+'.mp3');cb.fetch_file(x['props']['src'],p)
  r=subprocess.run(['ffmpeg','-hide_banner','-i',str(p),'-af','volumedetect','-f','null','-'],capture_output=True,text=True,check=True);(media/(x['id']+'-level.log')).write_text(r.stderr)
  print('VOICE1',x['id'],[z.strip() for z in r.stderr.splitlines() if 'mean_volume' in z or 'max_volume' in z],flush=True)

def voices(a,cfg):
 out=Path(a.run)/'revisions/声音节奏与人物重选';st=cb.State(a.run)
 roles={
 '1125-L02':('zh_female_shuangkuaisisi_uranus_bigtts','像爱分享的时髦女生发现香味小秘密。开头好奇带笑，轻微上扬；小心思带一点俏皮，产品名清楚，重音自然，整体轻快利落，有互动感。不要播音腔，不要逐句平均用力，不喊叫。'),
 '1125-L03':('ICL_uranus_zh_female_kailangtingting_tob','像自信开朗的女生轻松晒好发型。开头有轻微得意的笑意，随便和精致有反差重音，短停顿，句尾干脆，像对朋友安利，有活力但不尖嗲，不要平铺直叙的广告朗诵。'),
 '1125-L04':('zh_female_zhishuaiyingzi_uranus_bigtts','像直爽女生吐槽头发毛躁。前两句有一点真实的无奈和吐槽，转到产品后轻快有把握，毛躁、卡、顺下来要有重音，短停顿，语气转折清楚，不生硬喊卖，不要新闻播音腔。'),
 '1125-L05':('ICL_uranus_zh_female_tiexinguimi_tob','像机灵时髦的闺蜜演示随手喷雾。讲究和省事带轻巧反差，轻轻一按就是雾说得爽快，轻、不粘腻说得清脆有节奏，末句含笑邀请。整体轻快明亮，有起伏，不要温吞治愈旁白，不拖长尾音。')}
 intents={}
 for bid,(voice,instruction) in roles.items():
  c=cb.api('GET',f"/video-cuts/{st.videos[bid+'-V01']['cut_id']}");rows=c['random_result']['vo_rewrite'];text='\n'.join(x['content'] for x in rows)
  body={'text':'[#'+instruction+']\n'+text,'voice':{'platform':'volcengine','voice_type':voice,'resource_id':'seed-tts-2.0','speed_ratio':1.4},'force':False};intents[bid]={'body':body,'spoken':text,'segments':rows}
 path=out/'voice-intent.json'
 if a.dry_run:save(path,intents);print('dry-run: 4条整篇配音，各1条；4种已核验音色，全部1.4；仅更换音色表演，不改口播文本。');return
 if not path.exists() or json.loads(path.read_text())!=intents:raise SystemExit('Missing exact 4-voice dry-run')
 from concurrent.futures import ThreadPoolExecutor
 def one(item):
  bid,data=item;dest=out/(bid+'-tts.json')
  if dest.exists():t=json.loads(dest.read_text())
  else:t=cb.api('POST','/tts',data['body'],tries=1);save(dest,t)
  if norm(t['synthesized_text'])!=norm(data['spoken']) or not (t.get('timeline') or {}).get('items'):raise RuntimeError('Voice text/timing mismatch '+bid)
  cb.fetch_file(t['audio_url'],out/(bid+'-voice.mp3'))
  print(bid,data['body']['voice']['voice_type'],1.4,t['audio']['duration_ms'],'ms',flush=True)
 with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(one,intents.items()))

def samples(a,cfg):
 out=Path(a.run)/'revisions/声音节奏与人物重选';music=out/'music';music.mkdir(exist_ok=True)
 chosen=['6ac72f6b4fc86c540004c80a','6ac72f6b4fc86c540004c809','6ac72f6a4fc86c540004c808','6ac72f694fc86c540004c807','6aafcfe4f406342b4dc429c5','6aafcff1f406342b4dc42a88']
 for bid in chosen:
  catalog_rows=[row for path in out.glob('bgm-*.json') for row in json.loads(path.read_text()).get('items',[])];meta=next(x for x in catalog_rows if x['id']==bid);save(music/(bid+'.json'),meta);url=meta.get('url') or meta.get('object_url') or meta.get('audio_url')
  if not url:raise SystemExit('BGM no media URL: '+str(list(meta)))
  p=music/(bid+'.mp3');cb.fetch_file(url,p)
  q=subprocess.run(['ffmpeg','-hide_banner','-ss','0','-t','20','-i',str(p),'-af','volumedetect','-f','null','-'],capture_output=True,text=True,check=True);(music/(bid+'-level.log')).write_text(q.stderr)
  print('MUSIC',bid,meta.get('name'),[z.strip() for z in q.stderr.splitlines() if 'mean_volume' in z or 'max_volume' in z],flush=True)
 groups={'1125-L02':('jingyou',['6acde24c1d9d785472995310','6acde24c1d9d785472995305','6acde24c1d9d785472995301','6acde24c1d9d7854729952fe','6acde24c1d9d7854729952f6']), '1125-L03':('jingyou',['6acde24c1d9d78547299530c','6acde24c1d9d7854729952f2','6acde24c1d9d7854729952ef','6acde24c1d9d7854729952ea']), '1125-L04':('jingyou',['6acde24c1d9d785472995300','6acde24c1d9d7854729952fd']), '1125-L05':('spray',['6acf770171205abaa72a540d','6acf770171205abaa72a53f3','6acf770171205abaa72a53fc'])}
 result={}
 for group,(pk,ids) in groups.items():
  library=json.loads((out/f'hair-{pk}.json').read_text());by={z['id']:z for z in library};result[group]=[]
  for sid in ids:
   meta=by[sid];folder=out/'hair'/group;folder.mkdir(parents=True,exist_ok=True);p=folder/(sid+'.mp4');cb.fetch_file(meta['object_url'],p)
   probe=json.loads(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(p)],capture_output=True,text=True,check=True).stdout)
   subprocess.run(['ffmpeg','-v','error','-y','-i',str(p),'-vf','fps=2,scale=180:320,tile=4x1','-frames:v','1',str(folder/(sid+'.jpg'))],check=True)
   result[group].append({'segment':meta,'file':str(p),'duration':float(probe['format']['duration'])});print('HAIR_SAMPLE',group,sid,probe['format']['duration'],flush=True)
 save(out/'visual-groups.json',result)

def assets(a,cfg):
 out=Path(a.run)/'revisions/声音节奏与人物重选';receipt=out/'assets-intent.json'
 spec={'voice_gain_db':4,'voice_uploads':6,'upper_title_templates':1,'scope':'brief1口播增益；5组独立上方花字模板配置'}
 if a.dry_run:save(receipt,spec);print('dry-run: brief1原口播6段增益4dB并上传；新增1个可配置上方花字模板，不改其他共享模板。');return
 if not receipt.exists() or json.loads(receipt.read_text())!=spec:raise SystemExit('Missing asset dry-run')
 cache_path=out/'assets.json';cache=json.loads(cache_path.read_text()) if cache_path.exists() else {'voice1':{}}
 before=json.loads((out/'brief1-before.json').read_text());host='https://'+urllib.parse.urlparse(next(x['props']['src'] for x in before['draft']['clips'] if x['trackId']=='voiceover')).netloc
 for clip in before['draft']['clips']:
  if clip['trackId']!='voiceover' or clip['id'] in cache['voice1']:continue
  key=clip['id'];source=out/'brief1-voice'/(key+'.mp3');dest=out/'brief1-voice'/(key+'-plus4dB.wav')
  subprocess.run(['ffmpeg','-v','error','-y','-i',str(source),'-af','volume=4dB','-c:a','pcm_s16le',str(dest)],check=True)
  tok=cb.api('POST','/storage/upload-tokens',{'filename':dest.name,'content_type':'audio/wav','content_length':dest.stat().st_size,'purpose':'material'})
  request=urllib.request.Request(tok['upload_url'],data=dest.read_bytes(),method='PUT',headers=dict(tok.get('headers') or {},**{'Content-Type':'audio/wav'}));urllib.request.urlopen(request,timeout=120).read()
  cache['voice1'][key]={'src':host+'/'+tok['object_key'],'object_key':tok['object_key'],'gain_db':4};save(cache_path,cache);print('voice1 gain uploaded',key,flush=True)
 if not cache.get('upper_template'):
  code=(Path(__file__).resolve().parent.parent/'templates/lux-effects/UpperSellingTitle.tsx').read_text();catalog=cb.api('GET','/resource/remotion-effect-template?limit=100');rows=catalog.get('items',[]) if isinstance(catalog,dict) else catalog;name='LUX1125｜上方花字·人物避让'
  found=[x for x in rows if x.get('name')==name]
  if len(found)>1:raise SystemExit('Duplicate named template; reconcile first')
  if found:created=cb.api('GET','/resource/remotion-effect-template/'+found[0]['id'])
  else:
   body={'folderId':rows[0]['folderId'],'name':name,'code':code,'params':{'text':'发丝有点小心机','accent':'#D7B76D','pale':'#FFF9E8','variant':1,'x':540,'top':96,'fontSize':52,'durationFrames':45},'width':1080,'height':1920,'fps':30,'durationInFrames':45}
   intent=out/'upper-template-intent.json'
   if intent.exists():raise SystemExit('Unresolved template create intent; no duplicate request')
   save(intent,body);created=cb.api('POST','/resource/remotion-effect-template',body,tries=1);save(out/'upper-template-created.json',created)
  actual=cb.api('GET','/resource/remotion-effect-template/'+created['id'])
  if actual['code']!=code:raise SystemExit('Upper template source readback differs')
  save(out/'upper-template-readback.json',actual);cache['upper_template']=actual;save(cache_path,cache)
 print('Native Clipo library upper-title template:',cache['upper_template']['id'])

def gain4(a,cfg):
 out=Path(a.run)/'revisions/声音节奏与人物重选';gains={'1125-L02':2.5,'1125-L03':2.5,'1125-L04':5.5,'1125-L05':2.5};receipt=out/'gain4-intent.json'
 if a.dry_run:save(receipt,gains);print('dry-run: 4份已生成口播做保真增益，保持原生时间轴；不重新合成。');return
 if not receipt.exists() or json.loads(receipt.read_text())!=gains:raise SystemExit('Missing gain dry-run')
 path=out/'assets.json';cache=json.loads(path.read_text());host='https://'+urllib.parse.urlparse(json.loads((out/'1125-L02-tts.json').read_text())['audio_url']).netloc;cache.setdefault('voice4',{})
 for bid,gain in gains.items():
  if bid in cache['voice4']:continue
  source=out/(bid+'-voice.mp3');dest=out/(bid+'-voice-gain.wav');subprocess.run(['ffmpeg','-v','error','-y','-i',str(source),'-af',f'volume={gain}dB','-c:a','pcm_s16le',str(dest)],check=True)
  tok=cb.api('POST','/storage/upload-tokens',{'filename':dest.name,'content_type':'audio/wav','content_length':dest.stat().st_size,'purpose':'material'});request=urllib.request.Request(tok['upload_url'],data=dest.read_bytes(),method='PUT',headers=dict(tok.get('headers') or {},**{'Content-Type':'audio/wav'}));urllib.request.urlopen(request,timeout=120).read()
  cache['voice4'][bid]={'src':host+'/'+tok['object_key'],'gain_db':gain};save(path,cache);print(bid,'gain',gain,'uploaded',flush=True)

def align_native(cut,tts,rows,style):
 d=copy.deepcopy(cut['draft']);fps=d['composition']['fps'];total=math.ceil(tts['audio']['duration_ms']*fps/1000);tokens=[];pos=0
 for z in tts['timeline']['items']:
  n=norm(z['text'])
  if n:tokens.append({**z,'a':pos,'b':pos+len(n)});pos+=len(n)
 whole=''.join(norm(z['text']) for z in tts['timeline']['items']);oldcues=sorted([x for x in d['clips'] if x['trackId']=='overlay'],key=lambda x:x['from'])
 if ''.join(norm(z['props']['text']) for z in oldcues).casefold()!=whole.casefold():raise RuntimeError('Speech timing differs from caption copy')
 aligned=[];pos=0;previous=0
 for x0 in oldcues:
  x=copy.deepcopy(x0);a=pos;b=a+len(norm(x['props']['text']));pos=b;hits=[z for z in tokens if z['b']>a and z['a']<b];start=math.floor(hits[0]['start_ms']*fps/1000);end=math.floor(hits[-1]['end_ms']*fps/1000)
  if start<previous or end<=start:raise RuntimeError('Native cue overlap or zero length')
  x.update(**{'from':start,'durationInFrames':end-start,'motion':None});x['props']={**style,'text':x0['props']['text']};aligned.append(x);previous=end
 bounds=[];pos=0
 for row in rows:
  end=pos+len(norm(row['content']));hits=[z for z in tokens if z['b']>pos and z['a']<end];pos=end;bounds.append(math.floor(hits[0]['start_ms']*fps/1000))
 bounds[0]=0;bounds.append(total);return aligned,bounds

def original_style(root,key):
 before=json.loads((root/'clipo/single-line-repair'/(key+'-before.json')).read_text());p=copy.deepcopy(next(x['props'] for x in before['draft']['clips'] if x['trackId']=='overlay'));p.pop('text',None);p['font_size']=36;return p

def plan(a,cfg):
 import numpy as np
 import footage_qc as fq
 root=Path(a.run);out=root/'revisions/声音节奏与人物重选';assets=json.loads((out/'assets.json').read_text());
 if assets.get('upper_template_retired_for_this_run'):raise RuntimeError('用户已否定自制花字；仅使用native-final的Clipo现有模板配置，不重建旧方案。')
 visuals=json.loads((out/'visual-groups.json').read_text());intents=json.loads((out/'voice-intent.json').read_text());st=cb.State(root)
 visuals['1125-L05']=[x for x in visuals['1125-L05'] if x['segment']['id']!='6acf770171205abaa72a53fc']
 seen=set()
 for bid,pool in visuals.items():
  paths={urllib.parse.urlparse(x['segment']['object_url']).path for x in pool}
  if seen & paths:raise RuntimeError('Visual source crosses appearance groups')
  seen.update(paths)
 titlemap={
 '1125-L01':[('1','头发先营业'),('4','撩一下，自带光'),('6','替你加点分')],
 '1125-L02':[('4','谁把香味藏这儿'),('5','答案在发丝里'),('6','小心思，藏不住')],
 '1125-L03':[('3','随便披，不随便'),('4','随性也精致'),('6','头发也要出片')],
 '1125-L04':[('1','发尾又卡住了'),('3','毛躁往后稍稍'),('6','顺顺披下来')],
 '1125-L05':[('3','讲究，也能顺手'),('4','轻轻一按就成雾'),('6','发丝的小讲究')]}
 palette={1:('#D5B36B','#FFF8DB'),2:('#8E68A8','#FCF7FF'),3:('#C2A46C','#FFF9EA'),4:('#C79B99','#FFF5F1'),5:('#629F98','#F1FFFB')}
 musicmap={'1125-L02':('6ac72f6b4fc86c540004c80a',.30),'1125-L03':('6aafcfe4f406342b4dc429c5',.16),'1125-L04':('6ac72f6a4fc86c540004c808',.28),'1125-L05':('6ac72f694fc86c540004c807',.24)}
 for key,v in sorted(st.videos.items()):
  bid=v['brief_id'];n=int(bid[-2:]);cut=cb.api('GET',f"/video-cuts/{v['cut_id']}");draft=copy.deepcopy(cut['draft']);b=next(x for x in cb.load_briefs(root) if x['brief_id']==bid);style=original_style(root,'1125-L01-V01');fps=draft['composition']['fps'];oldmain=sorted([x for x in draft['clips'] if x['trackId']=='main'],key=lambda x:x['from']);oldvoices=sorted([x for x in draft['clips'] if x['trackId']=='voiceover'],key=lambda x:x['from']);newhair=[];audio_evidence={}
  if n==1:
   bounds=[x['from'] for x in oldvoices]+[draft['composition']['durationInFrames']]
   for clip in draft['clips']:
    if clip['trackId']=='voiceover':clip['props']['src']=assets['voice1'][clip['id']]['src']
    if clip['trackId']=='overlay':clip['props']={**style,'text':clip['props']['text']}
   voice=v.get('voice');bgm_volume=.2
  else:
   tts=json.loads((out/(bid+'-tts.json')).read_text());aligned,bounds=align_native(cut,tts,intents[bid]['segments'],style);total=bounds[-1]
   oldbounds=json.loads((root/'revisions/brief2连贯配音/timing-evidence.json').read_text())['segment_frames'] if n==2 else [x['from'] for x in oldvoices]+[draft['composition']['durationInFrames']]
   def tf(frame):
    if frame>=oldbounds[-1]:return total
    for i in range(6):
     if oldbounds[i]<=frame<oldbounds[i+1]:return bounds[i]+round((frame-oldbounds[i])/(oldbounds[i+1]-oldbounds[i])*(bounds[i+1]-bounds[i]))
    raise RuntimeError('Invalid old frame')
   main=[];pick=0;pool=visuals.get(bid,[]);durations={}
   for i,segment in enumerate(b['segments']):
    sid=segment['id'];start,end=bounds[i:i+2];pieces=[x for x in oldmain if x['id'].split('-')[0]==sid]
    if '头发顺滑' in segment['visual_tags']:
     if not pool:raise RuntimeError('No unique visual pool '+bid)
     length=end-start;count=math.ceil(length/50);cursor=start
     for j in range(count):
      row=pool[pick%len(pool)];pick+=1;frames=length//count+(j<length%count);clip=copy.deepcopy(pieces[0]);clip.update(id=sid+'-'+str(j),**{'from':cursor,'durationInFrames':frames,'transitionIn':None,'transitionOut':None});clip['props'].update(src=row['segment']['object_url'],startFrom=3,playbackRate=1.0,scale=1.38,x=0,y=0,alt=row['segment'].get('description'));cursor+=frames
      if (3+frames)/fps>row['duration']-.07:raise RuntimeError('Selected hair window beyond safe end')
      main.append(clip);newhair.append({'clip':clip['id'],'segment_id':row['segment']['id'],'source':urllib.parse.urlparse(row['segment']['object_url']).path,'appearance_group':bid});durations[urllib.parse.urlparse(clip['props']['src']).path]=row['duration']
    else:
     for old in pieces:
      x=copy.deepcopy(old);ss=tf(x['from']);ee=tf(x['from']+x['durationInFrames']);x.update(**{'from':ss,'durationInFrames':ee-ss,'transitionIn':None,'transitionOut':None})
      media=out/'source-probes';media.mkdir(exist_ok=True);source=x['props']['src'];p=media/(hashlib.sha256(source.split('?')[0].encode()).hexdigest()[:16]+'.mp4')
      if not p.exists():cb.fetch_file(source,p)
      duration=float(json.loads(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(p)],text=True,capture_output=True,check=True).stdout)['format']['duration']);source_end=(x['props'].get('startFrom',0)+x['durationInFrames'])/fps
      if source_end>duration-.02:
       safe_start=max(0,math.floor(duration*fps)-x['durationInFrames']-3);x['props']['startFrom']=safe_start
      if 'f7b05199dacd459ba5d1f6bcceb2fd20' in source and (x['props']['startFrom']+x['durationInFrames'])/fps>2.0:raise RuntimeError('Spray unstable tail reintroduced')
      main.append(x);durations[urllib.parse.urlparse(source).path]=duration
   cursor=0
   for x in sorted(main,key=lambda x:x['from']):
    if x['from']!=cursor or x['durationInFrames']<20:raise RuntimeError('Picture gap/flash cut')
    cursor=x['from']+x['durationInFrames']
   if cursor!=total:raise RuntimeError('Incomplete picture timeline')
   others=[]
   for old in draft['clips']:
    if old['trackId'] in ['main','voiceover','overlay','caption-decor'] or old['id']=='cover':continue
    x=copy.deepcopy(old)
    if x['trackId']=='audio':continue
    x['from']=tf(x['from']);x['durationInFrames']=min(x['durationInFrames'],total-x['from'])
    if x['id'].startswith('brand-sticker-'):
     j=int(x['id'].rsplit('-',1)[1]);x['from']=aligned[j]['from'];x['durationInFrames']=min(x['durationInFrames'],aligned[j]['durationInFrames'])
    others.append(x)
   voiceclip=copy.deepcopy(oldvoices[0]);voiceclip.update(id='voice-continuous',**{'from':0,'durationInFrames':total});voiceclip['props'].update(src=assets['voice4'][bid]['src'],startFrom=0,playbackRate=1,volume=1)
   musicid,bgm_volume=musicmap[bid];music=json.loads((out/'music'/(musicid+'.json')).read_text());mp3=out/'music'/(musicid+'.mp3');duration=music['duration_seconds'];pcm=subprocess.run(['ffmpeg','-v','error','-i',str(mp3),'-t','60','-ar','8000','-ac','1','-f','f32le','-'],capture_output=True,check=True).stdout;y=np.frombuffer(pcm,dtype='<f4');seconds=len(y)//8000;window=math.ceil(total/fps)+1;energies=np.array([np.mean(y[i*8000:(i+1)*8000]**2) for i in range(seconds)]);maxstart=max(0,seconds-window);offset=max(range(maxstart+1),key=lambda k:float(np.mean(energies[k:k+window]))) if maxstart else 0
   musicclip=copy.deepcopy(next(x for x in draft['clips'] if x['trackId']=='audio'));musicclip.update(id='bgm',**{'from':0,'durationInFrames':total});musicclip['props'].update(src=music['url'],volume=bgm_volume,startFrom=offset*fps,loop=False,fadeIn=3,fadeOut=8)
   draft['clips']=main+aligned+[voiceclip,musicclip]+others;draft['composition']['durationInFrames']=total;voice=intents[bid]['body']['voice']['voice_type'];audio_evidence={'speed_ratio':1.4,'voice':voice,'bgm_id':musicid,'bgm_name':music['name'],'bgm_volume':bgm_volume,'bgm_start_seconds':offset,'native_tts_duration_ms':tts['audio']['duration_ms'],'source_durations':durations}
  draft['clips']=[x for x in draft['clips'] if x['trackId']!='caption-decor' and x['id']!='cover'];draft['tracks']=[x for x in draft['tracks'] if x['id']!='caption-decor'];accent,pale=palette[n];titles=[]
  template=assets['upper_template']
  for sid,text in titlemap[bid]:
   i=int(sid)-1;start=bounds[i];length=min(45,bounds[i+1]-start);params={**template['params'],'text':text,'accent':accent,'pale':pale,'variant':n,'top':96,'fontSize':52,'durationFrames':length}
   draft['clips'].append({'id':'upper-'+sid,'trackId':'effect','from':start,'durationInFrames':length,'component':'RemotionClip','transitionIn':None,'transitionOut':None,'motion':None,'props':{'code':template['code'],'params':params,'x':0,'y':0,'scale':1}});titles.append({'segment_id':sid,'template_id':template['id'],'params':params,'from':start,'frames':length})
  draft=fq.with_watermark(draft,cb.ai_spans(b['product_key'],draft)) if (cfg.get('watermark') or {}).get('add') and (cfg.get('watermark') or {}).get('ai_only') else draft
  cues=sorted([x for x in draft['clips'] if x['trackId']=='overlay'],key=lambda x:x['from']);styles=[{k:z for k,z in x['props'].items() if k not in ['text','font_url']} for x in cues]
  if not all(z==styles[0] for z in styles):raise RuntimeError('Mixed native subtitle style '+bid)
  if any(a['from']+a['durationInFrames']>b['from'] for a,b in zip(cues,cues[1:])):raise RuntimeError('Subtitle overlap '+bid)
  script=cb.api('GET',f"/video-scripts/{cut['script_id']}");wanted=copy.deepcopy(script);styleid='6ab0980646ba4a9c28773861';wanted['config']['subtitle_style'].update(style_ids=[styleid],font_size=36,animations=[])
  if n!=1:
   wanted['config']['tts']['speech_rate']=1.4;wanted['config']['bgm']={'mode':'ids','volume':bgm_volume,'props':{'ids':[musicmap[bid][0]],'categories':[]}}
  bytitle={z['segment_id']:z for z in titles}
  for seg in wanted['segments']:
   sid=seg['id'];seg['effect']={k:bytitle[sid][k] for k in ['template_id','params']} if sid in bytitle else None
   if n!=1 and seg.get('tts'):seg['tts']['speech_rate']=1.4
   pool=visuals.get(bid,[])
   if n!=1 and '头发顺滑' in next(z for z in b['segments'] if z['id']==sid)['visual_tags']:seg['visual']={'mode':'material_segment_ids','props':{'segment_ids':[z['segment']['id'] for z in pool]}}
  if [x['content'] for x in wanted['segments']]!=[x['content'] for x in script['segments']]:raise RuntimeError('Owned script copy changed')
  bpath=root/'briefs'/(bid+'.json');b['mix']={'bgm_volume':bgm_volume,'voice_volume':1,'instruction':'用户要求brief1口播大一点，另外四组BGM不寡淡并重做音色；按实测选择混音比例。'};bpath.write_text(json.dumps(b,ensure_ascii=False,indent=2))
  save(out/(key+'-plan.json'),{'before':cut,'draft':draft,'script_before':script,'script_intended':wanted,'actual_voice':voice,'native_style_id':styleid,'upper_titles':titles,'visual_assignments':newhair,'audio_evidence':audio_evidence})
  print(key,'native uniform subtitles; upper titles configured',len(titles),'new hair windows',len(newhair),'duration',round(draft['composition']['durationInFrames']/30,2),flush=True)

def apply(a,cfg):
 import footage_qc as fq
 from qc import cut_checks
 out=Path(a.run)/'revisions/声音节奏与人物重选';paths=sorted(out.glob('*-plan.json'));st=cb.State(a.run)
 if len(paths)!=5:raise RuntimeError('Expected exactly5 demo plans')
 digest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths};receipt=out/'render-dry-run.json'
 if not a.dry_run and (not receipt.exists() or json.loads(receipt.read_text())!=digest):raise RuntimeError('Missing exact5-demo dry-run')
 for path in paths:
  plan=json.loads(path.read_text());key=path.name[:-10];v=st.videos[key]
  if v.get('creative_rework_revision')==1:print(key,'already submitted; no duplicate');continue
  current=cb.api('GET',f"/video-cuts/{v['cut_id']}")
  if fq.stable_media(current['draft'])!=fq.stable_media(plan['before']['draft']):raise RuntimeError('Cut changed after plan: '+key)
  b=next(x for x in cb.load_briefs(a.run) if x['brief_id']==v['brief_id']);issues=cut_checks(b,plan['draft'],plan['actual_voice'])
  if issues:raise RuntimeError(str(issues))
  if a.dry_run:print(key,'将配置系统脚本及重渲1条：原生主字幕统一，上方花字3处，配音/BGM/素材按本组计划');continue
  script=plan['script_intended'];body={k:script[k] for k in ['product_id','title','video','config','vo_rewrite_prompt','segments']};cb.api('PUT',f"/video-scripts/{script['id']}",body);script_back=cb.api('GET',f"/video-scripts/{script['id']}")
  if any(script_back[k]!=body[k] for k in body):raise RuntimeError('System script readback differs')
  save(out/(key+'-script-after.json'),script_back)
  cb.api('PUT',f"/video-cuts/{v['cut_id']}",{'script_id':current['script_id'],'draft':plan['draft']});back=cb.api('GET',f"/video-cuts/{v['cut_id']}")
  if fq.stable_media(back['draft'])!=fq.stable_media(plan['draft']):raise RuntimeError('Cut readback differs '+key)
  save(out/(key+'-after.json'),back)
  payload={'spec':back['draft'],'options':{'codec':'h264','quality':'high','scale':1},'cut_id':v['cut_id'],'idempotency_key':'udb-'+v['cut_id']+'-creative-top01'}
  intent=out/(key+'-render-intent.json')
  if intent.exists():raise RuntimeError('Unresolved render submission; reconcile before retry')
  save(intent,payload);job=cb.api('POST','/render-jobs',payload,tries=1);save(out/(key+'-render.json'),job)
  v.update(render_job=job['job_id'],stage='rendering',ai_review=None,ai_reason=None,creative_rework_revision=1,voice=plan['actual_voice'],duration_s=round(back['draft']['composition']['durationInFrames']/30,2),qc_issues=issues,upper_title_configured=True);v.pop('object_key',None);st.save();print(key,'system script readback verified; render',job['job_id'],flush=True)
 if a.dry_run:save(receipt,digest);print('dry-run total:5 demo rerenders,0 bulk,5 owned system script updates')

def native_choices(a,cfg):
 out=Path(a.run)/'revisions/声音节奏与人物重选/native-flowers';out.mkdir(exist_ok=True)
 ids=['6afd05026af35ab34a0a5cb5','6afd05026af35ab34a0a5cb4','6afd05036af35ab34a0a5cc4','6afd05026af35ab34a0a5cb7','6afd05026af35ab34a0a5cb9']
 for sid in ids:
  d=cb.api('GET','/resource/remotion-effect-template/'+sid);save(out/(sid+'.json'),d);url=d.get('previewUrl');print(sid,d['name'],bool(url),flush=True)
  if url:
   video=out/(sid+'.preview');cb.fetch_file(url,video)
   subprocess.run(['ffmpeg','-v','error','-y','-ss','0.6','-i',str(video),'-frames:v','1',str(out/(sid+'.jpg'))],check=True)

def stock_titles(a,cfg):
 import footage_qc as fq
 out=Path(a.run)/'revisions/声音节奏与人物重选';dest=out/'native-final';dest.mkdir(exist_ok=True);st=cb.State(a.run)
 routes={1:['6afd05026af35ab34a0a5cb4','6afd05026af35ab34a0a5cb5','6afd05036af35ab34a0a5cc4'],2:['6afd05026af35ab34a0a5cb4','6afd05026af35ab34a0a5cb7','6afd05036af35ab34a0a5cc4'],3:['6afd05026af35ab34a0a5cb5','6afd05026af35ab34a0a5cb4','6afd05026af35ab34a0a5cb7'],4:['6afd05026af35ab34a0a5cb7','6afd05026af35ab34a0a5cb4','6afd05026af35ab34a0a5cb5'],5:['6afd05026af35ab34a0a5cb7','6afd05026af35ab34a0a5cb4','6afd05036af35ab34a0a5cc4']}
 for key,v in sorted(st.videos.items()):
  if v.get('stock_title_revision')==1:print(key,'already submitted');continue
  n=int(v['brief_id'][-2:]);before=cb.api('GET',f"/video-cuts/{v['cut_id']}");draft=copy.deepcopy(before['draft']);upper=sorted([x for x in draft['clips'] if x['id'].startswith('upper-')],key=lambda x:x['from']);script=cb.api('GET',f"/video-scripts/{before['script_id']}");wanted=copy.deepcopy(script);labels=[]
  for i,clip in enumerate(upper):
   native=json.loads((out/'native-flowers'/(routes[n][i]+'.json')).read_text());old=clip['props']['params'];params={**native.get('params',{}),'text':old['text'],'accent':old['accent'],'position':'top_center','safeTop':84,'safeBottom':100,'maxWidth':780,'scale':.62 if native['name']!='悬浮光晕' else .74,'x':0,'y':0,'plate':False}
   clip['props']['code']=native['code'];clip['props']['params']=params;sid=clip['id'].split('-')[1];segment=next(x for x in wanted['segments'] if x['id']==sid);segment['effect']={'template_id':native['id'],'params':params};labels.append({'segment':sid,'name':native['name'],'text':params['text']})
  for clip in draft['clips']:
   if clip['trackId']!='overlay':continue
   text=clip['props']['text']
   if '力士' in text:clip['props']['text_color']='#F1BFD8'
   elif any(x in text for x in ['玻尿酸','鲜花精油','柔顺','光泽','顺又亮','化雾','不粘腻','就是雾']):clip['props']['text_color']='#FFD16A'
  core=lambda d:[x for x in d['clips'] if not x['id'].startswith('upper-') and x['trackId']!='overlay']
  if core(draft)!=core(before['draft']):raise RuntimeError('Non-title/caption content changed')
  cue=lambda d:[(x['id'],x['from'],x['durationInFrames'],x['props']['text']) for x in d['clips'] if x['trackId']=='overlay']
  if cue(draft)!=cue(before['draft']):raise RuntimeError('Caption text/timing changed')
  if a.dry_run:save(dest/(key+'-plan.json'),{'before':before,'draft':draft,'script_before':script,'script_intended':wanted,'stock_titles':labels});print(key,'native library titles',labels,'; native subtitle color only');continue
  plan=json.loads((dest/(key+'-plan.json')).read_text())
  if fq.stable_media(before['draft'])!=fq.stable_media(plan['before']['draft']):raise RuntimeError('Title plan stale')
  save(dest/(key+'-before.json'),before);body={k:wanted[k] for k in ['product_id','title','video','config','vo_rewrite_prompt','segments']};cb.api('PUT',f"/video-scripts/{script['id']}",body);after_script=cb.api('GET',f"/video-scripts/{script['id']}")
  if any(after_script[k]!=body[k] for k in body):raise RuntimeError('Stock title script readback differs')
  save(dest/(key+'-script-after.json'),after_script)
  cb.api('PUT',f"/video-cuts/{v['cut_id']}",{'script_id':before['script_id'],'draft':draft});after=cb.api('GET',f"/video-cuts/{v['cut_id']}")
  if fq.stable_media(after['draft'])!=fq.stable_media(draft):raise RuntimeError('Stock title cut readback differs')
  save(dest/(key+'-after.json'),after);job=cb.api('POST','/render-jobs',{'spec':after['draft'],'options':{'codec':'h264','quality':'high','scale':1},'cut_id':v['cut_id'],'idempotency_key':'udb-'+v['cut_id']+'-stock-titles01'},tries=1);save(dest/(key+'-render.json'),job)
  v.update(render_job=job['job_id'],stage='rendering',ai_review=None,ai_reason=None,stock_title_revision=1,native_subtitle_style='6ab0980646ba4a9c28773861');v.pop('object_key',None);st.save();print(key,'Clipo stock title configs verified, render',job['job_id'],flush=True)

def layout2(a,cfg):
 import footage_qc as fq
 out=Path(a.run)/'revisions/声音节奏与人物重选/native-final/layout-corrected';out.mkdir(exist_ok=True);st=cb.State(a.run)
 for key,v in sorted(st.videos.items()):
  if v.get('stock_layout_revision')==2:continue
  before=cb.api('GET',f"/video-cuts/{v['cut_id']}");draft=copy.deepcopy(before['draft']);script=cb.api('GET',f"/video-scripts/{before['script_id']}");wanted=copy.deepcopy(script)
  for x in draft['clips']:
   if x['id'].startswith('upper-'):x['props']['params'].update(safeTop=.055,safeBottom=.10)
   if key!='1125-L01-V01' and x['trackId']=='sound_effects':x['props']['volume']=.72
  for seg in wanted['segments']:
   if seg.get('effect'):seg['effect']['params'].update(safeTop=.055,safeBottom=.10)
   if key!='1125-L01-V01' and isinstance(seg.get('sound_effects'),dict) and 'volume' in seg['sound_effects']:seg['sound_effects']['volume']=.72
  if a.dry_run:save(out/(key+'-plan.json'),{'before':before,'draft':draft,'script_before':script,'script_intended':wanted});print(key,'native safeTop比例.055=105.6px; 保留配音BGM镜头，校准音效峰值');continue
  plan=json.loads((out/(key+'-plan.json')).read_text())
  if fq.stable_media(before['draft'])!=fq.stable_media(plan['before']['draft']):raise RuntimeError('Layout plan stale')
  body={k:wanted[k] for k in ['product_id','title','video','config','vo_rewrite_prompt','segments']};cb.api('PUT',f"/video-scripts/{script['id']}",body);back_script=cb.api('GET',f"/video-scripts/{script['id']}")
  if any(back_script[k]!=body[k] for k in body):raise RuntimeError('Layout system script readback differs')
  save(out/(key+'-script-after.json'),back_script);cb.api('PUT',f"/video-cuts/{v['cut_id']}",{'script_id':before['script_id'],'draft':draft});after=cb.api('GET',f"/video-cuts/{v['cut_id']}")
  if fq.stable_media(after['draft'])!=fq.stable_media(draft):raise RuntimeError('Layout cut readback differs')
  save(out/(key+'-after.json'),after);job=cb.api('POST','/render-jobs',{'spec':after['draft'],'options':{'codec':'h264','quality':'high','scale':1},'cut_id':v['cut_id'],'idempotency_key':'udb-'+v['cut_id']+'-stocklayout02'},tries=1);save(out/(key+'-render.json'),job)
  v.update(render_job=job['job_id'],stage='rendering',ai_review=None,ai_reason=None,stock_layout_revision=2);v.pop('object_key',None);st.save();print(key,'position corrected; render',job['job_id'],flush=True)

def client_layout_review(a,cfg):
 from qc import cut_checks
 out=Path(a.run)/'revisions/声音节奏与人物重选/native-final/layout-corrected';st=cb.State(a.run);records=[]
 instruction='用户：先每组出一条demo给我看看；明天上午我来验收。最新明确要求：统一原生字幕是必须的，其他brief也一个道理，我要你在配置里加入上方的花字。'
 for key,v in sorted(st.videos.items()):
  c=cb.api('GET',f"/video-cuts/{v['cut_id']}");b=next(x for x in cb.load_briefs(a.run) if x['brief_id']==v['brief_id']);issues=cut_checks(b,c['draft'],v.get('voice'))
  if issues:raise RuntimeError('Mechanical QC failed '+str(issues))
  cues=sorted([x for x in c['draft']['clips'] if x['trackId']=='overlay'],key=lambda x:x['from']);styles=[{k:z for k,z in x['props'].items() if k not in ['text','text_color','font_url']} for x in cues]
  if not all(x==styles[0] for x in styles) or any('\n' in x['props']['text'] for x in cues) or any(x['from']+x['durationInFrames']>y['from'] for x,y in zip(cues,cues[1:])):raise RuntimeError('Native caption style/line QA failed')
  v['qc_issues']=issues
  if v.get('ai_review')=='reject':
   why=v.get('ai_reason') or ''
   if not ('字幕' in why and '两行' in why):raise RuntimeError('Not a layout-only rejection; cannot release '+why)
   record={'video':key,'cut_id':v['cut_id'],'output_object_key':v.get('object_key'),'ai_review':v['ai_review'],'ai_reason':why,'native_caption_single_row_verified':True,'upper_titles_independent':True,'instruction':instruction,'scope':'本轮demo仅供用户验收；不是AI通过，不是用户验收通过，不授权量产'};records.append(record)
   if not a.dry_run:v.update(review_waived=instruction+'（仅该版demo格式争议交用户验收）',user_review_pending=True,content_review_status='user_review_pending',layout_review_note=record)
  print(key,'native caption/mechanical QC passed; AI',v.get('ai_review'),'client review pending',flush=True)
 if not a.dry_run:save(out/'client-layout-review.json',records);st.save()

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['inventory','voices','samples','assets','gain4','plan','apply','native-choices','stock-titles','layout2','client-layout-review']);p.add_argument('--config',required=True);p.add_argument('--run',required=True);p.add_argument('--dry-run',action='store_true');a=p.parse_args();cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';{'inventory':inventory,'voices':voices,'samples':samples,'assets':assets,'gain4':gain4,'plan':plan,'apply':apply,'native-choices':native_choices,'stock-titles':stock_titles,'layout2':layout2,'client-layout-review':client_layout_review}[a.cmd](a,cfg)
if __name__=='__main__':main()
