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
 keys=[a.brief+f'-V{i:02d}' for i in range(2,b['quantity']+1)]
 save(root/(a.brief+'-budget.json'),{'at':time.time(),'keys':keys,'quantity':n,'whole_tts':n,'speed':1.4,'authorization':b['demo_approval'],'source':'full current tag libraries, no fixed real pool'})
 print('DRY-RUN',a.brief,'new videos',n,'whole TTS',n,'native voice/style/music choices',len(script['config']['tts']['voices']),len(script['config']['subtitle_style']['style_ids']),len(script['config']['bgm']['props']['ids']),flush=True)

def prepare(api_module,st,b,v,key,cut,d):
 td.cb=api_module;cb.CFG=api_module.CFG;cb.BASE=api_module.BASE;cfg=api_module.CFG;root=st.path.parent.parent;assets=root/'task-bulk';plan=read(assets/(b['brief_id']+'-budget.json'))
 validate_budget(plan,key,time.time())
 script=read(assets/(b['brief_id']+'-script.json'));library=read(assets/(b['brief_id']+'-library.json'));work=assets/key;work.mkdir(exist_ok=True);i=int(key.rsplit('-V',1)[1])-2
 voice=script['config']['tts']['voices'][i%len(script['config']['tts']['voices'])];spoken=''.join(s['text'] for s in b['segments']);body={'text':spoken,'voice':{'platform':voice['platform'],'voice_type':voice['voice_type'],'resource_id':voice['config']['resource_id'],'speed_ratio':1.4},'force':False};receipt=work/'tts.json';intent=work/'tts-intent.json'
 if receipt.exists():
  if read(intent)!=body:raise RuntimeError('Bulk TTS cached input changed')
  tts=read(receipt)
 else:
  if intent.exists():raise RuntimeError('Uncertain TTS outcome; no duplicate submit')
  if sum(1 for p in assets.glob(b['brief_id']+'-V*/tts-intent.json'))>=plan['whole_tts']:raise RuntimeError('TTS budget exhausted')
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
 for s in rows['segments']:
  hints=(cfg.get('demo_caption_chunks',{}).get(b['brief_id'],{})).get(s['id'])
  if hints:s['caption_chunks']=hints
 draft,evidence=td.build(rows,{**cut,'draft':d},tts,script,library,cfg,key)
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
 got=[s['text'] for s in b['segments']];prod=cfg['products'][b['product_key']];issues=[m for t in got for level,m in lint_text(t,cfg,prod) if level=='error']+[m for level,m in lint_script(got,voiced(b['segments']),b,cfg,prod) if level=='error']
 if issues:raise RuntimeError('Approved copy invalid '+str(issues))
 v.update(voice=voice['voice_type'],texts=got,copy_issues=[],voice_cmd=False,duration_s=draft['composition']['durationInFrames']/draft['composition']['fps'],native_variant={'subtitle_style_id':styleid,'bgm_id':musicid,'upper_templates':[effects[z]['id'] for z in patterns[i%len(patterns)]],'voice_gain_db':asset['gain_db']})
 save(work/'prepared.json',{'draft':draft,'evidence':evidence,'variant':v['native_variant'],'voice':v['voice']});return draft

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['budget']);p.add_argument('--config',required=True);p.add_argument('--run',required=True);p.add_argument('--brief',required=True);p.add_argument('--quantity',type=int,required=True);p.add_argument('--dry-run',action='store_true');a=p.parse_args();cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';budget(a,cfg)
if __name__=='__main__':main()
