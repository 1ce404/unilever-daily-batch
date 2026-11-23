#!/usr/bin/env python3
"""
[INPUT]: effects 的纯草稿构建、固定计划和写入账本；mock 隔离平台。
[OUTPUT]: 透明特效轨道边界、短片、参数、上传中断与批量门禁回归。
[POS]: tests 的特效生产保护；不把离线断言当云渲染或视觉验收。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""
import copy
import sys
import tempfile
import time
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import effects as fx


def fixture():
    draft = {'composition': {'durationInFrames': 180, 'fps': 30},
             'tracks': [{'id':'main'}, {'id':'effect'}, {'id':'subtitle'}],
             'clips': [
                 {'id':'1-0', 'trackId':'main', 'from':0, 'durationInFrames':60, 'props':{'src':'https://test/a?sig=1'}},
                 {'id':'2-0', 'trackId':'main', 'from':60, 'durationInFrames':60, 'props':{'src':'https://test/b'}},
                 {'id':'3-0', 'trackId':'main', 'from':120, 'durationInFrames':60, 'props':{'src':'https://test/c'}},
                 {'id':'old', 'trackId':'effect', 'from':0, 'durationInFrames':180, 'props':{'code':'旧默认花字'}},
                 {'id':'sub', 'trackId':'subtitle', 'from':0, 'durationInFrames':180, 'props':{'text':'已审原字'}}]}
    brief = {'brief_id':'1120-L02', 'segments':[
        {'visual_tags':['产品外观']}, {'visual_tags':['头发顺滑']}, {'visual_tags':['头发顺滑']}]}
    return draft, brief


class TestEffects(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.args = Namespace(out=Path(self.temp.name), folder='existing', dry_run=False, run='unused', video=None, wait=0)
        fx.save(self.args.out / 'folders.json', [{'id':'existing'}])
        fx.save(self.args.out / 'register-dryrun.json', {'signature':fx.digest({'folder':'existing','templates':fx.definitions()}), 'at':time.time()})

    def test_three_sources_are_platform_self_contained(self):
        rows = fx.definitions()
        self.assertEqual(set(rows), {'sweep','particles','glow'})
        for row in rows.values():
            self.assertIn('useCurrentFrame', row['code'])
            self.assertIn('props.params', row['code'])
            self.assertNotIn('<text', row['code'])
            self.assertEqual(row['fps'], 30)

    def test_sample_replaces_only_effects_with_nondefault_params(self):
        before, brief = fixture()
        snapshot = copy.deepcopy(before)
        after, moments = fx.build_draft(before, brief, fx.definitions(), True)
        self.assertEqual(before, snapshot)
        self.assertEqual(fx.protected(before), fx.protected(after))
        self.assertEqual(len(moments), 3)
        self.assertEqual({m['params']['accent'] for m in moments}, {'#F08EC3'})
        self.assertEqual(moments[1]['params']['count'], 8)
        self.assertNotIn('old', [c['id'] for c in after['clips']])
        self.assertEqual(after['clips'][-1]['props']['scale'], 1)

    def test_normal_clip_has_two_effects_and_is_deterministic(self):
        d, b = fixture()
        one = fx.build_draft(d, b, fx.definitions())
        two = fx.build_draft(d, b, fx.definitions())
        self.assertEqual(one, two)
        self.assertEqual(len(one[1]), 2)

    def test_short_windows_trim_and_single_frame_is_rejected(self):
        d, b = fixture()
        d['clips'][0]['durationInFrames'] = 3
        after, moments = fx.build_draft(d, b, fx.definitions())
        self.assertEqual(moments[0]['frames'], 3)
        d['clips'][0]['durationInFrames'] = 1
        with self.assertRaisesRegex(RuntimeError, '不足两帧'):
            fx.build_draft(d, b, fx.definitions())

    def test_missing_visual_tags_are_not_guessed(self):
        d, b = fixture()
        b['segments'][0]['visual_tags'] = []
        with self.assertRaisesRegex(RuntimeError, '缺少产品'):
            fx.build_draft(d, b, fx.definitions())

    def test_non_effect_edit_is_blocked(self):
        d, b = fixture()
        after, _ = fx.build_draft(d, b, fx.definitions())
        after['clips'][0]['props']['playbackRate'] = .5
        with self.assertRaisesRegex(RuntimeError, '非特效'):
            fx.validate_change(d, after)

    def test_out_of_bounds_effect_is_blocked(self):
        d, b = fixture()
        after, _ = fx.build_draft(d, b, fx.definitions())
        after['clips'][-1]['from'] = 179
        with self.assertRaisesRegex(RuntimeError, '时段无效'):
            fx.validate_change(d, after)

    def test_unknown_template_creation_is_not_reposted(self):
        with patch.object(fx.cb, 'api', side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                fx.register(self.args)
        with patch.object(fx.cb, 'api') as api:
            with self.assertRaisesRegex(RuntimeError, '禁止重复 POST'):
                fx.register(self.args)
            api.assert_not_called()

    def test_readback_mismatch_stops_after_first_template(self):
        with patch.object(fx.cb, 'api', side_effect=[{'id':'new-id'}, {}]) as api:
            with self.assertRaisesRegex(RuntimeError, '回读不一致'):
                fx.register(self.args)
            self.assertEqual(api.call_count, 2)
            self.assertEqual(api.call_args_list[0].kwargs['tries'], 1)

    def test_sample_without_proof_blocks_batch(self):
        with self.assertRaisesRegex(RuntimeError, '没有试样'):
            fx.require_sample(self.args, {'sample':fx.SAMPLE})

    def test_changed_proof_object_blocks_batch(self):
        fx.save(self.args.out / 'sample-proof.json', {'plan_hash':'p', 'object_key':'old'})
        fx.save(self.args.out / 'ledger.json', {'videos':{fx.SAMPLE:{'phase':'done', 'ai_review':'pass','object_key':'new'}}})
        with self.assertRaisesRegex(RuntimeError, '过时'):
            fx.require_sample(self.args, {'sample':fx.SAMPLE, 'plan_hash':'p'})

    def test_cover_signature_changes_but_object_changes_do_not_match(self):
        a, _ = fixture()
        a['cover_url'] = 'https://test/cover.jpg?sig=one'
        a['clips'][0]['props']['font_url'] = 'https://test/font.ttf?sig=one'
        b = copy.deepcopy(a)
        b['cover_url'] = 'https://test/cover.jpg?sig=two'
        b['clips'][0]['props']['font_url'] = 'https://test/font.ttf?sig=two'
        self.assertEqual(fx.normalized(a), fx.normalized(b))
        b['cover_url'] = 'https://test/other.jpg?sig=two'
        self.assertNotEqual(fx.normalized(a), fx.normalized(b))

    def test_same_output_commands_are_exclusive(self):
        with fx.exclusive(self.args.out):
            with self.assertRaisesRegex(RuntimeError, '不并发写入'):
                with fx.exclusive(self.args.out):
                    self.fail('不能取得第二把锁')
        with fx.exclusive(self.args.out):
            pass

    def test_exact_authorized_video_scope_rejects_extra_and_duplicate_cut(self):
        rows = {k:{'brief_id':k.rsplit('-V',1)[0], 'cut_id':k} for k in fx.AUTHORIZED}
        fx.check_scope(rows)
        rows['1120-L01-V11'] = {'brief_id':'1120-L01', 'cut_id':'extra'}
        with self.assertRaisesRegex(RuntimeError, '共50条'):
            fx.check_scope(rows)
        rows.pop('1120-L01-V11')
        rows['1120-L01-V02']['cut_id'] = rows['1120-L01-V01']['cut_id']
        with self.assertRaisesRegex(RuntimeError, '重复'):
            fx.check_scope(rows)

    def test_skipped_review_does_not_accept_old_pass(self):
        d, _ = fixture()
        row = {'phase':'rendering','render_job':'job','cut_id':'cut','draft_hash':fx.digest(fx.normalized(d))}
        fx.save(self.args.out/'ledger.json', {'videos':{'v':row}})
        responses = [{'status':'success','object_key':'new'}, {'draft':d,'output_object_key':'new'}, {'queued':0,'skipped':1}]
        with patch.object(fx.cb,'api',side_effect=responses):
            with self.assertRaisesRegex(RuntimeError,'未明确受理'):
                fx.poll(self.args)
        saved = fx.load(self.args.out/'ledger.json')['videos']['v']
        self.assertEqual(saved['phase'],'review-unknown')
        self.assertNotIn('ai_review',saved)

    def test_review_receipt_saved_before_later_request_failure(self):
        d, _ = fixture()
        row = {'phase':'rendering','render_job':'job','cut_id':'cut','draft_hash':fx.digest(fx.normalized(d))}
        fx.save(self.args.out/'ledger.json', {'videos':{'one':row,'two':{**row,'cut_id':'cut2'}}})
        responses = [{'status':'success','object_key':'new'}, {'draft':d,'output_object_key':'new'},
                     {'queued':1,'skipped':0}, {'ai_review_status':'pending'}, TimeoutError()]
        with patch.object(fx.cb,'api',side_effect=responses):
            with self.assertRaises(TimeoutError):
                fx.poll(self.args)
        saved = fx.load(self.args.out/'ledger.json')['videos']['one']
        self.assertEqual(saved['phase'],'reviewing')
        self.assertTrue(saved['review_active_seen'])
        self.assertEqual(saved['review_receipt']['queued'],1)

    def test_review_terminal_without_observed_new_execution_blocks(self):
        row = {'phase':'reviewing','cut_id':'cut','object_key':'new','review_active_seen':False}
        fx.save(self.args.out/'ledger.json', {'videos':{'v':row}})
        with patch.object(fx.cb,'api',return_value={'ai_review_status':'success','ai_review_result':'pass','output_object_key':'new'}):
            with self.assertRaisesRegex(RuntimeError,'未观察到本次审核'):
                fx.poll(self.args)

    def test_upload_without_fresh_preflight_is_blocked(self):
        fx.save(self.args.out/'register-dryrun.json', {'signature':'old','at':time.time()})
        with patch.object(fx.cb,'api') as api:
            with self.assertRaisesRegex(RuntimeError,'dry-run'):
                fx.register(self.args)
            api.assert_not_called()

    def test_changed_live_draft_is_never_overwritten(self):
        d, b = fixture()
        after, _ = fx.build_draft(d, b, fx.definitions())
        fx.save(self.args.out / 'drafts' / 'cut.json', after)
        plan = {'videos':{'video':{'cut_id':'cut','draft_hash':fx.digest(fx.normalized(after)), 'before_hash':fx.digest(fx.normalized(d))}}}
        changed = copy.deepcopy(d)
        changed['composition']['fps'] = 60
        with patch.object(fx.cb, 'api', return_value={'draft':changed}) as api:
            with self.assertRaisesRegex(RuntimeError, '线上草稿已变化'):
                fx.apply_one(self.args, 'video', plan, {'videos':{}}, self.args.out / 'ledger.json')
            self.assertEqual(api.call_count, 1)


if __name__ == '__main__':
    unittest.main()
