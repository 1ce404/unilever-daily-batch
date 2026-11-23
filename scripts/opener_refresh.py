#!/usr/bin/env python3
"""
[INPUT]: 依赖 common 的人审与配置；依赖 clipo_batch 的平台入口；依赖 footage_qc 的返工契约。
[OUTPUT]: 提供分组返工 prepare/apply/download。固定原分享顺序，逐条分配独立 AIGC 来源。
[POS]: scripts 的已交付分组返工入口。只改指定开头、显式音量与 AI 角标；不写共享 state。
       prepare 保存原分享、完整草稿与源时长。apply 复用备份、回读和单次渲染意图。
       download 只下载指定分组，旧本地文件移到恢复目录，不删除。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""
import argparse
import copy
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clipo_batch as cb
import footage_qc as fq
from common import load_config, approval_problem


def prepare(cfg, args):
    root = Path(args.out)
    if (root / 'batch.json').exists():
        raise RuntimeError('返工清单已存在；使用 apply 续跑，不覆盖原快照')
    state = fq.load(Path(args.run) / 'clipo/state.json')
    by_cut = {v['cut_id']: dict(v, video_id=k) for k, v in state['videos'].items() if v.get('cut_id')}
    by_object = {v.get('object_key'): cid for cid, v in by_cut.items()}
    gid = args.group.rstrip('/').rsplit('/', 1)[-1]
    group = cb.api('GET', f'/video-share-groups/{gid}')
    slots = fq.load(args.aigc_state)['slots']
    selections = fq.load(args.selection)
    pools, sources = {}, {}
    for bid, rule in selections.items():
        pools[bid] = [dict(v, slot=k) for k, v in sorted(slots.items())
                      if v.get('scene') == rule['scene'] and v.get('segment_id')]
        result = cb.api('POST', '/materials/segments/search', {
            'product_id': cfg['products'][args.product]['clipo_product_id'],
            'tags': [rule['tag']], 'limit': 200})
        sources.update({x['segment']['id']: x['segment'] for x in result.get('items', [])})
    bgm, voice = cb.mix_volumes(args.product)
    if bgm is None or voice is None:
        raise RuntimeError('产品配置必须明确 BGM 与口播音量')
    records, seen = [], set()
    for summary in group['shares']:
        share = cb.api('GET', f'/video-shares/{summary["id"]}')
        share_rows = []
        for index, item in enumerate(share['videos']):
            cid = item.get('cut_id') or by_object.get(fq.key(item['video_url']))
            if not cid or cid in seen:
                raise RuntimeError('原分享缺少剪辑归属或存在重复剪辑')
            seen.add(cid)
            v = by_cut.get(cid)
            if not v:
                raise RuntimeError(f'状态无法确定剪辑归属：{cid}')
            bid = v['brief_id']
            brief = fq.load(Path(args.run) / 'briefs' / f'{bid}.json')
            problem = approval_problem(brief)
            if problem:
                raise RuntimeError(f'{bid}：{problem}')
            cut = cb.api('GET', f'/video-cuts/{cid}')
            if cut.get('output_object_key') != fq.key(item['video_url']):
                raise RuntimeError(f'{cid}：原分享不是当前输出，拒绝覆盖并发返工')
            main = fq.main_clips(cut['draft'])
            new = copy.deepcopy(main)
            durations, chosen = {}, None
            if bid in selections:
                pool = pools[bid]
                if len(pool) < len(share['videos']):
                    raise RuntimeError(f'{bid} 新来源不足 {len(share["videos"])} 条')
                chosen = pool[index]
                source = sources.get(chosen['segment_id'])
                if not source:
                    raise RuntimeError(f'新素材尚未可检索：{chosen["segment_id"]}')
                # ---------- 同段多镜头连续读取同一新源，不重复播放片头 ----------
                ids = set(selections[bid]['segments'])
                shots = [c for c in new if str(c['id']).split('-')[0] in ids]
                if not shots:
                    raise RuntimeError(f'{cid} 未找到指定开头段')
                fps = cut['draft']['composition']['fps']
                duration = chosen['duration_ms'] / 1000
                frames = sum(c['durationInFrames'] for c in shots)
                # 橙绿对白完整放入原三秒窗口；其他镜头保持原速。
                fitted = (math.floor(duration * fps) - 1) / frames
                rate = fitted if selections[bid].get('fit_full') else min(1, fitted)
                offset = 0
                for c in shots:
                    c['props'].update(src=source['object_url'], startFrom=round(offset), playbackRate=rate,
                                      alt=chosen.get('description', '')[:120])
                    c['props'].pop('loop', None)
                    if selections[bid].get('original_audio'):
                        c['props']['volume'] = voice
                    offset += c['durationInFrames'] * rate
                durations[fq.key(source['object_url'])] = duration
            draft = copy.deepcopy(cut['draft'])
            draft['clips'] = [c for c in draft['clips'] if c.get('trackId') != 'main'] + new
            # ---------- 标签只定位 AI 时段；不抽帧，不做人工内容检查 ----------
            spans = cb.ai_spans(brief['product_key'], draft)
            if chosen:
                ids = set(selections[bid]['segments'])
                windows = [(c['from'], c['durationInFrames']) for c in new if str(c['id']).split('-')[0] in ids]
                spans = merge_spans(spans + windows)
            plan = {'cut_id': cid, 'instruction': args.instruction,
                    'reason': f'{bid} 第{index + 1}条：独立新开头与调音' if chosen else f'{bid} 第{index + 1}条：仅调音',
                    'expected_draft': cut['draft'], 'expected_main': main, 'replacement_main': new,
                    'source_durations': durations, 'audio_mix': {
                        'instruction': args.mix_instruction, 'bgm_volume': bgm, 'voice_volume': voice},
                    'ai_spans': spans, 'material_check': args.material_instruction,
                    'hook_slot': chosen['slot'] if chosen else None,
                    'review_waived': v.get('review_waived')}
            fq.validate_plan(cut['draft'], fq.authorized_tracks(draft, plan), plan)
            fq.validate_sources(draft, durations, cut['draft'])
            file = root / f'plan-{cid}.json'
            fq.save(file, plan)
            share_rows.append({'cut_id': cid, 'brief_id': bid, 'video_id': v['video_id'], 'plan': str(file.resolve())})
            print(f'{bid} {index + 1:02d} → {chosen["slot"] if chosen else "原开头"}；BGM {bgm}，口播 {voice}', flush=True)
        records.append({'before': share, 'videos': share_rows})
    fq.save(root / 'batch.json', {'group': group, 'shares': records, 'instruction': args.instruction})
    print(f'清单完成：{len(records)} 组，{len(seen)} 条', flush=True)


def merge_spans(spans):
    out = []
    for a, n in sorted(spans):
        if out and a <= out[-1][0] + out[-1][1]:
            out[-1][1] = max(out[-1][0] + out[-1][1], a + n) - out[-1][0]
        else:
            out.append([a, n])
    return out


def apply(cfg, args):
    batch = fq.load(Path(args.out) / 'batch.json')
    for share in batch['shares']:
        for row in share['videos']:
            receipt = Path(args.out) / f'render-{row["cut_id"]}.json'
            if receipt.exists():
                print(f'已提交，跳过 {row["cut_id"]}', flush=True)
                continue
            fq.repair(cfg, SimpleNamespace(plan=row['plan'], out=args.out, dry_run=args.dry_run, resume_backup=None))


def safe_name(name):
    return name.replace("/", "_").replace("\\", "_")


def download(cfg, args):
    gid = args.group.rstrip('/').rsplit('/', 1)[-1]
    group = cb.api('GET', f'/video-share-groups/{gid}')
    out = Path(args.download_out)
    manifest = out / '下载清单.json'
    if manifest.exists():
        old = fq.load(manifest)
        if old.get('group') != args.group:
            raise RuntimeError('本地目录不属于此分组')
        # 旧版本完整保留；新版在同一路径恢复目录结构。
        archive = Path(args.out) / 'previous-delivery'
        archive.mkdir(parents=True, exist_ok=True)
        for child in list(out.iterdir()):
            if (archive / child.name).exists():
                raise RuntimeError('本地恢复目录已存在，拒绝覆盖')
            child.rename(archive / child.name)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for summary in group['shares']:
        share = cb.api('GET', f'/video-shares/{summary["id"]}')
        folder = out / safe_name(share['title'])
        folder.mkdir(parents=True, exist_ok=True)
        videos = []
        for index, item in enumerate(share['videos']):
            file = folder / (safe_name(item.get('name') or f'{share["title"]}-{index + 1}') + '.mp4')
            cb.fetch_file(item['video_url'], file)
            videos.append({'file': file.name, 'bytes': file.stat().st_size, 'object_key': fq.key(item['video_url'])})
        rows.append({'title': share['title'], 'share': share['id'], 'videos': videos})
        print(f'下载 {share["title"]}：{len(videos)} 条', flush=True)
    fq.save(manifest, {'group': args.group, 'shares': rows})


def main():
    p = argparse.ArgumentParser(description='已交付分组的新开头与显式调音返工')
    p.add_argument('cmd', choices=['prepare', 'apply', 'download'])
    p.add_argument('--config', required=True)
    p.add_argument('--run', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--group')
    p.add_argument('--product', default='selenium')
    p.add_argument('--aigc-state')
    p.add_argument('--selection')
    p.add_argument('--instruction')
    p.add_argument('--mix-instruction')
    p.add_argument('--material-instruction')
    p.add_argument('--download-out')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    cfg = load_config(args.config)
    cb.CFG, cb.BASE = cfg, cfg['clipo']['base_url'].rstrip('/') + '/api/v1'
    {'prepare': prepare, 'apply': apply, 'download': download}[args.cmd](cfg, args)


if __name__ == '__main__':
    main()
