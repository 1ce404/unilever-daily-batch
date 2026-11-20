"""
[INPUT]: 依赖 clipo_batch 的 dry_run_gate、gen_quantity；依赖 qc 的 brief_checks；依赖 lint_copy 的 lint_visual_plan。
[OUTPUT]: 验证不双倍产、无 dry-run 收据不提交、开头重复拦截、产品名与成分同句报错、每段必须写画面。
[POS]: tests 的交付闸门与提交门禁回归；不调用平台接口。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import clipo_batch as cb
from qc import brief_checks
from lint_copy import lint_visual_plan

PRODUCT = {'full_name': '清扬控油蓬松洗发水', 'name_aliases': ['清扬控油蓬松洗发水'],
           'ingredient_terms': ['SCA'], 'intro_footage': ['产品外观']}


class GateTest(unittest.TestCase):
    def setUp(self):
        cb.CFG = {'brand_key': 'qingyang', 'generate_factor': 2}
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_never_doubles_from_config(self):
        self.assertEqual(cb.gen_quantity(cb.CFG, {'quantity': 10}, SimpleNamespace(factor=None)), 10)

    def test_submit_needs_matching_dry_run(self):
        run = self.tmp.name
        self.assertFalse(cb.dry_run_gate(run, 'submit', {'B01': 10}, False))
        cb.dry_run_gate(run, 'submit', {'B01': 10}, True)
        self.assertFalse(cb.dry_run_gate(run, 'submit', {'B01': 20}, False))
        cb.dry_run_gate(run, 'submit', {'B01': 10}, True)
        self.assertTrue(cb.dry_run_gate(run, 'submit', {'B01': 10}, False))
        self.assertFalse(cb.dry_run_gate(run, 'submit', {'B01': 10}, False))

    def test_repeated_openers_blocked(self):
        vids = [('V1', {'pinned_clips': ['a']}), ('V2', {'pinned_clips': ['a']}), ('V3', {'pinned_clips': ['b']})]
        self.assertEqual(len(brief_checks({}, vids)), 1)

    def test_name_and_ingredient_split(self):
        segs = [{'id': '1', 'role': 'product', 'text': '清扬控油蓬松洗发水，有SCA技术。', 'visual_tags': ['产品外观']}]
        self.assertTrue(any('拆成两段' in m for _, m in lint_visual_plan(segs, PRODUCT)))

    def test_name_line_shows_bottle(self):
        segs = [{'id': '1', 'role': 'product', 'text': '试试清扬控油蓬松洗发水。', 'visual_tags': ['产品质地']}]
        self.assertTrue(any('产品亮相' in m for _, m in lint_visual_plan(segs, PRODUCT)))

    def test_every_segment_plans_footage(self):
        segs = [{'id': '1', 'role': 'hook', 'text': '头发又塌了。'}]
        self.assertTrue(any('没写画面' in m for _, m in lint_visual_plan(segs, PRODUCT)))


if __name__ == '__main__':
    unittest.main()
