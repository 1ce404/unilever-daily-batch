#!/usr/bin/env python3
"""One explicitly approved copy/whole-voice correction on an existing batch cut.
Preserves the original TTS receipts and production snapshot. Never creates a cut,
tops up a generation, or retries an uncertain paid request.
"""
import argparse
import copy
import importlib.util
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from task_caption_repair import reflow, norm


def native_defaults(draft):
    value=copy.deepcopy(draft)
    for clip in value['clips']:
        for key in ('transitionIn','transitionOut','motion'):
            clip.setdefault(key,None)
    return value


def main():
    p=argparse.ArgumentParser()
    for name in ('config','run','production-skill','plan'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--resume',action='store_true',help='Only resume an exactly matching already-written draft; no TTS or PUT')
    a=p.parse_args();root=Path(a.run).resolve();snapshot=Path(a.production_skill).resolve()
    prior=json.loads((root/'clipo/18条返工与量产-后台收据.json').read_text())
    if str(snapshot)!=prior['production_snapshot']:
        raise RuntimeError('Original production snapshot mismatch')
    sys.path.insert(0,str(snapshot/'scripts'))
    import clipo_batch as cb
    import task_demo as td
    from common import load_config
    from footage_qc import stable_media
    from qc import cut_checks
    from quality_contract import cut_facts
    spec=importlib.util.spec_from_file_location('explicit_task_bulk_repair',Path(__file__).with_name('task_bulk.py'))
    bulk=importlib.util.module_from_spec(spec);spec.loader.exec_module(bulk)
    cfg=load_config(a.config);cb.CFG=cfg;cb.BASE=cfg['clipo']['base_url'].rstrip('/')+'/api/v1'
    st=cb.State(root);plan=td.load(a.plan);key=plan['key'];v=st.videos[key]
    if v.get('customer_rework_required') or key.endswith('V01') or not plan.get('authorization') or plan.get('whole_tts')!=1:
        raise RuntimeError('One approved new-batch correction required')
    bid=v['brief_id'];b=next(b for b in cb.load_briefs(root) if b['brief_id']==bid)
    original_budget=td.load(root/'task-bulk'/(bid+'-budget.json'))
    if key not in original_budget['keys'] or len(plan['texts'])!=len(b['segments']):
        raise RuntimeError('Not a current existing batch variant')
    cut=cb.api('GET','/video-cuts/'+v['cut_id'])
    if cut['id']!=plan['cut_id']:
        raise RuntimeError('Owned cut changed')
    old_intent=td.load(root/'task-bulk'/key/'tts-intent.json')
    if old_intent['voice']['speed_ratio']!=1.4:
        raise RuntimeError('Original approved speed differs')
    revision=plan['revision'];work=root/'task-bulk'/key/'revisions'/revision
    if not td.re.fullmatch(r'[a-zA-Z0-9_-]{1,40}',revision):
        raise RuntimeError('Invalid revision')
    work.mkdir(parents=True,exist_ok=True)
    receipt=work/'applied.json';budget=work/'repair-budget.json'
    fixed={'plan':plan,'keys':[key],'whole_tts':1,'authorization':plan['authorization'],'original_voice':old_intent['voice'],'new_videos':0,'same_cut_renders':1}
    if a.dry_run:
        td.save(budget,{**fixed,'at':time.time()})
        print('DRY-RUN',key,'1 whole TTS, original voice/speed1.4, 1 same-cut render, 0 new videos')
        return
    if receipt.exists():
        if td.load(receipt)['plan']!=plan:
            raise RuntimeError('Already applied different correction')
        print(key,'already applied; no resubmit');return
    authorized=td.load(budget)
    if {k:authorized[k] for k in fixed}!=fixed or time.time()-authorized['at']>3600:
        raise RuntimeError('Missing exact recent speech-repair dry-run')
    v=copy.deepcopy(v);v['texts']=plan['texts']
    if a.resume:
        if not (work/'cut-before.json').exists():
            raise RuntimeError('No original native update to resume')
        draft=native_defaults(td.load(work/'draft.json'))
        if stable_media(native_defaults(cut['draft']))!=stable_media(draft):
            raise RuntimeError('Already-written draft differs; do not overwrite')
        prepared=td.load(work/'prepared.json')
        v.update(voice=prepared['voice'],native_variant=prepared['variant'],copy_issues=[],voice_cmd=False,duration_s=draft['composition']['durationInFrames']/draft['composition']['fps'],task_bulk_prepared_path=str((work/'prepared.json').resolve()))
    else:
        if (work/'cut-before.json').exists():
            raise RuntimeError('Prior update exists; use explicit --resume to verify it')
        draft=bulk.prepare(cb,st,b,v,key,cut,cut['draft'],repair_revision=revision,repair_budget=authorized)
        tts=td.load(work/'tts.json');draft=native_defaults(reflow(draft,tts,plan['texts'],cfg))
    intent=td.load(work/'tts-intent.json');tts=td.load(work/'tts.json')
    if intent['voice']!=old_intent['voice'] or norm(tts['synthesized_text']).casefold()!=norm(''.join(plan['texts'])).casefold():
        raise RuntimeError('Voice, speed or synthesized text changed')
    issues=cut_checks(b,draft,v.get('voice'))
    if issues:
        raise RuntimeError('Corrected variant gate '+str(issues))
    signature=td.digest(stable_media(draft));before=work/'cut-before.json'
    if a.resume:
        back=cut
    else:
        td.save(work/'draft.json',draft);td.save(before,cut)
        cb.api('PUT','/video-cuts/'+cut['id'],{'script_id':cut['script_id'],'draft':draft},tries=1)
        back=cb.api('GET','/video-cuts/'+cut['id'])
    td.save(work/'cut-after.json',back)
    if stable_media(native_defaults(back['draft']))!=stable_media(draft):
        raise RuntimeError('Corrected variant readback mismatch')
    if (work/'render-intent.json').exists():
        raise RuntimeError('Uncertain render request; reconcile its idempotency key before retry')
    td.save(work/'render-intent.json',{'cut_id':cut['id'],'hash':signature})
    job=cb.api('POST','/render-jobs',{'spec':back['draft'],'cut_id':cut['id'],'options':{'codec':'h264','quality':'high','scale':1},'idempotency_key':'approved-copy-repair-'+cut['id']+'-'+signature[:16]},tries=1)
    td.save(receipt,{'plan':plan,'job':job,'original_voice':old_intent['voice']})
    v.update(render_job=job['job_id'],stage='rendering',ai_review=None,ai_reason=None,qc_issues=[],quality_facts=cut_facts(back['draft']),copy_repair_approval=plan['authorization'])
    for k in ('last_error','error','object_key','media_qc','visual_review','review_waived'):
        v.pop(k,None)
    st.videos[key].clear();st.videos[key].update(v);st.save()
    print(key,'approved copy correction rendering',job['job_id'],flush=True)


if __name__=='__main__':
    main()
