"""
[INPUT]: 依赖 delivery_share 的单条交付、九十条批次与写入收据。
[OUTPUT]: 验证配额、审核门禁、对象顺序、完整成员与不确定写入阻断；用户检查仅豁免内容审核，十条合并只写独立分享。
[POS]: tests 的交付安全回归；平台 API 全部使用离线桩，保留单条兼容测试。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""
import copy
import fcntl
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import delivery_share as ds


class DeliveryShareTest(unittest.TestCase):
    def setUp(self):
        self.cfg = {'products': {'product': {'clipo_product_id': 'pid'}}}
        self.entry = {'cut_id': 'cut', 'script_id': 'script', 'product_key': 'product',
                      'expected_output_object_key': 'outputs/new.mp4', 'title': '新版'}
        self.cut = {'output_status': 'success', 'script_id': 'script',
                    'output_object_key': 'outputs/new.mp4'}

    def test_old_output_rejected_before_share(self):
        self.cut['output_object_key'] = 'outputs/old.mp4'
        with patch.object(ds.cb, 'api', return_value=self.cut) as api:
            with self.assertRaises(RuntimeError):
                ds.video_payload(self.cfg, self.entry)
            self.assertEqual(api.call_count, 1)

    def test_wrong_product_rejected(self):
        with patch.object(ds.cb, 'api', side_effect=[self.cut, {'product_id': 'wrong'}]):
            with self.assertRaises(RuntimeError):
                ds.video_payload(self.cfg, self.entry)

    def test_concurrent_output_rejected(self):
        with patch.object(ds.cb, 'api', side_effect=[self.cut, {'product_id': 'pid'}]), \
             patch.object(ds.cb, 'share_video', return_value={'video_object_key': 'outputs/changed.mp4'}):
            with self.assertRaises(RuntimeError):
                ds.video_payload(self.cfg, self.entry)

    def test_readback_signed_url_same_object(self):
        payload = {'title': '新版', 'videos': [{'video_object_key': 'outputs/new.mp4'}]}
        ds.verify_share({'title': '新版', 'videos': [{'video_url': 'https://host/outputs/new.mp4?sign=1'}]}, payload)
        with self.assertRaises(RuntimeError):
            ds.verify_share({'title': '新版', 'videos': [{'video_object_key': 'outputs/old.mp4'}]}, payload)


class PackDeliveryTest(unittest.TestCase):
    def test_ten_revised_outputs_create_one_share_and_preserve_old_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = {'instruction': '交付定点新版', 'title': '油头修订十条', 'entries': [
                {'cut_id': f'c{i}', 'script_id': f's{i % 3}', 'product_key': 'product',
                 'expected_output_object_key': f'outputs/new{i}.mp4'} for i in range(10)]}
            path = ds.save(root / 'plan.json', plan)
            args = SimpleNamespace(plan=path, out=str(root / 'out'), dry_run=True)
            created = {}
            def video(entry, item):
                self.assertEqual(entry['product_id'], 'pid')
                return {'cut_id': item['cut_id'], 'video_object_key': item['expected_output_object_key']}
            def api(method, endpoint, body=None, **kwargs):
                if method == 'POST':
                    self.assertEqual(endpoint, '/video-shares')
                    self.assertEqual(kwargs['tries'], 1)
                    intent = ds.load(Path(args.out) / 'pack-links.json')['operations']['pack-share']
                    self.assertEqual(intent['state'], 'intent')
                    created.update(body, id='new-share')
                    return {'id': 'new-share'}
                self.assertEqual((method, endpoint), ('GET', '/video-shares/new-share'))
                return copy.deepcopy(created)
            with patch.object(ds, 'batch_video', side_effect=video), patch.object(ds.cb, 'BASE', 'https://offline.invalid/api/v1'), patch.object(ds.cb, 'api', side_effect=api) as mocked:
                ds.pack({'products': {'product': {'clipo_product_id': 'pid'}}}, args)
                mocked.assert_not_called()
                args.dry_run = False
                ds.pack({'products': {'product': {'clipo_product_id': 'pid'}}}, args)
                ds.pack({'products': {'product': {'clipo_product_id': 'pid'}}}, args)
                self.assertEqual(sum(c.args[0] == 'POST' for c in mocked.call_args_list), 1)
            self.assertEqual(len(created['videos']), 10)
            self.assertNotIn('group_id', created)
            self.assertNotIn('script_id', created)
            self.assertTrue(ds.load(Path(args.out) / 'pack-links.json')['verified'])

    def test_pack_quantity_and_duplicate_gates_precede_platform_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = {'instruction': '交付新版', 'title': '十条', 'entries': [
                {'cut_id': 'same', 'product_key': 'product'} for _ in range(10)]}
            path = ds.save(Path(directory) / 'plan.json', plan)
            args = SimpleNamespace(plan=path, out=directory, dry_run=False)
            with patch.object(ds, 'batch_video') as mocked:
                with self.assertRaises(RuntimeError):
                    ds.pack({}, args)
                plan['entries'].pop()
                ds.save(path, plan)
                with self.assertRaises(RuntimeError):
                    ds.pack({}, args)
                mocked.assert_not_called()


def batch_plan():
    groups = []
    for gi, count in enumerate((3, 6)):
        entries = []
        for ei in range(count):
            sid = f'script-{gi}-{ei}'
            entries.append({'title': f'账号{gi}-brief{ei}', 'script_id': sid,
                            'product_id': f'product-{gi}', 'videos': [
                                {'cut_id': f'{sid}-{vi}',
                                 'expected_output_object_key': f'outputs/{sid}-{vi}.mp4'}
                                for vi in range(10)]})
        groups.append({'name': f'20261118-账号{gi}', 'entries': entries})
    return {'instruction': '交付已审核的11月18日两账号九十条成片', 'groups': groups}


class OfflinePlatform:
    """保留真实 wrapper 的 GET；模拟平台返回签名 URL 和倒序分组成员。"""
    def __init__(self, plan, root):
        self.root = root
        self.groups, self.shares, self.cuts, self.scripts = {}, {}, {}, {}
        self.calls = []
        self.fail_post = None
        self.fail_read = False
        self.after_get = None
        for group in plan['groups']:
            for entry in group['entries']:
                self.scripts[entry['script_id']] = {'product_id': entry['product_id']}
                for video in entry['videos']:
                    self.cuts[video['cut_id']] = {
                        'script_id': entry['script_id'], 'product_id': entry['product_id'],
                        'output_status': 'success',
                        'output_object_key': video['expected_output_object_key'],
                        'ai_review_status': 'success', 'ai_review_result': 'pass',
                        'draft': {'composition': {'durationInFrames': 300, 'fps': 30,
                                                  'width': 1080, 'height': 1920}}}

    def api(self, method, path, body=None, **kwargs):
        self.calls.append((method, path, copy.deepcopy(body), kwargs))
        if method == 'POST':
            if kwargs.get('tries') != 1:
                raise AssertionError('POST 必须禁用 wrapper 重试')
            journal = ds.load(self.root / 'batch-links.json')
            intents = [op for op in journal['operations'].values()
                       if op['state'] == 'intent' and op['path'] == path and op['payload'] == body]
            if len(intents) != 1:
                raise AssertionError('POST 前缺少唯一落盘意图')
            if path == '/video-share-groups':
                result = {'id': f'group-{len(self.groups)}', 'name': body['name'], 'shares': []}
                self.groups[result['id']] = result
            elif path == '/video-shares':
                sid = f'share-{len(self.shares)}'
                result = {k: copy.deepcopy(v) for k, v in body.items() if k != 'group_id'}
                result['id'] = sid
                self.shares[sid] = result
                self.groups[body['group_id']]['shares'].insert(0, {
                    'id': sid, 'title': body['title'], 'script_id': body['script_id'],
                    'product_id': body['product_id'], 'video_count': len(body['videos'])})
            else:
                raise AssertionError(path)
            if self.fail_post == path:
                raise TimeoutError('平台已写入，但响应丢失')
            return copy.deepcopy(result)
        if method != 'GET':
            raise AssertionError(f'禁止写入 {method} {path}')
        resource, ident = path.strip('/').split('/')
        if resource == 'video-cuts':
            result = copy.deepcopy(self.cuts[ident])
        elif resource == 'video-scripts':
            result = copy.deepcopy(self.scripts[ident])
        elif resource in ('video-shares', 'video-share-groups'):
            journal = ds.load(self.root / 'batch-links.json')
            if not any(op.get('response', {}).get('id') == ident and op['state'] == 'created'
                       for op in journal['operations'].values()):
                raise AssertionError('读回前缺少已保存收据')
            if resource == 'video-shares':
                if self.fail_read:
                    raise TimeoutError('读回暂时失败')
                result = copy.deepcopy(self.shares[ident])
                for video in result['videos']:
                    video['video_url'] = 'https://host/' + video.pop('video_object_key') + '?sign=1'
            else:
                result = copy.deepcopy(self.groups[ident])
        else:
            raise AssertionError(path)
        if self.after_get:
            self.after_get(resource, ident, result)
        return result

    @property
    def posts(self):
        return [call for call in self.calls if call[0] == 'POST']


class UserContentReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.platform = OfflinePlatform(batch_plan(), Path(self.temp.name))
        self.entry = copy.deepcopy(batch_plan()['groups'][0]['entries'][0])
        self.video = self.entry['videos'][0]
        self.cid = self.video['cut_id']
        self.platform.cuts[self.cid]['ai_review_result'] = 'reject'
        self.policy = {'mode': 'user', 'instruction': '我来检查', 'by': '用户'}
        self.addCleanup(patch.stopall)
        patch.object(ds.cb, 'api', side_effect=self.platform.api).start()

    def test_explicit_user_review_preserves_platform_rejection(self):
        with self.assertRaisesRegex(RuntimeError, '最新审核'):
            ds.batch_video(self.entry, self.video)
        self.entry['content_review'] = self.policy
        result = ds.batch_video(self.entry, self.video)
        self.assertEqual(result['video_object_key'], self.video['expected_output_object_key'])
        self.assertEqual(self.platform.cuts[self.cid]['ai_review_result'], 'reject')
        self.assertEqual(self.platform.posts, [])

    def test_invalid_user_policy_rejected_before_platform_reads(self):
        for policy in (None, {}, 'user', dict(self.policy, mode='platform'),
                       dict(self.policy, by='Agent'), dict(self.policy, instruction=' ')):
            with self.subTest(policy=policy):
                self.entry['content_review'] = policy
                with self.assertRaisesRegex(RuntimeError, '用户自行检查'):
                    ds.batch_video(self.entry, self.video)
        self.assertEqual(self.platform.calls, [])

    def test_user_review_keeps_output_product_and_script_gates(self):
        self.entry['content_review'] = self.policy
        original = copy.deepcopy(self.platform.cuts[self.cid])
        for field, value in [('output_status', 'failed'), ('output_object_key', 'outputs/old.mp4'),
                             ('script_id', 'wrong'), ('product_id', 'wrong')]:
            with self.subTest(field=field):
                self.platform.cuts[self.cid] = dict(original, **{field: value})
                with self.assertRaises(RuntimeError):
                    ds.batch_video(self.entry, self.video)
        self.platform.cuts[self.cid] = original
        self.platform.scripts[self.entry['script_id']]['product_id'] = 'wrong'
        with self.assertRaisesRegex(RuntimeError, '脚本所属产品'):
            ds.batch_video(self.entry, self.video)
        self.assertEqual(self.platform.posts, [])

    def test_null_cut_product_requires_matching_script_product(self):
        self.platform.cuts[self.cid].update(product_id=None, ai_review_result='pass')
        result = ds.batch_video(self.entry, self.video)
        self.assertEqual(result['video_object_key'], self.video['expected_output_object_key'])
        self.platform.scripts[self.entry['script_id']]['product_id'] = 'wrong'
        with self.assertRaisesRegex(RuntimeError, '脚本所属产品'):
            ds.batch_video(self.entry, self.video)
        self.assertEqual(self.platform.posts, [])

    def test_user_review_rechecks_concurrent_output(self):
        self.entry['content_review'] = self.policy
        reads = 0
        def change_output(resource, ident, result):
            nonlocal reads
            if resource == 'video-cuts':
                reads += 1
                if reads == 3:
                    result['output_object_key'] = 'outputs/other.mp4'
        self.platform.after_get = change_output
        with self.assertRaisesRegex(RuntimeError, '已变化'):
            ds.batch_video(self.entry, self.video)
        self.assertEqual(self.platform.posts, [])

    def test_pack_passes_user_policy_to_each_video(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = {'instruction': '允许创建分享', 'title': '用户检查十条',
                    'content_review': self.policy, 'entries': [
                        {'cut_id': f'c{i}', 'script_id': 's', 'product_key': 'p',
                         'expected_output_object_key': f'outputs/new{i}.mp4'} for i in range(10)]}
            path = ds.save(Path(directory) / 'plan.json', plan)
            def video(entry, item):
                self.assertEqual(entry['content_review'], self.policy)
                return {'video_object_key': item['expected_output_object_key']}
            with patch.object(ds, 'batch_video', side_effect=video) as mocked:
                ds.pack({'products': {'p': {'clipo_product_id': 'pid'}}},
                        SimpleNamespace(plan=path, out=directory, dry_run=True))
                self.assertEqual(mocked.call_count, 10)
        self.assertEqual(self.platform.calls, [])


class BatchDeliveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = batch_plan()
        self.plan_path = self.root / 'plan.json'
        self.args = SimpleNamespace(plan=str(self.plan_path), out=str(self.root / 'out'), dry_run=False)
        self.out = Path(self.args.out)
        self.platform = OfflinePlatform(self.plan, self.out)
        self.addCleanup(patch.stopall)
        patch.object(ds.cb, 'api', side_effect=self.platform.api).start()
        patch.object(ds.cb, 'BASE', 'https://offline.invalid/api/v1').start()
        self.stdout = io.StringIO()
        self.redirect = redirect_stdout(self.stdout)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def run_batch(self):
        ds.save(self.plan_path, self.plan)
        ds.batch({}, self.args)

    def test_complete_batch_and_rerun_reuse_all_receipts(self):
        with patch.object(ds.cb, 'share_video', wraps=ds.cb.share_video) as wrapper:
            self.run_batch()
            self.assertEqual(wrapper.call_count, 90)
        self.assertEqual(len(self.platform.groups), 2)
        self.assertEqual(len(self.platform.shares), 9)
        self.assertEqual(len(self.platform.posts), 11)
        for gi, group in enumerate(self.plan['groups']):
            remote = self.platform.groups[f'group-{gi}']
            self.assertEqual(remote['name'], group['name'])
            self.assertEqual(len(remote['shares']), len(group['entries']))
            for entry in group['entries']:
                matches = [s for s in self.platform.shares.values() if s['title'] == entry['title']]
                self.assertEqual(len(matches), 1)
                self.assertEqual([v['video_object_key'] for v in matches[0]['videos']],
                                 [v['expected_output_object_key'] for v in entry['videos']])
        journal = ds.load(self.out / 'batch-links.json')
        self.assertTrue(journal['verified'])
        self.assertTrue(all(op['state'] == 'created' for op in journal['operations'].values()))
        self.run_batch()
        self.assertEqual(len(self.platform.posts), 11)
        self.assertEqual(len(list(self.out.glob('batch-share-*.json'))), 9)
        self.assertEqual(len(list(self.out.glob('batch-group-*.json'))), 2)

    def test_invalid_plan_rejected_without_platform_reads(self):
        mutations = [
            lambda p: p.update(instruction=''),
            lambda p: p['groups'].pop(),
            lambda p: p['groups'].reverse(),
            lambda p: p['groups'][0].update(name=p['groups'][1]['name']),
            lambda p: p['groups'][0]['entries'][0]['videos'].pop(),
            lambda p: p['groups'][1]['entries'][-1]['videos'][-1].update(
                cut_id=p['groups'][0]['entries'][0]['videos'][0]['cut_id']),
            lambda p: p['groups'][0]['entries'][0]['videos'][0].update(script_id='wrong'),
            lambda p: p['groups'][0]['entries'][0]['videos'][0].update(product_id='wrong'),
            lambda p: p['groups'][0]['entries'][0]['videos'][0].update(product_key='unknown'),
            lambda p: p['groups'][0]['entries'][1].update(
                script_id=p['groups'][0]['entries'][0]['script_id']),
        ]
        original = copy.deepcopy(self.plan)
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.plan = copy.deepcopy(original)
                mutate(self.plan)
                with self.assertRaises(RuntimeError):
                    self.run_batch()
        self.assertEqual(self.platform.calls, [])

    def test_last_video_failure_prevents_every_post(self):
        cut_id = self.plan['groups'][1]['entries'][-1]['videos'][-1]['cut_id']
        original = copy.deepcopy(self.platform.cuts[cut_id])
        for field, value in [('script_id', 'wrong'), ('product_id', 'wrong'),
                             ('output_status', 'failed'), ('output_object_key', 'outputs/old.mp4'),
                             ('ai_review_status', 'pending'), ('ai_review_result', 'reject'),
                             ('ai_review_result', None)]:
            with self.subTest(field=field, value=value):
                self.platform.cuts[cut_id] = dict(original, **{field: value})
                with self.assertRaises(RuntimeError):
                    self.run_batch()
        self.assertEqual(self.platform.posts, [])

    def test_script_product_checked_against_brief(self):
        self.platform.scripts['script-1-5']['product_id'] = 'wrong'
        with self.assertRaisesRegex(RuntimeError, '产品'):
            self.run_batch()
        self.assertEqual(self.platform.posts, [])

    def test_latest_review_rechecked_after_wrapper(self):
        reads = 0
        def change_review(resource, ident, result):
            nonlocal reads
            if resource == 'video-cuts':
                reads += 1
                if reads == 3:
                    result['ai_review_result'] = 'reject'
        self.platform.after_get = change_review
        with self.assertRaisesRegex(RuntimeError, '最新审核'):
            self.run_batch()
        self.assertEqual(self.platform.posts, [])

    def test_wrapper_object_change_rejected(self):
        with patch.object(ds.cb, 'share_video', return_value={'video_object_key': 'outputs/old.mp4'}):
            with self.assertRaisesRegex(RuntimeError, '分享对象'):
                self.run_batch()
        self.assertEqual(self.platform.posts, [])

    def test_ambiguous_posts_block_retry_even_when_remote_write_succeeded(self):
        for path in ('/video-share-groups', '/video-shares'):
            with self.subTest(path=path):
                self.args.out = str(self.root / path.strip('/'))
                self.out = Path(self.args.out)
                self.platform = OfflinePlatform(self.plan, self.out)
                ds.cb.api.side_effect = self.platform.api
                self.platform.fail_post = path
                with self.assertRaises(TimeoutError):
                    self.run_batch()
                posts = len(self.platform.posts)
                journal = ds.load(self.out / 'batch-links.json')
                uncertain = [op for op in journal['operations'].values() if op['state'] == 'uncertain']
                self.assertEqual(len(uncertain), 1)
                self.assertEqual(uncertain[0]['path'], path)
                with self.assertRaisesRegex(RuntimeError, '禁止自动重试'):
                    self.run_batch()
                self.assertEqual(len(self.platform.posts), posts)

    def test_interrupted_intent_blocks_retry(self):
        self.platform.fail_post = '/video-share-groups'
        with self.assertRaises(TimeoutError):
            self.run_batch()
        receipt_path = self.out / 'batch-links.json'
        journal = ds.load(receipt_path)
        journal['operations']['group/0']['state'] = 'intent'
        ds.save_journal(receipt_path, journal)
        self.platform.calls.clear()
        with self.assertRaisesRegex(RuntimeError, '禁止自动重试'):
            self.run_batch()
        self.assertEqual(self.platform.calls, [])

    def test_intent_save_failure_prevents_post(self):
        durable_save = ds.save_journal
        def fail_intent(path, journal):
            if journal['operations']:
                raise OSError('磁盘写前意图失败')
            durable_save(path, journal)
        with patch.object(ds, 'save_journal', side_effect=fail_intent):
            with self.assertRaises(OSError):
                self.run_batch()
        self.assertEqual(self.platform.posts, [])
        self.run_batch()
        self.assertEqual(len(self.platform.posts), 11)

    def test_receipt_save_failure_leaves_intent_and_blocks_retry(self):
        durable_save = ds.save_journal
        def fail_receipt(path, journal):
            if any(op['state'] == 'created' for op in journal['operations'].values()):
                raise OSError('磁盘写后收据失败')
            durable_save(path, journal)
        with patch.object(ds, 'save_journal', side_effect=fail_receipt):
            with self.assertRaises(OSError):
                self.run_batch()
        self.assertEqual(len(self.platform.groups), 1)
        journal = ds.load(self.out / 'batch-links.json')
        self.assertEqual(journal['operations']['group/0']['state'], 'intent')
        with self.assertRaisesRegex(RuntimeError, '禁止自动重试'):
            self.run_batch()
        self.assertEqual(len(self.platform.posts), 1)

    def test_failed_readback_resumes_without_duplicate_share(self):
        self.platform.fail_read = True
        with self.assertRaises(TimeoutError):
            self.run_batch()
        journal = ds.load(self.out / 'batch-links.json')
        self.assertEqual(journal['operations']['share/0/0']['state'], 'created')
        self.platform.fail_read = False
        self.run_batch()
        self.assertEqual(len(self.platform.posts), 11)
        self.assertEqual(len(self.platform.shares), 9)

    def test_changed_plan_cannot_reuse_receipts(self):
        self.run_batch()
        self.plan['groups'][0]['entries'][0]['title'] += '-changed'
        self.platform.calls.clear()
        with self.assertRaisesRegex(RuntimeError, '另一批次'):
            self.run_batch()
        self.assertEqual(self.platform.calls, [])

    def test_dry_run_has_no_posts_or_receipts(self):
        self.args.dry_run = True
        self.run_batch()
        self.assertEqual(self.platform.posts, [])
        self.assertFalse(self.out.exists())

    def test_concurrent_run_blocked_before_platform_reads(self):
        self.out.mkdir()
        with (self.out / '.batch.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, '并发'):
                self.run_batch()
        self.assertEqual(self.platform.calls, [])

    def test_full_group_membership_titles_and_counts_must_match(self):
        self.run_batch()
        original = copy.deepcopy(self.platform.groups['group-0'])
        mutations = [lambda g: g['shares'].pop(),
                     lambda g: g['shares'].append(dict(g['shares'][0], id='old-share')),
                     lambda g: g['shares'].append(copy.deepcopy(g['shares'][0])),
                     lambda g: g['shares'][0].update(title='wrong'),
                     lambda g: g['shares'][0].update(product_id='wrong'),
                     lambda g: g['shares'][0].update(script_id='wrong'),
                     lambda g: g['shares'][0].update(video_count=9),
                     lambda g: g.update(name='wrong')]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.platform.groups['group-0'] = copy.deepcopy(original)
                mutate(self.platform.groups['group-0'])
                with self.assertRaises(RuntimeError):
                    self.run_batch()
        self.assertEqual(len(self.platform.posts), 11)

    def test_full_share_order_objects_titles_and_bindings_must_match(self):
        self.run_batch()
        original = copy.deepcopy(self.platform.shares['share-0'])
        mutations = [lambda s: s['videos'].reverse(),
                     lambda s: s['videos'].pop(),
                     lambda s: s['videos'][0].update(video_object_key='outputs/old.mp4'),
                     lambda s: s.update(title='wrong'),
                     lambda s: s.update(script_id='wrong'),
                     lambda s: s.update(product_id='wrong')]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.platform.shares['share-0'] = copy.deepcopy(original)
                mutate(self.platform.shares['share-0'])
                with self.assertRaises(RuntimeError):
                    self.run_batch()
        self.assertEqual(len(self.platform.posts), 11)

    def test_old_share_command_keeps_single_cut_behavior(self):
        cfg = {'products': {'product': {'clipo_product_id': 'pid'}}}
        entry = {'cut_id': 'cut', 'script_id': 'script', 'product_key': 'product',
                 'expected_output_object_key': 'outputs/new.mp4', 'title': '新版'}
        self.plan = {'instruction': '交付修订', 'group_name': '三条修订', 'entries': [entry]}
        payload = {'script_id': 'script', 'product_id': 'pid', 'title': '新版',
                   'video_name_prefix': '新版-', 'videos': [{'video_object_key': 'outputs/new.mp4'}]}
        ds.cb.api.side_effect = [
            {'output_status': 'success', 'script_id': 'script', 'output_object_key': 'outputs/new.mp4'},
            {'product_id': 'pid'}, {'id': 'group'}, {'id': 'share'},
            dict(payload, id='share'), {'shares': [{'id': 'share'}]}]
        ds.save(self.plan_path, self.plan)
        with patch.object(ds.cb, 'share_video', return_value=payload['videos'][0]):
            ds.share(cfg, self.args)
        self.assertEqual(ds.load(self.out / 'links.json')['shares']['cut']['id'], 'share')


if __name__ == '__main__':
    unittest.main()
