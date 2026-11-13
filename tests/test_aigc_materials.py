import copy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import aigc_materials as am
import clipo_batch as cb


def group(source="source-one", name="A01", video="B01-V01", cut="cut-one"):
    return {"name": name, "video_key": video, "cut_id": cut, "source_generation_id": source,
            "segments": [{"segment": n, "object_key": f"materials/{name}-{n}.mp4",
                          "source_interval": [a, b], "duration": b - a}
                         for n, a, b in ((1, 0, 3), (2, 3, 6), (4, 6, 10))]}


def draft(g):
    return {"composition": {"fps": 30}, "clips": [
        {"id": str(x["segment"]) + "-1", "trackId": "main", "durationInFrames": 60,
         "props": {"src": x["object_key"], "startFrom": 0, "playbackRate": 1}}
        for x in g["segments"]]}


class MaterialBindingTests(unittest.TestCase):
    def test_renaming_set_does_not_make_same_source_distinct(self):
        first = group()
        second = group(name="A02", video="B01-V02", cut="cut-two")
        briefs = {"B01": {"within_folder_material_diversity_required": True, "visual_sets": [first, second]}}
        videos = {"B01-V01": {"cut_id": "cut-one"}, "B01-V02": {"cut_id": "cut-two"}}
        with self.assertRaisesRegex(RuntimeError, "复用"):
            am.validate_selection(briefs, videos, {"B01": list(videos)})

    def test_distinct_sources_are_deliverable_groups(self):
        first = group()
        second = group(source="source-two", name="A02", video="B01-V02", cut="cut-two")
        briefs = {"B01": {"within_folder_material_diversity_required": True, "visual_sets": [first, second]}}
        videos = {"B01-V01": {"cut_id": "cut-one"}, "B01-V02": {"cut_id": "cut-two"}}
        self.assertEqual(am.validate_selection(briefs, videos, {"B01": list(videos)}), 2)

    def test_overlapping_actions_rejected(self):
        g = group()
        g["segments"][1]["source_interval"] = [2, 6]
        with self.assertRaisesRegex(RuntimeError, "重叠"):
            am.segments(g)

    def test_unbound_draft_rejected(self):
        g = group()
        d = draft(g)
        d["clips"][1]["props"]["src"] = "materials/unrelated.mp4"
        with self.assertRaisesRegex(RuntimeError, "未绑定"):
            am.validate_draft(g, d)

    def test_loop_and_overlong_playback_rejected(self):
        g = group()
        d = draft(g)
        d["clips"][0]["props"]["loop"] = True
        with self.assertRaisesRegex(RuntimeError, "循环"):
            am.validate_draft(g, d)
        d = draft(g)
        d["clips"][0]["durationInFrames"] = 300
        with self.assertRaisesRegex(RuntimeError, "超出源时长"):
            am.validate_draft(g, d)

    def test_user_review_does_not_submit_ai_review(self):
        instruction = "对，你不要检查了，我来检查"
        with tempfile.TemporaryDirectory() as directory:
            cb.CFG = {"brand_key": "qingyang"}
            state = cb.State(directory)
            state.data["content_review"] = {"mode": "user", "instruction": instruction}
            state.videos["B01-V01"] = {"brief_id": "B01", "stage": "rendering", "cut_id": "cut-one", "render_job": "job-one"}
            state.save()
            calls = []
            def api(method, path, body=None):
                calls.append((method, path))
                if method == "GET" and path == "/render-jobs/job-one":
                    return {"status": "success", "object_key": "output.mp4"}
                raise AssertionError((method, path))
            with patch.object(cb, "load_briefs", return_value=[{"brief_id": "B01"}]), patch.object(cb, "api", side_effect=api):
                with self.assertRaises(SystemExit) as result:
                    cb.cmd_poll({}, directory, SimpleNamespace(wait=0, interval=0))
            self.assertEqual(result.exception.code, 0)
            video = cb.State(directory).videos["B01-V01"]
            self.assertEqual(video["review_waived"], instruction)
            self.assertTrue(video["user_review_pending"])
            self.assertNotIn("ai_review", video)
            self.assertEqual(calls, [("GET", "/render-jobs/job-one")])


if __name__ == "__main__":
    unittest.main()
