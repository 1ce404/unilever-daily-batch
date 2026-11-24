#!/usr/bin/env python3
"""
[INPUT]: 依赖 resume_batch 的快照、REST 封装和文案检查；读取画面返工与音高证据。
[OUTPUT]: 提供九组新版交付状态、原任务补齐、追加终态等待与完整画面契约。
[POS]: scripts 的合格数量补齐边界；不建脚本，不改文案、人审或共享提示词。
       不限制平台选择的音色；保留逐条音高证据与其他交付门禁。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""
import argparse
import copy
import hashlib
import math
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import resume_batch as rb
import footage_qc as qc
from common import load_config

BRIEFS = tuple(f'B{i:02d}' for i in range(1, 10))
KNOWN_BAD = {'6afd679e067ddc831972950e': 'SEA/SCA 口播错误'}


def seeds(root):
    base = root / 'resumed-six'
    data = [rb.load(base / name) for name in ('online.json', 'continued-online.json')]
    merged = {key: [item for d in data for item in d[key]] for key in ('scripts', 'cuts', 'generations')}
    scripts = {s['title'].split('-')[1]: s for s in merged['scripts']}
    if set(scripts) != set(BRIEFS):
        raise RuntimeError('必须找到当天九组原脚本')
    tasks = {s['id']: [g for g in merged['generations'] if g['script_id'] == s['id']] for s in scripts.values()}
    if any(len(v) != 1 for v in tasks.values()):
        raise RuntimeError('每组必须只有一个原生成任务')
    return scripts, {bid: tasks[s['id']][0] for bid, s in scripts.items()}


def verify_script(original, current):
    for key in ('id', 'title', 'product_id', 'segments'):
        if rb.normalize(original[key]) != rb.normalize(current[key]):
            raise RuntimeError('原脚本已变，拒绝续产或交付：' + original['id'])


def receipts(root):
    result = {}
    for folder in ('rendered-three', 'resumed-six/b03-render', 'remaining-render', 'supplemental-render'):
        for path in (root / folder).glob('render-*.json'):
            result[rb.load(path)['cut_id']] = (path, rb.load(path))
    return result


def contracts(root):
    result = {}
    for folder in ('remaining-five/plans', 'resumed-six/continued-ready', 'resumed-six/b03-ready', 'supplemental/plans'):
        for path in (root / folder).glob('*.json'):
            p = rb.load(path)
            result[p['cut_id']] = p
    for bid in ('B01', 'B04', 'B09'):
        p = rb.load(root / f'repair-{bid}.json')
        result[p['cut_id']] = p
    return result


def copy_issues(cfg, script, cut):
    # ---------- 文案沿用既有门禁；未知源不冒充内容驳回 ----------
    product = next((k for k, p in cfg['products'].items() if p.get('clipo_product_id') == script['product_id']), None)
    if product not in ('2x', 'huxi'):
        raise RuntimeError('产品不在今日两号范围')
    row = rb.inspect_cut(cfg, script, cut, {}, {})
    issues = row['copy_issues']
    if not any(c.get('trackId') == 'watermark' for c in cut['draft']['clips']):
        issues.append('缺少水印')
    if cut['id'] in KNOWN_BAD:
        issues.append(KNOWN_BAD[cut['id']])
    return issues


def status(cfg, args):
    root = Path(args.root)
    original, tasks = seeds(root)
    live_scripts = rb.parallel(lambda s: rb.cb.api('GET', '/video-scripts/' + s['id']), original.values())
    scripts = {s['id']: s for s in live_scripts}
    for s in original.values():
        verify_script(s, scripts[s['id']])
    live_tasks = rb.parallel(lambda g: rb.cb.api('GET', '/video-cut-generations/' + g['id']), tasks.values())
    cut_ids = list(dict.fromkeys(cid for g in live_tasks for cid in g.get('cut_ids', [])))
    cuts = rb.parallel(lambda cid: rb.cb.api('GET', '/video-cuts/' + cid), cut_ids)
    rb.save(root / 'supplemental/online.json', {'scripts': live_scripts, 'cuts': cuts, 'generations': live_tasks})
    recs, plans = receipts(root), contracts(root)
    voices = {}
    for path in [root / 'batch-voice-check.json'] + sorted((root / 'supplemental').glob('voice-check-*.json')):
        if path.exists():
            voices.update(rb.load(path))
    rows = []
    for cut in cuts:
        cid = cut['id']
        script = scripts[cut['script_id']]
        bid = script['title'].split('-')[1]
        issues = copy_issues(cfg, script, cut)
        copy_ok = not issues
        receipt = recs.get(cid, (None, {}))[1]
        job = receipt.get('result') or {}
        expected = job.get('object_key')
        rendered = bool(expected and job.get('status') == 'success' and cut.get('output_status') == 'success' and cut.get('output_object_key') == expected)
        plan = plans.get(cid)
        visual = bool(plan and rb.normalize(qc.main_clips(cut['draft'])) == rb.normalize(plan['replacement_main']))
        if visual:
            qc.validate_sources(cut['draft'], plan['source_durations'])
        reviewed = cut.get('ai_review_status') == 'success' and cut.get('ai_review_result') == 'pass'
        local = receipt.get('local_file') or str(root / 'rendered-three/final' / f'{cid}.mp4')
        downloaded = Path(local).is_file()
        voice = voices.get(cid) or {}
        pitch_ok = bool(voice.get('f0') and all(voice['f0']) and voice.get('spread', math.inf) < 1.35)
        if not pitch_ok:
            issues.append('音高检查未通过或未完成')
        provisional = copy_ok and rendered and visual and reviewed and downloaded
        rows.append({'brief': bid, 'cut_id': cid, 'script_id': script['id'], 'product_id': script['product_id'],
                     'expected_output_object_key': expected, 'copy_ok': copy_ok, 'rendered': rendered,
                     'visual_contract': visual, 'review_status': cut.get('ai_review_status'),
                     'review_result': cut.get('ai_review_result'), 'review_reason': cut.get('ai_review_reason'),
                     'downloaded': downloaded, 'voice_ok': pitch_ok, 'issues': issues,
                     'provisional': provisional, 'qualified': provisional and pitch_ok})
    totals = {}
    for bid in BRIEFS:
        group = [r for r in rows if r['brief'] == bid]
        totals[bid] = {'target': 10, 'candidates': len(group), 'provisional': sum(r['provisional'] for r in group),
                       'qualified': sum(r['qualified'] for r in group)}
        print(bid, totals[bid], flush=True)
    rb.save(root / 'batch-completion.json', {'rows': rows, 'totals': totals})
    if all(t['qualified'] >= 10 for t in totals.values()):
        groups = []
        for name, bids in [('旗舰店', BRIEFS[:3]), ('洗护号', BRIEFS[3:])]:
            entries = []
            for bid in bids:
                selected = [r for r in rows if r['brief'] == bid and r['qualified']][:10]
                s = scripts[selected[0]['script_id']]
                entries.append({'title': s['title'], 'script_id': s['id'], 'product_id': s['product_id'], 'videos': selected})
            groups.append({'name': f'20261118-清扬-{name}-最终交付', 'entries': entries})
        rb.save(root / 'batch-share-plan.json', {'instruction': args.instruction, 'groups': groups})
        print('两个完整交付清单已准备，共90条合格新版', flush=True)


def continue_batch(cfg, args):
    if not args.instruction:
        raise RuntimeError('补齐缺少本批用户授权')
    plan = rb.load(args.plan)
    scripts, tasks = seeds(Path(args.root))
    rows = plan['rows']
    if len({r['brief'] for r in rows}) != len(rows):
        raise RuntimeError('补齐清单重复 brief')
    prepared = []
    for row in rows:
        bid, quantity = row['brief'], row['quantity']
        if bid not in scripts or type(quantity) is not int or not 1 <= quantity <= 15:
            raise RuntimeError('补齐范围或条数无效')
        current = rb.cb.api('GET', '/video-scripts/' + scripts[bid]['id'])
        verify_script(scripts[bid], current)
        task = rb.cb.api('GET', '/video-cut-generations/' + tasks[bid]['id'])
        if task['script_id'] != current['id'] or task['status'] in ('running', 'pending', 'queued'):
            raise RuntimeError('原任务不匹配或仍在运行')
        p = Path(args.out) / f'continue-{bid}.json'
        if p.exists():
            raise RuntimeError('本轮已有提交收据，禁止重复追加')
        prepared.append((p, {'brief': bid, 'quantity': quantity, 'generation_id': task['id'],
                             'script_id': current['id'], 'before_cut_ids': task.get('cut_ids', []),
                             'instruction': args.instruction, 'status': 'submitting'}))
    print('本次原任务补生成', sum(r['quantity'] for _, r in prepared), '条', [(r['brief'], r['quantity']) for _, r in prepared], flush=True)
    if args.dry_run:
        return
    for p, receipt in prepared:
        rb.save(p, receipt)
        try:
            receipt['response'] = rb.cb.api('POST', '/video-cut-generations/' + receipt['generation_id'] + '/continue', {'quantity': receipt['quantity']}, tries=1)
            receipt['status'] = 'submitted'
        except Exception as exc:
            receipt.update(status='uncertain', error=str(exc))
            rb.save(p, receipt)
            raise
        rb.save(p, receipt)


def poll_generations(cfg, args):
    paths = sorted(Path(args.out).glob('continue-B*.json'))
    if not paths:
        raise RuntimeError('没有追加收据')
    recs = [rb.load(p) for p in paths]
    end = time.monotonic() + args.wait
    while True:
        tasks = rb.parallel(lambda r: rb.cb.api('GET', '/video-cut-generations/' + r['generation_id']), recs)
        rb.save(Path(args.out) / 'generation-status.json', tasks)
        print('补生成进度', [(r['brief'], g['status'], len(g.get('cut_ids') or [])) for r, g in zip(recs, tasks)], flush=True)
        for r, g in zip(recs, tasks):
            if g['script_id'] != r['script_id']:
                raise RuntimeError('追加任务脚本不一致')
            if g['status'] in ('failed', 'canceled', 'cancelled', 'timeout'):
                raise RuntimeError('补生成失败：' + r['brief'])
        if all(g['status'] == 'success' and len(g.get('cut_ids') or []) >= len(r['before_cut_ids']) + r['quantity'] for r, g in zip(recs, tasks)):
            ids = [cid for r, g in zip(recs, tasks) for cid in g['cut_ids'] if cid not in r['before_cut_ids']]
            rb.save(Path(args.out) / 'new-cuts.json', rb.parallel(lambda cid: rb.cb.api('GET', '/video-cuts/' + cid), ids))
            return
        if time.monotonic() >= end:
            raise RuntimeError('补生成等待超时')
        time.sleep(args.interval)


def prepare(cfg, args):
    root = Path(args.root)
    scripts, tasks = seeds(root)
    by_id = {s['id']: s for s in scripts.values()}
    cut_script = {cid: task['script_id'] for task in tasks.values() for cid in task.get('cut_ids', [])}
    guides = {}
    for p in contracts(root).values():
        sid = p.get('script_id') or cut_script.get(p['cut_id'])
        if sid in by_id:
            guides.setdefault(sid, p)
    new = rb.load(Path(args.out) / 'new-cuts.json')
    paths = []
    skip = []
    for cut in new:
        script = by_id[cut['script_id']]
        issues = copy_issues(cfg, script, cut)
        if issues:
            skip.append({'cut_id': cut['id'], 'issues': issues})
            continue
        guide = guides[cut['script_id']]
        safe = {n: [c for c in guide['replacement_main'] if str(c['id']).split('-')[0] == n] for n in ('1', '2', '3', '4', '5')}
        original = cut['draft']
        main = qc.main_clips(original)
        fps = original['composition']['fps']
        durations = dict(guide['source_durations'])
        replacements = []
        pain_frames = sum(c['durationInFrames'] for c in main if str(c['id']).split('-')[0] in ('1', '2'))
        continuous_pain = qc.key(safe['1'][0]['props']['src']) == qc.key(safe['2'][0]['props']['src'])
        pain_available = sum(c['durationInFrames'] * c['props'].get('playbackRate', 1) for n in ('1', '2') for c in safe[n])
        pain_rate = min(1, pain_available / pain_frames)
        pain_cursor = safe['1'][0]['props'].get('startFrom', 0)
        for n in ('1', '2', '3', '4', '5'):
            clips = [c for c in main if str(c['id']).split('-')[0] == n]
            if not clips or not safe[n]:
                raise RuntimeError('缺少分段或目检安全窗口')
            template = copy.deepcopy(clips[0])
            count = sum(c['durationInFrames'] for c in clips)
            source = safe[n][0]
            available = sum(c['durationInFrames'] * c['props'].get('playbackRate', 1) for c in safe[n])
            rate = pain_rate if continuous_pain and n in ('1', '2') else min(1, available / count)
            if rate < 0.4:
                raise RuntimeError('追加口播过长，拒绝当前安全窗口')
            start = round(pain_cursor) if continuous_pain and n in ('1', '2') else round(source['props'].get('startFrom', 0))
            if continuous_pain and n in ('1', '2'):
                pain_cursor += count * rate
            template.update(id=n + '-supplemental-revision', durationInFrames=count)
            template['props'].update(src=source['props']['src'], startFrom=start, playbackRate=rate, loop=False)
            replacements.append(template)
        # ---------- 保留 CTA；只测时长并收敛画面超源 ----------
        replacements += copy.deepcopy([c for c in main if str(c['id']).split('-')[0] not in safe])
        for clip in replacements:
            props = clip['props']
            props['startFrom'] = round(props.get('startFrom', 0))
            key = qc.key(props['src'])
            if key not in durations:
                local = root / 'supplemental/source-cache' / hashlib.sha256(key.encode()).hexdigest()[:20] / 'source.mp4'
                local.parent.mkdir(parents=True, exist_ok=True)
                if not local.exists():
                    rb.cb.fetch_file(props['src'], local)
                durations[key] = float(qc.probe(local)['format']['duration'])
            limit = (durations[key] * fps - props['startFrom'] - 1) / clip['durationInFrames']
            props['playbackRate'] = min(props.get('playbackRate', 1), limit)
        replacements.sort(key=lambda c: c['from'])
        after = copy.deepcopy(original)
        after['clips'] = [c for c in original['clips'] if c.get('trackId') != 'main'] + replacements
        qc.validate_repair(original, after)
        qc.validate_sources(after, durations)
        plan = {'cut_id': cut['id'], 'script_id': cut['script_id'], 'brief': script['title'].split('-')[1],
                'instruction': args.instruction, 'reason': '原任务合格数量补齐；复用已目检画面窗口，技术段只取纯质地，保留原声音字幕。',
                'expected_main': main, 'replacement_main': replacements, 'source_durations': durations}
        paths.append(str(rb.save(root / 'supplemental/plans' / (cut['id'] + '.json'), plan)))
    rb.save(Path(args.out) / 'plan-index.json', paths)
    rb.save(Path(args.out) / 'copy-rejected.json', skip)
    rb.save(Path(args.out) / 'voice-state.json', {'videos': {rb.load(p)['cut_id']: {'cut_id': rb.load(p)['cut_id']} for p in paths}, 'shares': {}})
    print('补生成画面契约', len(paths), '条，文案拒绝', len(skip), '条', flush=True)


def poll_reviews(cfg, args):
    paths = sorted(Path(args.out).glob('render-*.json'))
    if not paths:
        raise RuntimeError('没有渲染收据')
    end = time.monotonic() + args.wait
    finishing = argparse.Namespace(out=args.out, batch_root=str(Path(args.root) / 'supplemental'), delivery=args.delivery)
    while True:
        rb.finish(cfg, finishing)
        recs = [rb.load(p) for p in paths]
        if any(r.get('result', {}).get('status') in ('failed', 'canceled', 'cancelled') for r in recs):
            raise RuntimeError('补生成渲染失败')
        if all(r.get('result', {}).get('status') == 'success' and r.get('review_status') in ('success', 'failed') for r in recs):
            return
        if time.monotonic() >= end:
            raise RuntimeError('新版渲染或审核等待超时')
        time.sleep(args.interval)


def main():
    ap = argparse.ArgumentParser(description='今日九组原任务的合格数量与新版交付边界')
    ap.add_argument('cmd', choices=['status', 'continue', 'poll-generations', 'prepare', 'poll-reviews'])
    ap.add_argument('--config', default='qingyang')
    ap.add_argument('--root', required=True)
    ap.add_argument('--out')
    ap.add_argument('--plan')
    ap.add_argument('--instruction', default='')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--wait', type=int, default=1800)
    ap.add_argument('--delivery', default='/Users/bingmeishi/Projects/myskill/本地交付/20261118-清扬-修订')
    ap.add_argument('--interval', type=int, default=30)
    args = ap.parse_args()
    if args.wait <= 0 or args.interval < 1:
        ap.error('等待时长与间隔必须为正')
    cfg = load_config(args.config)
    rb.cb.CFG, rb.cb.BASE = cfg, cfg['clipo']['base_url'].rstrip('/') + '/api/v1'
    {'status': status, 'continue': continue_batch, 'poll-generations': poll_generations, 'prepare': prepare, 'poll-reviews': poll_reviews}[args.cmd](cfg, args)


if __name__ == '__main__':
    main()
