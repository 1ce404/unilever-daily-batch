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


if __name__ == "__main__":
    unittest.main()
