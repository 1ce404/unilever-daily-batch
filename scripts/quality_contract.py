#!/usr/bin/env python3
# ---------- 文件契约 ----------
# [INPUT]: 依赖品牌质量配置、原生脚本与草稿、真实素材和逐处花字成片验收凭据。
# [OUTPUT]: 提供配置、单片、整组与交付质量检查；不要求开头素材数量匹配交付条数。
# [POS]: scripts 的配置级质量契约；由 qc 与生产入口调用，不写共享资源。
# [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""[INPUT] Brand quality contract, current Clipo script/cut and actual source metadata.
[OUTPUT] Pure configuration/cut/batch gates plus a read-only native configuration audit.
[POS] LUX customer requirements; enabled by config, no product facts or shared resource writes.
"""
import argparse,hashlib,json,re,sys
from pathlib import Path
from urllib.parse import urlparse
sys.path.insert(0,str(Path(__file__).resolve().parent))

def contract(cfg):
 name=(cfg or {}).get('quality_contract')
 if not name:return {}
 path=Path(name)
 if not path.is_absolute():path=Path(__file__).resolve().parent.parent/'config'/path
 return json.loads(path.read_text())

def config_issues(script,b,cfg,library=(),sound_ids=None):
 rules=contract(cfg)
 if not rules:return []
 out=[];c=script.get('config') or {};q=max(1,int(b.get('quantity',1)));style=(c.get('subtitle_style') or {}).get('style_ids') or [];music=(c.get('bgm') or {}).get('props',{}).get('ids') or [];voices=(c.get('tts') or {}).get('voices') or []
 for label,have,need in [('原生字幕样式',len(set(style)),rules['min_subtitle_styles']),('BGM',len(set(music)),rules['min_bgm']),('音色候选',len({x.get('id') or x.get('voice_type') for x in voices}),rules['min_voice_choices'])]:
  if q>1 and have<need:out.append(f'{label}实际只绑定{have}个，批量要求至少{need}个')
 meta={x['id']:x for x in library};upper=0
 for i,s in enumerate(script.get('segments',[])):
  briefseg=(b.get('segments') or [])[i] if i<len(b.get('segments') or []) else {};ai=any(str(t).startswith('AI') for t in briefseg.get('visual_tags',[]));vis=s.get('visual') or {};ids=vis.get('props',{}).get('segment_ids') or [];need=min(rules['min_ai_hooks'] if ai else rules['min_real_pool'],q) if q>1 else 1
  if vis.get('mode')=='material_segment':
   tags=vis.get('props',{}).get('tags') or []
   if not tags:out.append(f"S{s.get('id')}未配置原生素材标签")
   elif meta:
    candidates=[x for x in meta.values() if set(tags)&set(x.get('tags') or []) and bool(x.get('ai_generated'))==ai]
    if len(candidates)<need:out.append(f"S{s.get('id')}标签库实际{len(candidates)}条，至少需{need}条")
  elif vis.get('mode')!='material_segment_ids':out.append(f"S{s.get('id')}未配置原生标签随机或明确指定素材")
  elif len(set(ids))<need:out.append(f"S{s.get('id')}素材池{len(set(ids))}条，至少需{need}条")
  if ids and meta:
   missing=[x for x in ids if x not in meta]
   if missing:out.append(f"S{s.get('id')}引用不存在的切片")
   paths={urlparse(meta[x].get('object_url') or '').path for x in ids if x in meta}
   if len(paths)<len(set(ids)):out.append(f"S{s.get('id')}不同ID实为同一源素材")
  if sound_ids is not None:
   chosen=(s.get('sound_effects') or {}).get('props',{}).get('ids') or []
   if any(x not in sound_ids for x in chosen):out.append(f"S{s.get('id')}音效引用已被源库删除")
  text=(s.get('effect') or {}).get('params',{}).get('text','')
  if text:
   upper+=1
   if rules.get('upper_single_line') and ('\n' in text or '\r' in text):out.append(f"S{s.get('id')}上方花字含换行")
 if upper<rules['min_upper_titles']:out.append(f'上方花字真实配置仅{upper}处')
 return out

def upper_titles(d):
 return [x for x in sorted(d.get('clips',[]),key=lambda x:x.get('from',0)) if (x.get('trackId')=='effect' or x.get('id','').startswith('upper-')) and x.get('props',{}).get('params',{}).get('text')]

def cut_facts(d):
 clips=d.get('clips',[]);cues=sorted([x for x in clips if x.get('trackId')=='overlay'],key=lambda x:x.get('from',0));styles=[{k:v for k,v in x.get('props',{}).items() if k not in ['text','text_color','font_url']} for x in cues];sig=hashlib.sha256(json.dumps(styles[0] if styles else {},sort_keys=True).encode()).hexdigest();music=next((x for x in clips if x.get('trackId')=='audio'),{})
 upper_codes=[hashlib.sha256(x.get('props',{}).get('code','').encode()).hexdigest() for x in upper_titles(d)]
 upper_sig=hashlib.sha256(json.dumps(upper_codes).encode()).hexdigest() if upper_codes else None
 return {'upper_title_count':len(upper_codes),'upper_style_signature':upper_sig,'style_signature':sig,'uniform':bool(styles) and all(x==styles[0] for x in styles),'bgm_source':urlparse(music.get('props',{}).get('src','')).path,'voice_tracks':sum(x.get('trackId')=='voiceover' for x in clips)}

def cut_issues(d,cfg):
 rules=contract(cfg)
 if not rules:return []
 out=[];seen={};end=0;facts=cut_facts(d)
 for x in upper_titles(d):
  if rules.get('upper_single_line') and any(c in x['props']['params']['text'] for c in '\n\r'):out.append('上方花字未满足单行要求')
 for x in sorted([x for x in d.get('clips',[]) if x.get('trackId')=='main'],key=lambda x:x.get('from',0)):
  path=urlparse(x.get('props',{}).get('src','')).path
  if path in seen:out.append(f"片内重复源素材：{x['id']} / {seen[path]}")
  seen[path]=x['id']
  if x['from']!=end:out.append('主画面时间线有空洞或重叠')
  end=x['from']+x['durationInFrames']
 if end!=d.get('composition',{}).get('durationInFrames'):out.append('主画面未覆盖整片')
 if rules.get('subtitle_uniform_within_video') and not facts['uniform']:out.append('一条片内原生主字幕样式不统一')
 for x in d.get('clips',[]):
  if x.get('trackId')!='overlay':continue
  p=x.get('props',{});text=p.get('text','');units=sum(.5 if ord(c)<128 else 1 for c in text if not c.isspace())
  if '\n' in text or '\r' in text or units>rules['subtitle_max_units']:out.append('主字幕未满足单行短句要求')
  if p.get('font_size',0)>rules['subtitle_max_font_size']:out.append('主字幕字号超过已定上限')
  if p.get('top') is not None and p['top']<rules['subtitle_safe_top_percent']:out.append('主字幕位置进入人物上部区域，需调整并看图')
 if rules.get('whole_voice_required') and facts['voice_tracks']!=1:out.append('口播未采用整篇单轨合成；已批准的局部保真例外须任务留档')
 return list(dict.fromkeys(out))

def batch_issues(b,videos,cfg):
 rules=contract(cfg)
 if not rules or len(videos)<int(b.get('quantity',1)) or len(videos)<2:return []
 facts=[v.get('quality_facts') for _,v in videos];out=[]
 if any(x is None for x in facts):return ['整组真实字幕/BGM检查记录不完整']
 for label,key,need in [('字幕样式','style_signature',max(rules['min_subtitle_styles'],(cfg.get('batch_native_variants') or {}).get('required_actual_styles',0))),('BGM','bgm_source',max(rules['min_bgm'],(cfg.get('batch_native_variants') or {}).get('required_actual_bgm',0)))]:
  n=len({f[key] for f in facts})
  if n<min(len(facts),need):out.append(f'整组真实{label}仅{n}种，不能以候选配置数代替实际变化')
 need=(b.get('upper_title_variation') or {}).get('min_actual_sets',0)
 if need:
  actual={f.get('upper_style_signature') for f in facts if f.get('upper_style_signature')}
  if len(actual)<min(need,len(facts)):out.append(f'整组上方花字实际仅{len(actual)}套，要求至少{need}套；不能用主字幕样式或只换颜色代替花字变化')
 return out

def audit(a,cfg):
 import clipo_batch as cb
 cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';st=cb.State(a.run);report=[]
 for b in cb.load_briefs(a.run,a.brief):
  sid=st.data.get('briefs',{}).get(b['brief_id'],{}).get('script_id')
  if not sid:raise RuntimeError('尚无系统脚本，不能声称配置完成')
  script=cb.api('GET',f'/video-scripts/{sid}');sound_rows=[];skip=0
  while True:
   sound_catalog=cb.api('GET',f'/resource/sound-effect?limit=100&skip={skip}');page=sound_catalog if isinstance(sound_catalog,list) else sound_catalog.get('items',[]);sound_rows.extend(page)
   if len(page)<100 or isinstance(sound_catalog,list) or len(sound_rows)>=sound_catalog.get('total',len(sound_rows)):break
   skip+=len(page)
  sound_ids={x['id'] for x in sound_rows};lib=cb.list_segments(cfg['products'][b['product_key']]['clipo_product_id']);issues=config_issues(script,b,cfg,lib,sound_ids);report.append({'brief':b['brief_id'],'script_id':sid,'issues':issues});print(b['brief_id'],'PASS' if not issues else 'BLOCK '+str(issues),flush=True)
 p=Path(a.run)/'clipo/quality-config-audit.json';p.write_text(json.dumps(report,ensure_ascii=False,indent=2));return int(any(x['issues'] for x in report))

def main():
 from common import load_config
 p=argparse.ArgumentParser();p.add_argument('cmd',choices=['audit']);p.add_argument('--config',required=True);p.add_argument('--run',required=True);p.add_argument('--brief');a=p.parse_args();sys.exit(audit(a,load_config(a.config)))
if __name__=='__main__':main()

def delivery_issues(video,cfg):
 rules=contract(cfg)
 if not rules:return []
 out=[];e=video.get('media_qc') or {}
 if rules.get('require_media_qc') and (not e.get('technical_pass') or e.get('render_job')!=video.get('render_job') or e.get('output_object_key')!=video.get('object_key')):out.append('当前渲染的黑帧/音频检查未完成或失败')
 if rules.get('require_visual_review') and (not e.get('visual_pass') or not e.get('reviewer') or not e.get('review_note')):out.append('当前成片空镜/抖动/遮挡等实际视觉复核未完成')
 if rules.get('require_upper_title_samples') and e.get('upper_title_sample_count',0)<3*(video.get('quality_facts') or {}).get('upper_title_count',0):out.append('上方花字未逐处抽帧检查自动换行')
 return out
