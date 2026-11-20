#!/usr/bin/env python3
"""
[INPUT]: 依赖 clipo_batch 的 REST 封装、配置和下载器；读取既有线上脚本与剪辑。
[OUTPUT]: 提供原任务续产、本地返工契约；finish 汇合九组下载送审，review-status 只读查询新输出审核。
[POS]: scripts 的断点恢复边界。续产仅追加原任务；返工采用 footage_qc 契约；不建脚本、不改文案、不记录人审、不建分享。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""
import argparse
import concurrent.futures
import copy
import hashlib
import json
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import load_config
from lint_copy import lint_text, lint_script
from urllib.parse import urlparse


def object_key(value):
    return urlparse(value).path.lstrip('/') if '://' in value else value.split('?')[0]


def normalize(value):
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items() if not (k == 'loop' and v in (None, False))}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if isinstance(value, str) and value.startswith(('https://', 'http://')):
        return object_key(value)
    return value

BRIEFS = ('B02', 'B03', 'B05', 'B06', 'B07', 'B08')


def load(path):
    return json.loads(Path(path).read_text())


def save(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return p


def backup(root, label, data):
    return save(Path(root) / 'backups' / f'{label}-{datetime.now():%Y%m%d-%H%M%S-%f}.json', data)


def listing(resource, query=''):
    rows, skip = [], 0
    while True:
        data = cb.api('GET', f'/{resource}?skip={skip}&limit=100{query}')
        page = data if isinstance(data, list) else data.get('items', [])
        rows.extend(page)
        if len(page) < 100 or (isinstance(data, dict) and skip + len(page) >= data.get('total', 10**9)):
            return rows
        skip += 100
        if skip >= 10000:
            raise RuntimeError(f'{resource} 列表超过安全上限')


def parallel(fn, items):
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(fn, items))


def schema(cfg, args):
    with urllib.request.urlopen(cb.BASE + '/openapi.json', timeout=60) as r:
        data = json.load(r)
    picked = {k: v for k, v in data['paths'].items() if any(s in k for s in ['render-jobs', 'video-cut-generations', 'video-cuts'])}
    save(Path(args.out) / 'api-schema.json', {'paths': picked, 'components': data.get('components')})
    for path, methods in picked.items():
        print(path, ','.join(methods))
        if path.endswith(('render-jobs', 'video-cut-generations', 'video-cuts')):
            print(json.dumps(methods.get('get'), ensure_ascii=False))


def status(cfg, args):
    # ---------- 恢复线上事实，不依赖空 brief ----------
    source = load(args.source)
    scripts = {x['script']['id']: x['script'] for x in source if x['script']['title'].split('-')[1] in BRIEFS}
    if len(scripts) != 6:
        raise RuntimeError('必须精确找到六个 brief 的原脚本')
    cuts = listing('video-cuts')
    cuts = [c for c in cuts if c.get('script_id') in scripts]
    full = parallel(lambda c: cb.api('GET', '/video-cuts/' + c['id']), cuts)
    detail = parallel(lambda sid: cb.api('GET', '/video-scripts/' + sid), scripts)
    generations = listing('video-cut-generations')
    generations = [g for g in generations if g.get('script_id') in scripts]
    # 平台没有渲染列表；剪辑 output_status 与本命令收据共同防重复。
    renders = []
    for p in sorted((Path(args.out) / 'renders').glob('*.json')):
        receipt = load(p)
        job = cb.api('GET', '/render-jobs/' + receipt['job_id'])
        renders.append(dict(job, cut_id=receipt['cut_id']))
    value = {'captured_at': datetime.now().isoformat(), 'scripts': detail, 'cuts': full,
             'generations': generations, 'renders': renders}
    if (Path(args.out) / 'online.json').exists():
        backup(args.out, 'online-snapshot', load(Path(args.out) / 'online.json'))
    save(Path(args.out) / 'online.json', value)
    for script in detail:
        bid = script['title'].split('-')[1]
        group = [c for c in full if c['script_id'] == script['id']]
        counts = {}
        for c in group:
            key = str((c.get('output_status'), c.get('ai_review_result')))
            counts[key] = counts.get(key, 0) + 1
        gs = [g for g in generations if g.get('script_id') == script['id']]
        print(bid, script['id'], len(group), counts, 'generations', [(g['id'], g.get('status'), g.get('quantity')) for g in gs], flush=True)
    print('render states', [(r.get('job_id') or r.get('id'), r.get('cut_id'), r.get('status')) for r in renders], flush=True)


def inspect_cut(cfg, script, cut, library, pool):
    pk = '2x' if script['product_id'] == cfg['products']['2x']['clipo_product_id'] else 'huxi'
    product = cfg['products'][pk]
    segments = [dict(s, text=s.get('content', ''), role=('cta' if i == len(script['segments']) - 1 else 'product' if i == 2 else 'hook' if i == 0 else 'body')) for i, s in enumerate(script['segments'])]
    title_parts = script['title'].split('-')
    brief = {'selling_point': title_parts[3], 'segments': segments}
    raw = [r['content'] for r in cut.get('random_result', {}).get('vo_rewrite', [])]
    texts = [re.sub(r'\[#[^\]]*\]', '', t) for t in raw]
    issues = [m for t in texts for level, m in lint_text(t, cfg, product) if level == 'error']
    issues += [m for level, m in lint_script(texts, segments, brief, cfg, product) if level == 'error']
    if len(texts) != len(segments):
        issues.append('平台改写分段数量不符')
    if any('[#' in t for t in raw):
        issues.append('口播包含语音指令')
    draft = cut['draft']
    fps = draft['composition']['fps']
    main = sorted([x for x in draft['clips'] if x.get('trackId') == 'main'], key=lambda x: x['from'])
    structural, sources, technical = [], [], []
    end = 0
    for clip in main:
        p = clip.get('props', {})
        src = library.get(object_key(p.get('src', '')))
        n = str(clip['id']).split('-')[0]
        row = {'clip_id': clip['id'], 'from': clip['from'], 'frames': clip['durationInFrames'],
               'source_id': src.get('id') if src else None, 'source_name': src.get('name') if src else None,
               'object_key': object_key(p.get('src', '')), 'start_from': p.get('startFrom', 0),
               'playback_rate': p.get('playbackRate', 1)}
        if clip['from'] != end or clip['durationInFrames'] < 2 or p.get('loop'):
            structural.append(f'{clip["id"]} 空隙、重叠、单帧或循环')
        end = clip['from'] + clip['durationInFrames']
        if src:
            duration = (src.get('video_info') or {}).get('duration_ms') or src.get('end_ms', 0) - src.get('begin_ms', 0)
            row['source_duration'] = duration / 1000
            consumed = p.get('startFrom', 0) / fps + clip['durationInFrames'] / fps * p.get('playbackRate', 1)
            if consumed > duration / 1000 + 1 / fps:
                structural.append(f'{clip["id"]} 播放超出源片 {consumed:.3f}>{duration/1000:.3f}')
        else:
            structural.append(f'{clip["id"]} 来源尚未对照素材库')
        if n in ('3', '4') and object_key(p.get('src', '')) not in pool:
            technical.append(row)
        if n in ('1', '2', '5'):
            sources.append(row)
        row['segment'] = n
    if end != draft['composition']['durationInFrames']:
        structural.append('主画面未覆盖整片')
    # 用户 11-18：删掉「同一条视频人脸一致」的要求，人物窗口可以来自不同源片
    voice = cut.get('random_result', {}).get('tts_speaker_id')
    voices = set(v for group in cfg['tts'].get('voices_by_gender', {}).values() for v in group)
    if voice not in voices:
        structural.append(f'音色不在配置白名单：{voice}')
    if not any(x.get('trackId') == 'watermark' for x in draft['clips']):
        structural.append('缺少合规水印轨道内容')
    return {'brief': title_parts[1], 'cut_id': cut['id'], 'product_key': pk, 'copy_issues': list(dict.fromkeys(issues)),
            'structural_issues': structural, 'technical_unverified': technical, 'person_sources': sources,
            'output_status': cut.get('output_status'), 'ai_review_result': cut.get('ai_review_result'),
            'ai_review_reason': cut.get('ai_review_reason'),
            'deliverable': cut.get('output_status') == 'success' and cut.get('ai_review_result') == 'pass' and not (issues or structural or technical)}


def audit(cfg, args):
    root = Path(args.out)
    online = load(root / 'online.json')
    lib = load(args.library)['records']
    library = {object_key(r['segment'].get('object_url') or ''): r['segment'] for r in lib}
    retained = load(args.pool)['records']
    pool = {object_key(r['segment']['object_url']): r for r in retained}
    scripts = {s['id']: s for s in online['scripts']}
    rows = [inspect_cut(cfg, scripts[c['script_id']], c, library, pool) for c in online['cuts']]
    save(root / 'audit.json', {'generated_at': datetime.now().isoformat(), 'rows': rows})
    plans = []
    for row, cut in zip(rows, online['cuts']):
        if not row['technical_unverified']:
            continue
        draft = copy.deepcopy(cut['draft'])
        main = sorted([c for c in draft['clips'] if c.get('trackId') == 'main'], key=lambda c: c['from'])
        # 两个技术窗口各选独立纯质地长源，按源片真实时长限界。
        selected = [r for r in retained if r['product_key'] == row['product_key'] and r['segment']['id'] in (
            '6ad5b22e43d91170f3d22595', '6ad5ce6c43d91170f3d225f2',
            '6adb50275772ef9c43bab411', '6adb504a5772ef9c43bab433')]
        if len(selected) < 2:
            raise RuntimeError('复核纯质地源不足两条')
        for n, source in zip(('3', '4'), selected):
            clips = [c for c in main if str(c['id']).split('-')[0] == n]
            if not clips:
                raise RuntimeError('无法定位技术窗口')
            first, frames = min(c['from'] for c in clips), sum(c['durationInFrames'] for c in clips)
            template = copy.deepcopy(clips[0])
            duration = float(source['probe']['format']['duration'])
            fps = draft['composition']['fps']
            rate = min(1.0, (duration - 2/fps) / (frames/fps))
            if rate < 0.5:
                raise RuntimeError('纯质地源过短，拒绝过度慢放')
            template.update(id=f'{n}-0-pure-texture', durationInFrames=frames, **{'from': first})
            template['props'].update(src=source['segment']['object_url'], startFrom=0, playbackRate=rate, loop=False)
            main = [c for c in main if str(c['id']).split('-')[0] != n] + [template]
        main.sort(key=lambda c: c['from'])
        plan = {'cut_id': cut['id'], 'script_id': cut['script_id'], 'instruction': '今天旗舰店和洗护号给我继续产出，应该已经完成了一批，只不过要修改一下...优化一下 然后给我最终的交付结果',
                'reason': '技术窗口仅绑定已视觉复核的全段纯膏体源；保留声音字幕时长。人物问题另列，不冒充整片通过。',
                'expected_draft': normalize(cut['draft']), 'replacement_main': main,
                'source_durations': {object_key(r['segment']['object_url']): float(r['probe']['format']['duration'])
                                     for r in retained if r.get('probe')},
                'remaining_issues': row['structural_issues'] + row['copy_issues']}
        path = save(root / 'plans' / f'{cut["id"]}.json', plan)
        plans.append(str(path))
    save(root / 'repair-plan-index.json', plans)
    for bid in BRIEFS:
        group = [r for r in rows if r['brief'] == bid]
        print(bid, 'existing', len(group), 'deliverable', sum(r['deliverable'] for r in group),
              'copy_fail', sum(bool(r['copy_issues']) for r in group), 'structure_fail', sum(bool(r['structural_issues']) for r in group),
              'texture_fix', sum(bool(r['technical_unverified']) for r in group), flush=True)
    print('纯质地修复清单', len(plans), '条；未写平台', flush=True)


def repair(cfg, args):
    root = Path(args.out)
    plan = load(args.plan)
    if not plan.get('instruction') or not plan.get('reason'):
        raise RuntimeError('返工清单缺少用户授权或原因')
    before = cb.api('GET', '/video-cuts/' + plan['cut_id'])
    script = cb.api('GET', '/video-scripts/' + before['script_id'])
    if script['title'].split('-')[1] not in BRIEFS:
        raise RuntimeError('拒绝修改六 brief 以外的剪辑')
    if before.get('output_status') in ('running', 'rendering', 'queued'):
        raise RuntimeError('已有渲染进行中，拒绝并发写入')
    draft = copy.deepcopy(before['draft'])
    draft['clips'] = [c for c in draft['clips'] if c.get('trackId') != 'main'] + plan['replacement_main']
    already_applied = normalize(before['draft']) == normalize(draft)
    if not already_applied and normalize(before['draft']) != normalize(plan['expected_draft']):
        raise RuntimeError('线上时间轴已经变化，拒绝覆盖并发更新')
    import footage_qc
    footage_qc.validate_repair(before['draft'], draft)
    footage_qc.validate_sources(draft, plan.get('source_durations') or {})
    digest = hashlib.sha256(json.dumps(normalize(draft), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
    print(plan['cut_id'], plan['reason'], '剩余问题', plan.get('remaining_issues', []), flush=True)
    print('线上已是目标画面' if already_applied else '等待应用此修复', flush=True)
    if args.dry_run:
        return
    if already_applied:
        existing = sorted((root / 'backups').glob('cut-' + plan['cut_id'] + '-*.json'))
        if not existing:
            raise RuntimeError('目标已应用但缺少原版备份，停止写入')
        bp, got = existing[0], before
    else:
        bp = backup(root, 'cut-' + plan['cut_id'], {'before': before, 'plan': plan})
        cb.api('PUT', '/video-cuts/' + plan['cut_id'], {'script_id': before['script_id'], 'draft': draft})
        got = cb.api('GET', '/video-cuts/' + plan['cut_id'])
    save(root / 'repair-readback' / f'{plan["cut_id"]}.json', {'backup': str(bp), 'cut': got, 'digest': digest})
    if normalize(got['draft']) != normalize(draft):
        raise RuntimeError('平台读回不一致，不提交渲染')
    print('备份、写入、读回一致；未渲染', flush=True)


def materials(cfg, args):
    rows = load(args.library)['records']
    wanted = set(args.segment or [])
    selected = [r for r in rows if r['segment']['id'] in wanted]
    if len(selected) != len(wanted):
        raise RuntimeError('素材快照未包含全部指定源')
    import subprocess
    for row in selected:
        sid = row['segment']['id']
        dest = Path(args.out) / 'sources' / sid / 'source.mp4'
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            cb.fetch_file(row['segment']['object_url'], dest)
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=width,height,avg_frame_rate', '-of', 'json', str(dest)], capture_output=True, text=True, check=True)
        save(dest.parent / 'record.json', dict(row, local_file=str(dest), probe=json.loads(result.stdout)))
        print(sid, str(dest), flush=True)


def prepare(cfg, args):
    """把已目检的完整计划转换为 footage_qc 唯一返工契约。"""
    import subprocess
    root = Path(args.out)
    paths = load(args.plan_index)
    plans = [load(p) for p in paths]
    lib = {object_key(r['segment'].get('object_url') or ''): r for r in load(args.library)['records']}
    known = {object_key(r['segment']['object_url']): r for r in load(args.pool)['records']}
    for p in root.glob('sources/*/record.json'):
        row = load(p)
        known[object_key(row['segment']['object_url'])] = row
    urls = {object_key(c['props']['src']): c['props']['src'] for p in plans for c in p['replacement_main']}
    def measure(item):
        key, url = item
        row = known.get(key) or lib.get(key)
        if not row:
            raise RuntimeError('素材库没有源：' + key)
        local = Path(row.get('local_file') or root / 'source-cache' / row['segment']['id'] / 'source.mp4')
        local.parent.mkdir(parents=True, exist_ok=True)
        if not local.exists():
            cb.fetch_file(row['segment']['object_url'], local)
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(local)], capture_output=True, text=True, check=True)
        return key, {'duration': float(json.loads(result.stdout)['format']['duration']), 'local_file': str(local), 'segment_id': row['segment']['id']}
    measured = dict(parallel(measure, urls.items()))
    save(root / 'measured-sources.json', measured)
    durations = {k: v['duration'] for k, v in measured.items()}
    current = {c['id']: c for c in load(root / 'online.json')['cuts']}
    ready = []
    for p in plans:
        existing = current[p['cut_id']]['draft']
        # 已修首条以读回时间轴为并发基线；其他条仍使用原计划基线。
        before = existing if normalize([x for x in existing['clips'] if x.get('trackId') == 'main']) == normalize(p['replacement_main']) else p['expected_draft']
        p = copy.deepcopy(p)
        fps = existing['composition']['fps']
        for clip in p['replacement_main']:
            props = clip['props']
            limit = durations[object_key(props['src'])] * fps
            end = props.get('startFrom', 0) + clip['durationInFrames'] * props.get('playbackRate', 1)
            if end > limit:
                props['playbackRate'] = min(props.get('playbackRate', 1), (limit - props.get('startFrom', 0) - 1) / clip['durationInFrames'])
        contract = dict(p, expected_main=sorted([x for x in before['clips'] if x.get('trackId') == 'main'], key=lambda x: x['from']), source_durations=durations)
        import footage_qc
        after = copy.deepcopy(existing)
        after['clips'] = [x for x in existing['clips'] if x.get('trackId') != 'main'] + p['replacement_main']
        footage_qc.validate_repair(existing, after)
        footage_qc.validate_sources(after, durations)
        dest = save(root / 'b03-ready' / f'{p["cut_id"]}.json', contract)
        ready.append(str(dest))
    save(root / 'b03-ready-index.json', ready)
    print('已实测源片', len(measured), '份；footage_qc返工契约', len(ready), '份', flush=True)


def finish(cfg, args):
    """查询官方返工收据，下载真正新成片并按新输出送审。"""
    import subprocess
    root = Path(args.out)
    original = load(args.batch_root + '/online.json')
    continued_path = Path(args.batch_root) / 'continued-online.json'
    extra = load(continued_path) if continued_path.exists() else {'scripts': [], 'cuts': [], 'generations': []}
    online = {k: original[k] + extra[k] for k in ('scripts', 'cuts', 'generations')}
    scripts = {s['id']: s for s in online['scripts']}
    cuts = {c['id']: c for c in online['cuts']}
    sequence = {cid: i for g in online['generations'] for i, cid in enumerate(g['cut_ids'], 1)}
    records = []
    ready_review = []
    for p in sorted(root.glob('render-*.json')):
        receipt = load(p)
        cid = receipt['cut_id']
        cut = cuts[cid]
        bid = scripts[cut['script_id']]['title'].split('-')[1]
        if bid not in BRIEFS + ('B01', 'B04', 'B09'):
            raise RuntimeError('渲染收据超出今日九组范围')
        job = cb.api('GET', '/render-jobs/' + receipt['job_id'])
        receipt['result'] = job
        if job['status'] == 'success':
            account = '旗舰店' if bid in ('B01', 'B02', 'B03') else '洗护号'
            destination = Path(args.delivery) / f'{bid}-{account}-修订-{sequence[cid]:02d}.mp4'
            destination.parent.mkdir(parents=True, exist_ok=True)
            if receipt.get('download_object_key') != job.get('object_key') or not destination.exists():
                cb.fetch_file(job['download_url'], destination)
                probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=width,height,codec_name', '-of', 'json', str(destination)], capture_output=True, text=True, check=True)
                receipt['probe'] = json.loads(probe.stdout)
                subprocess.run(['ffmpeg', '-v', 'error', '-i', str(destination), '-f', 'null', '-'], capture_output=True, check=True)
                receipt.update(download_object_key=job['object_key'], local_file=str(destination), full_decode='pass')
                print('NEW_FILE', bid, cid, str(destination), flush=True)
            live = cb.api('GET', '/video-cuts/' + cid)
            receipt['live_output_matches_job'] = live.get('output_object_key') == job.get('object_key')
            if not receipt['live_output_matches_job']:
                raise RuntimeError('成片对象与新渲染收据不同，停止交付')
            if receipt.get('review_for_job') != receipt['job_id']:
                ready_review.append((p, receipt))
            else:
                receipt['review_status'] = live.get('ai_review_status')
                receipt['review_result'] = live.get('ai_review_result')
                receipt['review_reason'] = live.get('ai_review_reason')
        save(p, receipt)
        records.append(receipt)
        print(bid, cid, job['status'], receipt.get('review_result'), flush=True)
    if ready_review:
        backup(root, 'before-review', [r for _, r in ready_review])
        response = cb.api('POST', '/video-cuts/review', {'cut_ids': [r['cut_id'] for _, r in ready_review]})
        for p, receipt in ready_review:
            receipt.update(review_for_job=receipt['job_id'], review_submit=response, review_status='pending')
            save(p, receipt)
    save(root / 'completion.json', records)


def shortfall(cfg, args):
    """只读恢复父会话三组原任务；数量来自90条授权，不冒充本地人审。"""
    source = load(args.source)
    target_briefs = ('B01', 'B04', 'B09')
    scripts = {x['script']['id']: x['script'] for x in source if x['script']['title'].split('-')[1] in target_briefs}
    generations = [g for g in listing('video-cut-generations') if g.get('script_id') in scripts]
    cuts = [c for c in listing('video-cuts') if c.get('script_id') in scripts]
    details = parallel(lambda sid: cb.api('GET', '/video-scripts/' + sid), scripts)
    rows = []
    for script in details:
        bid = script['title'].split('-')[1]
        existing = [c for c in cuts if c['script_id'] == script['id']]
        tasks = [g for g in generations if g['script_id'] == script['id']]
        pending = [g for g in tasks if g.get('status') in ('running', 'pending')]
        row = {'brief': bid, 'target': 10, 'existing_cuts': len(existing), 'shortfall': max(0, 10-len(existing)),
               'script_id': script['id'], 'script_title': script['title'], 'generation_tasks': tasks,
               'running_tasks': len(pending), 'can_plan_continue_existing_task': len(tasks) == 1 and not pending,
               'copy_policy': '平台既有脚本原文保持；不写本地approved；用户已授权同批90条续产。',
               'execution_blocked': True, 'planned_action_only': 'continue原任务；当前权限拒绝后不执行'}
        rows.append(row)
        print(bid, '已有', len(existing), '目标10，缺', row['shortfall'], '原任务', [(g['id'], g['status'], g['quantity'], len(g.get('cut_ids') or [])) for g in tasks], flush=True)
    report = {'mode': 'dry-run-read-only', 'rows': sorted(rows, key=lambda r: r['brief']), 'total_shortfall': sum(r['shortfall'] for r in rows), 'generations_submitted': 0, 'scripts': details}
    save(Path(args.out) / 'original-three-shortfall.json', report)
    print('合计待补', report['total_shortfall'], '条；只读dry-run，未提交', flush=True)


def continue_batch(cfg, args):
    """按本批90条授权补原任务缺口；不重建脚本或重复提交不确定请求。"""
    if not args.instruction:
        raise RuntimeError('续产缺少本次用户指令')
    root = Path(args.out)
    report = load(root / 'original-three-shortfall.json')
    scripts = {s['id']: s for s in report['scripts']}
    rows = report['rows']
    if {r['brief'] for r in rows} != {'B01', 'B04', 'B09'}:
        raise RuntimeError('续产只覆盖本批三组原任务')
    current_cuts = listing('video-cuts')
    todo = []
    for row in rows:
        sid = row['script_id']
        original = scripts[sid]
        live = cb.api('GET', '/video-scripts/' + sid)
        for field in ('title', 'product_id', 'segments'):
            if normalize(live.get(field)) != normalize(original.get(field)):
                raise RuntimeError(f'{row["brief"]} 原脚本已变，拒绝续产')
        tasks = row['generation_tasks']
        if len(tasks) != 1:
            raise RuntimeError('原任务不唯一')
        task = cb.api('GET', '/video-cut-generations/' + tasks[0]['id'])
        if task['script_id'] != sid:
            raise RuntimeError('原任务脚本不匹配')
        existing = [c for c in current_cuts if c.get('script_id') == sid]
        if task['status'] in ('pending', 'running'):
            print(row['brief'], '原任务仍在运行，不重复提交', flush=True)
            continue
        if task['status'] != 'success':
            raise RuntimeError('原任务未成功，不能按缺口追加')
        if len(existing) != len(task.get('cut_ids') or []):
            raise RuntimeError('当前剪辑与原任务数量不一致，先刷新')
        missing = max(0, row['target'] - len(existing))
        if not missing:
            print(row['brief'], '已达到10条，不重复生成', flush=True)
            continue
        receipt_path = root / 'continued' / f'{task["id"]}.json'
        if receipt_path.exists():
            raise RuntimeError('已有续产收据；先恢复其任务状态，不再次提交')
        todo.append((row, live, task, missing, receipt_path))
    print('本次原任务追加', sum(x[3] for x in todo), '条', flush=True)
    if args.dry_run:
        for row, _, task, missing, _ in todo:
            print(row['brief'], task['id'], '追加', missing, flush=True)
        return
    for row, live, task, missing, receipt_path in todo:
        bp = backup(root, 'before-continue-' + task['id'], {'script': live, 'generation': task, 'row': row})
        receipt = {'brief': row['brief'], 'generation_id': task['id'], 'script_id': live['id'],
                   'before_cut_ids': task.get('cut_ids') or [], 'quantity_requested': missing,
                   'instruction': args.instruction, 'backup': str(bp), 'request_state': 'submitting'}
        save(receipt_path, receipt)
        # 网络异常后不自动重试；先读取任务，以免同一追加请求执行两次。
        try:
            result = cb.api('POST', '/video-cut-generations/' + task['id'] + '/continue',
                            {'quantity': missing}, tries=1)
        except Exception as exc:
            receipt.update(request_state='uncertain', error=str(exc))
            save(receipt_path, receipt)
            raise
        receipt.update(request_state='submitted', response=result)
        save(receipt_path, receipt)
        print(row['brief'], '已向原任务追加', missing, '条', flush=True)


def review_status(cfg, args):
    """只读查询已渲染版本的审核；没有提交、补生成或分享分支。"""
    root = Path(args.out)
    receipts = [load(p) for p in sorted(root.glob('render-*.json'))]
    if not receipts:
        raise RuntimeError('没有已提交渲染收据')
    def inspect(receipt):
        job = cb.api('GET', '/render-jobs/' + receipt['job_id'])
        cut = cb.api('GET', '/video-cuts/' + receipt['cut_id'])
        matches = job['status'] == 'success' and cut.get('output_object_key') == job.get('object_key')
        return {'cut_id': receipt['cut_id'], 'job_id': receipt['job_id'],
                'render_status': job['status'], 'new_output_matches': matches,
                'review_submitted_for_job': receipt.get('review_for_job') == receipt['job_id'],
                'review_status': cut.get('ai_review_status'), 'review_result': cut.get('ai_review_result'),
                'review_reason': cut.get('ai_review_reason'), 'cut': cut}
    rows = parallel(inspect, receipts)
    save(root / 'review-readback.json', rows)
    for row in rows:
        print(row['cut_id'], row['render_status'], '新版匹配', row['new_output_matches'],
              row['review_status'], row['review_result'], row['review_reason'], flush=True)


def wait_generations(cfg, args):
    """等待已经提交的追加任务；失败或超时也保存状态，不再次提交。"""
    if args.wait <= 0 or args.interval < 1:
        raise RuntimeError('等待时限须为正数，查询间隔至少一秒')
    root = Path(args.out)
    receipts = [load(p) for p in sorted((root / 'continued').glob('*.json'))]
    if len(receipts) != 3:
        raise RuntimeError('需要三组原任务追加收据')
    deadline = time.monotonic() + args.wait
    previous = None
    while True:
        tasks = parallel(lambda r: cb.api('GET', '/video-cut-generations/' + r['generation_id']), receipts)
        summary = [(r['brief'], g['status'], g.get('generated_count'), g.get('quantity')) for r, g in zip(receipts, tasks)]
        if summary != previous:
            print('原任务追加进度', summary, flush=True)
            previous = summary
        save(root / 'continued-status.json', {'tasks': tasks, 'receipts': receipts})
        if any(g['status'] in ('failed', 'canceled', 'cancelled') for g in tasks):
            raise RuntimeError('原任务追加失败；状态已保存，不自动重试')
        if any(g['script_id'] != r['script_id'] for r, g in zip(receipts, tasks)):
            raise RuntimeError('追加任务脚本不匹配')
        complete = all(g['status'] == 'success' and len(g.get('cut_ids') or []) >= len(r['before_cut_ids']) + r['quantity_requested'] for r, g in zip(receipts, tasks))
        if complete:
            scripts = parallel(lambda r: cb.api('GET', '/video-scripts/' + r['script_id']), receipts)
            cut_ids = [cid for g in tasks for cid in g['cut_ids']]
            cuts = parallel(lambda cid: cb.api('GET', '/video-cuts/' + cid), cut_ids)
            save(root / 'continued-online.json', {'scripts': scripts, 'cuts': cuts, 'generations': tasks})
            print('三组追加已完成；完整剪辑', len(cuts), '条', flush=True)
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('追加等待超时；状态已保存，不自动重复提交')
        time.sleep(args.interval)


def prepare_three(cfg, args):
    """把原三条已核验窗口适配到同脚本新剪辑，保持新声音字幕与总帧数。"""
    import footage_qc
    if not args.instruction:
        raise RuntimeError('返工计划缺少本次用户指令')
    root = Path(args.out)
    online = load(root / 'continued-online.json')
    scripts = {s['id']: s for s in online['scripts']}
    approved = {s['id']: s for s in load(root / 'original-three-shortfall.json')['scripts']}
    for sid, script in scripts.items():
        if sid not in approved or any(normalize(script.get(k)) != normalize(approved[sid].get(k)) for k in ('title', 'product_id', 'segments')):
            raise RuntimeError('原脚本已变，拒绝创建续产返工计划')
    old_ids = {cid for r in load(root / 'continued-status.json')['receipts'] for cid in r['before_cut_ids']}
    templates = {bid: load(root.parent / f'repair-{bid}.json') for bid in ('B01', 'B04', 'B09')}
    retained = load(args.pool)['records']
    active = {r['segment']['id'] for r in load(args.active_pool)['records']}
    retained = [r for r in retained if r['segment']['id'] in active]
    ready = []
    for cut in online['cuts']:
        if cut['id'] in old_ids:
            continue
        bid = scripts[cut['script_id']]['title'].split('-')[1]
        seed = templates[bid]
        original = cut['draft']
        fps = original['composition']['fps']
        main = sorted([c for c in original['clips'] if c.get('trackId') == 'main'], key=lambda c: c['from'])
        windows = {n: [c for c in main if str(c['id']).split('-')[0] == n] for n in ('1', '2', '3', '4', '5')}
        if any(not clips for clips in windows.values()):
            raise RuntimeError('新剪辑缺少分段主画面')
        seed_main = seed['replacement_main']
        people = {n: next(c for c in seed_main if str(c['id']).split('-')[0] == n) for n in ('1', '2', '5')}
        target_pain_frames = sum(c['durationInFrames'] for n in ('1', '2') for c in windows[n])
        safe_pain_frames = people['1']['durationInFrames'] + people['2']['durationInFrames']
        pain_rate = min(1.0, safe_pain_frames / target_pain_frames)
        if pain_rate < 0.6:
            raise RuntimeError('新口播过长，不能用当前人物安全窗口')
        durations = dict(seed['source_durations'])
        replacements = []
        source_cursor = people['1']['props']['startFrom']
        product_key = '2x' if bid == 'B01' else 'huxi'
        textures = [r for r in retained if r['product_key'] == product_key and float(r['probe']['format']['duration']) >= 3]
        rotation = int(hashlib.sha256(cut['id'].encode()).hexdigest()[:8], 16)
        for n in ('1', '2', '3', '4', '5'):
            clips = windows[n]
            frame_count = sum(c['durationInFrames'] for c in clips)
            template = copy.deepcopy(clips[0])
            template.update(id=f'{n}-batch-revision', durationInFrames=frame_count)
            if n in ('1', '2'):
                template['props'].update(src=people[n]['props']['src'], startFrom=round(source_cursor), playbackRate=pain_rate, loop=False)
                source_cursor += frame_count * pain_rate
            elif n == '5':
                rate = min(1.0, people[n]['durationInFrames'] / frame_count)
                if rate < 0.6:
                    raise RuntimeError('效果口播过长，不能用当前安全窗口')
                template['props'].update(src=people[n]['props']['src'], startFrom=people[n]['props']['startFrom'], playbackRate=rate, loop=False)
            else:
                candidates = textures[rotation % len(textures):] + textures[:rotation % len(textures)]
                source = next((r for r in candidates if float(r['probe']['format']['duration']) - 1/fps >= frame_count/fps), None)
                if source is None:
                    raise RuntimeError('没有足够长的已复核纯质地源')
                src = source['segment']['object_url']
                durations[object_key(src)] = float(source['probe']['format']['duration'])
                template['props'].update(src=src, startFrom=0, playbackRate=1, loop=False)
                rotation += 1
            replacements.append(template)
        replacements += copy.deepcopy([c for c in main if str(c['id']).split('-')[0] not in windows])
        replacements.sort(key=lambda c: c['from'])
        # CTA 来自新剪辑，须先下载并实测，不能借用旧 CTA 的时长证据。
        for clip in replacements:
            src = object_key(clip['props']['src'])
            if src not in durations:
                import subprocess
                local = root / 'continued-source-cache' / hashlib.sha256(src.encode()).hexdigest()[:20] / 'source.mp4'
                local.parent.mkdir(parents=True, exist_ok=True)
                if not local.exists():
                    cb.fetch_file(clip['props']['src'], local)
                durations[src] = float(json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(local)], text=True))['format']['duration'])
            p = clip['props']
            rate_limit = (durations[src]*fps - p.get('startFrom', 0) - 1) / clip['durationInFrames']
            p['playbackRate'] = min(p.get('playbackRate', 1), rate_limit)
        draft = copy.deepcopy(original)
        draft['clips'] = [c for c in original['clips'] if c.get('trackId') != 'main'] + replacements
        footage_qc.validate_repair(original, draft)
        footage_qc.validate_sources(draft, durations)
        plan = {'cut_id': cut['id'], 'script_id': cut['script_id'], 'instruction': args.instruction,
                'reason': '今日同批续产；人物复用已目检安全窗口，技术段只取纯质地，保留新口播字幕与时长。',
                'expected_main': main, 'replacement_main': replacements, 'source_durations': durations, 'brief': bid}
        ready.append(str(save(root / 'continued-ready' / f'{cut["id"]}.json', plan)))
    save(root / 'continued-ready-index.json', ready)
    print('新追加剪辑完整返工契约', len(ready), '条', flush=True)


def output(cfg, args):
    # ---------- 输出摘要提供现签成片地址；不触发任何平台写入 ----------
    wanted = set(args.cut or [])
    rows = [c for c in listing('video-cuts') if c['id'] in wanted]
    for c in rows:
        save(Path(args.out) / f'output-{c["id"]}.json', c)
        if args.download and c.get('output_url'):
            dest = Path(args.out) / 'outputs' / f'{c["id"]}.mp4'
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                cb.fetch_file(c['output_url'], dest)
            print(c['id'], c.get('output_object_key'), str(dest), flush=True)
        else:
            print(c['id'], c.get('output_status'), c.get('output_object_key'), flush=True)
    if len(rows) != len(wanted):
        raise RuntimeError('部分指定剪辑未在平台列表中找到')


def main():
    ap = argparse.ArgumentParser(description='只恢复六个已授权 brief 的线上生产，不修改脚本文字')
    ap.add_argument('cmd', choices=['schema', 'status', 'output', 'audit', 'repair', 'materials', 'prepare', 'finish', 'shortfall', 'continue', 'wait-generations', 'prepare-three', 'review-status'])
    ap.add_argument('--config', default='qingyang')
    ap.add_argument('--source', default='/Users/bingmeishi/Projects/myskill/runs/20261118/footage-qc-r1/today-found.json')
    ap.add_argument('--out', required=True)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--instruction', help='continue / prepare-three: 本次用户续产授权原话')
    ap.add_argument('--wait', type=int, default=1800)
    ap.add_argument('--interval', type=int, default=30)
    ap.add_argument('--cut', action='append')
    ap.add_argument('--download', action='store_true')
    ap.add_argument('--library', default='/Users/bingmeishi/Projects/myskill/runs/20261118/footage-qc-r1/full-library/segments-20261118-182719-055267.json')
    ap.add_argument('--active-pool', default='/Users/bingmeishi/Projects/myskill/runs/20261118/footage-qc-r1/clean-pool/segments-20261118-191053-071222.json')
    ap.add_argument('--pool', default='/Users/bingmeishi/Projects/myskill/runs/20261118/footage-qc-r1/reviewed-pool.json')
    ap.add_argument('--plan')
    ap.add_argument('--segment', action='append')
    ap.add_argument('--plan-index')
    ap.add_argument('--batch-root', default='/Users/bingmeishi/Projects/myskill/runs/20261118/footage-qc-r1/resumed-six')
    ap.add_argument('--delivery', default='/Users/bingmeishi/Projects/myskill/本地交付/20261118-清扬-修订')
    args = ap.parse_args()
    cfg = load_config(args.config)
    cb.CFG = cfg
    cb.BASE = cfg['clipo']['base_url'].rstrip('/') + '/api/v1'
    {'schema': schema, 'status': status, 'output': output, 'audit': audit, 'repair': repair, 'materials': materials, 'prepare': prepare, 'finish': finish, 'shortfall': shortfall, 'continue': continue_batch, 'wait-generations': wait_generations, 'prepare-three': prepare_three, 'review-status': review_status}[args.cmd](cfg, args)


if __name__ == '__main__':
    main()
