"""
[INPUT]: 依赖 footage_qc 的主画面结构与实测源时长校验。
[OUTPUT]: 验证非主画面不可变、连续时间轴、对象键与超源拒绝。
[POS]: tests 的画面返工安全回归；不调用平台接口。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
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

    def test_object_identity(self):
        self.assertEqual(key('https://foo.aliyuncs.com/materials/a.mp4?signature=secret'), 'materials/a.mp4')
        self.assertEqual(key('materials/a.mp4'), 'materials/a.mp4')


if __name__ == '__main__':
    unittest.main()
