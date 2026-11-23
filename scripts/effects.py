#!/usr/bin/env python3
"""
[INPUT]: common 的配置与人审校验；clipo_batch 的 API 与状态；力士模板源码和固定批次。
[OUTPUT]: 提供目录查询、只读草稿差异、固定计划、模板登记、单条应用、轮询、证据校验和批量应用命令。
[POS]: scripts 的透明特效入口，只替换 effect 轨道；不改文案、素材、声音、审核规则或旧分享。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""
import argparse
from contextlib import contextmanager
import fcntl
import copy
import hashlib
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import approval_problem, copy_hash, load_config

TEMPLATES = Path(__file__).resolve().parents[1] / 'templates' / 'lux-effects'
SAMPLE = '1120-L02-V09'
AUTHORIZED = {f'1120-L{b:02d}-V{v:02d}' for b in range(1, 6) for v in range(1, 11)}
INSTRUCTION = '你不能自己写点特效 然后用上吗 我不是支持嘛'


def load(path):
    return json.loads(Path(path).read_text())


def save(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.pending')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def digest(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def normalized(draft):
    d = copy.deepcopy(draft)
    for c in d.get('clips', []):
        p = c.get('props') or {}
        for key in ('src', 'font_url'):
            if isinstance(p.get(key), str):
                p[key] = p[key].split('?')[0]
    if isinstance(d.get('cover_url'), str):
        d['cover_url'] = d['cover_url'].split('?')[0]
    return d


@contextmanager
def exclusive(out):
    """同一特效批次只能有一个命令推进账本。"""
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'command.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('另一特效命令仍在运行，不并发写入') from error
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def check_scope(videos):
    if set(videos) != AUTHORIZED:
        raise RuntimeError('本次只授权五个 brief 各十条，共50条')
    if len({v['cut_id'] for v in videos.values()}) != 50:
        raise RuntimeError('cut ID 重复')
    for key, row in videos.items():
        if row['brief_id'] != key.rsplit('-V', 1)[0]:
            raise RuntimeError('视频与 brief 绑定不符')


def protected(draft):
    d = copy.deepcopy(draft)
    d['clips'] = [c for c in d['clips'] if c.get('trackId') != 'effect']
    d['tracks'] = [t for t in d.get('tracks', []) if t.get('id') != 'effect']
    return normalized(d)


def validate_change(before, after):
    if protected(before) != protected(after):
        raise RuntimeError('非特效轨道发生变化，拒绝写入')
    ids = set()
    for c in after['clips']:
        if c['id'] in ids:
            raise RuntimeError('clip ID 重复')
        ids.add(c['id'])
        if c.get('trackId') == 'effect':
            if (c.get('component') != 'RemotionClip' or not isinstance(c['from'], int)
                    or not isinstance(c['durationInFrames'], int) or c['durationInFrames'] < 2
                    or c['from'] < 0 or c['from'] + c['durationInFrames'] > after['composition']['durationInFrames']
                    or not c.get('props', {}).get('code')):
                raise RuntimeError('特效组件、源码或时段无效')


def definitions():
    manifest = load(TEMPLATES / 'manifest.json')
    out = {}
    for key, row in manifest['templates'].items():
        path = (TEMPLATES / row['source']).resolve()
        if path.parent != TEMPLATES.resolve():
            raise RuntimeError('模板来源越界')
        code = path.read_text()
        imports = re.findall(r'\bfrom\s*[\'\"]([^\'\"]+)', code)
        if imports != ['react', 'remotion'] or 'export const Composition' not in code or len(code) > 200000:
            raise RuntimeError(f'{key} 导出、依赖或长度不符合约定')
        if re.search(r'<(?:text|foreignObject)\b|Math\.random\s*\(|setInterval\s*\(', code):
            raise RuntimeError(f'{key} 存在文字或非确定性动画')
        out[key] = {**row, 'code': code, 'code_hash': hashlib.sha256(code.encode()).hexdigest(),
                    **{k: manifest[k] for k in ('width', 'height', 'fps', 'durationInFrames')}}
    return out


def current_briefs(args):
    briefs = {b['brief_id']: b for b in cb.load_briefs(args.run)}
    if set(briefs) != {f'1120-L{b:02d}' for b in range(1, 6)} or any(b['quantity'] != 10 for b in briefs.values()):
        raise RuntimeError('本次只授权五个力士 brief 各十条')
    for b in briefs.values():
        error = approval_problem(b)
        if error:
            raise RuntimeError(f'{b["brief_id"]}：{error}')
        if b.get('disable_effects'):
            raise RuntimeError('本批去花字标志仍启用，不能静默清除')
    return briefs


def build_draft(before, brief, templates, sample=False):
    d = copy.deepcopy(before)
    d['clips'] = [c for c in d['clips'] if c.get('trackId') != 'effect']
    if not any(t['id'] == 'effect' for t in d.get('tracks', [])):
        d.setdefault('tracks', []).append({'id': 'effect', 'name': '力士透明特效', 'enabled': True})
    for t in d['tracks']:
        if t['id'] == 'effect':
            t['enabled'] = True
    main = [c for c in d['clips'] if c.get('trackId') == 'main']
    windows = []
    for i, sg in enumerate(brief['segments'], 1):
        clips = [c for c in main if str(c['id']).split('-')[0] == str(i)]
        if clips:
            windows.append((min(c['from'] for c in clips), max(c['from'] + c['durationInFrames'] for c in clips),
                            set(sg.get('visual_tags') or [])))
    product = next((w for w in windows if '产品外观' in w[2]), None)
    results = [w for w in windows if '头发顺滑' in w[2]]
    if product is None or not results:
        raise RuntimeError('缺少产品亮相或结果时段，不按猜测放特效')
    chosen = [('sweep', product), ('particles', results[0])]
    if sample:
        third = next((w for w in windows if w[0] not in (product[0], results[0][0]) and '头发顺滑' in w[2]), None)
        if third is None:
            third = next((w for w in windows if w[0] not in (product[0], results[0][0])), None)
        if third is None:
            raise RuntimeError('试样没有第三个独立窗口')
        chosen.append(('glow', third))
    elif digest(brief['brief_id'])[0] in '01234567':
        chosen[1] = ('glow', results[0])
    moments = []
    for key, (start, end, _) in chosen:
        length = min(42, end - start, d['composition']['durationInFrames'] - start)
        if length < 2:
            raise RuntimeError('特效窗口不足两帧')
        row = templates[key]
        params = {**row['params'], 'durationFrames': length}
        # ---------- 非默认粉色与八粒光点，用于云端参数验证 ----------
        if sample:
            params['accent'] = '#F08EC3'
            if key == 'particles':
                params['count'] = 8
        d['clips'].append({'id': f'lux-fx-{key}-{start}', 'trackId': 'effect', 'component': 'RemotionClip',
                          'from': start, 'durationInFrames': length,
                          'props': {'code': row['code'], 'params': params, 'x': 0, 'y': 0, 'scale': 1}})
        moments.append({'key': key, 'from': start, 'frames': length, 'params': params, 'code_hash': row['code_hash']})
    validate_change(before, d)
    return d, moments


def inventory(args):
    folders = cb.api('GET', '/resource/remotion-effect-folder')
    save(args.out / 'folders.json', folders)
    print(json.dumps(folders, ensure_ascii=False, indent=2))


def plan(args):
    briefs, st, templates = current_briefs(args), cb.State(args.run), definitions()
    path = args.out / 'plan.json'
    if path.exists():
        existing = load(path)
        if existing['templates_hash'] != digest(templates):
            raise RuntimeError('源码已变，请使用新 --out 目录，不覆盖原计划')
        if not args.rebuild:
            print(f'沿用固定计划 {len(existing["videos"])} 条')
            return
        if any((args.out / f'{name}.json').exists() for name in ('registry', 'ledger', 'sample-proof')):
            raise RuntimeError('已有平台写入收据，不覆盖原计划')
        save(args.out / 'plan-previous.json', existing)
    selected = {k: v for k, v in st.videos.items() if v['brief_id'] in briefs}
    check_scope(selected)
    rows = {}
    for key, v in sorted(selected.items()):
        cut = cb.api('GET', f'/video-cuts/{v["cut_id"]}')
        b = briefs[v['brief_id']]
        draft, moments = build_draft(cut['draft'], b, templates, key == SAMPLE)
        save(args.out / 'before' / f'{v["cut_id"]}.json', cut)
        save(args.out / 'drafts' / f'{v["cut_id"]}.json', draft)
        rows[key] = {'cut_id': v['cut_id'], 'brief_id': v['brief_id'], 'copy_hash': copy_hash(b),
                     'before_hash': digest(normalized(cut['draft'])), 'draft_hash': digest(normalized(draft)), 'moments': moments}
        print(f'{key}：替换旧花字，{len(moments)} 处透明特效', flush=True)
    result = {'instruction': INSTRUCTION, 'sample': SAMPLE, 'templates_hash': digest(templates), 'videos': rows}
    result['plan_hash'] = digest(result)
    save(path, result)
    print(f'固定 {len(rows)} 条；先试样 {SAMPLE}')


def inspect(args):
    p = checked_plan(args)
    key = args.video or p['sample']
    if key not in p['videos']:
        raise RuntimeError('目标不在白名单')
    row = p['videos'][key]
    cut = cb.api('GET', '/video-cuts/' + row['cut_id'])
    save(args.out / 'inspect' / f'{row["cut_id"]}.json', cut)
    before = load(args.out / 'before' / f'{row["cut_id"]}.json')['draft']
    def differences(a, b, path='draft'):
        if isinstance(a, dict) and isinstance(b, dict):
            for name in sorted(set(a) | set(b)):
                if name not in a or name not in b:
                    print(path + '.' + name, '字段增删')
                else:
                    differences(a[name], b[name], path + '.' + name)
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                print(path, '长度变化', len(a), len(b))
            for i, (x, y) in enumerate(zip(a, b)):
                differences(x, y, path + f'[{i}]')
        elif a != b:
            print(path, repr(a)[:180], '→', repr(b)[:180])
    differences(normalized(before), normalized(cut['draft']))


def register(args):
    path = args.out / 'registry.json'
    data = load(path) if path.exists() else {'templates': {}}
    if not args.folder:
        raise RuntimeError('必须给已确认存在的 --folder ID')
    folders_path = args.out / 'folders.json'
    if not folders_path.exists() or args.folder not in {f['id'] for f in load(folders_path)}:
        raise RuntimeError('目录未在本批 inventory 中确认')
    dryrun_path = args.out / 'register-dryrun.json'
    signature = digest({'folder': args.folder, 'templates': definitions()})
    if not args.dry_run:
        preflight = load(dryrun_path) if dryrun_path.exists() else {}
        if preflight.get('signature') != signature or time.time() - preflight.get('at', 0) > 3600:
            raise RuntimeError('模板上传需要一小时内匹配的 dry-run 收据')
    for key, row in definitions().items():
        payload = {k: row[k] for k in ('name', 'code', 'params', 'width', 'height', 'fps', 'durationInFrames')}
        payload.update(name=row['name'] + ' · ' + row['code_hash'][:8], folderId=args.folder)
        known = data['templates'].get(key)
        if known and known['payload_hash'] != digest(payload):
            raise RuntimeError('源码版本与登记不符，不覆盖共享模板')
        if args.dry_run:
            print(f'拟新建 {payload["name"]}，源码 {len(payload["code"])} 字符')
            continue
        if not known:
            known = {'phase': 'unknown', 'payload_hash': digest(payload)}
            data['templates'][key] = known
            save(path, data)
            result = cb.api('POST', '/resource/remotion-effect-template', payload, tries=1)
            known.update(id=result['id'], phase='created')
            save(path, data)
        if not known.get('id'):
            raise RuntimeError(f'{key} 创建结果不确定，禁止重复 POST')
        live = cb.api('GET', '/resource/remotion-effect-template/' + known['id'])
        if any(live.get(k) != value for k, value in payload.items()):
            raise RuntimeError(f'{key} 完整回读不一致')
        known['phase'] = 'verified'
        save(args.out / 'template-readback' / f'{key}.json', live)
        save(path, data)
        print(f'{payload["name"]} → {known["id"]}，完整回读一致', flush=True)
    if args.dry_run:
        save(dryrun_path, {'signature': signature, 'at': time.time(), 'quantity': 3})


def ledger(args):
    path = args.out / 'ledger.json'
    return path, load(path) if path.exists() else {'videos': {}}


def checked_plan(args):
    p = load(args.out / 'plan.json')
    body = {k: v for k, v in p.items() if k != 'plan_hash'}
    if digest(body) != p['plan_hash'] or digest(definitions()) != p['templates_hash']:
        raise RuntimeError('固定计划或源码已变化')
    check_scope(p['videos'])
    briefs = current_briefs(args)
    for row in p['videos'].values():
        if copy_hash(briefs[row['brief_id']]) != row['copy_hash']:
            raise RuntimeError('文案人审指纹已变化')
    return p


def require_sample(args, p):
    proof = args.out / 'sample-proof.json'
    if not proof.exists():
        raise RuntimeError('没有试样视觉与参数证据，禁止批量')
    evidence = load(proof)
    _, data = ledger(args)
    row = data['videos'].get(p['sample'], {})
    if (evidence.get('plan_hash') != p['plan_hash'] or evidence.get('object_key') != row.get('object_key')
            or row.get('phase') != 'done' or row.get('ai_review') != 'pass' or not row.get('review_receipt')):
        raise RuntimeError('试样未通过或证据版本已过时')
    live = cb.api('GET', '/video-cuts/' + row['cut_id'])
    if (live.get('output_object_key') != row['object_key'] or live.get('ai_review_status') != 'success'
            or live.get('ai_review_result') != 'pass'
            or digest(normalized(live['draft'])) != p['videos'][p['sample']]['draft_hash']):
        raise RuntimeError('试样线上版本或最新审核已变化')


def apply_one(args, key, p, data, path):
    row = p['videos'][key]
    cut_id = row['cut_id']
    draft = load(args.out / 'drafts' / f'{cut_id}.json')
    if digest(normalized(draft)) != row['draft_hash']:
        raise RuntimeError('计划草稿已被修改')
    state = data['videos'].get(key)
    live = cb.api('GET', f'/video-cuts/{cut_id}')
    live_hash = digest(normalized(live['draft']))
    if live_hash not in (row['before_hash'], row['draft_hash']):
        raise RuntimeError(f'{key} 线上草稿已变化，不覆盖')
    # ---------- 只续签原对象地址，不用计划中的过期签名渲染 ----------
    live_clips = {c['id']: c for c in live['draft']['clips']}
    if 'cover_url' in live['draft']:
        draft['cover_url'] = live['draft']['cover_url']
    for clip in draft['clips']:
        current = live_clips.get(clip['id'], {}).get('props', {})
        if clip.get('trackId') != 'effect':
            for field in ('src', 'font_url'):
                if field in current:
                    clip['props'][field] = current[field]
    validate_change(live['draft'], draft)
    if state and state.get('phase') in ('rendering', 'reviewing', 'done'):
        if live_hash != row['draft_hash']:
            raise RuntimeError('已提交草稿被外部替换，不沿用旧任务')
        print(f'{key} 沿用已提交收据')
        return
    if args.dry_run:
        print(f'{key}：{len(row["moments"])} 处特效，保留其他轨道')
        return
    if not state:
        st = cb.State(args.run)
        state = {'phase': 'applying', 'cut_id': cut_id, 'draft_hash': row['draft_hash'],
                 'previous_batch_state': copy.deepcopy(st.videos[key])}
        data['videos'][key] = state
        save(path, data)
    if live_hash == row['before_hash']:
        cb.api('PUT', f'/video-cuts/{cut_id}', {'script_id': live['script_id'], 'draft': draft}, tries=1)
    got = cb.api('GET', f'/video-cuts/{cut_id}')
    if digest(normalized(got['draft'])) != row['draft_hash']:
        raise RuntimeError('写后完整回读不一致，不渲染')
    if state.get('phase') == 'render-unknown':
        raise RuntimeError('渲染结果不确定，禁止重复提交')
    state['phase'] = 'render-unknown'
    save(path, data)
    job = cb.api('POST', '/render-jobs', {'spec': got['draft'], 'options': {'codec': 'h264', 'quality': 'high', 'scale': 1},
                 'cut_id': cut_id, 'idempotency_key': f'udb-fx-{cut_id}-{row["draft_hash"][:16]}'}, tries=1)
    state.update(phase='rendering', render_job=job['job_id'])
    save(path, data)
    st = cb.State(args.run)
    st.videos[key].update(stage='rendering', render_job=job['job_id'], ai_review=None, ai_reason=None,
                          effects_draft_hash=row['draft_hash'], effects_moments=row['moments'])
    st.save()
    print(f'{key}：完整回读一致，渲染 {job["job_id"]}', flush=True)


def apply(args, batch=False):
    p = checked_plan(args)
    if not args.dry_run:
        registered = load(args.out / 'registry.json')['templates']
        if any(registered.get(k, {}).get('phase') != 'verified' for k in definitions()):
            raise RuntimeError('三份模板没有完成上传回读')
    if batch:
        require_sample(args, p)
        keys = [k for k in p['videos'] if k != p['sample']]
    else:
        key = args.video or p['sample']
        if key not in p['videos']:
            raise RuntimeError('目标不在固定白名单')
        if key != p['sample']:
            require_sample(args, p)
        keys = [key]
    if not args.dry_run:
        receipt_path = args.out / 'apply-dryrun.json'
        receipt = load(receipt_path) if receipt_path.exists() else {}
        if (receipt.get('keys') != keys or receipt.get('plan_hash') != p['plan_hash']
                or time.time() - receipt.get('at', 0) > 3600):
            raise RuntimeError('缺少一小时内匹配条数与源码的 dry-run 收据')
    path, data = ledger(args)
    for key in keys:
        apply_one(args, key, p, data, path)
    if args.dry_run:
        save(args.out / 'apply-dryrun.json', {'keys': keys, 'plan_hash': p['plan_hash'], 'at': time.time()})


def poll(args):
    path, data = ledger(args)
    deadline = time.time() + args.wait
    while True:
        pending = 0
        for key, row in data['videos'].items():
            if row['phase'] == 'rendering':
                job = cb.api('GET', '/render-jobs/' + row['render_job'])
                if job['status'] in ('failed', 'canceled', 'cancelled'):
                    row.update(phase='failed', error=job)
                elif job['status'] == 'success':
                    current = cb.api('GET', '/video-cuts/' + row['cut_id'])
                    if (current.get('output_object_key') != job['object_key']
                            or digest(normalized(current['draft'])) != row['draft_hash']):
                        raise RuntimeError('送审前输出或草稿被替换，不送审')
                    row.update(phase='review-unknown', object_key=job['object_key'])
                    save(path, data)
                    receipt = cb.api('POST', '/video-cuts/review', {'cut_ids': [row['cut_id']]}, tries=1)
                    row['review_receipt'] = receipt
                    save(path, data)
                    if receipt.get('queued') != 1 or receipt.get('skipped', 0) != 0:
                        raise RuntimeError('本次审核未明确受理，不接收旧审核结论')
                    row.update(phase='reviewing', review_active_seen=False)
                    save(path, data)
                    active = cb.api('GET', '/video-cuts/' + row['cut_id'])
                    if active.get('ai_review_status') in ('pending', 'queued', 'running'):
                        row['review_active_seen'] = True
                        save(path, data)
            elif row['phase'] == 'reviewing':
                cut = cb.api('GET', '/video-cuts/' + row['cut_id'])
                if cut.get('ai_review_status') in ('pending', 'queued', 'running'):
                    row['review_active_seen'] = True
                    save(path, data)
                if cut.get('ai_review_status') in ('success', 'failed'):
                    if not row.get('review_active_seen'):
                        raise RuntimeError('未观察到本次审核执行状态，不接收终态；保留收据')
                    if cut.get('output_object_key') != row['object_key']:
                        raise RuntimeError('审核输出版本已变，不能接收旧结论')
                    row.update(phase='done', ai_review=cut.get('ai_review_result'), ai_reason=cut.get('ai_review_reason'))
                    save(args.out / 'review' / f'{row["cut_id"]}.json', cut)
                    save(path, data)
            pending += row['phase'] in ('rendering', 'reviewing')
            print(key, row['phase'], row.get('ai_review'), (row.get('ai_reason') or '')[:160], flush=True)
        save(path, data)
        st = cb.State(args.run)
        for key, row in data['videos'].items():
            if row['phase'] in ('done', 'failed'):
                st.videos[key].update(stage='done' if row['phase'] == 'done' else 'failed', object_key=row.get('object_key'),
                                     ai_review=row.get('ai_review'), ai_reason=row.get('ai_reason'))
        st.save()
        if any(r['phase'].endswith('unknown') for r in data['videos'].values()):
            raise RuntimeError('存在结果不确定的写请求，停止并保留收据')
        if not pending:
            break
        if time.time() >= deadline:
            raise SystemExit(3)
        time.sleep(30)


def accept(args):
    p = checked_plan(args)
    _, data = ledger(args)
    row = data['videos'].get(p['sample'], {})
    if row.get('phase') != 'done' or row.get('ai_review') != 'pass':
        raise RuntimeError('试样尚未完成原平台审核并通过')
    if not args.evidence:
        raise RuntimeError('必须提供抽帧与非默认参数验证证据 JSON')
    e = load(args.evidence)
    if (e.get('object_key') != row['object_key'] or e.get('params_verified') is not True
            or e.get('no_extra_text') is not True or e.get('no_occlusion') is not True
            or set(e.get('effects_verified', [])) != {'sweep', 'particles', 'glow'} or not e.get('files')):
        raise RuntimeError('视觉、参数或三种效果证据缺失')
    if any(not Path(f).is_file() for f in e['files']):
        raise RuntimeError('证据文件不存在')
    save(args.out / 'sample-proof.json', {**e, 'plan_hash': p['plan_hash']})
    print('单条云渲染、三种视觉与参数验证完成，允许批量')


def download(args):
    p = checked_plan(args)
    _, data = ledger(args)
    key = args.video or p['sample']
    if key not in p['videos']:
        raise RuntimeError('下载目标不在本批白名单')
    row = data['videos'][key]
    job = cb.api('GET', '/render-jobs/' + row['render_job'])
    if job['status'] != 'success' or job.get('object_key') != row.get('object_key'):
        raise RuntimeError('下载对象与本次成功渲染不一致')
    dest = args.out / f'{key}-custom-effects.mp4'
    cb.fetch_file(job['download_url'], dest)
    save(args.out / 'download.json', {'file': str(dest.resolve()), 'object_key': row['object_key'], 'moments': p['videos'][key]['moments']})
    print(dest.resolve())


def main():
    ap = argparse.ArgumentParser(description='力士透明特效：固定计划→上传→单条→原审核→验证→批量')
    ap.add_argument('cmd', choices=['inventory', 'inspect', 'plan', 'register', 'apply', 'poll', 'accept', 'batch', 'download'])
    ap.add_argument('--config', default='lux', choices=['lux'])
    ap.add_argument('--run', required=True)
    ap.add_argument('--out', type=Path)
    ap.add_argument('--folder')
    ap.add_argument('--video')
    ap.add_argument('--evidence')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--rebuild', action='store_true', help='仅无平台写入收据时可重新生成本地计划')
    ap.add_argument('--wait', type=int, default=0)
    args = ap.parse_args()
    args.out = args.out or Path(args.run) / 'lux-effects'
    cfg = load_config(args.config)
    cb.CFG, cb.BASE = cfg, cfg['clipo']['base_url'].rstrip('/') + '/api/v1'
    with exclusive(args.out):
        {'inventory': inventory, 'inspect': inspect, 'plan': plan, 'register': register, 'apply': apply, 'poll': poll,
         'accept': accept, 'batch': lambda a: apply(a, True), 'download': download}[args.cmd](args)


if __name__ == '__main__':
    main()
