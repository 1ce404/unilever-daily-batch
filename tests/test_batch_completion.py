"""
[INPUT]: 依赖 batch_completion 的原任务补齐与脚本不变门禁。
[OUTPUT]: 验证授权、条数、原文、只读预演与不确定请求不重复提交。
[POS]: tests 的九组补齐回归；模拟平台接口，不发网络请求。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import batch_completion as bc


class CompletionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.script = {'id': 's', 'title': '1118-B02-test-selling', 'product_id': 'p', 'segments': [{'content': '原文'}]}
        self.task = {'id': 'g', 'script_id': 's', 'status': 'success', 'cut_ids': ['old']}
        self.plan = Path(self.tmp.name) / 'plan.json'
        bc.rb.save(self.plan, {'rows': [{'brief': 'B02', 'quantity': 2}]})
        self.args = SimpleNamespace(root=self.tmp.name, out=self.tmp.name, plan=str(self.plan), instruction='批准本批写入', dry_run=True)

    def api(self, method, path, *args, **kwargs):
        if method != 'GET':
            raise RuntimeError('不确定网络结果')
        return copy.deepcopy(self.script if path.startswith('/video-scripts/') else self.task)

    def run_continue(self, api=None):
        with patch.object(bc, 'seeds', return_value=({'B02': self.script}, {'B02': self.task})), patch.object(bc.rb.cb, 'api', side_effect=api or self.api) as mock:
            bc.continue_batch({}, self.args)
            return mock

    def test_dry_run_only_get(self):
        calls = self.run_continue().call_args_list
        self.assertTrue(all(c.args[0] == 'GET' for c in calls))
        self.assertFalse((Path(self.tmp.name) / 'continue-B02.json').exists())

    def test_authorization_required(self):
        self.args.instruction = ''
        with self.assertRaises(RuntimeError):
            self.run_continue()

    def test_original_text_must_match(self):
        changed = copy.deepcopy(self.script)
        changed['segments'][0]['content'] = '新文案'
        with self.assertRaises(RuntimeError):
            bc.verify_script(self.script, changed)

    def test_running_task_refuses(self):
        self.task['status'] = 'running'
        with self.assertRaises(RuntimeError):
            self.run_continue()

    def test_uncertain_receipt_blocks_retry(self):
        self.args.dry_run = False
        with self.assertRaises(RuntimeError):
            self.run_continue()
        receipt = bc.rb.load(Path(self.tmp.name) / 'continue-B02.json')
        self.assertEqual(receipt['status'], 'uncertain')
        with self.assertRaisesRegex(RuntimeError, '禁止重复'):
            self.run_continue()

    def test_positive_dry_clean_words_not_negative(self):
        from lint_copy import after_use_bad
        for text in ('头皮舒服又干净', '发根轻盈又蓬松，摸着干爽舒服', '洗感清爽又干净'):
            self.assertIsNone(after_use_bad(text), text)
        for text in ('洗完没多久又干了', '头发一天下来又塌了', '下午头发又油'):
            self.assertIsNotNone(after_use_bad(text), text)

    def test_temporal_first_not_quality_superlative(self):
        from common import load_config
        from lint_copy import lint_text
        cfg = load_config('qingyang')
        p = cfg['products']['huxi']
        self.assertFalse(any(level == 'error' and '极限词' in msg for level, msg in lint_text('最先出油的是刘海。', cfg, p)))
        self.assertTrue(any(level == 'error' and '极限词' in msg for level, msg in lint_text('最强的洗发水。', cfg, p)))

    def test_fractional_quantity_rejected(self):
        bc.rb.save(self.plan, {'rows': [{'brief': 'B02', 'quantity': 2.5}]})
        with self.assertRaises(RuntimeError):
            self.run_continue()


if __name__ == '__main__':
    unittest.main()
