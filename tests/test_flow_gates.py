"""
[INPUT]: 依赖 common 的 demo_first、aigc_problem；依赖 review_doc 的 approve_aigc、approve_demo；依赖 clipo_batch 的 parallel、cmd_poll。
[OUTPUT]: 验证用户 11-22 流程门禁与提速：默认 demo；AI 素材人审先于 demo；poll 并发且不越 brief；并发建脚本；渲染前保留重复开头、不为去重写草稿；闸门不过不渲染；尾部轮询加快。
[POS]: tests 的流程顺序回归；不调用平台接口。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import clipo_batch as cb
import review_doc as rd
from common import aigc_problem, copy_hash, demo_first


def brief(tags=None):
    b = {'brief_id': 'B1', 'product_key': 'p', 'status': 'approved', 'quantity': 10,
         'segments': [{'id': '1', 'role': '钩子', 'text': '开头', 'visual_tags': tags or ['产品外观']}]}
    b['approval'] = {'by': '用户', 'note': '通过', 'copy_hash': copy_hash(b)}
    return b


class FlowGateTest(unittest.TestCase):
    def test_every_brand_demo_first_by_default(self):
        self.assertTrue(demo_first({}, {}))
        self.assertTrue(demo_first({'demo_first': True}, {}))
        self.assertFalse(demo_first({}, {'demo_first': False}))

    def test_real_footage_needs_no_aigc_review(self):
        self.assertIsNone(aigc_problem(brief()))

    def test_aigc_footage_needs_recorded_review(self):
        b = brief(['AI发根蓬松', '产品外观'])
        self.assertIn('还没人审过', aigc_problem(b))
        b['aigc_approval'] = {'by': '用户', 'tags': ['AI发根蓬松']}
        self.assertIsNone(aigc_problem(b))
        b['segments'][0]['visual_tags'].append('AI浴室吹发')
        self.assertIn('AI浴室吹发', aigc_problem(b))

    def test_approve_aigc_then_demo_order(self):
        with tempfile.TemporaryDirectory() as root:
            run = Path(root)
            (run / 'briefs').mkdir()
            path = run / 'briefs' / 'B1.json'
            path.write_text(json.dumps(brief(['AI发根蓬松']), ensure_ascii=False))
            rd.approve_demo({}, run, ['B1'], '用户', 'demo 可以')
            self.assertNotIn('demo_approval', json.loads(path.read_text()))
            rd.approve_aigc({}, run, ['B1'], '用户', '素材可以')
            self.assertEqual(json.loads(path.read_text())['aigc_approval']['tags'], ['AI发根蓬松'])
            rd.approve_demo({}, run, ['B1'], '用户', 'demo 可以')
            self.assertEqual(json.loads(path.read_text())['demo_approval']['note'], 'demo 可以')

    def test_submit_holds_aigc_brief_before_material_review(self):
        with tempfile.TemporaryDirectory() as root:
            cb.CFG = {'brand_key': 'qingyang', 'products': {'p': {'clipo_product_id': 'pid'}}}
            args = SimpleNamespace(dry_run=True, scripts_only=False, brief=None, retry_failed=False, factor=None, _quiet=True)
            with patch.object(cb, 'load_briefs', return_value=[brief(['AI发根蓬松'])]), \
                 patch.object(cb, 'lint_day', return_value={}), patch.object(cb, 'lint_variety', return_value={}), \
                 patch.object(cb, 'lint_brief', return_value=[]), patch.object(cb, 'tag_counts', return_value={'AI发根蓬松': 9}), patch.object(cb, 'video_title', return_value='t'), \
                 patch.object(cb, 'api', side_effect=AssertionError('dry-run 不调平台写接口')):
                self.assertEqual(cb.cmd_submit(cb.CFG, root, args), {})

    def test_submit_first_round_is_one_demo(self):
        with tempfile.TemporaryDirectory() as root:
            cb.CFG = {'brand_key': 'qingyang', 'products': {'p': {'clipo_product_id': 'pid'}}}
            b = brief()
            b['quantity'] = 10
            args = SimpleNamespace(dry_run=True, scripts_only=False, brief=None, retry_failed=False, factor=None, _quiet=True)
            with patch.object(cb, 'load_briefs', return_value=[b]), \
                 patch.object(cb, 'lint_day', return_value={}), patch.object(cb, 'lint_variety', return_value={}), \
                 patch.object(cb, 'lint_brief', return_value=[]), patch.object(cb, 'tag_counts', return_value={'产品外观': 20}), \
                 patch.object(cb, 'video_title', return_value='t'), patch.object(cb, 'api', side_effect=AssertionError):
                self.assertEqual(cb.cmd_submit(cb.CFG, root, args), {'B1': 1})

    def test_parallel_keeps_order_and_runs_all(self):
        self.assertEqual(cb.parallel(lambda x: x * 2, list(range(20))), [x * 2 for x in range(20)])

    def test_poll_reads_videos_in_parallel_only_for_selected_brief(self):
        with tempfile.TemporaryDirectory() as root:
            cb.CFG = {'brand_key': 'qingyang'}
            st = cb.State(root)
            for i in range(5):
                st.videos[f'B1-V{i}'] = {'brief_id': 'B1', 'stage': 'rendering', 'render_job': f'j{i}', 'cut_id': f'c{i}'}
            st.videos['B2-V1'] = {'brief_id': 'B2', 'stage': 'rendering', 'render_job': 'other', 'cut_id': 'x'}
            st.save()
            calls = []
            def api(method, path, body=None, **kw):
                calls.append((method, path, body))
                if method == 'GET':
                    return {'status': 'success', 'object_key': 'o.mp4'}
                return {}
            with patch.object(cb, 'load_briefs', return_value=[{'brief_id': 'B1'}]), patch.object(cb, 'api', side_effect=api):
                with self.assertRaises(SystemExit):
                    cb.cmd_poll({}, root, SimpleNamespace(brief='B1', wait=0, interval=0))
            self.assertNotIn(('GET', '/render-jobs/other', None), calls)
            posts = [c for c in calls if c[0] == 'POST']
            self.assertEqual(len(posts), 1)
            self.assertEqual(sorted(posts[0][2]['cut_ids']), [f'c{i}' for i in range(5)])
            self.assertEqual(cb.State(root).videos['B2-V1']['stage'], 'rendering')


def gen_draft(src='hook-a.mp4'):
    return {'composition': {'fps': 30, 'durationInFrames': 90}, 'tracks': [{'id': 'main'}, {'id': 'watermark', 'name': '水印'}],
            'clips': [{'id': '1-0', 'trackId': 'main', 'from': 0, 'durationInFrames': 60, 'props': {'src': f'https://x.test/{src}?s=1'}},
                      {'id': '2-0', 'trackId': 'main', 'from': 60, 'durationInFrames': 30, 'props': {'src': 'https://x.test/body.mp4'}}]}


class RenderOnceTest(unittest.TestCase):
    """渲染前保留平台开头；重复不触发草稿写入，其他闸门不过仍不渲染。"""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cb.CFG = {'brand_key': 'qingyang', 'watermark': {'add': True, 'ai_only': True},
                  'products': {'p': {'clipo_product_id': 'pid'}}}
        self.idx = {f'hook-{c}.mp4': {'id': c, 'object_url': f'https://x.test/hook-{c}.mp4', 'begin_ms': 0, 'end_ms': 5000,
                                      'tags': ['开头']} for c in 'abc'}
        self.idx['body.mp4'] = {'id': 'body', 'object_url': 'https://x.test/body.mp4', 'tags': ['质地']}
        self.brief = {'brief_id': 'B1', 'product_key': 'p', 'quantity': 3,
                      'segments': [{'id': '1', 'text': '开头', 'visual_tags': ['开头'],
                                    'footage_pool': {'tags': ['开头'], 'clips': [{'segment_id': c} for c in 'abc']}},
                                   {'id': '2', 'text': '质地', 'visual_tags': ['质地']}]}

    def run_pull(self, qc_issues=()):
        st = cb.State(self.tmp.name)
        job = {'generation_id': 'g', 'stage': 'generating', 'rounds': [], 'landed': 0, 'texts': ['开头', '质地']}
        drafts = {f'c{i}': gen_draft() for i in range(3)}
        calls = []
        def api(method, path, body=None, **kw):
            calls.append((method, path))
            if path == '/video-cut-generations/g':
                return {'status': 'success', 'cut_ids': list(drafts)}
            cid = path.rsplit('/', 1)[-1]
            if method == 'GET':
                return {'id': cid, 'script_id': 's', 'draft': drafts[cid], 'random_result': {}}
            if method == 'PUT':
                drafts[cid] = body['draft']
                return {}
            return {'job_id': 'j-' + str(len(calls))}
        with patch.object(cb, 'api', side_effect=api), patch.object(cb, 'footage_index', return_value=self.idx), \
             patch.object(cb, 'lint_text', return_value=[]), patch.object(cb, 'lint_script', return_value=[]), \
             patch.object(cb, 'video_title', return_value='t'), patch('qc.cut_checks', return_value=list(qc_issues)):
            cb.pull_generation(st, cb.CFG, self.brief, job, job)
        return st, calls, drafts

    def test_repeated_openers_render_without_deduplication_puts(self):
        st, calls, drafts = self.run_pull()
        self.assertTrue(all(v['pinned_clips'][0] == 'a' and 'hook_clip' not in v for v in st.videos.values()))
        self.assertTrue(all(draft == gen_draft() for draft in drafts.values()))
        self.assertFalse(any(m == 'PUT' for m, _ in calls))
        self.assertEqual(sum(p == '/render-jobs' for _, p in calls), 3)
        self.assertEqual(sum(m == 'GET' and p.startswith('/video-cuts/') for m, p in calls), 3)

    def test_gate_failure_skips_render(self):
        st, calls, _ = self.run_pull(['BGM 音量不是 0.2'])
        self.assertFalse(any(p == '/render-jobs' for _, p in calls))
        self.assertTrue(all(v['stage'] == 'failed' and '渲染前闸门' in v['error'] for v in st.videos.values()))
        self.assertTrue(all(cb.settled(v) for v in st.videos.values()))

    def test_tail_polls_faster(self):
        self.assertEqual(cb.poll_interval(30, 3, 0), 10)
        self.assertEqual(cb.poll_interval(30, 3, 1), 30)
        self.assertEqual(cb.poll_interval(30, 40, 0), 30)

    def test_scripts_built_in_parallel(self):
        import threading, time as t
        live, peak = [0], [0]
        lock = threading.Lock()
        def build(pid, b, title):
            with lock:
                live[0] += 1; peak[0] = max(peak[0], live[0])
            t.sleep(0.05)
            with lock:
                live[0] -= 1
            return 'sid-' + b['brief_id']
        briefs = []
        for i in range(4):
            b = brief(); b['brief_id'] = f'B{i}'; briefs.append(b)
        args = SimpleNamespace(dry_run=False, scripts_only=True, brief=None, retry_failed=False, factor=None)
        with patch.object(cb, 'load_briefs', return_value=briefs), patch.object(cb, 'build_script', side_effect=build), \
             patch.object(cb, 'lint_day', return_value={}), patch.object(cb, 'lint_variety', return_value={}), \
             patch.object(cb, 'lint_brief', return_value=[]), patch.object(cb, 'tag_counts', return_value={'产品外观': 20}), \
             patch.object(cb, 'video_title', return_value='t'):
            cb.cmd_submit(cb.CFG, self.tmp.name, args)
        self.assertGreater(peak[0], 1)
        self.assertEqual(sorted(j['script_id'] for j in cb.State(self.tmp.name).data['briefs'].values()),
                         ['sid-B0', 'sid-B1', 'sid-B2', 'sid-B3'])


if __name__ == '__main__':
    unittest.main()
