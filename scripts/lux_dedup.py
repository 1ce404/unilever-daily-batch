#!/usr/bin/env python3
"""[INPUT] Current LUX demo cuts, original Clipo material IDs and user complaint about replayed slices.
[OUTPUT] Auditable no-reuse replacement plans, disjoint per-segment script pools, same-cut rerenders.
[POS] Current five demos only; never changes audio, captions, effects or duration.
"""
import argparse,copy,hashlib,json,math,subprocess,sys,urllib.parse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import load_config

def save(p,d):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,indent=2));return p

def source(c):return urllib.parse.urlparse(c['props']['src']).path

def mainclips(d):return sorted([x for x in d['clips'] if x['trackId']=='main'],key=lambda x:x['from'])

def audit(a,cfg):
 root=Path(a.run);out=root/'revisions/切片去重';out.mkdir(exist_ok=True);st=cb.State(root);global_seen={};report=[]
 for key,v in sorted(st.videos.items()):
  cut=cb.api('GET',f"/video-cuts/{v['cut_id']}");script=cb.api('GET',f"/video-scripts/{cut['script_id']}");save(out/(key+'-before.json'),{'cut':cut,'script':script});seen={};duplicates=[]
  for c in mainclips(cut['draft']):
   path=source(c)
   if path in seen:duplicates.append({'clip':c['id'],'same_as':seen[path],'source':path})
   seen[path]=c['id'];global_seen.setdefault(path,[]).append({'video':key,'clip':c['id']})
  report.append({'video':key,'main_clip_count':len(mainclips(cut['draft'])),'within_video_repeats':duplicates});print(key,'片内重复',duplicates,flush=True)
 cross={p:xs for p,xs in global_seen.items() if len({x['video'] for x in xs})>1};save(out/'audit.json',{'videos':report,'cross_demo_repeats':cross});print('跨demo同源',cross,flush=True)
 for pk in ['jingyou','spray']:
  rows=cb.list_segments(cfg['products'][pk]['clipo_product_id']);save(out/('library-'+pk+'.json'),rows)
  tags=['头发顺滑','CTA'] if pk=='jingyou' else ['头发顺滑','产品质地']
  for tag in tags:
   selected=[s for s in rows if tag in s.get('tags',[]) and s.get('review_result')!='reject' and urllib.parse.urlparse(s.get('object_url') or '').path not in global_seen]
   print('CANDIDATE',pk,tag,[(x['id'],x.get('name')) for x in selected],flush=True)

def expand_plan(a,cfg):
 out=Path(a.run)/'revisions/切片去重';st=cb.State(a.run);result={};sizes={'头发顺滑':16,'产品外观':12,'产品质地':12,'CTA':12}
 for bid in ['1125-L02','1125-L03','1125-L04','1125-L05']:
  b=next(x for x in cb.load_briefs(a.run) if x['brief_id']==bid);pk=b['product_key'];library=json.loads((out/('library-'+pk+'.json')).read_text());used=set();segments={}
  for seg in b['segments']:
   tag=seg['visual_tags'][0];want=10 if tag=='头发干枯毛躁' and seg['id']=='1' else 9 if tag=='头发干枯毛躁' else sizes[tag]
   candidates=[x for x in library if tag in x.get('tags',[]) and x.get('review_result')=='pass' and x.get('object_url') and x['object_url'].split('?')[0].rsplit('/',1)[-1] not in (cfg.get('banned_footage_files') or {}) and not any(w in (x.get('description') or '') for w in cb.banned_words_for(pk)) and not any(str(t).startswith('AI-') for t in x.get('tags',[])) and urllib.parse.urlparse(x['object_url']).path not in used and not any(w in (x.get('description') or '') for w in ['价格信息','二维码','字幕遮挡','黑屏'])]
   candidates.sort(key=lambda x:hashlib.sha256((bid+'|'+seg['id']+'|'+x['id']).encode()).hexdigest())
   picked=[]
   for x in candidates:
    path=urllib.parse.urlparse(x['object_url']).path
    if path in used:continue
    duration=(x.get('video_info') or {}).get('duration_ms',x.get('end_ms',0)-x.get('begin_ms',0))/1000
    if duration<.9:continue
    picked.append(x);used.add(path)
    if len(picked)==want:break
   if len(picked)<want:raise RuntimeError(bid+' S'+seg['id']+' expanded unique pool insufficient '+str(len(picked))+'/'+str(want))
   segments[seg['id']]={'tag':tag,'candidates':picked}
  result[bid]={'product_key':pk,'script_id':st.data['briefs'][bid]['script_id'],'segments':segments}
  print(bid,'独立候选数',[(sid,len(x['candidates'])) for sid,x in segments.items()],'unique',len(used),flush=True)
 save(out/'expanded-pools.json',result)

def inspect_pools(a,cfg):
 from concurrent.futures import ThreadPoolExecutor
 import numpy as np
 out=Path(a.run)/'revisions/切片去重';plan=json.loads((out/'expanded-pools.json').read_text());rows={}
 for b in plan.values():
  for seg in b['segments'].values():
   for x in seg['candidates']:rows[urllib.parse.urlparse(x['object_url']).path]=x
 media=out/'pool-media';media.mkdir(exist_ok=True)
 def one(item):
  key,meta=item;h=hashlib.sha256(key.encode()).hexdigest()[:20];p=media/(h+'.mp4');j=media/(h+'.json')
  if j.exists():return key,json.loads(j.read_text())
  if not p.exists():cb.fetch_file(meta['object_url'],p)
  probe=json.loads(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=width,height','-of','json',str(p)],capture_output=True,text=True,check=True).stdout);duration=float(probe['format']['duration']);start=.1 if duration<=3 else min(duration*.10,1.0);end=min(duration-.15,start+(4.3 if meta.get('ai_generated') else 2.4));window=end-start
  if window<.75:raise RuntimeError('Source too short '+meta['id'])
  q=subprocess.run(['ffmpeg','-hide_banner','-ss',str(start),'-t',str(window),'-i',str(p),'-vf','blackdetect=d=0.025:pix_th=0.10:pic_th=0.98','-an','-f','null','-'],capture_output=True,text=True,check=True)
  black=[z for z in q.stderr.splitlines() if 'black_start' in z]
  if black:raise RuntimeError('Pool source contains black frames '+meta['id']+' '+str(black))
  subprocess.run(['ffmpeg','-v','error','-y','-ss',str(start),'-i',str(p),'-t',str(window),'-vf','fps=2,scale=120:214,tile=4x1','-frames:v','1',str(media/(h+'.jpg'))],check=True)
  result={'segment_id':meta['id'],'file':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'duration':duration,'safe_start':round(start*30),'safe_end':math.floor(end*30),'sheet':str(media/(h+'.jpg')),'black_intervals':0};save(j,result);return key,result
 results={}
 with ThreadPoolExecutor(max_workers=6) as pool:
  for i,(key,data) in enumerate(pool.map(one,rows.items()),1):
   results[key]=data
   if i%20==0:print('素材窗口检查',i,'/',len(rows),flush=True)
 save(out/'pool-windows.json',results);print('Ready unique sources',len(results),flush=True)

def configure_pools(a,cfg):
 out=Path(a.run)/'revisions/切片去重';plan=json.loads((out/'expanded-pools.json').read_text());windows=json.loads((out/'pool-windows.json').read_text())
 for bid,row in plan.items():
  sid=row['script_id'];before=cb.api('GET',f'/video-scripts/{sid}');wanted=copy.deepcopy(before)
  for seg in wanted['segments']:
   candidates=row['segments'][seg['id']]['candidates']
   if any(urllib.parse.urlparse(x['object_url']).path not in windows for x in candidates):raise RuntimeError('Unchecked pool source')
   seg['visual']={'mode':'material_segment_ids','props':{'segment_ids':[x['id'] for x in candidates]}}
  wanted['config']['subtitle_style']['font_ids']=['6ae1d5b0b8882b89361e09d5']
  wanted['config']['tts']['voices']=[x for x in wanted['config']['tts']['voices'] if x['id'] in ['zh_female_shuangkuaisisi_uranus_bigtts','ICL_uranus_zh_female_kailangtingting_tob','zh_female_zhishuaiyingzi_uranus_bigtts','ICL_uranus_zh_female_tiexinguimi_tob']]
  if a.dry_run:save(out/(bid+'-pool-config-plan.json'),{'before':before,'wanted':wanted});print(bid,'will configure',[(x['id'],len(x['visual']['props']['segment_ids'])) for x in wanted['segments']]);continue
  record=json.loads((out/(bid+'-pool-config-plan.json')).read_text())
  if before['segments']!=record['before']['segments'] or before['config']!=record['before']['config']:raise RuntimeError('Owned script changed after pool dry-run')
  save(out/(bid+'-script-pool-before.json'),before);body={k:wanted[k] for k in ['product_id','title','video','config','vo_rewrite_prompt','segments']};cb.api('PUT',f'/video-scripts/{sid}',body);back=cb.api('GET',f'/video-scripts/{sid}')
  if any(back[k]!=body[k] for k in body):raise RuntimeError('Expanded script pool readback differs')
  save(out/(bid+'-script-pool-after.json'),back);print(bid,'expanded disjoint pools verified',flush=True)

def repair_plan(a,cfg):
 out=Path(a.run)/'revisions/切片去重';pools=json.loads((out/'expanded-pools.json').read_text());windows=json.loads((out/'pool-windows.json').read_text());used=set();snapshots={}
 for n in range(1,6):
  key=f'1125-L{n:02d}-V01';snapshots[key]=json.loads((out/(key+'-before.json')).read_text())
  used.update(source(c) for c in mainclips(snapshots[key]['cut']['draft']))
 changes={'1125-L02-V01':['3-1','6-1'],'1125-L03-V01':['5-0','5-1'],'1125-L05-V01':['2-0','2-1','4-0','5-0','5-1']};board=[]
 for key,ids in changes.items():
  cut=snapshots[key]['cut'];draft=copy.deepcopy(cut['draft']);rows=[]
  for clip in mainclips(draft):
   if clip['id'] not in ids:continue
   pool=pools[key.rsplit('-',1)[0]]['segments'][clip['id'].split('-')[0]]['candidates'];selected=None
   for x in pool:
    path=urllib.parse.urlparse(x['object_url']).path;w=windows[path]
    if path not in used and w['safe_end']-w['safe_start']>=clip['durationInFrames']:
     selected=(x,path,w);break
   if selected is None:raise RuntimeError('No unique safe replacement '+key+' '+clip['id'])
   x,path,w=selected;used.add(path);clip['props'].update(src=x['object_url'],startFrom=w['safe_start'],playbackRate=1,alt=x.get('description',''));clip['props'].pop('loop',None)
   rows.append({'clip':clip['id'],'segment_id':x['id'],'safe_start':w['safe_start'],'sheet':w['sheet'],'durationInFrames':clip['durationInFrames']});board.append((key+' '+clip['id'],w['sheet']))
  paths=[source(c) for c in mainclips(draft)]
  if len(paths)!=len(set(paths)):raise RuntimeError('Repeated source remains '+key)
  save(out/(key+'-repair-plan.json'),{'before':cut,'draft':draft,'replacements':rows});print(key,'replacement count',len(rows),flush=True)
 from PIL import Image,ImageDraw
 im=Image.new('RGB',(520,len(board)*140),'white');draw=ImageDraw.Draw(im)
 for i,(label,path) in enumerate(board):
  shot=Image.open(path);shot.thumbnail((480,112));im.paste(shot,(10,i*140+24));draw.text((10,i*140+5),label,fill='black')
 im.save(out/'replacement-review.jpg');print('replacement-review.jpg',flush=True)

def repair_apply(a,cfg):
 from qc import cut_checks
 out=Path(a.run)/'revisions/切片去重';st=cb.State(a.run)
 for key in ['1125-L02-V01','1125-L03-V01','1125-L05-V01']:
  plan=json.loads((out/(key+'-repair-plan.json')).read_text());v=st.videos[key];now=cb.api('GET',f"/video-cuts/{v['cut_id']}");draft=plan['draft']
  canonical=lambda d: [(x['id'],source(x),x['props'].get('startFrom')) for x in mainclips(d)]
  if canonical(now['draft']) not in [canonical(plan['before']['draft']),canonical(draft)]:raise RuntimeError('Source changed outside repair '+key)
  b=next(x for x in cb.load_briefs(a.run) if x['brief_id']==v['brief_id']);issues=cut_checks(b,draft,v.get('voice'))
  if issues:raise RuntimeError(str(issues))
  if a.dry_run:print(key,'9-shot dedup plan preserves all non-main clips; QC pass');continue
  receipt=out/(key+'-render-receipt.json')
  if receipt.exists():print(key,'already submitted');continue
  if canonical(now['draft'])!=canonical(draft):
   cb.api('PUT',f"/video-cuts/{v['cut_id']}",{'script_id':now['script_id'],'draft':draft})
  back=cb.api('GET',f"/video-cuts/{v['cut_id']}");cb.check_readback(draft,back['draft'],False)
  oldother=[x for x in plan['before']['draft']['clips'] if x['trackId']!='main'];newother=[x for x in draft['clips'] if x['trackId']!='main']
  if oldother!=newother:raise RuntimeError('Non-main clips altered')
  job=cb.api('POST','/render-jobs',{'spec':back['draft'],'options':{'codec':'h264','quality':'high','scale':1},'cut_id':v['cut_id'],'idempotency_key':'lux-dedup-'+v['cut_id']})
  save(receipt,job);save(out/(key+'-after.json'),back);v.update(render_job=job['job_id'],stage='rendering',ai_review=None,ai_reason=None,qc_issues=[],dedup_replacements=plan['replacements'])
  for field in ['object_key','review_waived','layout_review_note','user_review_pending']:v.pop(field,None)
  st.save();print(key,'render submitted',job['job_id'],flush=True)

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['audit','expand-plan','inspect-pools','configure-pools','repair-plan','repair-apply']);p.add_argument('--config',required=True);p.add_argument('--run',required=True);p.add_argument('--dry-run',action='store_true');a=p.parse_args();cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';{'audit':audit,'expand-plan':expand_plan,'inspect-pools':inspect_pools,'configure-pools':configure_pools,'repair-plan':repair_plan,'repair-apply':repair_apply}[a.cmd](a,cfg)
if __name__=='__main__':main()
