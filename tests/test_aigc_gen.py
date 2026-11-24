"""Offline checks for aigc_gen retry rules (no platform calls)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import aigc_gen as ag  # noqa: E402


def run_with(plan):
    d = tempfile.mkdtemp()
    p = Path(d) / "plan.json"
    p.write_text(json.dumps(plan, ensure_ascii=False))
    cfg = {"products": {"2x": {"clipo_product_id": "x" * 24}, "selenium": {"clipo_product_id": "y" * 24}}, "banned_footage_words": ["秃顶"]}
    return ag.Run(cfg, p)


class RetryRules(unittest.TestCase):
    def setUp(self):
        self.r = run_with({"product": "2x", "scenes": [
            {"id": "golf", "prompt": "原方案", "versions": 1, "fallback_prompts": ["新方案"]},
            {"id": "bare", "prompt": "只有一版", "versions": 1}]})

    def fail(self, key, blocked):
        s = self.r.st["slots"][key]
        s["attempts"].append({"prompt": s["attempts"][-1]["prompt"] if s["attempts"] else s["prompt"],
                              "status": "failed", "blocked": blocked})
        return s

    def test_blocked_uses_fallback_then_stops(self):
        s = self.fail("golf-01", True)
        self.assertEqual(self.r.next_prompt(s), "新方案")
        s["attempts"].append({"prompt": "新方案", "status": "failed", "blocked": True})
        self.assertIsNone(self.r.next_prompt(s))

    def test_blocked_without_fallback_never_repeats(self):
        self.assertIsNone(self.r.next_prompt(self.fail("bare-01", True)))

    def test_timeout_retries_same_prompt(self):
        self.assertEqual(self.r.next_prompt(self.fail("bare-01", False)), "只有一版")

    def test_banned_clip_not_ready(self):
        s = self.r.st["slots"]["golf-01"]
        s.update(segment_id="seg", description="男士头顶稀疏秃顶")
        self.assertEqual(self.r.ready("golf"), [])


class LookRule(unittest.TestCase):
    """清扬用户 11-18：AIGC 角色都要帅哥美女，但不要明显脸谱。"""
    def errs(self, prompt):
        r = run_with({"product": "2x", "tag": "AI-1124-双人演绎", "scenes": [{"id": "s", "prompt": prompt, "fallback_prompts": ["x"]}]})
        r.cfg["aigc"] = {"look_words": ["帅气", "好看"], "natural_words": ["自然", "真实"], "mask_words": ["网红脸", "精致五官"]}
        orig = ag.cb.api
        ag.cb.api = lambda *a, **k: {"tags": ["AI双人演绎"]}
        try:
            return ag.check_plan(r)[0]
        finally:
            ag.cb.api = orig

    def test_ok(self):
        self.assertEqual(self.errs("28岁帅气男生在浴室抓头发，自然真实的生活感"), [])

    def test_missing_look_and_natural(self):
        e = self.errs("28岁男生在浴室抓头发")
        self.assertTrue(any("外貌" in x for x in e) and any("自然真实" in x for x in e))

    def test_mask_word_but_negation_ok(self):
        self.assertTrue(any("脸谱" in x for x in self.errs("帅气男生，网红脸，自然光")))
        self.assertEqual(self.errs("帅气男生，不要网红脸，自然光"), [])

    def test_empty_shot_skipped(self):
        self.assertEqual(self.errs("浴室台面上一瓶洗发水，晨光"), [])



class ReviewBlocks(unittest.TestCase):
    """用户 11-24：Cyber 卡审库。文案 high 报错、上下文短词只提醒；画面条目提醒 AIGC 提示词。"""
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from common import load_config
        self.cfg = load_config("qingyang")
        self.prod = self.cfg["products"]["selenium"]

    def lint(self, text):
        from lint_copy import lint_text
        return [(l, m) for l, m in lint_text(text, self.cfg, self.prod) if m.startswith("卡审 ")]

    def test_high_risk_copy_is_error(self):
        self.assertTrue(any(l == "error" and "CP-007" in m for l, m in self.lint("洗完几天都不塌，")))
        self.assertTrue(any(l == "warn" and "CP-001" in m for l, m in self.lint("黄金浓度二硫化硒，")))  # 库里 mid

    def test_context_words_warn_only(self):
        hits = self.lint("长发从发根梳到发尾，")
        self.assertTrue(hits and all(l == "warn" for l, _ in hits))

    def test_clean_copy_and_other_products(self):
        self.assertEqual(self.lint("二硫化硒搭配清扬配方，头皮清爽，"), [])
        from lint_copy import lint_text
        self.assertFalse(any(m.startswith("卡审 ") for _, m in lint_text("几天都不塌，", self.cfg, self.cfg["products"]["2x"])))

    def test_visual_blocks_warn_on_prompt(self):
        r = run_with({"product": "selenium", "tag": "AI-1124-油头", "scenes": [
            {"id": "a", "prompt": "女生拨开头发露出头皮，头屑特写", "fallback_prompts": ["x"]},
            {"id": "b", "prompt": "女生对镜梳头，不出现头屑特写", "fallback_prompts": ["x"]}]})
        r.product = self.prod
        orig = ag.cb.api
        ag.cb.api = lambda *a, **k: {"tags": ["AI-1124-油头"]}
        try:
            warns = ag.check_plan(r)[1]
        finally:
            ag.cb.api = orig
        self.assertTrue(any(w.startswith("a-01") and "卡审画面" in w for w in warns))
        self.assertFalse(any(w.startswith("b-01") and "卡审画面" in w for w in warns))


if __name__ == "__main__":
    unittest.main()
