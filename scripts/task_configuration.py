#!/usr/bin/env python3
"""[INPUT] Approved task briefs, explicit native-resource plan and current Clipo product libraries.
[OUTPUT] Read-only inventory and task-owned script drafts with immutable intents, backups and full readback.
[POS] No TTS/video generation, no shared resource edits, no inherited approval, no manual state edits.
"""
import argparse,copy,hashlib,json,sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import load_config,approval_problem,copy_hash

def save(p,d):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,indent=2));return p

def native_body(d,keys):
 """Server emits optional segment subtitle_style=null for inherited global styles."""
 out=copy.deepcopy({k:d.get(k) for k in keys})
 for segment in out.get('segments',[]):
  if segment.get('subtitle_style') is None:segment.pop('subtitle_style',None)
  sound=segment.get('sound_effects')
  if isinstance(sound,dict) and sound.get('volume')==0.5:sound.pop('volume')
 return out

def digest(d):return hashlib.sha256(json.dumps(d,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def first_visual_only(current,wanted):
 base=copy.deepcopy(current)
 if not base.get('segments') or len(base['segments'])!=len(wanted.get('segments',[])):return False
 base['segments'][0]['visual']=copy.deepcopy(wanted['segments'][0]['visual'])
 return base==wanted
def items(d):return d if isinstance(d,list) else d.get('items',[])
def pages(path):
 rows=[]
 for skip in range(0,5000,100):
  d=cb.api('GET',path+f'?limit=100&skip={skip}');part=items(d);rows+=part
  if isinstance(d,list) or len(part)<100 or len(rows)>=d.get('total',len(rows)):return rows
 raise RuntimeError('Bounded pagination exceeded')

def inventory(a,cfg):
 p=json.loads(Path(a.plan).read_text());out=Path(a.run)/'clipo/task-inventory';out.mkdir(parents=True,exist_ok=True)
 work=[]
 for key,sid in p['templates'].items():
  pid=cfg['products'][key]['clipo_product_id']
  work.extend([(key+'-product',lambda pid=pid:cb.api('GET','/products/'+pid)),(key+'-prompts',lambda pid=pid:cb.api('GET','/products/'+pid+'/prompts')),(key+'-template',lambda sid=sid:cb.api('GET','/video-scripts/'+sid)),(key+'-library',lambda pid=pid:cb.list_segments(pid))])
 for key,path in [('bgm','/resource/bgm'),('sfx','/resource/sound-effect'),('effects','/resource/remotion-effect-template'),('fonts','/resource/font')]:work.append((key,lambda path=path:pages(path)))
 for i,q in enumerate(p.get('voice_queries',[])):
  work.append(('voice-query-'+str(i+1),lambda q=q:cb.api('POST','/tts/speaker/search',{'query':q,'platform':'volcengine','include_disabled':False,'limit':8})))
 for eid in p.get('effect_ids',[]):work.append(('effect-'+eid,lambda eid=eid:cb.api('GET','/resource/remotion-effect-template/'+eid)))
 work.append(('stickers',lambda:pages('/resource/sticker')))
 def one(item):
  name,fn=item;d=fn();save(out/(name+'.json'),d);return name,len(d) if isinstance(d,list) else 'saved'
 with ThreadPoolExecutor(max_workers=4) as pool:
  for name,n in pool.map(one,work):print(name,n,flush=True)
 if a.covers:
  rows=json.loads((out/'jingyou-library.json').read_text());rows=[s for s in rows if '头发顺滑' in s.get('tags',[]) and s.get('cover_url')]
  def cover(s):
   f=out/'hair-covers'/(s['id']+'.jpg');f.parent.mkdir(exist_ok=True)
   if not f.exists():cb.fetch_file(s['cover_url'],f)
  with ThreadPoolExecutor(max_workers=5) as pool:list(pool.map(cover,rows))
  print('hair covers',len(rows),flush=True)

def validate_body(body,b,library):
 why=approval_problem(b)
 if why:raise RuntimeError(why)
 if [s.get('content') for s in body['segments']]!=[s['text'] for s in b['segments']]:raise RuntimeError('Approved copy differs')
 valid={s['id'] for s in library};pending=[]
 for s in body['segments']:
  v=s.get('visual') or {};ids=v.get('props',{}).get('segment_ids',[])
  if v.get('mode')=='material_segment_ids':
   if not ids or len(ids)!=len(set(ids)) or any(x not in valid for x in ids):raise RuntimeError('Invalid native footage pool '+s['id'])
  elif v.get('mode')=='material_segment' and v.get('props',{}).get('tags'):
   tags=v['props']['tags']
   if all(t.startswith('AI-') for t in tags):pending.append(s['id'])
   elif any(t.startswith('AI-') for t in tags):raise RuntimeError('Do not mix AI and real tags')
   elif not any(set(tags)&set(x.get('tags',[])) and not x.get('ai_generated') for x in library):raise RuntimeError('No real sources under native tags')
  else:raise RuntimeError('Native tag selection or explicit reviewed IDs required')
 if len((body['config'].get('tts') or {}).get('voices',[]))<3:raise RuntimeError('At least3 native voices required')
 return pending

def permits_demo_revision(item,b,current):
 from demo_authorization import permits_preview
 a=item.get('revision_authorization') or {}
 return (permits_preview(b) and a.get('by')=='用户' and bool(a.get('instruction'))
  and a.get('brief_id')==b['brief_id'] and type(a.get('quantity')) is int and a['quantity']==1
  and a.get('previous_body_digest')==digest(current) and a.get('new_copy_hash')==copy_hash(b))

def inspect(a,cfg):
 plan=json.loads(Path(a.plan).read_text());st=cb.State(a.run);out=Path(a.run)/'clipo/task-configuration'
 for bid,item in plan['scripts'].items():
  sid=st.data.get('briefs',{}).get(bid,{}).get('script_id')
  if not sid:continue
  actual=cb.api('GET','/video-scripts/'+sid);save(out/(bid+'-current.json'),actual)
  print(bid,sid,'matches planned',native_body(actual,item['body'])==native_body(item['body'],item['body']),flush=True)
  if getattr(a,'generations',False):
   gid=st.data.get('briefs',{}).get(bid,{}).get('generation_id')
   if gid:
    generation=cb.api('GET','/video-cut-generations/'+gid);save(out/(bid+'-generation-current.json'),generation)
    print(bid,'native generation',gid,generation.get('status'),'cut records',len(generation.get('cut_ids') or []),'error',generation.get('error_message'),flush=True)

def apply(a,cfg):
 if not a.dry_run:
  from sync_skill import assert_current
  assert_current(Path(__file__).resolve().parent.parent)
 plan=json.loads(Path(a.plan).read_text());root=Path(a.run);out=root/'clipo/task-configuration';out.mkdir(parents=True,exist_ok=True);st=cb.State(root);briefs={b['brief_id']:b for b in cb.load_briefs(root)}
 if not plan.get('authorization'):raise RuntimeError('Missing human configuration authorization')
 results=[]
 for bid,item in plan['scripts'].items():
  b=briefs[bid];body=item['body'];pk=b['product_key'];pid=cfg['products'][pk]['clipo_product_id']
  if body['product_id']!=pid or item['copy_hash']!=copy_hash(b):raise RuntimeError('Wrong product or changed approved copy')
  library=cb.list_segments(pid);pending=validate_body(body,b,library)
  before=None;job=st.data.setdefault('briefs',{}).setdefault(bid,{});sid=job.get('script_id')
  if sid:
   before=cb.api('GET','/video-scripts/'+sid)
   if before['product_id']!=pid or before.get('title')!=body['title']:raise RuntimeError('Existing task identity mismatch')
   rec=out/(bid+'-applied.json')
   if rec.exists():
    prior=json.loads(rec.read_text());current=native_body(before,body)
    wanted=native_body(body,body)
    pinned_first_visual=item.get('expected_current_digest')==digest(current) and first_visual_only(current,wanted)
    if digest(current)!=prior['body_digest'] and current!=wanted and not pinned_first_visual and not permits_demo_revision(item,b,current):raise RuntimeError('Task script changed externally; refusing overwrite')
  if a.dry_run:
   save(out/(bid+'-dryrun.json'),{'body':body,'copy_hash':copy_hash(b),'pending_ai_segments':pending});print(bid,'DRY-RUN','segments',len(body['segments']),'pending_AI',pending,flush=True);continue
  intent=out/(bid+'-create-intent.json');created=out/(bid+'-created.json')
  if not sid:
   found=[s for s in items(cb.api('GET',f'/video-scripts?product_id={pid}&limit=100')) if s.get('title')==body['title']]
   if len(found)>1:raise RuntimeError('Ambiguous existing title')
   if found:
    candidate=cb.api('GET','/video-scripts/'+found[0]['id'])
    if native_body(candidate,body)!=native_body(body,body):raise RuntimeError('Existing title has different body; no duplicate creation')
    sid=candidate['id'];save(created,candidate)
   else:
    if intent.exists() and not created.exists():raise RuntimeError('Prior create outcome uncertain; reconcile instead of retry')
    save(intent,{'body':body,'authorization':plan['authorization']});d=cb.api('POST','/video-scripts',body,tries=1);sid=d['id'];save(created,d)
  else:
   if before and native_body(before,body)!=native_body(body,body):
    save(out/(bid+'-before-'+digest(before)[:12]+'.json'),before);cb.api('PUT','/video-scripts/'+sid,body,tries=1)
  after=cb.api('GET','/video-scripts/'+sid);save(out/(bid+'-after.json'),after)
  if native_body(after,body)!=native_body(body,body):raise RuntimeError('Native script readback mismatch '+bid)
  job.update(script_id=sid,texts=[s['text'] for s in b['segments']],configuration_only=True,pending_ai_segments=pending)
  if not job.get('generation_id'):job['stage']='script_ready'
  st.save();save(out/(bid+'-applied.json'),{'script_id':sid,'body_digest':digest(native_body(body,body)),'copy_hash':copy_hash(b),'pending_ai_segments':pending})
  c=after['config'];result={'brief':bid,'script_id':sid,'pending_ai_segments':pending,'styles':len(c.get('subtitle_style',{}).get('style_ids',[])),'BGM':len(c.get('bgm',{}).get('props',{}).get('ids',[])),'voices':len(c.get('tts',{}).get('voices',[]))};results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
 save(out/'summary.json',results)

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['inventory','inspect','apply']);p.add_argument('--config',required=True);p.add_argument('--run',required=True);p.add_argument('--plan',required=True);p.add_argument('--covers',action='store_true');p.add_argument('--generations',action='store_true');p.add_argument('--dry-run',action='store_true');a=p.parse_args();cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';globals()[a.cmd](a,cfg)
if __name__=='__main__':main()
