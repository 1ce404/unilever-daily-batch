#!/usr/bin/env python3
"""Clipo-native AI set + immutable real-pack pixels. No generative relabeling or local final render.
Plan -> dry-run receipt -> one representative cloud render -> inspected pilot -> remaining renders.
Stage output is an AI-material candidate, never a user approval or finished ad.
"""
import argparse,copy,hashlib,json,sys,time,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import clipo_batch as cb,ai_tags
from common import load_config
from effects import exclusive

def save(p,d):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix('.pending');tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2));tmp.replace(p)
def digest(d):return hashlib.sha256(json.dumps(d,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def filehash(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

CODE='''import React from 'react';
import {AbsoluteFill,Img} from 'remotion';
export const Composition=(props)=>{
 const p={...props,...(props.params||{})};const b=p.bounds;const k=p.placement.height/b[3];
 const w=b[2]*k,h=b[3]*k;const left=p.placement.center_x-w/2,top=p.placement.bottom-h;
 const polygon='polygon('+p.polygon.map(q=>`${q[0]/p.source_width*100}% ${q[1]/p.source_height*100}%`).join(',')+')';
 return <AbsoluteFill style={{pointerEvents:'none'}}>
 <div style={{position:'absolute',left:left-w*.12,top:p.placement.bottom-h*.022,width:w*1.24,height:h*.025,borderRadius:'50%',background:'rgba(71,48,25,0.27)',filter:'blur(5px)'}}/>
 <div style={{position:'absolute',left,top,width:w,height:h,overflow:'hidden'}}>
 <Img src={p.src} style={{position:'absolute',left:-b[0]*k,top:-b[1]*k,width:p.source_width*k,height:p.source_height*k,maxWidth:'none',clipPath:polygon}}/>
 </div></AbsoluteFill>;
};'''

def validate(item):
 p=Path(item['image']);
 if not p.is_file() or filehash(p)!=item['image_sha256']:raise RuntimeError('Real-pack image bytes changed')
 w,h=item['source_width'],item['source_height'];x,y,bw,bh=item['bounds']
 if min(w,h,bw,bh)<=0 or x<0 or y<0 or x+bw>w or y+bh>h:raise RuntimeError('Invalid pack bounds')
 pts=item['polygon']
 if len(pts)<6 or any(not(x<=qx<=x+bw and y<=qy<=y+bh) for qx,qy in pts):raise RuntimeError('Invalid product-only mask')
 if not item.get('source_segment_id') or not item.get('source_frame_time'):raise RuntimeError('Missing real image provenance')
 place=item['placement']
 if not(0<place['height']<=1000 and 0<place['bottom']<=1200 and 0<place['center_x']<720):raise RuntimeError('Invalid placement')
 if not item['final_tag'].startswith('AI-'):raise RuntimeError('Composite must keep AI lineage')

def upload(path,out):
 p=Path(path);sha=filehash(p);receipt=out/('upload-'+sha+'.json')
 if receipt.exists():return json.loads(receipt.read_text())['object_key']
 tok=cb.api('POST','/storage/upload-tokens',{'filename':p.name,'content_type':'image/png','content_length':p.stat().st_size,'purpose':'material'},tries=1)
 req=urllib.request.Request(tok['upload_url'],data=p.read_bytes(),method='PUT',headers=dict(tok.get('headers') or {},**{'Content-Type':'image/png'}));urllib.request.urlopen(req,timeout=180).read();save(receipt,{'object_key':tok['object_key'],'sha256':sha});return tok['object_key']

def spec(item,bg,src):
 params={k:copy.deepcopy(item[k]) for k in ['bounds','polygon','source_width','source_height','placement']};params['src']=src
 return {'composition':{'id':'pack-scene-'+item['key'],'name':item['key'],'width':720,'height':1280,'fps':30,'durationInFrames':120},'tracks':[{'id':'main','name':'AI布景','enabled':True},{'id':'effect','name':'真实包装原像素','enabled':True}], 'clips':[{'id':'background','trackId':'main','from':0,'durationInFrames':120,'component':'VideoClip','props':{'src':bg,'startFrom':0,'volume':0,'muted':True,'fit':'cover','playbackRate':1,**item.get('background_transform',{})}},{'id':'real-pack','trackId':'effect','from':0,'durationInFrames':120,'component':'RemotionClip','props':{'code':CODE,'params':params,'x':0,'y':0,'scale':1}}]}

def lookup_segment(pid,sid):
 matches=[s for s in cb.list_segments(pid) if s['id']==sid]
 if len(matches)!=1:raise RuntimeError('Expected one source segment in product library')
 return matches[0]

def selected(plan,key):
 rows=[x for x in plan['items'] if not key or x['key']==key]
 if not rows:raise RuntimeError('No selected items')
 return rows

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['render','poll','register','inspect']);p.add_argument('--config',required=True);p.add_argument('--run',required=True);p.add_argument('--plan',required=True);p.add_argument('--key');p.add_argument('--dry-run',action='store_true');p.add_argument('--review-note');a=p.parse_args()
 cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';plan=json.loads(Path(a.plan).read_text());rows=selected(plan,a.key);out=Path(a.run)/'aigc/native-product-scene';out.mkdir(exist_ok=True,parents=True)
 if not plan.get('authorization'):raise RuntimeError('Missing user instruction')
 for item in rows:validate(item)
 with exclusive(out):
  if a.cmd=='inspect':
   if not a.key or not a.review_note:raise RuntimeError('One actually-viewed key and inspection note required')
   item=rows[0];status=json.loads((out/(item['key']+'-status.json')).read_text());media=out/(item['key']+'.mp4')
   if status['status']!='success' or not media.exists():raise RuntimeError('No completed local composite')
   save(out/(item['key']+'-inspection.json'),{'passed':True,'item_hash':digest(item),'image_sha256':item['image_sha256'],'code_hash':hashlib.sha256(CODE.encode()).hexdigest(),'render_job_id':status['job_id'],'media_sha256':filehash(media),'reviewer':'Agent','review_note':a.review_note,'human_approved':False});print('Agent inspection recorded, human approval pending',item['key']);return
  if a.cmd=='render':
   from sync_skill import assert_current
   if not a.dry_run:assert_current(Path(__file__).resolve().parents[1])
   receipt=out/('dryrun-'+digest([x['key'] for x in rows])[:12]+'.json');payload={'plan_hash':digest(plan),'keys':[x['key'] for x in rows],'code_hash':hashlib.sha256(CODE.encode()).hexdigest()}
   if a.dry_run:save(receipt,{**payload,'at':time.time()});print('DRY-RUN cloud AI material composites',len(rows),payload['keys']);return
   prev=json.loads(receipt.read_text()) if receipt.exists() else {}
   if any(prev.get(k)!=v for k,v in payload.items()) or time.time()-prev.get('at',0)>3600:raise RuntimeError('Need matching dry-run')
   for item in rows:
    r=out/(item['key']+'-render.json')
    if r.exists():print(item['key'],'existing render',json.loads(r.read_text())['job_id']);continue
    if not item.get('pilot'):
     pilot=out/(item['pilot_key']+'-inspection.json')
     check=json.loads(pilot.read_text()) if pilot.exists() else {}
     if not check.get('passed') or check.get('image_sha256')!=item['image_sha256'] or check.get('code_hash')!=hashlib.sha256(CODE.encode()).hexdigest():raise RuntimeError('Actual same-product pilot inspection required')
    bg=lookup_segment(cfg['products'][item['product']]['clipo_product_id'],item['background_segment_id']);src=cb.api('POST','/storage/download-tokens',{'object_key':upload(item['image'],out)})['download_url']
    draft=spec(item,bg['object_url'],src);save(out/(item['key']+'-draft.json'),draft)
    body={'spec':draft,'options':{'codec':'h264','quality':'high','scale':1},'idempotency_key':'pack-'+digest({'item':item,'code':CODE})[:54]};save(out/(item['key']+'-intent.json'),body)
    result=cb.api('POST','/render-jobs',body,tries=1);save(r,result);print(item['key'],'render',result['job_id'],flush=True)
  elif a.cmd=='poll':
   for item in rows:
    r=out/(item['key']+'-render.json')
    if not r.exists():continue
    d=cb.api('GET','/render-jobs/'+json.loads(r.read_text())['job_id']);save(out/(item['key']+'-status.json'),d);print(item['key'],d['status'],d.get('error_message',''),flush=True)
    if d['status']=='success':cb.fetch_file(d['download_url'],out/(item['key']+'.mp4'))
  else:
   from sync_skill import assert_current
   if not a.dry_run:assert_current(Path(__file__).resolve().parents[1])
   if a.dry_run:print('DRY-RUN register task-owned AI composites',len(rows));save(out/'register-dryrun.json',{'keys':[x['key'] for x in rows],'plan_hash':digest(plan),'at':time.time()});return
   dry=json.loads((out/'register-dryrun.json').read_text())
   if dry['keys']!=[x['key'] for x in rows] or dry['plan_hash']!=digest(plan) or time.time()-dry['at']>3600:raise RuntimeError('Need matching register dry-run')
   for item in rows:
    r=out/(item['key']+'-registered.json')
    if r.exists():print(item['key'],'existing',json.loads(r.read_text())['id']);continue
    inspection=out/(item['key']+'-inspection.json')
    if not inspection.exists() or not json.loads(inspection.read_text()).get('passed'):raise RuntimeError('Actual composite inspection required')
    check=json.loads(inspection.read_text());status=json.loads((out/(item['key']+'-status.json')).read_text())
    if check.get('item_hash')!=digest(item) or check.get('render_job_id')!=status['job_id'] or check.get('media_sha256')!=filehash(out/(item['key']+'.mp4')):raise RuntimeError('Inspection does not match current actual output')
    pid=cfg['products'][item['product']]['clipo_product_id']
    if status['status']!='success':raise RuntimeError('Render not ready')
    src=out/(item['key']+'.mp4');tok=cb.api('POST','/storage/upload-tokens',{'filename':src.name,'content_type':'video/mp4','content_length':src.stat().st_size,'purpose':'material'},tries=1)
    req=urllib.request.Request(tok['upload_url'],data=src.read_bytes(),method='PUT',headers=dict(tok.get('headers') or {},**{'Content-Type':'video/mp4'}));urllib.request.urlopen(req,timeout=300).read()
    intent=out/(item['key']+'-register-intent.json')
    if intent.exists():raise RuntimeError('Uncertain prior register outcome; reconcile, do not duplicate')
    body={'object_key':tok['object_key'],'ai_generated':True};save(intent,{'body':body,'image_sha256':item['image_sha256'],'background_segment_id':item['background_segment_id'],'render_job_id':status['job_id']});d=cb.api('POST','/products/'+pid+'/segments',body,tries=1);save(r,d)
    ai_tags.add_product_tags(pid,[item['final_tag']]);ai_tags.set_tags(d['id'],[item['final_tag']]);back=lookup_segment(pid,d['id']);save(out/(item['key']+'-readback.json'),back)
    if back.get('tags')!=[item['final_tag']] or not back.get('ai_generated'):raise RuntimeError('AI lineage/tag readback mismatch')
    print(item['key'],'registered',d['id'],flush=True)
if __name__=='__main__':main()
