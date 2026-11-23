#!/usr/bin/env python3
"""
[INPUT]: 依赖 clipo_batch 的平台封装与下载器；依赖 common 的品牌配置。
[OUTPUT]: 提供分类与线上批次快照、画面返工、渲染查询、新输出审核和分享成片下载。
[POS]: 联合利华生产的画面返工边界。只改清单授权的标签或主画面；显式调音清单只改音量与角标。
       新来源与新播放窗口须有实测时长；完全未变的旧窗口不重复下载。
       回读只忽略媒体签名刷新；备份续跑不重复写入，渲染意图防重复提交。
       新输出核对后允许自动封面更新；字幕与音频来源不变，音量须显式授权。
       审核须精确受理本批数量，拒绝跳过后沿用旧结论。--wait 等整批终态后报告驳回。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""
import argparse
import copy
import hashlib
import json
import math
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import load_config


def load(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    return path


def backup(root, kind, value):
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    return save(Path(root) / f'{kind}-{stamp}.json', value)


def key(src):
    return urlparse(src).path.lstrip('/') if '://' in src else src.split('?')[0]


def probe(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                        'format=duration:stream=width,height,avg_frame_rate', '-of', 'json', str(path)],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def selected_products(cfg, products):
    names = products or list(cfg['products'])
    return [(name, cfg['products'][name]['clipo_product_id']) for name in names
            if cfg['products'][name].get('clipo_product_id')]


def snapshot(cfg, args):
    records = []
    for name, pid in selected_products(cfg, args.product):
        rows = cb.list_segments(pid)
        picked = [s for s in rows if (not args.tag or args.tag in (s.get('tags') or []))
                  and (not args.segment or s['id'] in args.segment)]
        print(f'{name}：{args.tag or "全部"} {len(picked)} 条', flush=True)
        records.extend({'product_key': name, 'product_id': pid, 'segment': s} for s in picked)
    dest = backup(args.out, 'segments', {'tag': args.tag, 'records': records})
    print(f'快照 → {dest}')
    if args.download:
        for i, row in enumerate(records, 1):
            s = row['segment']
            url = s.get('object_url') or s.get('video_url')
            if not url:
                raise RuntimeError(f'{s["id"]} 无视频地址，不能声称已复核')
            p = Path(args.out) / 'media' / f'{s["id"]}.mp4'
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                cb.fetch_file(url, p)
            row['local_file'] = str(p.resolve())
            row['probe'] = probe(p)
            print(f'{i}/{len(records)} {s["id"]} {s.get("name", "")[:40]}', flush=True)
        save(dest, {'tag': args.tag, 'records': records})


def apply_tags(cfg, args):
    plan = load(args.plan)
    if not plan.get('reviewed_by') or not plan.get('instruction'):
        raise RuntimeError('复核清单必须记录复核者与用户授权原话')
    changes = plan['changes']
    if len({x['segment_id'] for x in changes}) != len(changes):
        raise RuntimeError('清单重复切片')
    current = {}
    for _, pid in selected_products(cfg, args.product):
        current.update({s['id']: s for s in cb.list_segments(pid)})
    todo = []
    for x in changes:
        s = current[x['segment_id']]
        have = s.get('tags') or []
        wanted = [t for t in have if t != x['remove_tag']]
        if x['remove_tag'] not in have:
            continue
        if have != x['expected_tags']:
            raise RuntimeError(f'{s["id"]} 标签已变，重新复核，拒绝覆盖')
        if not x.get('reason') or not x.get('visual_evidence'):
            raise RuntimeError('每条移标签必须有实际画面证据与原因')
        todo.append((s, wanted))
    print(f'只移除误分类标签 {len(todo)} 条；保留其他标签与原素材')
    for s, tags in todo:
        print(f'{s["id"]} {s.get("name", "")[:32]}：{s.get("tags")} → {tags}')
    if args.dry_run:
        return
    p = backup(args.out, 'tag-backup', {'plan': plan, 'before': [s for s, _ in todo]})
    print(f'备份 → {p}')
    for s, tags in todo:
        cb.api('PATCH', f'/materials/segments/{s["id"]}', {'tags': tags})
    after = {}
    for _, pid in selected_products(cfg, args.product):
        after.update({s['id']: s for s in cb.list_segments(pid)})
    bad = [s['id'] for s, tags in todo if after.get(s['id'], {}).get('tags') != tags]
    save(Path(args.out) / 'tag-readback.json', {'changed': len(todo), 'mismatches': bad})
    if bad:
        raise RuntimeError(f'读回标签不一致：{bad}')
    print(f'读回一致 {len(todo)}/{len(todo)}')


def cut_snapshot(cfg, args):
    for cid in args.cut:
        cut = cb.api('GET', f'/video-cuts/{cid}')
        p = save(Path(args.out) / f'cut-{cid}.json', cut)
        print(f'剪辑 → {p}')
        if args.scripts:
            script = cb.api('GET', f'/video-scripts/{cut["script_id"]}')
            save(Path(args.out) / f'script-{cut["script_id"]}.json', script)
        draft = cut['draft']
        fps = draft['composition'].get('fps', 30)
        rows = []
        for c in sorted(draft['clips'], key=lambda c: c['from']):
            if c.get('trackId') != 'main':
                continue
            props = c.get('props') or {}
            row = {'id': c['id'], 'from': c['from'], 'frames': c['durationInFrames'],
                   'seconds': [c['from']/fps, (c['from']+c['durationInFrames'])/fps],
                   'src': key(props.get('src', '')), 'startFrom': props.get('startFrom', 0),
                   'playbackRate': props.get('playbackRate', 1)}
            if args.download and props.get('src', '').startswith('http'):
                media = Path(args.out) / 'media' / f'{cid}-{c["id"]}.mp4'
                media.parent.mkdir(parents=True, exist_ok=True)
                if not media.exists():
                    cb.fetch_file(props['src'], media)
                row['local_file'] = str(media.resolve())
                row['probe'] = probe(media)
            rows.append(row)
            print(f'  {row["id"]} {row["seconds"]} {row["src"]} start={row["startFrom"]} rate={row["playbackRate"]}')
        save(Path(args.out) / f'main-{cid}.json', rows)


def main_clips(draft):
    return sorted([c for c in draft['clips'] if c.get('trackId') == 'main'], key=lambda c: c['from'])


def stable_media(value):
    """只忽略媒体地址的临时签名；保留域名、对象路径与内容字段。"""
    if isinstance(value, list):
        return [stable_media(x) for x in value]
    if isinstance(value, dict):
        result = {}
        for name, item in value.items():
            if name in ('src', 'font_url', 'cover_url') and isinstance(item, str) and item.startswith(('https://', 'http://')):
                url = urlparse(item)
                item = url._replace(query='', fragment='').geturl()
            result[name] = stable_media(item)
        return result
    return value


def normalized_main(rows):
    rows = copy.deepcopy(rows)
    for clip in rows:
        if clip.get('props', {}).get('src'):
            clip['props']['src'] = key(clip['props']['src'])
        if clip.get('props', {}).get('loop') is False:
            clip['props'].pop('loop')
    return rows


def validate_repair(before, after, allow_render_cover=False):
    # ---------- 只允许画面变化，不重写声音、字幕或时长 ----------
    a, b = stable_media(before), stable_media(after)
    if allow_render_cover:
        # 仅在已核对新渲染对象后允许平台重新生成封面元数据。
        a.pop('cover_url', None)
        b.pop('cover_url', None)
    a['clips'] = [c for c in a['clips'] if c.get('trackId') != 'main']
    b['clips'] = [c for c in b['clips'] if c.get('trackId') != 'main']
    if a != b:
        raise RuntimeError('返工修改了非主画面内容；声音、字幕、时长必须原样保留')
    # ---------- 只换来源、不改时间轴时，沿用平台原时间轴 ----------
    # 平台生成的时间轴本身可能有片头偏移或几帧重叠（11-18 HC 13 条里有 3 条），能正常渲染；
    # 只换 src 的返工不该因此被拒。时间轴一变，就按连续、无单帧严格校验。
    timing = lambda d: [(c['id'], c['from'], c['durationInFrames']) for c in main_clips(d)]
    same_timing = timing(before) == timing(after)
    end = 0
    ids = set()
    for c in main_clips(after):
        if c['id'] in ids:
            raise RuntimeError('主画面 clip id 重复')
        ids.add(c['id'])
        if not same_timing and (c['from'] != end or c['durationInFrames'] < 2):
            raise RuntimeError('主画面有空隙、重叠或单帧镜头')
        end = c['from'] + c['durationInFrames']
        props = c.get('props') or {}
        rate, start = props.get('playbackRate', 1), props.get('startFrom', 0)
        if not isinstance(start, int):
            raise RuntimeError('主画面 startFrom 必须是整数帧')
        if (not props.get('src') or props.get('loop') or not math.isfinite(rate)
                or rate <= 0 or not math.isfinite(start) or start < 0):
            raise RuntimeError('主画面来源或播放区间无效')
    if not same_timing and end != after['composition']['durationInFrames']:
        raise RuntimeError('主画面未覆盖整片')


def validate_sources(draft, durations, before=None):
    """新窗口须有实测源时长；完全未变的旧窗口沿用原剪辑。"""
    def identity(clip):
        clip = copy.deepcopy(clip)
        clip['props']['src'] = key(clip['props']['src'])
        if clip['props'].get('loop') is False:
            clip['props'].pop('loop')
        return clip
    original = {c['id']: identity(c) for c in main_clips(before)} if before else {}
    fps = draft['composition']['fps']
    for clip in main_clips(draft):
        if before and before['composition']['fps'] == fps and original.get(clip['id']) == identity(clip):
            continue
        props = clip['props']
        src = key(props['src'])
        duration = durations.get(src)
        if duration is None or not math.isfinite(duration) or duration <= 0:
            raise RuntimeError(f'缺少有效源片时长证据：{src}')
        end = (props.get('startFrom', 0) + clip['durationInFrames'] * props.get('playbackRate', 1)) / fps
        if end > duration + 0.001:
            raise RuntimeError(f'播放超出源片：{src}，终点 {end:.3f}s > {duration:.3f}s')


def authorized_tracks(draft, plan):
    """默认声音不变；清单显式授权时只调音量与 AI 角标。"""
    d = copy.deepcopy(draft)
    mix = plan.get('audio_mix')
    if mix is not None:
        if not mix.get('instruction'):
            raise RuntimeError('调音清单缺少用户原话')
        for name in ('bgm_volume', 'voice_volume'):
            value = mix.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise RuntimeError(f'无效音量：{name}')
        for c in d['clips']:
            if c.get('trackId') in ('audio', 'voiceover'):
                c['props']['volume'] = mix['bgm_volume' if c['trackId'] == 'audio' else 'voice_volume']
    if 'ai_spans' in plan:
        end = d['composition']['durationInFrames']
        for a, n in plan['ai_spans']:
            if not isinstance(a, int) or not isinstance(n, int) or a < 0 or n <= 0 or a + n > end:
                raise RuntimeError('AI 角标时段无效')
        d = with_watermark(d, plan['ai_spans'])
    return d


def validate_plan(before, after, plan, allow_render_cover=False):
    validate_repair(authorized_tracks(before, plan), after, allow_render_cover)


def repair(cfg, args):
    plan = load(args.plan)
    if not plan.get('instruction') or not plan.get('reason'):
        raise RuntimeError('返工清单缺少用户授权或原因')
    cid = plan['cut_id']
    root = Path(args.out)
    intent_path = root / f'intent-render-{cid}.json'
    receipt_path = root / f'render-{cid}.json'
    if intent_path.exists() or receipt_path.exists():
        raise RuntimeError('已有渲染意图或收据；只读查询，禁止重复提交')
    current = cb.api('GET', f'/video-cuts/{cid}')
    resume = getattr(args, 'resume_backup', None)
    if resume:
        record = load(resume)
        before, p = record['before'], Path(resume)
        if record['plan'] != plan or before['id'] != cid or current['script_id'] != before['script_id']:
            raise RuntimeError('续跑备份不属于同一清单或脚本')
        draft = current['draft']
        if normalized_main(main_clips(draft)) != normalized_main(plan['replacement_main']):
            raise RuntimeError('续跑草稿不是清单指定的已写入版本')
    else:
        before = current
        draft = copy.deepcopy(before['draft'])
        if 'expected_draft' in plan and stable_media(draft) != stable_media(plan['expected_draft']):
            raise RuntimeError('线上草稿已变，拒绝覆盖并发修改')
        if normalized_main(main_clips(draft)) != normalized_main(plan['expected_main']):
            raise RuntimeError('线上主画面已变，拒绝覆盖并发修改')
        draft['clips'] = [c for c in draft['clips'] if c.get('trackId') != 'main'] + plan['replacement_main']
        draft = authorized_tracks(draft, plan)
    validate_plan(before['draft'], draft, plan)
    validate_sources(draft, plan.get('source_durations') or {}, before['draft'])
    print(f'{cid}：按清单换主画面、显式调音与角标；文案、字幕、时长不变，总帧 {draft["composition"]["durationInFrames"]}')
    if args.dry_run:
        return
    if not resume:
        p = backup(args.out, 'cut-backup', {'plan': plan, 'before': before})
        print(f'备份 → {p}')
        cb.api('PUT', f'/video-cuts/{cid}', {'script_id': before['script_id'], 'draft': draft})
        current = cb.api('GET', f'/video-cuts/{cid}')
    got = current['draft']
    validate_plan(before['draft'], got, plan)
    if normalized_main(main_clips(got)) != normalized_main(plan['replacement_main']):
        raise RuntimeError('回读主画面与返工清单不一致，不提交渲染')
    save(root / f'cut-readback-{cid}.json', current)
    digest = hashlib.sha256(json.dumps(stable_media(got), sort_keys=True).encode()).hexdigest()[:20]
    payload = {'spec': got, 'options': {'codec': 'h264', 'quality': 'high', 'scale': 1},
               'cut_id': cid, 'idempotency_key': f'udb-qc-{cid}-{digest}'}
    save(intent_path, {'cut_id': cid, 'backup': str(p), 'state': 'submitting', 'payload': payload})
    job = cb.api('POST', '/render-jobs', payload, tries=1)
    receipt = {'cut_id': cid, 'job_id': job['job_id'], 'backup': str(p), 'reason': plan['reason']}
    save(receipt_path, receipt)
    save(intent_path, dict(receipt, state='submitted'))
    print(f'渲染 → {job["job_id"]}')


def render_status(cfg, args):
    for f in sorted(Path(args.out).glob('render-*.json')):
        receipt = load(f)
        job = cb.api('GET', f'/render-jobs/{receipt["job_id"]}')
        receipt['result'] = job
        save(f, receipt)
        print(f'{receipt["cut_id"]} {job["status"]} {job.get("error_message") or ""}', flush=True)
        if job['status'] == 'success' and args.download:
            dest = Path(args.out) / 'final' / f'{receipt["cut_id"]}.mp4'
            dest.parent.mkdir(parents=True, exist_ok=True)
            cb.fetch_file(job['download_url'], dest)
            save(dest.with_suffix('.probe.json'), probe(dest))
            print(f'下载 → {dest}')


def review(cfg, args):
    """只审核返工收据指定的新输出；保留旧输出，不下载整批成片。"""
    root = Path(args.out)
    paths = sorted(root.glob('render-*.json'))
    if not paths:
        raise RuntimeError('没有本批返工渲染收据')
    deadline = time.monotonic() + 1800
    while True:
        rows, pending = [], []
        for path in paths:
            receipt = load(path)
            job = cb.api('GET', f'/render-jobs/{receipt["job_id"]}')
            receipt['result'] = job
            save(path, receipt)
            if job['status'] in ('failed', 'failure', 'error', 'cancelled', 'canceled', 'timeout'):
                raise RuntimeError(f'{receipt["cut_id"]} 渲染失败：{job}')
            if job['status'] != 'success':
                pending.append(receipt['cut_id'])
                continue
            cut = cb.api('GET', f'/video-cuts/{receipt["cut_id"]}')
            original = load(receipt['backup'])
            if cut['script_id'] != original['before']['script_id'] or cut.get('output_object_key') != job['object_key']:
                raise RuntimeError('脚本或输出与本批渲染收据不一致')
            validate_plan(original['before']['draft'], cut['draft'], original['plan'], allow_render_cover=True)
            if normalized_main(main_clips(cut['draft'])) != normalized_main(original['plan']['replacement_main']):
                raise RuntimeError('线上草稿不是本批定点修订版本')
            rows.append({'cut_id': receipt['cut_id'], 'job_id': receipt['job_id'],
                         'object_key': job['object_key']})
        if not pending:
            break
        print(f'等待本批重渲 {len(pending)} 条', flush=True)
        if args.dry_run or not args.wait:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('等待重渲超时；未提交审核')
        time.sleep(30)
    print(f'只重新审核本批 {len(rows)} 个新输出；不改文案或审核规则', flush=True)
    if args.dry_run:
        return
    intent_path = root / 'review-intent.json'
    if intent_path.exists():
        intent = load(intent_path)
        if intent['videos'] != rows or intent['state'] != 'submitted':
            raise RuntimeError('审核意图不一致或结果不确定；禁止重复提交')
    else:
        intent = {'state': 'submitting', 'videos': rows}
        save(intent_path, intent)
        response = cb.api('POST', '/video-cuts/review', {'cut_ids': [r['cut_id'] for r in rows]}, tries=1)
        intent.update(state='submitted', response=response)
        save(intent_path, intent)
    response = intent['response']
    if response.get('queued') != len(rows) or response.get('skipped', 0):
        raise RuntimeError(f'新版审核受理数量不符，不接收旧结论：{response}')
    while True:
        results = []
        for row in rows:
            cut = cb.api('GET', f'/video-cuts/{row["cut_id"]}')
            if cut.get('output_object_key') != row['object_key']:
                raise RuntimeError('审核期间输出已变化；停止交付')
            results.append(dict(row, status=cut.get('ai_review_status'), result=cut.get('ai_review_result'),
                                reason=cut.get('ai_review_reason')))
        save(root / 'review-results.json', results)
        print('新版审核', [(r['cut_id'], r['status'], r['result']) for r in results], flush=True)
        bad = [r for r in results if r['result'] == 'reject' or r['status'] in ('failed', 'failure', 'error')]
        terminal = all(r['status'] in ('success', 'failed', 'failure', 'error') for r in results)
        if bad and (not args.wait or terminal):
            raise RuntimeError(f'新版审核未通过：{bad}')
        if all(r['status'] == 'success' and r['result'] == 'pass' for r in results):
            return
        if not args.wait:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('等待新版审核超时；不创建分享')
        time.sleep(30)


def discover(cfg, args):
    """只读查找线上既有剪辑，避免把本地空 brief 当作已生产批次。"""
    records = []
    skip = 0
    resource = args.resource or ('video-scripts' if args.scripts else 'video-cuts')
    path = '/' + resource
    while True:
        data = cb.api('GET', f'{path}?skip={skip}&limit=100')
        rows = data if isinstance(data, list) else data.get('items', [])
        records.extend(rows)
        if len(rows) < 100:
            break
        skip += 100
        if skip >= 2000:
            break
    filename = 'discovered-' + resource.removeprefix('video-') + '.json'
    p = save(Path(args.out) / filename, records)
    print(f'只读 {resource} 列表 {len(records)} 条 → {p}')
    for c in records:
        title = c.get('title') or c.get('name') or ''
        if args.tag and args.tag not in title:
            continue
        print(c.get('id'), title, c.get('script_id'), c.get('created_at'))


def download(cfg, args):
    """用分享读回的现签地址下载，按对象键留存，不覆盖另一版成片。"""
    for sid in args.share or []:
        share = cb.api('GET', f'/video-shares/{sid}')
        save(Path(args.out) / f'share-{sid}.json', share)
        for v in share['videos']:
            object_key = v.get('video_object_key') or key(v['video_url'])
            wanted = set(args.object_key or [])
            if wanted and object_key not in wanted:
                continue
            dest = Path(args.out) / 'media' / Path(object_key).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                cb.fetch_file(v['video_url'], dest)
            save(dest.with_suffix('.probe.json'), probe(dest))
            print(f'下载 {object_key} → {dest}', flush=True)


WATERMARK_PROPS = {"text": "内容由AI生成", "top": 95.0, "from_bottom": False, "padding_x": 1.0, "align_type": "right",
                   "font_size": 18, "font_family": "PingFang SC", "bold": True, "italic": False, "underline": False,
                   "text_color": "#ffffff", "background_color": None, "stroke_color": None, "stroke_width": None,
                   "text_shadow": None, "font_url": None}


def with_watermark(draft, spans=None):
    """右下角「内容由AI生成」：和平台 global + bottom_right 生成的水印一模一样（11-18 力士样片）。
    spans=None 整片显示；给了 [(from, frames)] 就只盖这些 AI 镜头（用户 11-19：KP 客户要求非 AI 素材不加角标）。
    旧水印（左上、只盖 AI 切片的 segments 模式）整组换掉；其他轨道不动。"""
    d = copy.deepcopy(draft)
    if not any(t['id'] == 'watermark' for t in d['tracks']):
        d['tracks'].append({'id': 'watermark', 'name': '水印'})
    if spans is None:
        spans = [(0, d['composition']['durationInFrames'])]
    d['clips'] = [c for c in d['clips'] if c.get('trackId') != 'watermark'] + [{
        'id': f'compliance-watermark-{i}' if i else 'compliance-watermark', 'trackId': 'watermark', 'from': a,
        'durationInFrames': n, 'transitionIn': None, 'transitionOut': None,
        'motion': None, 'component': 'SubtitleClip', 'props': dict(WATERMARK_PROPS)} for i, (a, n) in enumerate(spans)]
    return d


def watermark(cfg, args):
    """用户 11-18：清扬两组交付 90 条右下角都要「AI生成」标识。逐条备份、写回、读回、渲染；--wait 等渲染完。"""
    out = Path(args.out)
    receipts = out / 'watermark-receipts.json'
    done = load(receipts) if receipts.exists() else {}
    for cid in args.cut:
        if cid in done:
            continue
        before = cb.api('GET', f'/video-cuts/{cid}')
        draft = with_watermark(before['draft'])
        a = [c for c in before['draft']['clips'] if c.get('trackId') != 'watermark']
        b = [c for c in draft['clips'] if c.get('trackId') != 'watermark']
        if a != b:
            raise RuntimeError(f'{cid} 加水印改动了别的轨道')
        if args.dry_run:
            print(f'{cid} 将加右下角水印（原有水印 {len(before["draft"]["clips"]) - len(a)} 段）')
            continue
        p = backup(out, f'wm-backup-{cid}', before)
        cb.api('PUT', f'/video-cuts/{cid}', {'script_id': before['script_id'], 'draft': draft})
        got = cb.api('GET', f'/video-cuts/{cid}')['draft']
        wm = [c for c in got['clips'] if c.get('trackId') == 'watermark']
        if len(wm) != 1 or wm[0]['props'] != WATERMARK_PROPS or wm[0]['durationInFrames'] != got['composition']['durationInFrames']:
            raise RuntimeError(f'{cid} 读回水印不对，不渲染')
        job = cb.api('POST', '/render-jobs', {'spec': got, 'options': {'codec': 'h264', 'quality': 'high', 'scale': 1},
                     'cut_id': cid, 'idempotency_key': f'udb-wm-{cid}'})
        done[cid] = {'job_id': job['job_id'], 'backup': str(p)}
        save(receipts, done)
        print(f'{cid} 渲染 {job["job_id"]}', flush=True)
    if args.wait and not args.dry_run:
        import time
        left = {c: done[c]['job_id'] for c in args.cut if done.get(c, {}).get('status') != 'success'}
        while left:
            time.sleep(15)
            for cid, jid in list(left.items()):
                j = cb.api('GET', f'/render-jobs/{jid}')
                if j['status'] in ('success', 'failed', 'canceled'):
                    done[cid].update(status=j['status'], object_key=j.get('object_key'))
                    if j['status'] != 'success':
                        # 11-18：源素材已被删（404）时渲染失败，平台把成片字段清空；把原成片写回，旧分享和下载不受影响
                        old = load(done[cid]['backup'])
                        if old.get('output_status') == 'success':
                            cb.api('PUT', f'/video-cuts/{cid}/output', {'status': 'success', 'object_key': old['output_object_key']})
                            done[cid]['restored_output'] = old['output_object_key']
                        done[cid]['error'] = (j.get('error_message') or '')[:200]
                    save(receipts, done)
                    left.pop(cid)
                    print(f'{cid} {j["status"]}', flush=True)


def main():
    ap = argparse.ArgumentParser(description='分类复核与成片画面返工；先快照，后清单，写入前 dry-run')
    ap.add_argument('cmd', choices=['snapshot', 'apply-tags', 'cut-snapshot', 'repair', 'render-status', 'review', 'discover', 'download', 'watermark'])
    ap.add_argument('--config', required=True)
    ap.add_argument('--product', action='append')
    ap.add_argument('--segment', action='append', help='snapshot: 只读指定切片 ID')
    ap.add_argument('--tag')
    ap.add_argument('--cut', action='append')
    ap.add_argument('--out', required=True)
    ap.add_argument('--plan')
    ap.add_argument('--resume-backup', help='repair: 核对已写入草稿后，仅续渲染；不再次 PUT')
    ap.add_argument('--download', action='store_true')
    ap.add_argument('--scripts', action='store_true', help='discover: 脚本列表；cut-snapshot: 同时读回完整脚本')
    ap.add_argument('--resource', choices=['video-scripts', 'video-cuts', 'video-shares', 'video-cut-generations'])
    ap.add_argument('--share', action='append', help='download: 分享 ID')
    ap.add_argument('--object-key', action='append', help='download: 只下载指定输出对象键')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--wait', action='store_true', help='watermark/review: 等渲染或新版审核终态')
    args = ap.parse_args()
    cfg = load_config(args.config)
    cb.BASE, cb.CFG = cfg['clipo']['base_url'].rstrip('/') + '/api/v1', cfg
    {'snapshot': snapshot, 'apply-tags': apply_tags, 'cut-snapshot': cut_snapshot,
     'repair': repair, 'render-status': render_status, 'review': review, 'discover': discover, 'download': download, 'watermark': watermark}[args.cmd](cfg, args)


if __name__ == '__main__':
    main()
