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
    cfg = {"products": {"2x": {"clipo_product_id": "x" * 24}}, "banned_footage_words": ["秃顶"]}
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


if __name__ == "__main__":
    unittest.main()
