"""Offline checks for the AI clip tag rule (scripts/ai_tags.py, user 11-18). No platform calls."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ai_tags  # noqa: E402
from test_aigc_gen import run_with  # noqa: E402


class Rule(unittest.TestCase):
    def test_problem(self):
        self.assertIsNone(ai_tags.problem({"ai_generated": True, "tags": ["AI小猫"]}))
        self.assertIsNone(ai_tags.problem({"ai_generated": False, "tags": ["使用后"]}))
        self.assertIn("普通标签", ai_tags.problem({"ai_generated": True, "tags": ["AI双人演绎", "使用后"]}))
        self.assertIn("没有标签", ai_tags.problem({"ai_generated": True, "tags": []}))
        self.assertIn("多个", ai_tags.problem({"ai_generated": True, "tags": ["AI小猫", "AI双人演绎"]}))
        self.assertIn("真实素材", ai_tags.problem({"ai_generated": False, "tags": ["AIlive图"]}))

    def test_target_keeps_ai_tag_else_first_keyword(self):
        maps = [("猫", "AI小猫"), ("女", "AI双人演绎"), ("男", "AI单人演绎")]
        self.assertEqual(ai_tags.target({"tags": ["AI超市选购", "使用后"]}, maps, "AI未分类"), ["AI超市选购"])
        self.assertEqual(ai_tags.target({"tags": ["使用后"], "name": "男生和女生对视"}, maps, "AI未分类"), ["AI双人演绎"])
        self.assertEqual(ai_tags.target({"tags": [], "name": "", "description": "空镜"}, maps, "AI未分类"), ["AI未分类"])


class SceneTag(unittest.TestCase):
    def test_scene_tag_overrides_plan_tag(self):
        r = run_with({"product": "2x", "tag": "AI双人演绎", "scenes": [
            {"id": "golf", "prompt": "p"}, {"id": "cat", "tag": "AI小猫", "prompt": "p"}]})
        self.assertEqual(r.tag("golf"), "AI双人演绎")
        self.assertEqual(r.tag("cat"), "AI小猫")
        self.assertEqual(r.tags(), ["AI双人演绎", "AI小猫"])



class NamingRule(unittest.TestCase):
    """用户 11-24：所有产品新生成的 AI 素材标签都是「AI-MMDD-素材方向」。"""
    def test_new_tag(self):
        self.assertEqual(ai_tags.new_tag("2026-11-24", "夫妻对话"), "AI-1124-夫妻对话")
        self.assertEqual(ai_tags.new_tag("20261124", "AI单人口播"), "AI-1124-单人口播")
        self.assertEqual(ai_tags.new_tag("1120", " 超市 货架 "), "AI-1120-超市货架")

    def test_name_problem(self):
        self.assertIsNone(ai_tags.name_problem("AI-1124-夫妻对话"))
        for bad in ["AI夫妻对话", "AI-1124", "AI-1124-B01-分镜1", "AI-1324-夫妻对话", "AI-1124-分镜1", "1124-夫妻对话"]:
            self.assertIsNotNone(ai_tags.name_problem(bad), bad)
        with self.assertRaises(ValueError):
            ai_tags.new_tag("1124", "B01")

    def test_plan_tags_checked_but_started_plans_grandfathered(self):
        import aigc_gen as ag
        r = run_with({"product": "2x", "tag": "AI双人演绎", "scenes": [{"id": "s", "prompt": "空镜台面", "fallback_prompts": ["x"]}]})
        orig = ag.cb.api
        ag.cb.api = lambda *a, **k: {"tags": []}
        try:
            self.assertTrue(any("AI-MMDD" in e for e in ag.check_plan(r)[0]))
            r.st["slots"]["s-01"]["attempts"].append({"status": "success"})
            errs, warns = ag.check_plan(r)
            self.assertFalse(any("AI-MMDD" in e for e in errs))
            self.assertTrue(any("AI-MMDD" in w for w in warns))
            r.plan["tag"] = "AI-1124-双人演绎"
            r.st["slots"]["s-01"]["attempts"].clear()
            self.assertFalse(any("AI-MMDD" in e for e in ag.check_plan(r)[0]))
        finally:
            ag.cb.api = orig


if __name__ == "__main__":
    unittest.main()
