#!/usr/bin/env python3
"""Rendered-video black/silence/peak checks and per-shot visual review evidence; no fake visual pass."""
import argparse,hashlib,json,math,re,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import load_config
from quality_contract import contract

def inspect(video,draft,out,rules):
 out=Path(out);out.mkdir(parents=True,exist_ok=True);video=Path(video);frames=[]
 p=subprocess.run(['ffmpeg','-hide_banner','-i',str(video),'-vf','crop=iw*.78:ih*.86:iw*.11:ih*.05,blackdetect=d=0.035:pix_th=0.1:pic_th=0.98','-af','astats=metadata=1:reset=0','-f','null','-'],capture_output=True,text=True,check=True)
 black=[{'start':float(a),'end':float(b),'duration':float(c)} for a,b,c in re.findall(r'black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)',p.stderr) if float(c)>=rules.get('max_black_interval_seconds',.035)]
 peaks=re.findall(r'Peak level dB: ([-\d.inf]+)',p.stderr);peak=max((float(x) for x in peaks),default=-math.inf);fps=draft['composition'].get('fps',30)
 # Every source window gets a beginning/middle/end view for empty tails, shake and overlays.
 for i,clip in enumerate(sorted([x for x in draft['clips'] if x.get('trackId')=='main'],key=lambda x:x['from'])):
  for j,fraction in enumerate([.12,.5,.88]):
   t=(clip['from']+clip['durationInFrames']*fraction)/fps;dest=out/f'shot-{i:02d}-{j}.jpg';subprocess.run(['ffmpeg','-v','error','-y','-ss',str(t),'-i',str(video),'-frames:v','1','-vf','scale=180:-2',str(dest)],check=True);frames.append({'clip':clip['id'],'time':t,'file':str(dest)})
 evidence={'video':str(video.resolve()),'sha256':hashlib.sha256(video.read_bytes()).hexdigest(),'draft_hash':hashlib.sha256(json.dumps(draft,sort_keys=True).encode()).hexdigest(),'black_intervals':black,'peak_dbfs':peak if math.isfinite(peak) else None,'technical_pass':not black and math.isfinite(peak) and -60<peak<=0,'visual_review':'pending','visual_review_required':['空镜拖长/产品出画','抖动/黑屏','字幕/花字挡脸头发产品','音画词级同步','包装与品牌适配'],'frames':frames}
 (out/'media-qc.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2));return evidence

def main():
 from quality_contract import cut_facts
 import clipo_batch as cb
 p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--video');p.add_argument('--draft');p.add_argument('--out');p.add_argument('--run');p.add_argument('--key');p.add_argument('--reviewer');p.add_argument('--review-note');a=p.parse_args();cfg=load_config(a.config);rules=contract(cfg)
 if a.run and a.key:
  cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';st=cb.State(a.run);v=st.videos[a.key];out=Path(a.run)/'qc'/a.key;out.mkdir(parents=True,exist_ok=True)
  if a.review_note:
   e=v.get('media_qc') or {}
   if not a.reviewer or not e.get('technical_pass') or e.get('render_job')!=v.get('render_job') or e.get('output_object_key')!=v.get('object_key'):raise RuntimeError('不能确认未检查或已换版本的视频')
   e.update(visual_pass=True,reviewer=a.reviewer,review_note=a.review_note);v['media_qc']=e;st.save();print(a.key,'visual review recorded for exact render');return 0
  job=cb.api('GET',f"/render-jobs/{v['render_job']}")
  if job.get('status')!='success' or job.get('object_key')!=v.get('object_key'):raise RuntimeError('当前成片输出未就绪/不匹配')
  video=out/'current.mp4';cb.fetch_file(job['download_url'],video);cut=cb.api('GET',f"/video-cuts/{v['cut_id']}");r=inspect(video,cut['draft'],out,rules);r.update(render_job=v['render_job'],output_object_key=v.get('object_key'));(out/'media-qc.json').write_text(json.dumps(r,ensure_ascii=False,indent=2));v['media_qc']={k:r[k] for k in ['technical_pass','render_job','output_object_key','sha256','draft_hash']};v['quality_facts']=cut_facts(cut['draft']);st.save();print(a.key,'technical PASS' if r['technical_pass'] else 'technical BLOCK',str(out/'media-qc.json'),'visual review pending');return 0 if r['technical_pass'] else 1
 if not (a.video and a.draft and a.out):p.error('使用 --run --key，或同时指定 --video --draft --out')
 d=json.loads(Path(a.draft).read_text());d=d.get('draft',d);r=inspect(a.video,d,a.out,rules);print(json.dumps({k:v for k,v in r.items() if k!='frames'},ensure_ascii=False));return 0 if r['technical_pass'] else 1
if __name__=='__main__':sys.exit(main())
