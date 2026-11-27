#!/usr/bin/env python3
"""Retiming an owned demo to one new whole voice, with exact reviewed local corrections.
No random source selection, no new AI material, no generated subtitles/timing guesses.
"""
import argparse,copy,json,math,sys,hashlib
from pathlib import Path
from urllib.parse import urlparse
sys.path.insert(0,str(Path(__file__).resolve().parent))
import clipo_batch as cb,task_demo as td
from common import load_config,approval_problem,aigc_problem
from product_scene import validate_code,upload
from task_caption_repair import reflow

DISSOLVE="""import React from 'react';import {AbsoluteFill,Img,useCurrentFrame,interpolate} from 'remotion';export const Composition=(props)=>{const p={...props,...props.params};const f=useCurrentFrame();return <AbsoluteFill style={{opacity:interpolate(f,[0,7],[1,0],{extrapolateRight:'clamp'}),pointerEvents:'none'}}><Img src={p.src} style={{width:'100%',height:'100%',objectFit:'cover'}}/></AbsoluteFill>};"""

def retime(draft,oldtts,newtts,b,cfg):
 result=copy.deepcopy(draft);fps=result['composition']['fps'];oldtotal,oldbounds,_=td.timing(oldtts,b['segments'],fps);total,bounds,_=td.timing(newtts,b['segments'],fps)
 if oldtotal!=draft['composition']['durationInFrames']:raise RuntimeError('Base whole voice/composition changed')
 def tf(frame):
  i=next((i for i in range(len(oldbounds)-1) if frame<oldbounds[i+1]),len(oldbounds)-2)
  return min(total,bounds[i]+round((frame-oldbounds[i])*(bounds[i+1]-bounds[i])/(oldbounds[i+1]-oldbounds[i])))
 for c in result['clips']:
  start,end=c['from'],c['from']+c['durationInFrames']
  if c['id']=='1-0' or c['id']=='snowkiss-soft-snow' or c['trackId']=='watermark':continue
  if c['trackId']=='voiceover':c.update(**{'from':0,'durationInFrames':total});c['props'].update(src=newtts['audio_url'],startFrom=0,playbackRate=1,volume=1);continue
  if c['trackId']=='audio':c.update(**{'from':0,'durationInFrames':total});continue
  c['from']=tf(start);c['durationInFrames']=max(1,tf(end)-c['from'])
  if c['id']=='2-1':c['from']=150;c['durationInFrames']=bounds[2]-150
  if c['trackId']=='sticker':c.update(**{'from':bounds[-2],'durationInFrames':total-bounds[-2]})
  if c['durationInFrames']<=0:raise RuntimeError('New voice is too short for confirmed opening')
 result['composition']['durationInFrames']=total
 chunks=[c['props']['text'] for c in sorted(draft['clips'],key=lambda c:c['from']) if c['trackId']=='overlay']
 result=reflow(result,newtts,[s['text'] for s in b['segments']],cfg,chunks=chunks)
 return result,bounds

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--run',required=True);p.add_argument('--brief',required=True);p.add_argument('--revision',required=True);p.add_argument('--plan',required=True);a=p.parse_args();cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';root=Path(a.run);plan=td.load(a.plan);st=cb.State(root);key=a.brief+'-V01';v=st.videos[key];b=next(x for x in cb.load_briefs(root) if x['brief_id']==a.brief)
 if not plan.get('instruction') or plan.get('key')!=key or plan.get('expected_render_job')!=v['render_job'] or approval_problem(b) or aigc_problem(b):raise RuntimeError('Exact current demo revision authorization required')
 out=root/'demo-native'/key/a.revision;tts=td.load(out/'tts.json')
 if (out/'gain-upload.json').exists():tts['audio_url']=cb.api('POST','/storage/download-tokens',{'object_key':td.load(out/'gain-upload.json')['object_key']})['download_url']
 cut=cb.api('GET','/video-cuts/'+v['cut_id']);draft,bounds=retime(cut['draft'],td.load(plan['base_tts']),tts,b,cfg)
 remove=set(plan['remove_effect_ids']+plan.get('remove_sound_ids',[]))
 if any(c['id'] in remove and c['trackId'] not in ['effect','sound_effects'] for c in draft['clips']):raise RuntimeError('Removal would alter protected non-effect track')
 draft['clips']=[c for c in draft['clips'] if c['id'] not in remove]
 library=cb.list_segments(cfg['products'][b['product_key']]['clipo_product_id']);byid={m['id']:m for m in library};bypath={urlparse(m.get('object_url') or '').path:m for m in library};fps=draft['composition']['fps'];used=[]
 for c in draft['clips']:
  if c['trackId']!='main':continue
  rule=plan.get('clips',{}).get(c['id'])
  if rule:
   m=byid[rule['segment_id']];end=rule['startFrom']+c['durationInFrames']
   if m.get('ai_generated') or m.get('review_result')!='pass' or end>rule['safe_end_frame']:raise RuntimeError('New voice exceeds inspected replacement window')
   c['props'].update(src=m['object_url'],alt=m.get('description',''),startFrom=rule['startFrom'])
  m=bypath[urlparse(c['props']['src']).path];available=math.floor((m['end_ms']-m['begin_ms'])*fps/1000)
  if c['props']['startFrom']+c['durationInFrames']>available:raise RuntimeError('New voice exceeds retained source '+c['id'])
  used.append(urlparse(c['props']['src']).path)
 if len(used)!=len(set(used)):raise RuntimeError('Repeated real source in revised demo')
 if plan.get('dissolve_image'):
  f=Path(plan['dissolve_image'])
  if hashlib.sha256(f.read_bytes()).hexdigest()!=plan['dissolve_sha256']:raise RuntimeError('Match frame changed')
  obj=upload(f,out);src=cb.api('POST','/storage/download-tokens',{'object_key':obj})['download_url'];validate_code(DISSOLVE)
  draft['clips'].append({'id':'opening-match-dissolve','trackId':'effect','from':150,'durationInFrames':8,'component':'RemotionClip','props':{'code':DISSOLVE,'params':{'src':src},'x':0,'y':0,'scale':1}})
 from qc import cut_checks
 issues=cut_checks(b,draft,td.load(out/'voice-intent.json')['voice']['voice_type']);td.save(out/'revision-before.json',cut);td.save(out/'draft.json',draft);td.save(out/'revision-plan.json',plan);td.save(out/'preflight.json',{'issues':issues,'draft_hash':td.digest(draft)})
 if issues:raise RuntimeError('Revised demo gate '+str(issues))
 print(key,'same sources retimed to actual new whole voice',bounds,'extra flashes removed; no AI generation')

if __name__=='__main__':main()
