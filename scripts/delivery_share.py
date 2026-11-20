#!/usr/bin/env python3
"""
[INPUT]: 依赖 clipo_batch 的 API 与分享视频契约；读取用户指定的新渲染交付清单。
[OUTPUT]: 提供 share、batch、collect 与 replace；收组或替换时保留原分享链接。
[POS]: scripts 的新分享交付入口；batch 写前记意图，写后存收据，逐组逐分享回读。
       不修改脚本或审核；不删除或更新旧分享。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""
import argparse
import fcntl
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import load_config
from footage_qc import backup, key, load, save


def video_payload(cfg, entry):
    cid = entry['cut_id']
    cut = cb.api('GET', f'/video-cuts/{cid}')
    if cut.get('output_status') != 'success':
        raise RuntimeError(f'{cid} 新版渲染尚未成功')
    if cut['script_id'] != entry['script_id']:
        raise RuntimeError(f'{cid} 脚本已变化')
    if cut.get('output_object_key') != entry['expected_output_object_key']:
        raise RuntimeError(f'{cid} 输出不是交付清单指定的新渲染')
    script = cb.api('GET', f'/video-scripts/{cut["script_id"]}')
    pid = cfg['products'][entry['product_key']]['clipo_product_id']
    if script['product_id'] != pid:
        raise RuntimeError(f'{cid} 产品不匹配')
    # ---------- 复用批量入口；再次读取也须保持同一个输出对象 ----------
    video = cb.share_video({'cut_id': cid, 'object_key': entry['expected_output_object_key']})
    if video['video_object_key'] != entry['expected_output_object_key']:
        raise RuntimeError(f'{cid} 生成分享数据期间输出已变化')
    return {'script_id': cut['script_id'], 'product_id': pid,
            'title': entry['title'], 'video_name_prefix': entry['title'] + '-',
            'videos': [video]}


def verify_share(share, payload):
    actual = [v.get('video_object_key') or key(v['video_url']) for v in share['videos']]
    expected = [v['video_object_key'] for v in payload['videos']]
    if actual != expected:
        raise RuntimeError('分享读回不是指定新版输出')
    if share['title'] != payload['title']:
        raise RuntimeError('分享标题读回不一致')


def share(cfg, args):
    plan = load(args.plan)
    if not plan.get('instruction') or not plan.get('entries'):
        raise RuntimeError('交付清单缺少用户指令或视频')
    ids = [entry['cut_id'] for entry in plan['entries']]
    if len(set(ids)) != len(ids):
        raise RuntimeError('交付清单含重复剪辑')
    payloads = [(entry, video_payload(cfg, entry)) for entry in plan['entries']]
    print(f'只新建或复用本批 {len(payloads)} 个分享；不删除旧链接', flush=True)
    if args.dry_run:
        for entry, payload in payloads:
            print(entry['title'], payload['videos'][0]['video_object_key'])
        return
    root = Path(args.out)
    receipt_path = root / 'links.json'
    receipt = load(receipt_path) if receipt_path.exists() else {'group': None, 'shares': {}}
    if receipt['group'] and receipt['group']['name'] != plan['group_name']:
        raise RuntimeError('已有收据属于另一交付分组，拒绝覆盖')
    backup(root, 'share-plan', plan)
    if not receipt['group']:
        group = cb.api('POST', '/video-share-groups', {'name': plan['group_name']})
        receipt['group'] = {'id': group['id'], 'name': plan['group_name']}
        save(receipt_path, receipt)
    group_id = receipt['group']['id']
    for entry, payload in payloads:
        cid = entry['cut_id']
        old = receipt['shares'].get(cid)
        if old:
            current = cb.api('GET', f'/video-shares/{old["id"]}')
            verify_share(current, payload)
        else:
            created = cb.api('POST', '/video-shares', dict(payload, group_id=group_id))
            # 写后立即留收据；读回失败也不丢已创建的分享编号。
            old = {'id': created['id'], 'title': payload['title'],
                   'object_key': payload['videos'][0]['video_object_key']}
            receipt['shares'][cid] = old
            save(receipt_path, receipt)
            current = cb.api('GET', f'/video-shares/{created["id"]}')
            verify_share(current, payload)
        save(root / f'share-{old["id"]}.json', current)
        print(f'{old["title"]}：{cb.share_url(old["id"])}', flush=True)
    group = cb.api('GET', f'/video-share-groups/{group_id}')
    if not {s['id'] for s in receipt['shares'].values()} <= {s['id'] for s in group['shares']}:
        raise RuntimeError('交付分组缺少刚创建的分享')
    save(root / 'group-readback.json', group)
    print(f'全部三条修订：{cb.BASE.replace("/api/v1", "")}/share/group/{group_id}', flush=True)


def validate_batch(cfg, plan):
    """先检查完整清单，再允许平台读取。账号顺序固定为三份、六份。"""
    groups = plan.get('groups')
    if not isinstance(plan.get('instruction'), str) or not plan['instruction'].strip():
        raise RuntimeError('批次缺少用户交付指令')
    if not isinstance(groups, list) or len(groups) != 2:
        raise RuntimeError('批次必须包含两个账号分组')
    cuts, names, scripts = set(), set(), set()
    for group, count in zip(groups, (3, 6)):
        name = group.get('name')
        if not isinstance(name, str) or not name.strip() or name in names:
            raise RuntimeError('账号分组名称必须非空且唯一')
        names.add(name)
        entries = group.get('entries')
        if not isinstance(entries, list) or len(entries) != count:
            raise RuntimeError('首账号须三份 brief，次账号须六份 brief')
        titles = set()
        for entry in entries:
            for field in ('title', 'script_id', 'product_id'):
                if not isinstance(entry.get(field), str) or not entry[field].strip():
                    raise RuntimeError(f'brief 缺少 {field}')
            if entry['title'] in titles:
                raise RuntimeError('同一账号的 brief 标题重复')
            if entry['script_id'] in scripts:
                raise RuntimeError('同一 brief 只能创建一个分享')
            scripts.add(entry['script_id'])
            titles.add(entry['title'])
            videos = entry.get('videos')
            if not isinstance(videos, list) or len(videos) != 10:
                raise RuntimeError('每份 brief 必须包含十条视频')
            for video in videos:
                for field in ('cut_id', 'expected_output_object_key'):
                    if not isinstance(video.get(field), str) or not video[field].strip():
                        raise RuntimeError(f'视频缺少 {field}')
                if video['cut_id'] in cuts:
                    raise RuntimeError('批次含重复剪辑')
                cuts.add(video['cut_id'])
                for field in ('script_id', 'product_id'):
                    if field in video and video[field] != entry[field]:
                        raise RuntimeError(f'{video["cut_id"]} 的 {field} 与 brief 不一致')
                for source in (entry, video):
                    if 'product_key' in source:
                        product = cfg.get('products', {}).get(source['product_key'], {})
                        if product.get('clipo_product_id') != entry['product_id']:
                            raise RuntimeError('product_key 与 brief 产品不一致')
    if len(cuts) != 90:
        raise RuntimeError('批次必须包含九十个唯一剪辑')


def batch_video(entry, video):
    cid, expected = video['cut_id'], video['expected_output_object_key']

    def check_cut(cut):
        if cut.get('script_id') != entry['script_id']:
            raise RuntimeError(f'{cid} 脚本不匹配')
        if 'product_id' in cut and cut['product_id'] != entry['product_id']:
            raise RuntimeError(f'{cid} 产品不匹配')
        if cut.get('output_status') != 'success' or cut.get('output_object_key') != expected:
            raise RuntimeError(f'{cid} 指定输出未成功或已变化')
        if cut.get('ai_review_status') != 'success' or cut.get('ai_review_result') != 'pass':
            raise RuntimeError(f'{cid} 最新审核未通过')

    check_cut(cb.api('GET', f'/video-cuts/{cid}'))
    script = cb.api('GET', f'/video-scripts/{entry["script_id"]}')
    if script.get('product_id') != entry['product_id']:
        raise RuntimeError(f'{cid} 脚本所属产品不匹配')
    result = cb.share_video({'cut_id': cid, 'object_key': expected})
    if result.get('video_object_key') != expected:
        raise RuntimeError(f'{cid} 分享对象已变化')
    # ---------- wrapper 会重读剪辑；随后再核对最新审核与绑定 ----------
    check_cut(cb.api('GET', f'/video-cuts/{cid}'))
    return result


def batch_payload(entry):
    return {'title': entry['title'], 'script_id': entry['script_id'],
            'product_id': entry['product_id'], 'video_name_prefix': entry['title'] + '-',
            'videos': [batch_video(entry, video) for video in entry['videos']]}


def save_journal(path, value):
    """原子替换并同步磁盘，保证意图先于 POST、收据先于读回。"""
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='.batch-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def batch_post(path, journal, operation, endpoint, payload):
    old = journal['operations'].get(operation)
    if old:
        def stable(value):
            if isinstance(value, dict):
                return {k: stable(v) for k, v in value.items()}
            if isinstance(value, list):
                return [stable(v) for v in value]
            return key(value) if isinstance(value, str) and value.startswith(('https://', 'http://')) else value
        if old['path'] != endpoint or stable(old['payload']) != stable(payload):
            raise RuntimeError('写入意图与当前交付数据不一致')
        if old['state'] != 'created':
            raise RuntimeError(f'{operation} 写入结果不确定；须人工核对，禁止自动重试')
        return old['response']
    intent = {'state': 'intent', 'method': 'POST', 'path': endpoint, 'payload': payload}
    journal['operations'][operation] = intent
    save_journal(path, journal)
    try:
        response = cb.api('POST', endpoint, payload, tries=1)
        if not response.get('id'):
            raise RuntimeError('POST 响应缺少编号')
    except Exception as exc:
        intent.update(state='uncertain', error=str(exc))
        save_journal(path, journal)
        raise
    intent.update(state='created', response=response)
    save_journal(path, journal)
    return response


def read_batch_share(root, share_id, payload):
    current = cb.api('GET', f'/video-shares/{share_id}')
    save(root / f'batch-share-{share_id}.json', current)
    if current.get('id') != share_id:
        raise RuntimeError('分享编号读回不一致')
    for field in ('script_id', 'product_id'):
        if current.get(field) != payload[field]:
            raise RuntimeError(f'分享 {field} 读回不一致')
    verify_share(current, payload)
    return current


def read_batch_group(root, index, group_id, name, shares):
    current = cb.api('GET', f'/video-share-groups/{group_id}')
    save(root / f'batch-group-{index}.json', current)
    if current.get('id') != group_id or current.get('name') != name:
        raise RuntimeError('账号分组编号或名称读回不一致')
    members = current['shares']
    if len(members) != len(shares) or {s['id'] for s in members} != set(shares):
        raise RuntimeError('账号分组分享成员读回不一致')
    for member in members:
        payload = shares[member['id']]
        for field in ('title', 'script_id', 'product_id'):
            if member.get(field) != payload[field]:
                raise RuntimeError(f'分组内分享 {field} 读回不一致')
        if member.get('video_count') != len(payload['videos']):
            raise RuntimeError('分组内分享视频数量读回不一致')
    return current


def run_batch(plan, root):
    receipt_path = root / 'batch-links.json'
    journal = load(receipt_path) if receipt_path.exists() else {'plan': plan, 'operations': {}}
    if journal['plan'] != plan:
        raise RuntimeError('已有收据属于另一批次，拒绝覆盖')
    for operation, intent in journal['operations'].items():
        if intent['state'] != 'created':
            raise RuntimeError(f'{operation} 写入结果不确定；须人工核对，禁止自动重试')
    # ---------- 全批预检先于任何 POST；失败不创建半份交付 ----------
    payloads = [[batch_payload(entry) for entry in group['entries']] for group in plan['groups']]
    journal['verified'] = False
    save_journal(receipt_path, journal)
    all_groups = []
    for gi, (group, entries) in enumerate(zip(plan['groups'], payloads)):
        created = batch_post(receipt_path, journal, f'group/{gi}',
                             '/video-share-groups', {'name': group['name']})
        gid = created['id']
        shares = {}
        for ei, payload in enumerate(entries):
            previous = journal['operations'].get(f'share/{gi}/{ei}')
            if previous:
                shares[previous['response']['id']] = payload
        # ---------- 新组须为空；重跑须恰好保留已记账的成员 ----------
        read_batch_group(root, gi, gid, group['name'], shares)
        for ei, payload in enumerate(entries):
            created_share = batch_post(receipt_path, journal, f'share/{gi}/{ei}',
                                       '/video-shares', dict(payload, group_id=gid))
            sid = created_share['id']
            read_batch_share(root, sid, payload)
            shares[sid] = payload
            print(f'{payload["title"]}：{cb.share_url(sid)}', flush=True)
        if len(shares) != len(entries):
            raise RuntimeError('平台返回了重复分享编号')
        read_batch_group(root, gi, gid, group['name'], shares)
        all_groups.append((gi, gid, group['name'], shares))
    if len({gid for _, gid, _, _ in all_groups}) != 2:
        raise RuntimeError('平台返回了重复账号分组编号')
    # ---------- 最终全量回读，覆盖写入期间的成员或视频变化 ----------
    for gi, gid, name, shares in all_groups:
        read_batch_group(root, gi, gid, name, shares)
        for sid, payload in shares.items():
            read_batch_share(root, sid, payload)
        print(f'{name}：{cb.BASE.replace("/api/v1", "")}/share/group/{gid}', flush=True)
    journal['verified'] = True
    save_journal(receipt_path, journal)


def batch(cfg, args):
    plan = load(args.plan)
    validate_batch(cfg, plan)
    if args.dry_run:
        for group in plan['groups']:
            for entry in group['entries']:
                payload = batch_payload(entry)
                print(group['name'], payload['title'],
                      [v['video_object_key'] for v in payload['videos']], flush=True)
        return
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    # ---------- 同一收据目录只允许一个写入进程 ----------
    with (root / '.batch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('同一批次正在交付，拒绝并发写入') from exc
        run_batch(plan, root)


def collect(cfg, args):
    """仅把用户指定的既有分享收进新组，保留原分享和其他分组。"""
    plan = load(args.plan)
    ids = plan['share_ids']
    if not plan.get('instruction') or not plan.get('name') or not ids or len(set(ids)) != len(ids):
        raise RuntimeError('缺少授权、名称或分享编号重复')
    shares = [cb.api('GET', '/video-shares/' + sid) for sid in ids]
    for sid, share in zip(ids, shares):
        if share.get('id') != sid or not share.get('videos'):
            raise RuntimeError('分享不存在或没有视频：' + sid)
        print(share['title'], len(share['videos']), flush=True)
    if args.dry_run:
        return
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'collect-receipt.json'
    journal = load(path) if path.exists() else {'plan': plan, 'operations': {}}
    if journal['plan'] != plan:
        raise RuntimeError('收据属于另一份清单')
    save(root / 'original-shares.json', shares)
    created = batch_post(path, journal, 'collect', '/video-share-groups',
                         {'name': plan['name'], 'video_share_ids': ids})
    group = cb.api('GET', '/video-share-groups/' + created['id'])
    save(root / 'collect-readback.json', group)
    if group.get('name') != plan['name'] or len(group['shares']) != len(ids) or {s['id'] for s in group['shares']} != set(ids):
        raise RuntimeError('分组成员读回不一致')
    for original in shares:
        current = cb.api('GET', '/video-shares/' + original['id'])
        objects = lambda sh: [key(v.get('video_object_key') or v['video_url']) for v in sh['videos']]
        if current['title'] != original['title'] or objects(current) != objects(original):
            raise RuntimeError('原分享标题或视频对象发生变化')
    journal['verified'] = True
    journal['url'] = cb.BASE.replace('/api/v1', '') + '/share/group/' + created['id']
    save_journal(path, journal)
    print('DELIVERY_LINK', journal['url'], flush=True)


def replace(cfg, args):
    """替换指定组的一份分享；改名另建分享，保留全部原链接。"""
    plan = load(args.plan)
    if not plan.get('instruction'):
        raise RuntimeError('缺少替换授权')
    group = cb.api('GET', '/video-share-groups/' + plan['group_id'])
    source = cb.api('GET', '/video-shares/' + plan['new_share_id'])
    if not source.get('videos'):
        raise RuntimeError('新分享没有视频')
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    save(root / 'source-share.json', source)
    print('新分享', source['title'], '视频数', len(source['videos']), flush=True)
    if source.get('script_id'):
        script = cb.api('GET', '/video-scripts/' + source['script_id'])
        save(root / 'source-script.json', script)
        print('脚本', script.get('title'), flush=True)
        print('文案', [s.get('content') for s in script.get('segments', [])], flush=True)
    if args.dry_run:
        return
    path = root / 'replace-receipt.json'
    journal = load(path) if path.exists() else {'plan': plan, 'operations': {}}
    if journal['plan'] != plan:
        raise RuntimeError('替换收据属于另一份清单')
    if 'before_group' not in journal:
        if plan['old_share_id'] not in {s['id'] for s in group['shares']}:
            raise RuntimeError('分组已变，旧分享不在组内')
        journal['before_group'] = group
        save_journal(path, journal)
    desired_title = plan['title']
    sid = source['id']
    if source['title'] != desired_title:
        videos = [{'video_object_key': key(v['video_url']),
                   'cover_object_key': key(v['cover_url']) if v.get('cover_url') else None,
                   'duration_seconds': float(v['duration_seconds']), 'width': int(v['width']), 'height': int(v['height'])}
                  for v in source['videos']]
        payload = {'script_id': source.get('script_id'), 'product_id': source['product_id'],
                   'title': desired_title, 'video_name_prefix': desired_title + '-', 'videos': videos}
        created = batch_post(path, journal, 'renamed-share', '/video-shares', payload)
        sid = created['id']
        renamed = cb.api('GET', '/video-shares/' + sid)
        verify_share(renamed, payload)
        save(root / 'renamed-share.json', renamed)
    before_ids = [s['id'] for s in journal['before_group']['shares']]
    wanted = [sid if old == plan['old_share_id'] else old for old in before_ids]
    current = cb.api('GET', '/video-share-groups/' + group['id'])
    current_ids = [s['id'] for s in current['shares']]
    if set(current_ids) != set(wanted):
        if set(current_ids) != set(before_ids):
            raise RuntimeError('分组被其他会话修改，拒绝覆盖')
        journal['patch_intent'] = {'video_share_ids': wanted}
        save_journal(path, journal)
        cb.api('PATCH', '/video-share-groups/' + group['id'], journal['patch_intent'], tries=1)
    after = cb.api('GET', '/video-share-groups/' + group['id'])
    save(root / 'group-readback.json', after)
    if len(after['shares']) != len(wanted) or {s['id'] for s in after['shares']} != set(wanted):
        raise RuntimeError('替换后组成员不一致')
    member = next(s for s in after['shares'] if s['id'] == sid)
    if member['title'] != desired_title or member['video_count'] != len(source['videos']):
        raise RuntimeError('新名称或视频数不一致')
    journal.update(verified=True, replacement_share_id=sid, url=cb.BASE.replace('/api/v1', '') + '/share/group/' + group['id'])
    save_journal(path, journal)
    print('DELIVERY_LINK', journal['url'], '视频总数', sum(s['video_count'] for s in after['shares']), flush=True)


def main():
    ap = argparse.ArgumentParser(description='指定新渲染的交付分享；不删旧链接')
    ap.add_argument('cmd', choices=['share', 'batch', 'collect', 'replace'])
    ap.add_argument('--config', required=True)
    ap.add_argument('--plan', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    cfg = load_config(args.config)
    cb.BASE, cb.CFG = cfg['clipo']['base_url'].rstrip('/') + '/api/v1', cfg
    {'share': share, 'batch': batch, 'collect': collect, 'replace': replace}[args.cmd](cfg, args)


if __name__ == '__main__':
    main()
