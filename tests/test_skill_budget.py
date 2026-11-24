"""用户 11-24：skill 不能越写越大。SKILL.md 每次都进 agent 上下文，只放流程与铁律；规则正文放 config / references，
卡审库这类数据只给脚本读。这里给常驻文本设上限，超了就该拆，不该加。"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Budget(unittest.TestCase):
    def test_skill_md_stays_small(self):
        n = len((ROOT / "SKILL.md").read_text())
        self.assertLessEqual(n, 7000, f"SKILL.md {n} 字符：规则正文移到 references/ 或 config，不往主流程加")

    def test_copy_rules_exclude_aigc_notes(self):
        """任务卡打印产品 rules：只放写稿规则，AIGC 画面规则放 aigc_notes。"""
        for f in (ROOT / "config").glob("*/*.json"):
            for r in json.loads(f.read_text()).get("rules") or []:
                self.assertFalse(r.startswith("外貌（用户 11-18）"), f"{f.name}: 外貌规则属于 aigc_notes")


if __name__ == "__main__":
    unittest.main()
