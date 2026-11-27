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
from common import load_config,demo_problem,aigc_problem
from task_configuration import pages

def save(p,x):return td.save(p,x)
def read(p):return td.load(p)
def validate_budget(plan,key,now):
 if key not in plan['keys'] or now-plan['at']>7200:raise RuntimeError('Missing current matching bulk TTS budget')

def trim_title_ends(clips,ends):
 for cid,end in ends.items():
  matches=[c for c in clips if c['id']==cid]
  if len(matches)!=1:raise RuntimeError('Exact title clip required')
  c=matches[0]
  if c['trackId']!='effect' or not isinstance(end,int) or not c['from']<end<=c['from']+c['durationInFrames']:raise RuntimeError('Only shortening an existing title is allowed')
  c['durationInFrames']=end-c['from']

def continuous_source_framing(draft,evidence,visuals):
 """Bind task-only source transforms to actual variable-count CTA clips."""
 from task_caption_repair import frame_main
 changes=copy.deepcopy(visuals.get('main_framing',{}));rules=visuals.get('source_framing',{})
 for row in evidence['source_windows']:
  rule=rules.get(row['source_segment_id'])
  if rule:
   if row['clip'].split('-')[0] != visuals.get('framing_segment_id'):raise RuntimeError('Source framing outside explicit segment')
   if row['clip'] in changes:raise RuntimeError('Ambiguous source and clip framing')
   changes[row['clip']]=rule
 result=frame_main(draft,changes)
 for cid in visuals.get('title_end_at_first_main_ids',[]):
  title=next(c for c in result['clips'] if c['id']==cid and c['trackId']=='effect')
  shot=next(c for c in result['clips'] if c['trackId']=='main' and c['id']==cid.split('-')[0]+'-0')
  trim_title_ends(result['clips'],{cid:min(title['from']+title['durationInFrames'],shot['from']+shot['durationInFrames'])})
 return result

def hold(a,cfg):
 st=cb.State(a.run);plan=read(a.plan);out=Path(a.run)/'revisions/customer-bulk-hold';out.mkdir(parents=True,exist_ok=True)
 if not plan.get('authorization') or not plan.get('reason') or not plan.get('keys'):raise RuntimeError('Missing explicit user rejection')
 for key in plan['keys']:
  if key not in st.videos or st.videos[key]['brief_id']!=a.brief:raise RuntimeError('Hold scope mismatch')
 if a.dry_run:save(out/'plan.json',plan);print('DRY-RUN hold',len(plan['keys']),'owned videos; preserve demo, raw files and old share for audit');return
 from sync_skill import assert_current
 assert_current(Path(__file__).resolve().parent.parent)
 if read(out/'plan.json')!=plan:raise RuntimeError('Hold dry-run mismatch')
 receipt=out/'applied.json'
 if receipt.exists():
  if any(st.videos[k].get('customer_rework_required')!=plan['reason'] for k in plan['keys']):raise RuntimeError('Previously held videos changed; do not reapply automatically')
  st.data.setdefault('briefs',{}).setdefault(a.brief,{})['customer_rework_hold']=plan['reason'];st.save();print('Hold already applied; production stop verified');return
 save(out/'before.json',st.data)
 for key in plan['keys']:st.videos[key]['customer_rework_required']=plan['reason']
 st.data.setdefault('briefs',{}).setdefault(a.brief,{})['customer_rework_hold']=plan['reason']
 group=st.data.get('share_group');share=st.data.get('shares',{}).get(a.brief)
 if share and group:
  before=cb.api('GET','/video-share-groups/'+group['id']);save(out/'group-before.json',before)
  wanted=[x['id'] for x in before['shares'] if x['id']!=share['id']];cb.api('PATCH','/video-share-groups/'+group['id'],{'video_share_ids':wanted},tries=1)
  after=cb.api('GET','/video-share-groups/'+group['id']);save(out/'group-after.json',after)
  if sorted(x['id'] for x in after['shares'])!=sorted(wanted):raise RuntimeError('Group withdrawal readback mismatch')
  st.data.setdefault('withdrawn_shares',[]).append({**share,'reason':plan['reason']});st.data['shares'].pop(a.brief,None)
 st.save();cb.write_delivery(st,{b['brief_id']:b for b in cb.load_briefs(a.run)},a.run);save(receipt,plan);print('Held',len(plan['keys']),'videos; batch share removed from delivery group, original demo retained')

def repair(a,cfg):
 from footage_qc import stable_media
 from qc import cut_checks
 st=cb.State(a.run);plan=read(a.plan);key=plan['key'];v=st.videos[key];b=next(x for x in cb.load_briefs(a.run) if x['brief_id']==v['brief_id'])
 if not plan.get('authorization') or b['brief_id']!=a.brief:raise RuntimeError('Repair authorization or task mismatch')
 cut=cb.api('GET','/video-cuts/'+v['cut_id']);draft=copy.deepcopy(cut['draft']);library={x['id']:x for x in cb.list_segments(cfg['products'][b['product_key']]['clipo_product_id'])};fps=draft['composition']['fps']
 cb._SEGMENTS[b['product_key']]={m['object_url'].split('?')[0].rsplit('/',1)[-1]:m for m in library.values() if m.get('object_url')}
 for c in draft['clips']:
  if c['id'] in plan.get('remove_transition_ids',[]):
   if c['trackId']!='main':raise RuntimeError('Only selected main-shot transitions may be removed')
   c['transitionIn']=None;c['transitionOut']=None
 for c in draft['clips']:
  if c['id'] not in plan['clips']:continue
  change=plan['clips'][c['id']];m=library[change['segment_id']];start=change['startFrom']
  if c['trackId']!='main' or m.get('ai_generated') or m.get('review_result')!='pass' or start+c['durationInFrames']>math.floor((m['end_ms']-m['begin_ms'])*fps/1000):raise RuntimeError('Unsafe real source replacement')
  c['props'].update(src=m['object_url'],startFrom=start,alt=m.get('description',''))
 for cid,scale in plan.get('clip_scales',{}).items():
  matched=[c for c in draft['clips'] if c['id']==cid]
  if len(matched)!=1 or matched[0]['trackId']!='main' or not isinstance(scale,(int,float)) or not 1<scale<=1.05:raise RuntimeError('Only explicit small main-frame crop permitted')
  matched[0]['props']['scale']=scale
 from task_caption_repair import frame_main
 draft=frame_main(draft,plan.get('main_framing',{}))
 expected=copy.deepcopy([x for x in cut['draft']['clips'] if x['trackId']!='main']);trim_title_ends(expected,plan.get('effect_end_frames',{}));trim_title_ends(draft['clips'],plan.get('effect_end_frames',{}))
 if plan.get('remove_task_corner_label'):
  wm=cfg.get('watermark') or {}
  if wm.get('add') is not False or not (wm.get('task_user_override') or {}).get('quote'):raise RuntimeError('Explicit task-level corner-label revision required')
  marked=[c for c in draft['clips'] if c['trackId']=='watermark']
  if any(c.get('props',{}).get('text')!='内容由AI生成' for c in marked):raise RuntimeError('Unknown watermark content; no broad removal')
  draft['clips']=[c for c in draft['clips'] if c['trackId']!='watermark'];expected=[c for c in expected if c['trackId']!='watermark']
 if expected!=[x for x in draft['clips'] if x['trackId']!='main']:raise RuntimeError('Protected tracks changed')
 issues=cut_checks(b,draft,v.get('voice'))
 if issues:raise RuntimeError('Repair gate '+str(issues))
 out=Path(a.run)/'task-bulk'/key/'source-repair';out.mkdir(exist_ok=True);sig=td.digest(stable_media(draft));budgetfile=out/'budget.json'
 if a.dry_run:save(budgetfile,{'at':time.time(),'hash':sig,'plan':plan});print('DRY-RUN',key,'1 source-only native rerender; 0 TTS, timing/captions/packaging/audio preserved');return
 rec=read(budgetfile)
 if rec['hash']!=sig or rec['plan']!=plan or time.time()-rec['at']>3600:raise RuntimeError('Missing exact source repair dry-run')
 receipt=out/(sig+'.json')
 if receipt.exists():job=read(receipt)
 else:
  save(out/'before.json',cut);cb.api('PUT','/video-cuts/'+v['cut_id'],{'script_id':cut['script_id'],'draft':draft},tries=1);back=cb.api('GET','/video-cuts/'+v['cut_id'])
  if stable_media(back['draft'])!=stable_media(draft):raise RuntimeError('Repair readback differs')
  save(out/'after.json',back);job=cb.api('POST','/render-jobs',{'spec':back['draft'],'cut_id':v['cut_id'],'options':{'codec':'h264','quality':'high','scale':1},'idempotency_key':'task-source-repair-'+v['cut_id']+'-'+sig[:16]},tries=1);save(receipt,job)
 from quality_contract import cut_facts
 prior_prepared=Path(v.get('task_bulk_prepared_path') or Path(a.run)/'task-bulk'/key/'prepared.json')
 if prior_prepared.exists():
  prepared=read(prior_prepared);prepared['draft']=draft
  for x in prepared['evidence']['source_windows']:
   change=plan['clips'].get(x['clip'])
   if change:
    m=library[change['segment_id']];x.update(source_segment_id=m['id'],source_path=td.urlparse(m['object_url']).path,startFrom=change['startFrom'],source_available_frames=math.floor((m['end_ms']-m['begin_ms'])*fps/1000))
  if plan.get('main_framing'):prepared['evidence']['reviewed_main_framing']=plan['main_framing']
  actual=out/(sig+'-prepared.json');save(actual,prepared);v['task_bulk_prepared_path']=str(actual.resolve())
 v.update(render_job=job['job_id'],stage='rendering',ai_review=None,ai_reason=None,qc_issues=[],quality_facts=cut_facts(draft))
 for k in ['error','last_error','object_key','bad_footage']:v.pop(k,None)
 st.save();print(key,'source-only repair rendering',job['job_id'])

def repair_batch(a,cfg):
 """Apply an explicit reviewed list serially, sharing only a fresh read-only source catalog."""
 rows=read(a.plan)
 if not isinstance(rows,list) or not rows or len({x['key'] for x in rows})!=len(rows):raise RuntimeError('Unique reviewed repair rows required')
 st=cb.State(a.run)
 for x in rows:
  p=read(x['plan'])
  if p['key']!=x['key'] or st.videos[x['key']]['brief_id']!=x['brief'] or st.videos[x['key']].get('customer_rework_required'):raise RuntimeError('Repair-list ownership or customer rejection mismatch')
 original=cb.list_segments;cache={}
 def catalog(pid):
  if pid not in cache:cache[pid]=original(pid)
  return cache[pid]
 cb.list_segments=catalog
 try:
  for x in rows:repair(argparse.Namespace(**{**vars(a),'brief':x['brief'],'plan':x['plan']}),cfg)
 finally:cb.list_segments=original
 print('DRY-RUN' if a.dry_run else 'APPLIED','explicit existing-cut repairs',len(rows),'TTS0 new-videos0',flush=True)

def random_title_templates(effects,key,count):
 """Draw from the current inspected catalog without a style/category whitelist.
 Two-title batches traverse a seeded shuffle of distinct ordered pairs, so nearby
 videos vary while re-running a video preserves its exact packaging choices.
 """
 import random
 if count<1:return []
 if len({x.get('id') for x in effects})!=len(effects) or len(effects)<count:raise RuntimeError('Insufficient distinct current title templates')
 bid,number=key.rsplit('-V',1);index=int(number)-2
 if index<0:raise RuntimeError('Bulk title selection requires a remaining video key')
 seed=bid+'|current-title-catalog|'+'|'.join(x['id'] for x in effects)
 if count==2:
  pairs=[(a,b) for a in range(len(effects)) for b in range(len(effects)) if a!=b];random.Random(seed).shuffle(pairs)
  return [effects[j] for j in pairs[index%len(pairs)]]
 if count==1:
  order=list(effects);random.Random(seed).shuffle(order);return [order[index%len(order)]]
 return random.Random(seed+'|'+key).sample(effects,count)

def title_template_instance(template,params,cfg):
 """Apply inspected template-specific coordinates without restricting style types."""
 options=cfg.get('batch_native_variants') or {};rule=(options.get('title_adapters') or {}).get(template['id']);code=template['code'];out=copy.deepcopy(params)
 if not rule:
  if options.get('require_title_adapters'):raise RuntimeError('Current title template lacks inspected parameter adapter')
  return code,out
 if hashlib.sha256(code.encode()).hexdigest()!=rule.get('source_code_sha256'):raise RuntimeError('Title template changed after its content/parameter inspection')
 out.update(copy.deepcopy(rule.get('params') or {}));out['text']=params['text']
 for replacement in rule.get('code_replacements') or []:
  before=replacement['from'];after=replacement['to']
  if not before or code.count(before)!=1:raise RuntimeError('Inspected title anchor no longer matches')
  code=code.replace(before,after)
 return code,out

def approved_copy_variants(b,key,plan,ref):
 from common import copy_hash,approval_problem
 if approval_problem(b) or demo_problem(b):raise RuntimeError('Actual mother-script/demo approval required')
 source=Path(ref['path'])
 if hashlib.sha256(source.read_bytes()).hexdigest()!=ref['sha256']:raise RuntimeError('Reviewed rewrite plan changed')
 reviewed=read(source)
 if reviewed.get('base_copy_hash')!=copy_hash(b) or not reviewed.get('instruction') or reviewed.get('reviewer')!='Agent' or set(reviewed['videos'])!=set(plan['keys']):raise RuntimeError('Rewrite scope mismatch')
 row=reviewed['videos'][key];texts=row['texts']
 if row['texts_sha256']!=td.digest(texts) or len(texts)!=len(b['segments']) or any(not isinstance(x,str) or not x for x in texts):raise RuntimeError('Rewrite content mismatch')
 for text,segment in zip(texts,b['segments']):
  if segment.get('role')=='product' and text!=segment['text']:raise RuntimeError('Product full name must remain exact')
 return texts

def refresh(a,cfg):
 """Refresh current owned native packaging before consuming the same nine speech slots."""
 root=Path(a.run)/'task-bulk';plan=read(root/(a.brief+'-budget.json'));st=cb.State(a.run);b=next(x for x in cb.load_briefs(a.run) if x['brief_id']==a.brief)
 if demo_problem(b) or any((root/key/'tts-intent.json').exists() for key in plan['keys']):raise RuntimeError('Cannot change bulk resources after speech consumption')
 sid=st.data['briefs'][a.brief]['script_id'];actual=cb.api('GET','/video-scripts/'+sid)
 if [x['content'] for x in actual['segments']]!=[x['text'] for x in b['segments']]:raise RuntimeError('Mother copy changed')
 if not a.dry_run:save(root/(a.brief+'-script-before-resource-refresh.json'),read(root/(a.brief+'-script.json')));save(root/(a.brief+'-script.json'),actual)
 print('DRY-RUN' if a.dry_run else 'SAVED','current native packaging; same budget keys/time, zero generation/TTS')

def budget(a,cfg):
 if not a.dry_run:raise RuntimeError('Budget requires --dry-run; no paid operations here')
 st=cb.State(a.run);b=next(x for x in cb.load_briefs(a.run) if x['brief_id']==a.brief)
 if demo_problem(b):raise RuntimeError(demo_problem(b))
 sid=st.data['briefs'][a.brief]['script_id'];script=cb.api('GET','/video-scripts/'+sid);root=Path(a.run)/'task-bulk';root.mkdir(exist_ok=True)
 save(root/(a.brief+'-script.json'),script);save(root/(a.brief+'-library.json'),cb.list_segments(script['product_id']));save(root/'styles.json',pages('/resource/subtitle-style'))
 music=read(Path(a.run)/'clipo/task-inventory/bgm.json');save(root/'music.json',music)
 ids=(cfg.get('batch_native_variants') or {}).get('upper_effect_ids') or []
 if len(ids)<3 or len(set(ids))!=len(ids):raise RuntimeError('At least three distinct current inspected title templates required')
 save(root/'fonts.json',read(Path(a.run)/'clipo/task-inventory/fonts.json'))
 with ThreadPoolExecutor(max_workers=4) as pool:effects=list(pool.map(lambda x:cb.api('GET','/resource/remotion-effect-template/'+x),ids))
 for template in effects:title_template_instance(template,{'text':'资源检查'},cfg)
 save(root/'effects.json',effects)
 n=a.quantity
 if n!=b['quantity']-1:raise RuntimeError('Only remaining quota after one approved demo')
 offset=max([int(k.rsplit('-V',1)[1]) for k in st.videos if k.startswith(a.brief+'-V')],default=1)
 keys=[a.brief+f'-V{i:02d}' for i in range(offset+1,offset+n+1)]
 save(root/(a.brief+'-budget.json'),{'at':time.time(),'keys':keys,'quantity':n,'whole_tts':n,'speed':1.4,'authorization':b.get('demo_approval') or b.get('checked_batch_authorization'),'source':'full current tag libraries, no fixed real pool'})
 print('DRY-RUN',a.brief,'new videos',n,'whole TTS',n,'native voice/style/music choices',len(script['config']['tts']['voices']),len(script['config']['subtitle_style']['style_ids']),len(script['config']['bgm']['props']['ids']),flush=True)

def allocate(a,cfg):
 """Register a visually reviewed whole-batch source plan; no paid calls or state fabrication."""
 plan=read(a.plan);root=Path(a.run)/'task-bulk';budget=read(root/(a.brief+'-budget.json'));b=next(x for x in cb.load_briefs(a.run) if x['brief_id']==a.brief)
 if not plan.get('reviewer') or not plan.get('evidence') or not plan.get('authorization'):raise RuntimeError('Visual grouping evidence and task authorization required')
 if set(plan.get('videos',{}))!=set(budget['keys']):raise RuntimeError('Allocation must cover the exact remaining video keys')
 library={x['id']:x for x in read(root/(a.brief+'-library.json'))};groups=plan.get('source_groups',{});counts={}
 for key,item in plan['videos'].items():
  used=set();people=set()
  for s in b['segments']:
   ids=item['segments'].get(s['id'],[])
   if not ids:raise RuntimeError('Missing role allocation '+key+'/'+s['id'])
   for sid in ids:
    m=library[sid];path=td.urlparse(m['object_url']).path
    ai_role=s is b['segments'][0] and bool(s['visual_tags']) and all(t.startswith('AI-') for t in s['visual_tags'])
    approved_ai=set((cfg.get('approved_ai_hook_ids') or {}).get(a.brief,[]))
    approved_hook=ai_role and m.get('ai_generated') and sid in approved_ai and not aigc_problem(b) and len(ids)==1
    if (not approved_hook and (m.get('ai_generated') or m.get('review_result')!='pass')) or not set(m.get('tags',[]))&set(s['visual_tags']) or path in used:raise RuntimeError('Invalid/duplicate source in allocation')
    used.add(path)
    if '头发顺滑' in s['visual_tags']:
     if not groups.get(sid):raise RuntimeError('Hair footage lacks reviewed person/appearance grouping')
     people.add(groups[sid])
  for person in people:counts[person]=counts.get(person,0)+1
 cap=plan.get('max_videos_per_person_group')
 if not isinstance(cap,int) or cap<1 or cap>=len(budget['keys']):raise RuntimeError('A real cross-video person cap is required')
 if any(n>cap for n in counts.values()):raise RuntimeError('Reviewed person-group frequency cap exceeded')
 target=root/(a.brief+'-source-allocation.json')
 if not a.dry_run:
  save(target,{'plan':plan,'counts':counts,'budget_at':budget['at']});st=cb.State(a.run);st.data.setdefault('briefs',{}).setdefault(a.brief,{}).pop('customer_rework_hold',None);st.save()
 print('DRY-RUN' if a.dry_run else 'SAVED','reviewed source allocation',len(plan['videos']),'videos; person groups',counts)

def copies(a,cfg):
 """Export actual native rewrites and actual used-source counts, never count planned resources as used."""
 st=cb.State(a.run);root=Path(a.run)/'task-bulk';budget=read(root/(a.brief+'-budget.json'));allocation=read(root/(a.brief+'-source-allocation.json'));groups=allocation['plan']['source_groups'];rows=[];people={};sources={}
 for key in budget['keys']:
  v=st.videos.get(key)
  if not v:continue
  text=''.join(v.get('texts') or []);prepared=Path(v.get('task_bulk_prepared_path') or root/key/'prepared.json');used=[]
  if prepared.exists():
   evidence=read(prepared)['evidence']['source_windows'];seen=set()
   for x in evidence:
    sid=x['source_segment_id'];sources.setdefault(sid,{'videos':set(),'shots':0,'frames':0});sources[sid]['videos'].add(key);sources[sid]['shots']+=1;sources[sid]['frames']+=x['used_frames']
    if sid in groups:seen.add(groups[sid])
   used=sorted(seen)
   for g in used:people[g]=people.get(g,0)+1
  rows.append({'key':key,'cut_id':v.get('cut_id'),'stage':v.get('stage'),'copy':text,'copy_hash':hashlib.sha256(td.norm(text).casefold().encode()).hexdigest(),'actual_person_groups':used,'last_error':v.get('last_error'),'native_copy_source':v.get('copy_repair_approval') or 'Clipo random_result.vo_rewrite → collected video texts'})
 serial={k:{**v,'videos':sorted(v['videos'])} for k,v in sources.items()};out=Path(a.run)/'revisions/新批量文案与人物分配';out.mkdir(parents=True,exist_ok=True);save(out/(a.brief+'-实际改写与用料.json'),{'rows':rows,'unique_actual_copy':len({x['copy_hash'] for x in rows if x['copy']}),'actual_person_groups':people,'actual_source_frequency':serial})
 (out/(a.brief+'-实际文案.md')).write_text('\n\n'.join('### '+x['key']+'\n\n'+x['copy'] for x in rows))
 print(a.brief,'collected',len(rows),'unique actual copy',len({x['copy_hash'] for x in rows if x['copy']}),'prepared',sum(bool(x['actual_person_groups']) for x in rows),'actual groups',people,'report',str(out/(a.brief+'-实际文案.md')))

def prepare(api_module,st,b,v,key,cut,d,repair_revision=None,repair_budget=None):
 td.cb=api_module;cb.CFG=api_module.CFG;cb.BASE=api_module.BASE;cfg=api_module.CFG;root=st.path.parent.parent;assets=root/'task-bulk';plan=read(assets/(b['brief_id']+'-budget.json'))
 validate_budget(repair_budget or plan,key,time.time())
 if repair_budget and (repair_budget.get('keys')!=[key] or repair_budget.get('whole_tts')!=1 or not repair_budget.get('authorization') or not repair_revision):raise RuntimeError('Only one explicitly approved same-cut speech repair')
 if repair_revision and (not repair_budget or not td.re.fullmatch(r'[a-zA-Z0-9_-]{1,40}',repair_revision)):raise RuntimeError('Invalid repair revision')
 script=read(assets/(b['brief_id']+'-script.json'));library=read(assets/(b['brief_id']+'-library.json'));work=assets/key
 if repair_revision:work=work/'revisions'/repair_revision
 work.mkdir(exist_ok=True,parents=True);i=int(key.rsplit('-V',1)[1])-2
 got=v.get('texts') or []
 from checked_batch_authorization import permits_checked_batch
 conditional=permits_checked_batch(b)
 if b.get('checked_batch_authorization') and not conditional:raise RuntimeError('Current delegated checks no longer match')
 if conditional:
  from checked_batch_authorization import REQUIRED_CHECKS
  a=b['checked_batch_authorization'];ctx=b['checked_batch_context'];refs=ctx['current_evidence']['rewrite_plan'];rewrite=read(refs[0]['path']);row=rewrite.get('videos',{}).get(key,{})
  texts=row.get('texts');signature=td.digest(texts)
  if key not in a['exact_video_keys'] or not texts or row.get('texts_sha256')!=signature:raise RuntimeError('Actual per-video rewrite not pinned in inspected plan')
  v['native_platform_texts_before_override']=got;got=texts;v['texts']=got;v['whole_voice_copy_source']={'source':'Agent inspected per-video rewrite plan','file':refs[0]['path'],'sha256':signature}
 ref=(cfg.get('task_batch_copy_variants') or {}).get(b['brief_id'])
 if ref:
  v['native_platform_texts_before_override']=got;got=approved_copy_variants(b,key,plan,ref);v['texts']=got;v['whole_voice_copy_source']={'source':'Agent-reviewed batch rewrite of approved mother script','file':ref['path'],'sha256':td.digest(got)}
 if len(got)!=len(b['segments']):raise RuntimeError('Missing actual per-video native rewrite')
 from lint_copy import lint_text,lint_script
 from common import voiced
 prod=cfg['products'][b['product_key']];issues=[m for t in got for level,m in lint_text(t,cfg,prod) if level=='error']+[m for level,m in lint_script(got,voiced(b['segments']),b,cfg,prod) if level=='error']
 if issues:raise RuntimeError('Actual rewrite rejected before whole TTS '+str(issues))
 opening=(cfg.get('task_semantic_openings') or {}).get(b['brief_id'],{})
 if opening and (not any(t in got[0] for t in opening.get('required_any',[])) or any(t in got[0] for t in opening.get('forbid',[]))):raise RuntimeError('Opening copy does not match the inspected hook footage')
 spoken=''.join(got)
 fingerprint=lambda texts:td.norm(''.join(texts)).casefold()
 for other,item in st.videos.items():
  if other!=key and item.get('brief_id')==b['brief_id'] and not item.get('customer_rework_required') and item.get('texts') and fingerprint(item['texts'])==fingerprint(got):raise RuntimeError('Duplicate batch copy before whole TTS: '+other)
 allocation=read(assets/(b['brief_id']+'-source-allocation.json'))
 if allocation['budget_at']!=plan['at'] or key not in allocation['plan']['videos']:raise RuntimeError('整组人物/造型与源频次分配尚未实际复核；禁止无分配直接随机生产')
 cfg={**cfg,'source_highlight_windows':{**cfg.get('source_highlight_windows',{}),**allocation['plan'].get('source_windows',{})}}
 voice=script['config']['tts']['voices'][i%len(script['config']['tts']['voices'])];body={'text':spoken,'voice':{'platform':voice['platform'],'voice_type':voice['voice_type'],'resource_id':voice['config']['resource_id'],'speed_ratio':1.4},'force':False};receipt=work/'tts.json';intent=work/'tts-intent.json'
 if receipt.exists():
  if read(intent)!=body:raise RuntimeError('Bulk TTS cached input changed')
  tts=read(receipt)
 else:
  if intent.exists():raise RuntimeError('Uncertain TTS outcome; no duplicate submit')
  if not repair_budget and sum((assets/k/'tts-intent.json').exists() for k in plan['keys'])>=plan['whole_tts']:raise RuntimeError('TTS budget exhausted')
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
 for s,text in zip(rows['segments'],got):s['text']=text;s.pop('caption_chunks',None)
 draft,evidence=td.build(rows,{**cut,'draft':d},tts,script,library,cfg,key,allocation=allocation['plan']['videos'][key]['segments'],authorization_brief=b)
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
 fonts=read(assets/'fonts.json');fontids=script['config']['subtitle_style'].get('font_ids') or []
 if len(set(fontids))<3:raise RuntimeError('At least three actual font resources are required')
 fontid=fontids[i%len(fontids)];font=next(z for z in fonts if z['id']==fontid)
 for x in draft['clips']:
  if x['trackId']!='overlay':continue
  text=x['props']['text'];p={'text':text,'font_family':font['name'],'font_url':font['url'],'font_size':36,'top':78,'from_bottom':None,'padding_x':None}
  p.update({dst:style.get(src) for src,dst in fieldmap.items()})
  for rule in (cfg.get('caption_highlights') or {}).get('rules',[]):
   if any(term in text for term in rule['terms']):p['text_color']=('#8F5700' if rule['color']=='#FFD36A' else '#944868') if p.get('text_color')=='#000000' else rule['color'];break
  x['component']='SubtitleClip';x['props']=p
 effects=read(assets/'effects.json');fxrows=[(j,s) for j,s in enumerate(script['segments']) if s.get('effect')];chosen_titles=random_title_templates(effects,key,len(fxrows));draft['clips']=[x for x in draft['clips'] if x['trackId']!='effect']
 for j,(segment_index,s) in enumerate(fxrows):
  template=chosen_titles[j];code,params=title_template_instance(template,s['effect']['params'],cfg);td.validate_code(code);start,end=evidence['segment_frames'][segment_index:segment_index+2];draft['clips'].append({'id':s['id']+'-x0','trackId':'effect','from':start,'durationInFrames':min(end-start,66),'component':'RemotionClip','props':{'code':code,'params':params,'x':0,'y':0,'scale':1}})
 visuals=(cfg.get('continuous_batch_visuals') or {}).get(key)
 if visuals:
  draft=continuous_source_framing(draft,evidence,visuals)
  if visuals.get('cta_sticker_category'):
   stickers=read(root/'clipo/task-inventory/stickers.json');pool=sorted([x for x in stickers if visuals['cta_sticker_category'] in x.get('categories',[])],key=lambda x:x['id'])
   if not pool:raise RuntimeError('No current native CTA sticker')
   selected=pool[i%len(pool)];draft['clips']=[c for c in draft['clips'] if c['trackId']!='sticker'];start,end=evidence['segment_frames'][-2:]
   draft['clips'].append({'id':'8-k0','trackId':'sticker','from':start,'durationInFrames':end-start,'component':'StickerClip','props':{'src':selected['url'],'width':1080,'height':1920,'x':0,'y':0,'scale':1,'rotation':0,'opacity':1,'z_index':1,'loop':True}})
   evidence['cta_sticker']={'id':selected['id'],'name':selected['name'],'source':'current native sticker catalog'}
  if visuals.get('snow'):
   draft['clips'].append({'id':'snowkiss-soft-snow','trackId':'effect','from':0,'durationInFrames':150,'component':'RemotionClip','props':{'code':td.SNOW_CODE,'params':{},'x':0,'y':0,'scale':1}})
  if visuals.get('dissolve_image'):
   from product_scene import upload
   from task_voice_revision import DISSOLVE
   image=Path(visuals['dissolve_image'])
   if hashlib.sha256(image.read_bytes()).hexdigest()!=visuals['dissolve_sha256']:raise RuntimeError('Continuous match frame changed')
   obj=upload(image,work);url=api_module.api('POST','/storage/download-tokens',{'object_key':obj})['download_url']
   draft['clips'].append({'id':'opening-match-dissolve','trackId':'effect','from':150,'durationInFrames':8,'component':'RemotionClip','props':{'code':DISSOLVE,'params':{'src':url},'x':0,'y':0,'scale':1}})
 from quality_contract import contract
 if contract(cfg).get('require_title_entry_sound'):
  from title_audio import from_inventory
  draft=from_inventory(draft,st.path.parent.parent,key)
 from lint_copy import lint_text,lint_script
 from common import voiced
 prod=cfg['products'][b['product_key']];issues=[m for t in got for level,m in lint_text(t,cfg,prod) if level=='error']+[m for level,m in lint_script(got,voiced(b['segments']),b,cfg,prod) if level=='error']
 if issues:raise RuntimeError('Approved copy invalid '+str(issues))
 v.update(voice=voice['voice_type'],texts=got,copy_issues=[],voice_cmd=False,duration_s=draft['composition']['durationInFrames']/draft['composition']['fps'],native_variant={'subtitle_style_id':styleid,'font_id':fontid,'font_family':font['name'],'bgm_id':musicid,'upper_templates':[x['id'] for x in chosen_titles],'voice_gain_db':asset['gain_db']})
 save(work/'prepared.json',{'draft':draft,'evidence':evidence,'variant':v['native_variant'],'voice':v['voice']})
 if repair_revision:v['task_bulk_prepared_path']=str((work/'prepared.json').resolve())
 return draft

def main():
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['budget','refresh','repair','repair_batch','hold','allocate','copies']);p.add_argument('--config',required=True);p.add_argument('--run',required=True);p.add_argument('--brief',required=True);p.add_argument('--quantity',type=int,default=9);p.add_argument('--plan');p.add_argument('--dry-run',action='store_true');a=p.parse_args();cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';globals()[a.cmd](a,cfg)
if __name__=='__main__':main()
