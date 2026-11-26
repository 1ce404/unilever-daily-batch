#!/usr/bin/env python3
"""Reflow native batch captions using cached word times, without TTS or source changes.
The original production snapshot supplies API/state/QC code; the repair has its own
exact plan, backup and idempotent receipt. Never alters the running production code.
"""
import argparse
import copy
import hashlib
import json
import re
import sys
import time
from pathlib import Path

TERMS = ('力士精油香氛洗发水', '力士护发精油喷雾', '天然鲜花精油', '4D玻尿酸')


def norm(text):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', text)


def semantic_cues(tts, texts, fps, max_units=10):
    whole = ''.join(norm(t) for t in texts)
    tokens, position, punct = [], 0, set()
    for item in tts.get('timeline', {}).get('items', []):
        word = norm(item['text'])
        if not word:
            if re.search(r'[，。！？、；：,.!?;:]', item['text']):
                punct.add(position)
            continue
        tokens.append({**item, 'a': position, 'b': position + len(word)})
        position += len(word)
        if re.search(r'[，。！？、；：,.!?;:]\s*$', item['text']):
            punct.add(position)
    if ''.join(norm(t['text']) for t in tokens).casefold() != whole.casefold():
        raise RuntimeError('Cached native word timing differs from actual full copy')
    atoms = sorted((m.start(), m.end()) for term in TERMS
                   for m in re.finditer(re.escape(term), whole))
    boundaries = {0, *[t['b'] for t in tokens]}
    if any(a not in boundaries or b not in boundaries for a, b in atoms):
        raise RuntimeError('A required name boundary bisects a native token; no guessed timing')
    cues, cursor = [], 0
    while cursor < len(whole):
        stop = min(len(whole), cursor + max_units)
        following = next(((a, b) for a, b in atoms if b > cursor), None)
        if following and following[0] == cursor:
            end = following[1]
        elif following and following[0] < stop:
            a, b = following
            before = [z for z in punct if cursor < z <= a and z in boundaries]
            if before:
                end = min(before)
            else:
                end = b if a - cursor == 1 and b - cursor <= max_units else a
        else:
            ends = [b for b in boundaries if cursor < b <= stop
                    and not any(a < b < z for a, z in atoms)]
            if not ends:
                raise RuntimeError('No single-line native token boundary')
            semantic = [b for b in ends if b in punct and b - cursor >= 2]
            end = min(semantic) if semantic else max(ends)
            next_phrase = min((z for z in punct if z > end), default=len(whole))
            if next_phrase - end in (1, 2):
                earlier = [b for b in ends if b <= end-2]
                if earlier:
                    end = max(earlier)
        selected = [t for t in tokens if t['b'] > cursor and t['a'] < end]
        if not selected or selected[0]['a'] != cursor or selected[-1]['b'] != end:
            raise RuntimeError('Caption would bisect native speech token')
        start = int(selected[0]['start_ms'] * fps // 1000)
        finish = int(selected[-1]['end_ms'] * fps // 1000)
        if finish <= start:
            raise RuntimeError('Invalid caption interval')
        cues.append({'text': whole[cursor:end], 'from': start,
                     'durationInFrames': finish - start})
        cursor = end
    for left, right in zip(cues, cues[1:]):
        left['durationInFrames'] = min(left['durationInFrames'], right['from'] - left['from'])
    return cues


def reflow(draft, tts, texts, cfg):
    result = copy.deepcopy(draft)
    old = [c for c in draft['clips'] if c['trackId'] == 'overlay']
    if not old or norm(''.join(c['props']['text'] for c in sorted(old, key=lambda c: c['from']))).casefold() != norm(''.join(texts)).casefold():
        raise RuntimeError('Existing subtitles and actual batch copy differ')
    signature = lambda c: {k: v for k, v in c['props'].items() if k not in ('text', 'text_color')}
    if any(signature(c) != signature(old[0]) for c in old):
        raise RuntimeError('Existing per-video subtitle style is not uniform')
    template = old[0]
    # Restore the actual non-highlight color, not the first cue's possible keyword color.
    rules = cfg.get('caption_highlights', {}).get('rules', [])
    ordinary = next((c for c in old if not any(term in c['props']['text'] for rule in rules for term in rule['terms'])), None)
    if ordinary is None:
        raise RuntimeError('No ordinary native caption style available')
    base_color = ordinary['props'].get('text_color')
    captions = []
    for i, cue in enumerate(semantic_cues(tts, texts, draft['composition']['fps'])):
        c = copy.deepcopy(template)
        c.update(cue, id=f's-reflow-{i}', transitionIn=None, transitionOut=None, motion=None)
        c['props']['text'] = c.pop('text')
        c['props']['text_color'] = base_color
        for rule in rules:
            if any(term in c['props']['text'] for term in rule['terms']):
                color = rule['color']
                if str(base_color).lower() == '#000000':
                    color = '#8F5700' if color == '#FFD36A' else '#944868'
                c['props']['text_color'] = color
                break
        captions.append(c)
    result['clips'] = [c for c in result['clips'] if c['trackId'] != 'overlay'] + captions
    if [c for c in result['clips'] if c['trackId'] != 'overlay'] != [c for c in draft['clips'] if c['trackId'] != 'overlay']:
        raise RuntimeError('Protected audio, source or packaging changed')
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    p.add_argument('--run', required=True)
    p.add_argument('--production-skill', required=True)
    p.add_argument('--plan', required=True)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    root = Path(a.run).resolve()
    snapshot = Path(a.production_skill).resolve()
    background = json.loads((root/'clipo/18条返工与量产-后台收据.json').read_text())
    if str(snapshot) != background['production_snapshot']:
        raise RuntimeError('Repair must use the saved original production snapshot')
    sys.path.insert(0, str(snapshot/'scripts'))
    import clipo_batch as cb
    import task_demo as td
    from common import load_config
    from footage_qc import stable_media
    from qc import cut_checks
    from quality_contract import cut_facts
    cfg = load_config(a.config)
    cb.CFG = cfg
    cb.BASE = cfg['clipo']['base_url'].rstrip('/')+'/api/v1'
    plan = td.load(a.plan)
    if not plan.get('authorization') or not plan.get('keys'):
        raise RuntimeError('Explicit scoped repair authorization required')
    st = cb.State(root)
    briefs = {b['brief_id']: b for b in cb.load_briefs(root)}
    out = root/'caption-repair'/plan['revision']
    out.mkdir(parents=True, exist_ok=True)
    prepared = []
    for key in plan['keys']:
        v = st.videos[key]
        if v.get('customer_rework_required') or v['brief_id'] not in ('1126-L02', '1126-L03') or key.endswith('V01'):
            raise RuntimeError('Outside authorized new-batch repair scope '+key)
        cut = cb.api('GET', '/video-cuts/'+v['cut_id'])
        tts = td.load(root/'task-bulk'/key/'tts.json')
        draft = reflow(cut['draft'], tts, v['texts'], cfg)
        issues = cut_checks(briefs[v['brief_id']], draft, v.get('voice'))
        if issues:
            raise RuntimeError('Caption repair gate '+key+': '+str(issues))
        sig = td.digest(stable_media(draft))
        prepared.append((key, cut, draft, sig))
    budget = {'plan': plan, 'hashes': {k:s for k,c,d,s in prepared}, 'TTS': 0, 'new_videos': 0, 'rerenders':len(prepared)}
    if a.dry_run:
        td.save(out/'budget.json', {**budget, 'at': time.time()})
        print('DRY-RUN',len(prepared),'same-cut caption rerenders, 0 TTS, 0 new videos')
        return
    prior = td.load(out/'budget.json')
    if time.time()-prior['at'] > 3600 or {k:prior[k] for k in budget} != budget:
        raise RuntimeError('Missing exact recent caption repair dry-run')
    for key, cut, draft, sig in prepared:
        receipt = out/(key+'-'+sig[:16]+'.json')
        if receipt.exists():
            print(key, 'reuse existing render receipt; no resubmit')
            continue
        before = out/(key+'-before.json')
        if before.exists():
            raise RuntimeError('Uncertain previous repair; inspect before retrying '+key)
        td.save(before, cut)
        cb.api('PUT', '/video-cuts/'+cut['id'], {'script_id':cut['script_id'], 'draft':draft}, tries=1)
        back = cb.api('GET', '/video-cuts/'+cut['id'])
        if stable_media(back['draft']) != stable_media(draft):
            raise RuntimeError('Caption repair readback mismatch')
        td.save(out/(key+'-after.json'), back)
        td.save(out/(key+'-render-intent.json'), {'cut_id':cut['id'], 'hash':sig})
        job = cb.api('POST', '/render-jobs', {'spec':back['draft'], 'cut_id':cut['id'], 'options':{'codec':'h264','quality':'high','scale':1}, 'idempotency_key':'caption-repair-'+cut['id']+'-'+sig[:16]}, tries=1)
        td.save(receipt, job)
        v = st.videos[key]
        v.update(render_job=job['job_id'], stage='rendering', ai_review=None, ai_reason=None, qc_issues=[], quality_facts=cut_facts(back['draft']), caption_repair=str(receipt))
        for field in ('object_key','error','last_error','media_qc','visual_review','review_waived'):
            v.pop(field, None)
        st.save()
        print(key,'caption repair rendering',job['job_id'],flush=True)


if __name__ == '__main__':
    main()
