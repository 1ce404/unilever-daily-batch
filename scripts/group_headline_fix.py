#!/usr/bin/env python3
"""[INPUT] Three actually inspected close-hair frames with an upper headline overlap.
[OUTPUT] Scoped packaging-only revision; voice, captions, sources and native right-avatar CTA preserved.
[POS] Fix only B01-V03/B01-V09/B02-V10 of the authorized 30-cut group.
"""
import argparse,copy,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import group_rework_apply as g
import clipo_batch as cb
from common import load_config
KEYS=['B01-V03','B01-V09','B02-V10']
def main():
 p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--dry-run',action='store_true');p.add_argument('--short-title',action='store_true');a=p.parse_args();root=Path(a.out);cfg=load_config('qingyang');cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1';archive=root/'attempts/headline-safe';intent=root/'headline-safe-plan.json'
 if g.read(root/'inventory.json')['group_id']!='6b068349429cee6fdc0865db':raise RuntimeError('Wrong authorized group')
 if a.short_title:
  key='B03-V03';archive=root/'attempts/headline-short';planpath=root/'plans'/(key+'.json');intent=root/'headline-short-plan.json'
  if (root/'headline-short-prepared.json').exists():print('Short headline already prepared');return
  before=g.read(planpath);wanted=copy.deepcopy(before);title=next(c for c in wanted['draft']['clips'] if c['id']=='upper-1');title['props']['params']['text']='SCA控油蓬松';g.validate(g.read(root/'cuts'/(key+'.json'))['draft'],wanted)
  if [c for c in before['draft']['clips'] if c['id']!='upper-1']!=[c for c in wanted['draft']['clips'] if c['id']!='upper-1']:raise RuntimeError('Non-headline changed')
  if a.dry_run:g.save(intent,{'before':before,'wanted':wanted});print('DRY-RUN existing 1 render; shorten only native upper title, preserve full spoken/native subtitle SCA technology name');return
  record=g.read(intent)
  if record!={'before':before,'wanted':wanted}:raise RuntimeError('Short title plan changed')
  current=cb.api('GET','/video-cuts/'+before['cut_id'])
  if g.digest(current['draft'])!=g.digest(before['draft']):raise RuntimeError('Concurrent cut change')
  g.save(archive/'cut-before.json',current);g.save(archive/'plan-before.json',before);g.save(planpath,wanted)
  for name in [key+'.json',key+'-intent.json']:
   f=root/'renders'/name
   if f.exists():f.rename(archive/name)
  f=root/'qc-new'/key
  if f.exists():f.rename(archive/(key+'-qc'))
  g.save(root/'headline-short-prepared.json',{'key':key,'reason':'Actual native upper headline wrapped final character; concise selling headline only. Original voiced full name and main caption unchanged.'});print('Single upper headline shortened; previous evidence preserved');return

 if (root/'headline-safe-prepared.json').exists():print('Scoped headline repair already prepared');return
 plans={}
 for key in KEYS:
  before=g.read(root/'plans'/(key+'.json'));wanted=copy.deepcopy(before);d=wanted['draft'];product=min([c for c in d['clips'] if c['trackId']=='main' and c['id'].startswith('3-')],key=lambda c:c['from']);title=next(c for c in d['clips'] if c['id']=='upper-0');title.update(**{'from':product['from']+4},durationInFrames=min(62,product['durationInFrames']-4));g.validate(g.read(root/'cuts'/(key+'.json'))['draft'],wanted)
  if [c for c in before['draft']['clips'] if c['id']!='upper-0']!=[c for c in wanted['draft']['clips'] if c['id']!='upper-0']:raise RuntimeError('Non-headline clip changed')
  plans[key]={'before':before,'wanted':wanted}
 if a.dry_run:g.save(intent,plans);print('DRY-RUN 3 packaging-only existing renders; move first title from hair close-up to clear product shot; no TTS/AIGC');return
 saved=g.read(intent)
 if saved!=plans:raise RuntimeError('Headline repair plan changed')
 for key,change in plans.items():
  current=cb.api('GET','/video-cuts/'+change['before']['cut_id'])
  if g.digest(current['draft'])!=g.digest(change['before']['draft']):raise RuntimeError('Concurrent cut change')
  g.save(archive/(key+'-cut-before.json'),current);g.save(archive/(key+'-plan-before.json'),change['before']);g.save(root/'plans'/(key+'.json'),change['wanted'])
  for name in [key+'.json',key+'-intent.json']:
   f=root/'renders'/name
   if f.exists():f.rename(archive/name)
  f=root/'qc-new'/key
  if f.exists():f.rename(archive/(key+'-qc'))
 # Keep the generating script safe for future close-up selections too.
 for sid in ['6b0666f8429cee6fdc0863c4','6b0666fa429cee6fdc0863c6']:
  current=cb.api('GET','/video-scripts/'+sid);g.save(archive/(sid+'-script-before.json'),current);body={k:copy.deepcopy(current[k]) for k in ['product_id','title','video','config','vo_rewrite_prompt','segments']};s2=next(s for s in body['segments'] if s['id']=='2');s3=next(s for s in body['segments'] if s['id']=='3');s3['effect']=copy.deepcopy(s2['effect']);s2['effect']=None;cb.api('PUT','/video-scripts/'+sid,body);back=cb.api('GET','/video-scripts/'+sid)
  if any(g.canonical(body[k])!=g.canonical(back[k]) for k in body):raise RuntimeError('Script headline readback mismatch')
  g.save(root/'script-headline-readbacks'/(sid+'.json'),back)
 g.save(root/'headline-safe-prepared.json',{'keys':KEYS,'reason':'Actual close-up review: first upper headline overlays hair. Only change headline timing; display over clean product shot.'});print('Three exact headline changes prepared; original rendered evidence preserved')
if __name__=='__main__':main()
