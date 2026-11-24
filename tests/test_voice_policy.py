"""
[INPUT]: 依赖 clipo_batch、qc、resume_batch 与 batch_completion；平台请求全部替换为离线桩。
[OUTPUT]: 验证平台音色保留、名单与性别不再否决；保护音量、文案与画面门禁。
[POS]: tests 的日常音色职责边界回归；不依赖独立音轨返工工具，不读取批次状态，不调用平台。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import clipo_batch as cb
import qc
import resume_batch as rb
import batch_completion as bc
import progress
from common import load_config


class VoicePolicyTest(unittest.TestCase):
    def setUp(self):
        self.cfg = {'brand_key': 'qingyang',
                    'products': {'p': {'clipo_product_id': 'pid', 'voice_gender': '男',
                                       'voice_volume': 1, 'bgm_volume': .2}},
                    'tts': {'voices': ['local-only'], 'voices_by_gender': {'男': ['local-only']},
                            'bad_voices': ['platform-female']}}
        self.b = {'brief_id': 'B1', 'product_key': 'p', 'voice_gender': '男',
                  'segments': [{'id': '1', 'text': '人审原字', 'role': 'body', 'visual_tags': ['产品外观']}]}
        self.addCleanup(patch.stopall)
        patch.object(cb, 'CFG', self.cfg).start()
        patch.object(cb, 'api', side_effect=AssertionError('禁止真实平台请求')).start()

    def build(self, config, corrupt_copy=False):
        platform = {'id': 's', 'product_id': 'pid', 'config': copy.deepcopy(config),
                    'segments': [{'id': '1', 'type': 'dubbing', 'content': '平台初稿',
                                  'tts': {'voice_type': 'platform-segment', 'speech_rate': 1.3}}]}
        saved, calls = {}, []
        def api(method, path, body=None, **kwargs):
            calls.append((method, path))
            if path == '/video-script-generations':
                return {'id': 'g'}
            if path == '/video-script-generations/g':
                return {'id': 'g', 'status': 'success', 'script_id': 's'}
            if method == 'GET' and path == '/video-scripts/s':
                result = copy.deepcopy(saved or platform)
                if saved and corrupt_copy:
                    result['segments'][0]['content'] = '偷偷改字'
                return result
            if method == 'PUT':
                saved.update(copy.deepcopy(body))
                return {}
            if method == 'DELETE':
                return {}
            raise AssertionError((method, path))
        with patch.object(cb, 'api', side_effect=api), patch.object(cb.time, 'sleep'), \
             patch.object(cb, 'pin_visual'), patch.object(cb, 'pin_decor'), \
             patch.object(cb, 'alt_prompt', return_value='参考'):
            sid = cb.build_script('pid', self.b, '标题')
        self.assertEqual(sid, 's')
        self.assertFalse(any(m == 'DELETE' for m, _ in calls))
        return saved

    def test_preserve_platform_global_and_segment_tts(self):
        tts = {'voices': [{'id': 'platform-female', 'platform': 'volcengine'}],
               'speech_rate': 1.6, 'volume': .9, 'emotion': 'happy'}
        result = self.build({'tts': tts, 'bgm': {'volume': .08}})
        self.assertEqual(result['config']['tts'], tts)
        self.assertEqual(result['segments'][0]['tts'], {'voice_type': 'platform-segment', 'speech_rate': 1.3})
        self.assertEqual(result['segments'][0]['content'], '人审原字')
        self.assertTrue(result['segments'][0]['ai_rewrite'])
        self.assertEqual(result['config']['bgm']['volume'], .2)

    def test_copy_readback_mismatch_still_blocks(self):
        with self.assertRaisesRegex(RuntimeError, '文案对不上'):
            self.build({'tts': {'voices': [{'id': 'platform-female'}]}}, corrupt_copy=True)

    def test_no_tts_or_no_voice_list_is_valid(self):
        for config in ({}, {'tts': {}}, {'tts': {'speech_rate': 1.4}}, {'tts': {'voices': []}}):
            with self.subTest(config=config):
                result = self.build(config)
                if 'tts' in config:
                    self.assertEqual(result['config']['tts'], config['tts'])
                else:
                    self.assertNotIn('tts', result['config'])

    def draft(self):
        return {'composition': {'fps': 30, 'durationInFrames': 90}, 'tracks': [],
                'clips': [{'id': '1-0', 'trackId': 'main', 'from': 0, 'durationInFrames': 90,
                           'props': {'src': 'https://x.test/main.mp4'}},
                          {'id': 'voice', 'trackId': 'voiceover', 'from': 0, 'durationInFrames': 90,
                           'props': {'volume': 1}},
                          {'id': 'bgm', 'trackId': 'audio', 'from': 0, 'durationInFrames': 90,
                           'props': {'volume': .2}}]}

    def test_qc_ignores_voice_id_and_gender_but_checks_volumes(self):
        draft = self.draft()
        with patch.object(cb, 'footage_index', return_value={}), patch.object(cb, 'bad_footage', return_value=[]):
            for voice in ('platform-female', 'never-listed', None):
                self.assertEqual(qc.cut_checks(self.b, draft, voice), [])
            draft['clips'][1]['props']['volume'] = .5
            draft['clips'][2]['props']['volume'] = .08
            issues = qc.cut_checks(self.b, draft, 'platform-female')
        self.assertIn('口播音量不是 1', issues)
        self.assertIn('BGM 音量不是 0.2', issues)

    def test_qc_keeps_visual_gate(self):
        with patch.object(cb, 'footage_index', return_value={}), \
             patch.object(cb, 'bad_footage', return_value=['女生镜头']):
            self.assertIn('禁用画面 女生镜头', qc.cut_checks(self.b, self.draft(), 'platform-female'))

    def pull(self, raw='人审原字', bgm=.2, lint=()):
        draft = self.draft()
        draft['clips'][2]['props']['volume'] = bgm
        cut = {'id': 'c', 'script_id': 's', 'draft': draft,
               'random_result': {'tts_speaker_id': 'platform-female', 'vo_rewrite': [{'content': raw}]}}
        renders = []
        def api(method, path, body=None, **kwargs):
            if path == '/video-cut-generations/g':
                return {'status': 'success', 'cut_ids': ['c']}
            if method == 'GET' and path == '/video-cuts/c':
                return copy.deepcopy(cut)
            if method == 'POST' and path == '/render-jobs':
                renders.append(body)
                return {'job_id': 'j'}
            raise AssertionError((method, path))
        with tempfile.TemporaryDirectory() as root:
            st = cb.State(root)
            job = {'generation_id': 'g', 'texts': ['人审原字'], 'stage': 'generating'}
            with patch.object(cb, 'api', side_effect=api), patch.object(cb, 'footage_index', return_value={}), \
                 patch.object(cb, 'bad_footage', return_value=[]), patch.object(cb, 'pinned_clips', return_value=[]), \
                 patch.object(cb, 'prepare_draft', return_value=(draft, False)), \
                 patch.object(cb, 'lint_text', return_value=list(lint)), patch.object(cb, 'lint_script', return_value=[]), \
                 patch.object(cb, 'video_title', return_value='标题'):
                cb.pull_generation(st, self.cfg, self.b, job, job)
            return copy.deepcopy(st.videos['B1-V01']), renders

    def test_poll_records_unlisted_platform_voice_and_renders(self):
        video, renders = self.pull()
        self.assertEqual(video['voice'], 'platform-female')
        self.assertNotIn('bad_voice', video)
        self.assertEqual(video['stage'], 'rendering')
        self.assertEqual(len(renders), 1)

    def test_poll_still_blocks_volume_voice_commands_and_copy(self):
        for args in ({'bgm': .08}, {'raw': '[#语音指令]人审原字'}, {'lint': [('error', '文案违规')]}):
            with self.subTest(args=args):
                video, renders = self.pull(**args)
                self.assertEqual(video['stage'], 'failed')
                self.assertEqual(renders, [])

    def test_deliverable_retires_bad_voice_only(self):
        video = {'stage': 'done', 'ai_review': 'pass', 'bad_voice': 'legacy'}
        self.assertTrue(cb.deliverable(video))
        self.assertNotIn('音色不对', progress.why_not(video))
        for field in ('copy_issues', 'watermark_missing', 'voice_cmd', 'voice_drift', 'bad_footage', 'qc_issues'):
            self.assertFalse(cb.deliverable(dict(video, **{field: True})), field)
        self.assertFalse(cb.deliverable(dict(video, ai_review='reject')))

    def test_resume_and_completion_need_no_tts_config(self):
        cfg = {'products': {'2x': {'clipo_product_id': 'pid'}, 'huxi': {'clipo_product_id': 'other'}}}
        script = {'title': 'day-B01-brand-selling', 'product_id': 'pid', 'segments': [{'content': '人审原字'}]}
        cut = {'id': 'c', 'draft': self.draft(), 'output_status': 'success', 'ai_review_result': 'pass',
               'random_result': {'tts_speaker_id': 'platform-female', 'vo_rewrite': [{'content': '人审原字'}]}}
        cut['draft']['clips'].append({'trackId': 'watermark'})
        library = {'main.mp4': {'id': 'source', 'name': '原片', 'video_info': {'duration_ms': 3000}}}
        with patch.object(rb, 'lint_text', return_value=[]), patch.object(rb, 'lint_script', return_value=[]):
            row = rb.inspect_cut(cfg, script, cut, library, {})
            self.assertTrue(row['deliverable'])
            self.assertEqual(row['voice'], 'platform-female')
            self.assertEqual(bc.copy_issues(cfg, script, cut), [])

    def test_brand_configs_have_no_local_voice_policy(self):
        for brand in ('qingyang', 'lux', 'ahc', 'hc', 'dove', 'luxpc'):
            cfg = load_config(brand)
            self.assertNotIn('tts', cfg, brand)
            for product in cfg['products'].values():
                self.assertNotIn('voice_gender', product)
        selenium = load_config('qingyang')['products']['selenium']
        self.assertEqual(selenium['voice_volume'], 1)
        self.assertEqual(selenium['bgm_volume'], .085)
        self.assertIn('女生', load_config('qingyang')['products']['2x']['aigc_rules']['forbid_words'])


if __name__ == '__main__':
    unittest.main()
