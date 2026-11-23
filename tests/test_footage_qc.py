"""
[INPUT]: 依赖 footage_qc 的主画面结构与实测源时长校验。
[OUTPUT]: 验证非主画面不可变、连续时间轴、对象键与超源拒绝；旧窗口沿用，新来源、速率、起点与帧率仍须实测时长。
[POS]: tests 的画面返工安全回归；媒体签名续期不改内容，备份续渲染不重复 PUT 或 POST。新版审核固定输出与受理数量；只允许重渲封面元数据更新；不确定受理拒绝重试。测试不调用平台。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""
import copy
import sys
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import footage_qc as fq
from footage_qc import key, validate_repair, validate_sources


def draft():
    return {'composition': {'fps': 30, 'durationInFrames': 90}, 'tracks': [{'id': 'main'}, {'id': 'vo'}],
            'clips': [{'id': '1-0', 'trackId': 'main', 'from': 0, 'durationInFrames': 90,
                       'props': {'src': 'materials/one.mp4', 'playbackRate': 1}},
                      {'id': 'vo-0', 'trackId': 'vo', 'from': 0, 'durationInFrames': 90,
                       'props': {'src': 'audio/original.mp3'}}]}


class RepairTest(unittest.TestCase):
    def test_main_source_only(self):
        a = draft()
        b = copy.deepcopy(a)
        b['clips'][0]['props']['src'] = 'materials/texture.mp4'
        validate_repair(a, b)

    def test_audio_immutable(self):
        a = draft()
        b = copy.deepcopy(a)
        b['clips'][1]['props']['src'] = 'audio/changed.mp3'
        with self.assertRaises(RuntimeError):
            validate_repair(a, b)

    def test_duration_immutable(self):
        a = draft()
        b = copy.deepcopy(a)
        b['composition']['durationInFrames'] = 91
        with self.assertRaises(RuntimeError):
            validate_repair(a, b)

    def test_single_frame_rejected(self):
        a = draft()
        b = copy.deepcopy(a)
        b['clips'][0]['durationInFrames'] = 1
        with self.assertRaises(RuntimeError):
            validate_repair(a, b)

    def test_gap_rejected(self):
        a = draft()
        b = copy.deepcopy(a)
        b['clips'][0]['from'] = 1
        with self.assertRaises(RuntimeError):
            validate_repair(a, b)

    def test_loop_rejected(self):
        a = draft()
        b = copy.deepcopy(a)
        b['clips'][0]['props']['loop'] = True
        with self.assertRaises(RuntimeError):
            validate_repair(a, b)

    def test_nonfinite_rate_rejected(self):
        for rate in [float('nan'), float('inf')]:
            a = draft()
            b = copy.deepcopy(a)
            b['clips'][0]['props']['playbackRate'] = rate
            with self.assertRaises(RuntimeError):
                validate_repair(a, b)

    def test_fractional_source_frame_rejected(self):
        a = draft()
        b = copy.deepcopy(a)
        b['clips'][0]['props']['startFrom'] = 224.125
        with self.assertRaisesRegex(RuntimeError, '整数帧'):
            validate_repair(a, b)

    def test_integer_source_frame_accepted(self):
        a = draft()
        b = copy.deepcopy(a)
        b['clips'][0]['props']['startFrom'] = 224
        validate_repair(a, b)

    def test_source_duration_bound(self):
        validate_sources(draft(), {'materials/one.mp4': 3})
        with self.assertRaises(RuntimeError):
            validate_sources(draft(), {'materials/one.mp4': 2.99})

    def test_source_evidence_required(self):
        with self.assertRaises(RuntimeError):
            validate_sources(draft(), {})

    def test_source_start_and_rate_bound(self):
        d = draft()
        d['clips'][0]['props'].update(startFrom=30, playbackRate=2)
        validate_sources(d, {'materials/one.mp4': 7})
        with self.assertRaises(RuntimeError):
            validate_sources(d, {'materials/one.mp4': 6.99})

    def test_unchanged_windows_do_not_need_new_source_probes(self):
        a = draft()
        b = copy.deepcopy(a)
        b['clips'][0]['props']['src'] = 'https://foo.aliyuncs.com/materials/one.mp4?signature=new'
        validate_sources(b, {}, a)

    def test_changed_source_still_requires_measured_duration(self):
        a = draft()
        b = copy.deepcopy(a)
        b['clips'][0]['props']['src'] = 'materials/oily.mp4'
        with self.assertRaises(RuntimeError):
            validate_sources(b, {}, a)
        validate_sources(b, {'materials/oily.mp4': 3}, a)
        with self.assertRaises(RuntimeError):
            validate_sources(b, {'materials/oily.mp4': 2.99}, a)

    def test_changed_window_or_fps_requires_new_probe(self):
        a = draft()
        for field, value in [('startFrom', 1), ('playbackRate', 2)]:
            b = copy.deepcopy(a)
            b['clips'][0]['props'][field] = value
            with self.assertRaises(RuntimeError):
                validate_sources(b, {}, a)
        b = copy.deepcopy(a)
        b['composition']['fps'] = 25
        with self.assertRaises(RuntimeError):
            validate_sources(b, {}, a)

    def test_render_cover_metadata_is_not_a_content_track(self):
        a = draft()
        a['cover_url'] = 'https://media.test/old.jpg'
        b = copy.deepcopy(a)
        b['cover_url'] = 'https://media.test/new.jpg'
        with self.assertRaises(RuntimeError):
            validate_repair(a, b)
        validate_repair(a, b, allow_render_cover=True)
        b['clips'][1]['props']['src'] = 'audio/changed.mp3'
        with self.assertRaises(RuntimeError):
            validate_repair(a, b, allow_render_cover=True)

    def test_media_signature_refresh_preserves_objects(self):
        a = draft()
        a['clips'][1]['props'].update(src='https://media.test/a.mp3?sign=old', font_url='https://media.test/font.ttf?sign=old')
        a['cover_url'] = 'https://media.test/cover.jpg?sign=old'
        b = copy.deepcopy(a)
        b['clips'][1]['props'].update(src='https://media.test/a.mp3?sign=new', font_url='https://media.test/font.ttf?sign=new')
        b['cover_url'] = 'https://media.test/cover.jpg?sign=new'
        validate_repair(a, b)
        for field, url in [('src', 'https://other.test/a.mp3'), ('font_url', 'https://media.test/other.ttf')]:
            changed = copy.deepcopy(b)
            changed['clips'][1]['props'][field] = url
            with self.assertRaises(RuntimeError):
                validate_repair(a, changed)
        b['clips'][1]['props']['text'] = '改字'
        with self.assertRaises(RuntimeError):
            validate_repair(a, b)

    def test_resume_saved_draft_never_puts_or_resubmits_render(self):
        with tempfile.TemporaryDirectory() as root:
            a, b = draft(), draft()
            b['clips'][0]['props']['src'] = 'materials/oily.mp4'
            plan = {'cut_id': 'c', 'instruction': '用户定点修改', 'reason': '油头配油头',
                    'expected_main': fq.main_clips(a), 'replacement_main': fq.main_clips(b),
                    'source_durations': {'materials/oily.mp4': 3}}
            plan_path = fq.save(Path(root) / 'plan.json', plan)
            backup_path = fq.save(Path(root) / 'before.json', {'plan': plan, 'before': {'id': 'c', 'script_id': 's', 'draft': a}})
            args = SimpleNamespace(plan=plan_path, out=root, resume_backup=backup_path, dry_run=False)
            cut = {'id': 'c', 'script_id': 's', 'draft': b}
            def api(method, path, *args, **kwargs):
                if method == 'GET':
                    return copy.deepcopy(cut)
                self.assertEqual((method, path), ('POST', '/render-jobs'))
                self.assertEqual(kwargs['tries'], 1)
                return {'job_id': 'j'}
            with patch.object(fq.cb, 'api', side_effect=api) as mocked:
                fq.repair({}, args)
                self.assertEqual([c.args[0] for c in mocked.call_args_list], ['GET', 'POST'])
                with self.assertRaisesRegex(RuntimeError, '禁止重复'):
                    fq.repair({}, args)
                self.assertEqual(mocked.call_count, 2)

    def test_review_is_bound_to_new_render_and_never_resubmits(self):
        with tempfile.TemporaryDirectory() as root:
            d = draft()
            plan = {'replacement_main': fq.main_clips(d)}
            original = {'id': 'c', 'script_id': 's', 'draft': d}
            bp = fq.save(Path(root) / 'before.json', {'before': original, 'plan': plan})
            fq.save(Path(root) / 'render-c.json', {'cut_id': 'c', 'job_id': 'j', 'backup': str(bp)})
            args = SimpleNamespace(out=root, dry_run=False, wait=False)
            live = dict(original, output_object_key='outputs/new.mp4', ai_review_status='success', ai_review_result='pass')
            def api(method, path, *args, **kwargs):
                if method == 'POST':
                    self.assertEqual(path, '/video-cuts/review')
                    self.assertEqual(args[0], {'cut_ids': ['c']})
                    self.assertEqual(kwargs['tries'], 1)
                    return {'queued': 1, 'skipped': 0}
                if path.startswith('/render-jobs/'):
                    return {'status': 'success', 'object_key': 'outputs/new.mp4'}
                return copy.deepcopy(live)
            with patch.object(fq.cb, 'api', side_effect=api) as mocked:
                fq.review({}, args)
                fq.review({}, args)
                self.assertEqual(sum(c.args[0] == 'POST' for c in mocked.call_args_list), 1)
                live['output_object_key'] = 'outputs/other.mp4'
                with self.assertRaisesRegex(RuntimeError, '不一致'):
                    fq.review({}, args)
                self.assertEqual(sum(c.args[0] == 'POST' for c in mocked.call_args_list), 1)

    def test_review_uncertain_intent_blocks_post(self):
        with tempfile.TemporaryDirectory() as root:
            d = draft()
            bp = fq.save(Path(root) / 'before.json', {'before': {'id': 'c', 'script_id': 's', 'draft': d},
                         'plan': {'replacement_main': fq.main_clips(d)}})
            fq.save(Path(root) / 'render-c.json', {'cut_id': 'c', 'job_id': 'j', 'backup': str(bp)})
            rows = [{'cut_id': 'c', 'job_id': 'j', 'object_key': 'outputs/new.mp4'}]
            fq.save(Path(root) / 'review-intent.json', {'state': 'submitting', 'videos': rows})
            def api(method, path, *args, **kwargs):
                self.assertEqual(method, 'GET')
                if path.startswith('/render-jobs/'):
                    return {'status': 'success', 'object_key': 'outputs/new.mp4'}
                return {'script_id': 's', 'draft': d, 'output_object_key': 'outputs/new.mp4'}
            with patch.object(fq.cb, 'api', side_effect=api):
                with self.assertRaisesRegex(RuntimeError, '禁止重复'):
                    fq.review({}, SimpleNamespace(out=root, dry_run=False, wait=False))
                fq.save(Path(root) / 'review-intent.json', {'state': 'submitted', 'videos': rows,
                        'response': {'queued': 0, 'skipped': 1}})
                with self.assertRaisesRegex(RuntimeError, '受理数量'):
                    fq.review({}, SimpleNamespace(out=root, dry_run=False, wait=False))

    def test_object_identity(self):
        self.assertEqual(key('https://foo.aliyuncs.com/materials/a.mp4?signature=secret'), 'materials/a.mp4')
        self.assertEqual(key('materials/a.mp4'), 'materials/a.mp4')


if __name__ == '__main__':
    unittest.main()
