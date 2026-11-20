"""
[INPUT]: 依赖 resume_batch 的主画面返工与规范化；替换平台封装为离线桩。
[OUTPUT]: 验证原脚本、授权与源时长门禁；保护不确定请求收据，确保只读审核没有写请求。
[POS]: tests 的断点恢复安全回归；不发平台请求。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import resume_batch as rb


class ResumeRepairTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        draft = {'composition': {'fps': 30, 'durationInFrames': 90},
                 'clips': [{'id': '1-0', 'trackId': 'main', 'from': 0,
                            'durationInFrames': 90,
                            'props': {'src': 'materials/old.mp4'}}]}
        self.before = {'id': 'cut', 'script_id': 'script', 'draft': draft}
        replacement = copy.deepcopy(draft['clips'])
        replacement[0]['props']['src'] = 'materials/new.mp4'
        self.plan = {'cut_id': 'cut', 'instruction': '用户授权画面返工',
                     'reason': '只改主画面', 'expected_draft': draft,
                     'replacement_main': replacement,
                     'source_durations': {'materials/new.mp4': 3}}

    def run_repair(self, dry_run=False):
        path = self.root / 'plan.json'
        path.write_text(json.dumps(self.plan))
        args = SimpleNamespace(out=str(self.root), plan=str(path), dry_run=dry_run)
        with patch.object(rb.cb, 'api', side_effect=[self.before, {'title': 'day-B03-brand-selling'}]) as api:
            rb.repair({}, args)
            return api

    def test_missing_authorization_blocks_before_api(self):
        self.plan.pop('instruction')
        with self.assertRaises(RuntimeError):
            self.run_repair()
        self.assertFalse((self.root / 'backups').exists())

    def test_missing_source_evidence_blocks_before_backup(self):
        self.plan.pop('source_durations')
        with self.assertRaisesRegex(RuntimeError, '源片时长证据'):
            self.run_repair()
        self.assertFalse((self.root / 'backups').exists())

    def test_overrun_blocks_before_backup(self):
        self.plan['source_durations']['materials/new.mp4'] = 2.99
        with self.assertRaisesRegex(RuntimeError, '播放超出源片'):
            self.run_repair()
        self.assertFalse((self.root / 'backups').exists())

    def test_valid_dry_run_is_read_only(self):
        api = self.run_repair(dry_run=True)
        self.assertEqual([call.args[0] for call in api.call_args_list], ['GET', 'GET'])
        self.assertFalse((self.root / 'backups').exists())

    def test_normalize_signature_and_false_loop(self):
        a = {'props': {'src': 'https://host/materials/a.mp4?sign=one', 'loop': False}}
        b = {'props': {'src': 'https://host/materials/a.mp4?sign=two'}}
        self.assertEqual(rb.normalize(a), rb.normalize(b))
        b['props']['loop'] = True
        self.assertNotEqual(rb.normalize(a), rb.normalize(b))


class ResumeContinueTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scripts = [{'id': b, 'title': f'day-{b}-brand-selling', 'product_id': 'p', 'segments': [{'content': '已审文案'}]} for b in ('B01', 'B04', 'B09')]
        self.tasks = {b: {'id': 'g' + b, 'script_id': b, 'status': 'success', 'cut_ids': ['old' + b]} for b in ('B01', 'B04', 'B09')}
        self.rows = [{'brief': b, 'target': 10, 'script_id': b, 'generation_tasks': [self.tasks[b]]} for b in self.tasks]
        rb.save(self.root / 'original-three-shortfall.json', {'scripts': self.scripts, 'rows': self.rows})
        self.cuts = [{'id': 'old' + b, 'script_id': b} for b in self.tasks]
        self.args = SimpleNamespace(out=str(self.root), instruction='两号当天完整交付', dry_run=True)

    def api(self, method, path, *args, **kwargs):
        if method == 'POST':
            raise TimeoutError('请求结果不确定')
        if path.startswith('/video-scripts/'):
            return copy.deepcopy(next(s for s in self.scripts if s['id'] == path.rsplit('/', 1)[1]))
        return copy.deepcopy(next(g for g in self.tasks.values() if g['id'] == path.rsplit('/', 1)[1]))

    def run_continue(self):
        with patch.object(rb, 'listing', return_value=self.cuts), patch.object(rb.cb, 'api', side_effect=self.api) as api:
            rb.continue_batch({}, self.args)
            return api

    def test_authorization_required_before_requests(self):
        self.args.instruction = ''
        with patch.object(rb.cb, 'api') as api, self.assertRaisesRegex(RuntimeError, '用户指令'):
            rb.continue_batch({}, self.args)
        api.assert_not_called()

    def test_dry_run_is_get_only(self):
        api = self.run_continue()
        self.assertEqual([c.args[0] for c in api.call_args_list], ['GET'] * 6)
        self.assertFalse((self.root / 'continued').exists())

    def test_script_change_blocks_before_post(self):
        self.scripts[0]['segments'][0]['content'] = '未经审核的新文案'
        with self.assertRaisesRegex(RuntimeError, '原脚本已变'):
            self.run_continue()
        self.assertFalse((self.root / 'continued').exists())

    def test_uncertain_request_is_not_resubmitted(self):
        self.args.dry_run = False
        with self.assertRaises(TimeoutError):
            self.run_continue()
        receipt = rb.load(self.root / 'continued/gB01.json')
        self.assertEqual(receipt['request_state'], 'uncertain')
        with patch.object(rb, 'listing', return_value=self.cuts), patch.object(rb.cb, 'api', side_effect=self.api) as api:
            with self.assertRaisesRegex(RuntimeError, '已有续产收据'):
                rb.continue_batch({}, self.args)
        self.assertTrue(all(c.args[0] == 'GET' for c in api.call_args_list))

    def test_running_tasks_are_not_submitted_again(self):
        for task in self.tasks.values():
            task['status'] = 'running'
        api = self.run_continue()
        self.assertTrue(all(c.args[0] == 'GET' for c in api.call_args_list))


class ResumeReadOnlyTest(unittest.TestCase):
    def test_review_status_has_no_write_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            rb.save(Path(temp) / 'render-cut.json', {'cut_id': 'cut', 'job_id': 'job', 'review_for_job': 'job'})
            job = {'status': 'success', 'object_key': 'outputs/new.mp4'}
            cut = {'output_object_key': 'outputs/new.mp4', 'ai_review_status': 'success', 'ai_review_result': 'pass'}
            with patch.object(rb.cb, 'api', side_effect=[job, cut]) as api:
                rb.review_status({}, SimpleNamespace(out=temp))
            self.assertEqual([c.args[0] for c in api.call_args_list], ['GET', 'GET'])
            self.assertTrue(rb.load(Path(temp) / 'review-readback.json')[0]['new_output_matches'])

    def test_old_success_cannot_count_as_completed_append(self):
        with tempfile.TemporaryDirectory() as temp:
            for b in ('B01', 'B04', 'B09'):
                rb.save(Path(temp) / 'continued' / f'{b}.json', {'brief': b, 'script_id': b, 'generation_id': b, 'before_cut_ids': ['old'], 'quantity_requested': 9})
            def api(method, path):
                return {'status': 'success', 'script_id': path.rsplit('/', 1)[1], 'cut_ids': ['old']}
            with patch.object(rb.cb, 'api', side_effect=api), patch.object(rb.time, 'monotonic', side_effect=[0, 2]):
                with self.assertRaisesRegex(RuntimeError, '超时'):
                    rb.wait_generations({}, SimpleNamespace(out=temp, wait=1, interval=30))
            self.assertFalse((Path(temp) / 'continued-online.json').exists())

    def test_cta_rate_adjustment_keeps_expected_main_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = parent / 'batch'
            script = {'id': 'B01', 'title': 'day-B01-brand-selling', 'product_id': 'p', 'segments': [{'content': '已审文案'}]}
            main = [{'id': f'{n}-0', 'trackId': 'main', 'from': (n-1)*30, 'durationInFrames': 30,
                     'props': {'src': 'materials/cta.mp4' if n == 6 else 'materials/old.mp4', 'startFrom': 0, 'playbackRate': 1}}
                    for n in range(1, 7)]
            original = {'composition': {'fps': 30, 'durationInFrames': 180}, 'clips': main}
            rb.save(root / 'continued-online.json', {'scripts': [script], 'cuts': [{'id': 'new', 'script_id': 'B01', 'draft': original}]})
            rb.save(root / 'original-three-shortfall.json', {'scripts': [script]})
            rb.save(root / 'continued-status.json', {'receipts': [{'before_cut_ids': ['old']}]})
            seed = copy.deepcopy(main)
            for clip in seed:
                if clip['id'].split('-')[0] in ('1', '2', '5'):
                    clip['props']['src'] = 'materials/person.mp4'
            for bid in ('B01', 'B04', 'B09'):
                rb.save(parent / f'repair-{bid}.json', {'replacement_main': seed,
                        'source_durations': {'materials/person.mp4': 3, 'materials/cta.mp4': 0.95}})
            pool = {'records': [{'product_key': '2x', 'segment': {'id': 'texture', 'object_url': 'materials/texture.mp4'},
                                 'probe': {'format': {'duration': '3.5'}}}]}
            rb.save(parent / 'pool.json', pool)
            args = SimpleNamespace(out=str(root), instruction='用户要求同批返工', pool=str(parent / 'pool.json'), active_pool=str(parent / 'pool.json'))
            with patch.object(rb.cb, 'api') as api, patch.object(rb.cb, 'fetch_file') as download:
                rb.prepare_three({}, args)
            api.assert_not_called()
            download.assert_not_called()
            plan = rb.load(root / 'continued-ready/new.json')
            self.assertEqual(plan['expected_main'], main)
            cta = next(c for c in plan['replacement_main'] if c['id'] == '6-0')
            self.assertLess(cta['props']['playbackRate'], 1)

    def test_new_plan_requires_user_instruction(self):
        with self.assertRaisesRegex(RuntimeError, '用户指令'):
            rb.prepare_three({}, SimpleNamespace(instruction=''))


if __name__ == '__main__':
    unittest.main()
