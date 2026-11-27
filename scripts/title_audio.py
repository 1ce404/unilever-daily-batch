"""Synchronize actual short library sounds to every title; preserve all existing clips."""
import copy,json,math,random
from pathlib import Path
from quality_contract import upper_titles

def audible_sound(c):
 p=c.get('props') or {}
 return c.get('component')=='AudioClip' and c.get('trackId') in ('sound_effects','sfx') and bool(p.get('src')) and p.get('volume',1)>0 and not p.get('muted') and c.get('durationInFrames',0)>0

def missing_title_sounds(d,tolerance=1):
 disabled={t['id'] for t in d.get('tracks',[]) if t.get('enabled') is False}
 sounds=[c for c in d.get('clips',[]) if audible_sound(c) and c['trackId'] not in disabled]
 return [t for t in upper_titles(d) if not any(abs(t['from']-s['from'])<=tolerance for s in sounds)]

def attach(d,catalog,seed):
 result=copy.deepcopy(d);missing=missing_title_sounds(result)
 if not missing:return result,[]
 pool=[s for s in catalog if s.get('url') and 0<float(s.get('duration') or 0)<=2 and '产品出场' in (s.get('categories') or [])]
 if not pool:raise RuntimeError('No current short product-entry SFX; refresh task inventory')
 if any(t.get('id')=='sound_effects' and t.get('enabled') is False for t in result.get('tracks',[])):raise RuntimeError('Sound effect track disabled')
 if not any(t.get('id')=='sound_effects' for t in result.get('tracks',[])):result.setdefault('tracks',[]).append({'id':'sound_effects','name':'音效','enabled':True})
 rng=random.Random(seed);rng.shuffle(pool);receipts=[];fps=result['composition']['fps']
 for i,t in enumerate(missing):
  s=pool[i%len(pool)];duration=min(math.floor(float(s['duration'])*fps),result['composition']['durationInFrames']-t['from'])
  if duration<1:raise RuntimeError('No sound time available')
  result['clips'].append({'id':'title-sfx-'+t['id'],'trackId':'sound_effects','component':'AudioClip','from':t['from'],'durationInFrames':duration,'props':{'src':s['url'],'volume':.35,'startFrom':0,'playbackRate':1,'loop':False,'fadeIn':0,'fadeOut':min(3,duration)}})
  receipts.append({'title_id':t['id'],'from':t['from'],'sound_id':s['id'],'sound_name':s.get('name')})
 return result,receipts

def from_inventory(d,run,key):
 p=Path(run)/'clipo/task-inventory/sfx.json'
 if not p.exists():raise RuntimeError('Refresh current task SFX inventory before preparing title sounds')
 return attach(d,json.loads(p.read_text()),key)[0]
