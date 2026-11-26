#!/usr/bin/env python3
"""Plan/register/download explicitly delegated one-demo previews without QC-pass claims."""
import argparse,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import load_config
from demo_authorization import permits_preview
from delivery_share import verify_share

def save(p,d):
 p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,indent=2))

def checked_preview_video(brief,video,job):
 if not permits_preview(brief):raise RuntimeError('No current demo-only production permission')
 if video.get('brief_id')!=brief['brief_id'] or video.get('stage')!='done' or not video.get('user_review_pending'):raise RuntimeError('Not a completed customer-pending demo')
 if not all(video.get(k) for k in ['cut_id','render_job','object_key']) or not job.get('script_id'):raise RuntimeError('Missing current output identity')
 if video.get('customer_rework_required'):raise RuntimeError('Customer-rejected cut cannot be released')
 return {'cut_id':video['cut_id'],'script_id':job['script_id'],'expected_output_object_key':video['object_key'],'product_key':brief['product_key'],'video_key':brief['brief_id']+'-V01','render_job':video['render_job']}

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['plan','finish']);p.add_argument('--config',required=True);p.add_argument('--run',required=True);p.add_argument('--out',required=True);p.add_argument('--download-to');a=p.parse_args();cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';st=cb.State(a.run);bs=cb.load_briefs(a.run);out=Path(a.out);planpath=out/'preview-plan.json'
 if a.cmd=='plan':
  entries=[]
  for b in bs:
   key=b['brief_id']+'-V01';v=st.videos.get(key) or {};e=checked_preview_video(b,v,st.data.get('briefs',{}).get(b['brief_id'],{}));e['title']=cb.delivery_name(cfg,b,bs)+'-demo待检查';e['authorization']=b['demo_production_authorization']['instruction'];entries.append(e)
  if not entries:raise RuntimeError('No preview entries')
  save(planpath,{'instruction':entries[0]['authorization'],'group_name':'TZ-'+bs[0]['date'][2:].replace('-','')+'-'+cfg.get('brand_tag',cfg['brand'])+'-demo待检查','entries':entries,'status':'customer_review_pending; no QC/approval pass claimed'})
  print('Prepared',len(entries),'one-demo exports',planpath);return
 plan=json.loads(planpath.read_text());receipt=json.loads((out/'links.json').read_text());wanted=[]
 for b in bs:
  key=b['brief_id']+'-V01';e=checked_preview_video(b,st.videos[key],st.data['briefs'][b['brief_id']]);prior=next(x for x in plan['entries'] if x['video_key']==key)
  if any(e[k]!=prior[k] for k in e):raise RuntimeError('Preview output changed since export plan')
  sh=receipt['shares'][e['cut_id']];back=cb.api('GET','/video-shares/'+sh['id']);verify_share(back,{'title':prior['title'],'videos':[{'video_object_key':e['expected_output_object_key']}]});wanted.append(sh['id'])
  st.data.setdefault('shares',{})[b['brief_id']]={'id':sh['id'],'title':prior['title'],'videos':[key],'cuts':[e['cut_id']],'at':time.strftime('%Y-%m-%d %H:%M'),'mode':'customer_pending_demo'}
 group=cb.api('GET','/video-share-groups/'+receipt['group']['id'])
 if sorted(x['id'] for x in group['shares'])!=sorted(wanted):raise RuntimeError('Preview group has unexpected or missing shares')
 st.data['share_group']=receipt['group'];st.data['preview_delivery']={'status':'customer_review_pending','instruction':plan['instruction'],'videos':len(wanted),'plan':str(planpath.resolve())};st.save();save(out/'verified-preview-delivery.json',{'verified':True,'count':len(wanted),'group':receipt['group'],'entries':plan['entries'],'quality_claim':'none; customer review pending'})
 print('Preview group',len(wanted),cb.BASE.replace('/api/v1','')+'/share/group/'+receipt['group']['id'],flush=True)
 if a.download_to:cb.cmd_download(cfg,a.run,argparse.Namespace(out=a.download_to,dry_run=False))
if __name__=='__main__':main()
