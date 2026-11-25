#!/usr/bin/env python3
"""[INPUT] User: remove transitions from current 30-cut Qingyang group.
[OUTPUT] Transition-only drafts/configs, strict one-frame black checks and cut-boundary review.
[POS] Keep every source/audio/caption/effect/sticker/timing unchanged; Clipo native rendering only.
"""
import argparse,copy,hashlib,json,math,re,subprocess,sys,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import group_rework_apply as g
import clipo_batch as cb
from common import load_config

def without(d):
 out=copy.deepcopy(d)
 for c in out['clips']:c['transitionIn']=None;c['transitionOut']=None
 return out

def check(before,after):
 if g.digest(without(before))!=g.digest(after):raise RuntimeError('Non-transition content changed')
 if any(c.get('transitionIn') or c.get('transitionOut') for c in after['clips']):raise RuntimeError('Transition remains')

def prepare(a,cfg):
 root=Path(a.out);work=root/'no-transitions';work.mkdir(exist_ok=True);receipt=work/'prepared.json';planfile=work/'plan.json';rows=g.read(root/'video-manifest.json')
 if receipt.exists():print('Transition-only preparation already completed');return
 if a.dry_run:
  def one(row):
   key=row['key'];before=cb.api('GET','/video-cuts/'+row['cut_id']);expected=g.read(root/'plans'/(key+'.json'));oldrec=g.read(root/'renders'/(key+'.json'))
   if g.digest(before['draft'])!=g.digest(expected['draft']) or before.get('output_object_key')!=oldrec['job']['object_key']:raise RuntimeError('Current output changed '+key)
   wanted=copy.deepcopy(expected);wanted['draft']=without(before['draft']);wanted['no_transitions']=True;check(before['draft'],wanted['draft']);g.validate(g.read(root/'cuts'/(key+'.json'))['draft'],wanted)
   changed=[{'id':c['id'],'frame':c['from'],'in':c.get('transitionIn'),'out':c.get('transitionOut')} for c in before['draft']['clips'] if c.get('transitionIn') or c.get('transitionOut')]
   if not changed:raise RuntimeError('No transitions '+key)
   return {'key':key,'before':before,'wanted':wanted,'changed':changed}
  with ThreadPoolExecutor(max_workers=6) as pool:cuts=list(pool.map(one,rows))
  scripts=[]
  for sid in sorted({r['script_id'] for r in rows}):
   before=cb.api('GET','/video-scripts/'+sid);body={k:copy.deepcopy(before[k]) for k in ['product_id','title','video','config','vo_rewrite_prompt','segments']}
   for s in body['segments']:s['transition']=[]
   scripts.append({'id':sid,'before':before,'body':body})
  g.save(planfile,{'at':time.time(),'instruction':'是的话这批转场删除吧，现在转场效果不好','cuts':cuts,'scripts':scripts,'renders':len(cuts),'TTS':0,'AIGC':0});print('DRY-RUN remove',sum(len(x['changed']) for x in cuts),'transition entries in',len(cuts),'existing videos; four scripts; TTS/AIGC 0');return
 p=g.read(planfile)
 if time.time()-p['at']>3600:raise RuntimeError('Preparation receipt expired')
 archive=work/'before';archive.mkdir(exist_ok=True)
 # Exact current snapshots protect concurrent edits; only local plans are staged here.
 for x in p['cuts']:
  key=x['key'];current=cb.api('GET','/video-cuts/'+x['before']['id'])
  if g.digest(current['draft'])!=g.digest(x['before']['draft']):raise RuntimeError('Concurrent cut change '+key)
 for s in p['scripts']:
  current=cb.api('GET','/video-scripts/'+s['id'])
  if any(g.canonical(current[k])!=g.canonical(s['before'][k]) for k in s['body']):raise RuntimeError('Concurrent script change')
  g.save(archive/'scripts'/(s['id']+'.json'),current);cb.api('PUT','/video-scripts/'+s['id'],s['body']);back=cb.api('GET','/video-scripts/'+s['id'])
  if any(g.canonical(back[k])!=g.canonical(s['body'][k]) for k in s['body']):raise RuntimeError('Script readback mismatch')
  g.save(work/'script-readbacks'/(s['id']+'.json'),back)
 for x in p['cuts']:
  key=x['key'];g.save(archive/'plans'/(key+'.json'),g.read(root/'plans'/(key+'.json')));g.save(archive/'cuts'/(key+'.json'),x['before']);g.save(root/'plans'/(key+'.json'),x['wanted'])
  for name in [key+'.json',key+'-intent.json']:
   f=root/'renders'/name
   if f.exists():target=archive/'renders'/name;target.parent.mkdir(exist_ok=True);f.rename(target)
 g.save(archive/'visual-ledger.json',g.read(root/'new-visual-ledger.json'));g.save(root/'new-visual-ledger.json',{});g.save(receipt,{'at':time.time(),'videos':30,'instruction':p['instruction']});print('30 transition-only drafts staged; four native script configs read back without transitions. Original media retained until new QC passes.')

def media(a,cfg):
 from PIL import Image,ImageDraw
 root=Path(a.out);work=root/'no-transitions';plans={x['key']:x for x in g.read(work/'plan.json')['cuts']};keys=a.keys.split(',') if a.keys else sorted(plans)
 def one(key):
  rec=g.read(root/'renders'/(key+'.json'));job=rec['job'];plan=g.read(root/'plans'/(key+'.json'));check(plans[key]['before']['draft'],plan['draft'])
  if job['status']!='success':raise RuntimeError('Render pending '+key)
  out=work/'qc'/key;out.mkdir(parents=True,exist_ok=True);f=out/'current.mp4';record=out/'media-qc.json'
  if record.exists():
   old=g.read(record)
   if old.get('fingerprint')!=rec['fingerprint']:raise RuntimeError('Stale QC')
   return old
  if not f.exists():cb.fetch_file(job['download_url'],f)
  # A 1/30-second black frame was below the old 0.035-second interval threshold.
  log=subprocess.run(['ffmpeg','-hide_banner','-threads','2','-i',str(f),'-vf','scale=216:384,crop=168:330:24:18,blackframe=amount=98:threshold=32','-af','astats=metadata=1:reset=0','-f','null','-'],capture_output=True,text=True,check=True).stderr
  blacks=[{'frame':int(n),'pblack':float(p),'time':float(t)} for n,p,t in re.findall(r'frame:(\d+) pblack:([\d.]+).*? t:([\d.]+)',log)];peaks=re.findall(r'Peak level dB: ([-\d.inf]+)',log);peak=max((float(v) for v in peaks),default=-math.inf)
  wanted=[]
  for x in plans[key]['changed']:
   for delta in [-3,-2,-1,0,1,2,3]:wanted.append(x['frame']+delta)
  wanted=sorted(set(wanted));expr='+'.join('eq(n,'+str(n)+')' for n in wanted);subprocess.run(['ffmpeg','-v','error','-threads','2','-i',str(f),'-vf',"select='"+expr+"',scale=120:-2",'-fps_mode','vfr','-q:v','3',str(out/'boundary-%03d.jpg')],check=True)
  files=sorted(out.glob('boundary-*.jpg'))
  if len(files)!=len(wanted):raise RuntimeError('Missing exact cut frames '+key)
  frames=[];board=Image.new('RGB',(840,math.ceil(len(files)/7)*240+28),'#eee');draw=ImageDraw.Draw(board);draw.text((3,4),key+' / NO TRANSITIONS / cut -3..+3 frames',fill='black')
  for i,(n,file) in enumerate(zip(wanted,files)):
   pic=Image.open(file).convert('RGB');pic.thumbnail((120,214));xx=i%7*120;yy=i//7*240+28;board.paste(pic,(xx,yy));draw.text((xx+2,yy+214),'f'+str(n)+' / '+str(round(n/30,3)),fill='black');frames.append({'clip':'cut-boundary','time':n/30,'file':str(file),'frame':n})
  boardfile=work/'boards'/(key+'.jpg');boardfile.parent.mkdir(exist_ok=True);board.save(boardfile,quality=90)
  result={'video':str(f),'fingerprint':rec['fingerprint'],'render_job':rec['job_id'],'sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'draft_hash':hashlib.sha256(json.dumps(plan['draft'],sort_keys=True).encode()).hexdigest(),'black_intervals':blacks,'peak_dbfs':peak if math.isfinite(peak) else None,'technical_pass':not blacks and math.isfinite(peak) and -60<peak<0,'visual_review':'pending','frames':frames,'board':str(boardfile),'scope':'Only transitionIn/transitionOut removed; all original reviewed sources, audio, timing, captions, upper packaging and right-avatar arrows unchanged.'};g.save(record,result);print(key,'single-frame black',len(blacks),'peak',peak,flush=True);return result
 with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(one,keys))
 g.save(work/'latest-media.json',[{k:r[k] for k in ['fingerprint','technical_pass','black_intervals','peak_dbfs','board']} for r in results])
 if any(not x['technical_pass'] for x in results):raise RuntimeError('Current output technical failure')

def accept(a,cfg):
 root=Path(a.out);work=root/'no-transitions'
 if not a.keys or not a.note:raise RuntimeError('Actually inspected explicit keys and note required')
 ledger=g.read(work/'visual-ledger.json') if (work/'visual-ledger.json').exists() else {}
 for key in a.keys.split(','):
  qc=g.read(work/'qc'/key/'media-qc.json');rec=g.read(root/'renders'/(key+'.json'))
  if not qc['technical_pass'] or qc['fingerprint']!=rec['fingerprint']:raise RuntimeError('Uninspected or stale media '+key)
  ledger[key]={'status':'pass','reviewer':'Agent','fingerprint':rec['fingerprint'],'board':qc['board'],'note':a.note,'at':time.time()}
 g.save(work/'visual-ledger.json',ledger);print('Actual cut-boundary review recorded',a.keys)

def activate(a,cfg):
 import os
 root=Path(a.out);work=root/'no-transitions';archive=work/'before';ledger=g.read(work/'visual-ledger.json');rows=g.read(root/'video-manifest.json')
 if (work/'activated.json').exists():print('Current no-transition QC already activated');return
 for row in rows:
  key=row['key'];rec=g.read(root/'renders'/(key+'.json'));qc=g.read(work/'qc'/key/'media-qc.json')
  if ledger.get(key,{}).get('status')!='pass' or ledger[key]['fingerprint']!=rec['fingerprint'] or not qc['technical_pass']:raise RuntimeError('Current QC incomplete '+key)
 # Keep stable local artifact paths valid until all new media is inspected.
 os.replace(root/'qc-new',archive/'full-qc');os.replace(work/'qc',root/'qc-new');os.replace(root/'新版检查图',archive/'full-shot-boards');os.replace(work/'boards',root/'新版检查图');
 for key,entry in ledger.items():entry['board']=str(root/'新版检查图'/(key+'.jpg'))
 g.save(root/'new-visual-ledger.json',ledger)
 target=root/'本地交付/清扬30条'
 if target.exists():os.replace(target,archive/'local-delivery')
 for p in (root/'qc-new').glob('*/media-qc.json'):
  d=g.read(p);d['video']=str(p.parent/'current.mp4');d['board']=str(root/'新版检查图'/(p.parent.name+'.jpg'));d['frames']=[dict(x,file=str(p.parent/Path(x['file']).name)) for x in d['frames']];g.save(p,d)
 g.save(work/'activated.json',{'videos':30,'at':time.time(),'note':'Transition-only current media and actual cut-boundary review activated; previous full-shot media and delivery preserved.'});print('30 inspected no-transition renders activated; ready for exact original-group readback')

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['prepare','media','accept','activate']);p.add_argument('--out',required=True);p.add_argument('--dry-run',action='store_true');p.add_argument('--keys');p.add_argument('--note');a=p.parse_args();root=Path(a.out)
 if g.read(root/'inventory.json')['group_id']!='6b068349429cee6fdc0865db':raise RuntimeError('Wrong authorized group')
 cfg=load_config('qingyang');cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';globals()[a.cmd](a,cfg)
if __name__=='__main__':main()
