"""
[INPUT]: 依赖 footage_qc 的显式调音契约与 opener_refresh 的时段合并。
[OUTPUT]: 验证默认音轨不可变、授权只改音量、角标边界和来源不变。
[POS]: tests 的新开头返工离线回归；不调用平台，不替代内容审核。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""
import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import footage_qc as fq
from opener_refresh import merge_spans


class ExplicitMixTest(unittest.TestCase):
    def setUp(self):
        self.d = {'composition': {'fps': 30, 'durationInFrames': 90},
                  'tracks': [{'id': 'main'}, {'id': 'audio'}, {'id': 'voiceover'}],
                  'clips': [{'id': '1-0', 'trackId': 'main', 'from': 0, 'durationInFrames': 90,
                             'props': {'src': 'materials/one.mp4'}},
                            {'id': 'bg', 'trackId': 'audio', 'props': {'src': 'bg.mp3', 'volume': .25}},
                            {'id': 'v1', 'trackId': 'voiceover', 'props': {'src': 'voice.mp3', 'volume': 1}}]}
        self.p = {'audio_mix': {'instruction': 'BGM大口播小，平衡一下', 'bgm_volume': .085, 'voice_volume': 1}}

    def test_default_rejects_mix(self):
        mixed = fq.authorized_tracks(self.d, self.p)
        with self.assertRaises(RuntimeError):
            fq.validate_plan(self.d, mixed, {})

    def test_explicit_mix_preserves_source(self):
        mixed = fq.authorized_tracks(self.d, self.p)
        fq.validate_plan(self.d, mixed, self.p)
        self.assertEqual(mixed['clips'][1]['props'], {'src': 'bg.mp3', 'volume': .085})
        self.assertEqual(mixed['clips'][2]['props'], {'src': 'voice.mp3', 'volume': 1})
        self.assertEqual(self.d['clips'][1]['props']['volume'], .25)

    def test_authorization_does_not_allow_new_voice(self):
        mixed = fq.authorized_tracks(self.d, self.p)
        mixed['clips'][2]['props']['src'] = 'new.mp3'
        with self.assertRaises(RuntimeError):
            fq.validate_plan(self.d, mixed, self.p)

    def test_invalid_volume_or_missing_instruction(self):
        for value in (float('nan'), float('inf'), -1, 1.4, 3, True):
            plan = copy.deepcopy(self.p)
            plan['audio_mix']['voice_volume'] = value
            with self.assertRaises(RuntimeError):
                fq.authorized_tracks(self.d, plan)
        plan = copy.deepcopy(self.p)
        plan['audio_mix']['instruction'] = ''
        with self.assertRaises(RuntimeError):
            fq.authorized_tracks(self.d, plan)

    def test_ai_label_only_on_explicit_window(self):
        plan = dict(self.p, ai_spans=[[0, 45]])
        d = fq.authorized_tracks(self.d, plan)
        fq.validate_plan(self.d, d, plan)
        wm = [c for c in d['clips'] if c.get('trackId') == 'watermark']
        self.assertEqual([(c['from'], c['durationInFrames']) for c in wm], [(0, 45)])
        with self.assertRaises(RuntimeError):
            fq.authorized_tracks(self.d, dict(self.p, ai_spans=[[0, 91]]))

    def test_merge_spans(self):
        self.assertEqual(merge_spans([[0, 40], [40, 40], [10, 20], [85, 5]]), [[0, 80], [85, 5]])


if __name__ == '__main__':
    unittest.main()
