"""用户 11-24：一屏进度（scripts/progress.py）。离线，不调平台。"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import clipo_batch as cb  # noqa: E402
import progress  # noqa: E402
from common import load_config  # noqa: E402

APPROVAL = {"by": "用户", "note": "通过"}


def make_run(briefs, videos=None, jobs=None, shares=None):
    run = Path(tempfile.mkdtemp())
    (run / "briefs").mkdir()
    (run / "clipo").mkdir()
    for b in briefs:
        (run / "briefs" / f"{b['brief_id']}.json").write_text(json.dumps(b, ensure_ascii=False))
    (run / "clipo" / "state.json").write_text(json.dumps(
        {"videos": videos or {}, "briefs": jobs or {}, "shares": shares or {}}, ensure_ascii=False))
    return run


class Progress(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config("qingyang")
        self._lint = progress.lint_brief
        progress.lint_brief = lambda b, cfg: []  # 只测进度判定，文案门禁另有测试

    def tearDown(self):
        progress.lint_brief = self._lint

    def brief(self, bid, **kw):
        b = {"brief_id": bid, "brand": "qingyang", "product_key": "2x", "quantity": 3, "date": "2026-11-24",
             "segments": [{"id": "1", "text": "清扬控油蓬松洗发水，"}], "status": "approved"}
        b.update(kw)
        return b

    def row(self, run, bid):
        return next(r for r in progress.day_progress(self.cfg, run)["briefs"] if r["brief"] == bid)

    def test_stages_and_next_commands(self):
        ok = lambda: {"stage": "done", "ai_review": "pass"}
        run = make_run(
            [self.brief("B01", segments=[]), self.brief("B02", status="draft"),
             self.brief("B03"), self.brief("B04"), self.brief("B05")],
            videos={"B04-V01": {"brief_id": "B04", **ok()},
                    "B04-V02": {"brief_id": "B04", "stage": "done", "ai_review": "reject", "ai_reason": "画面模糊"},
                    "B05-V01": {"brief_id": "B05", **ok()}, "B05-V02": {"brief_id": "B05", **ok()},
                    "B05-V03": {"brief_id": "B05", **ok()}},
            jobs={"B04": {"stage": "done"}, "B05": {"stage": "done"}})
        with unittest.mock.patch.object(progress, "approval_problem", lambda b: None if b.get("status") == "approved" else "未审"):
            self.assertEqual(self.row(run, "B01")["stage"], "写脚本")
            self.assertIn("brief_card.py", self.row(run, "B01")["next"])
            self.assertEqual(self.row(run, "B02")["stage"], "等脚本人审")
            self.assertTrue(self.row(run, "B02")["next"].startswith("⏸"))
            self.assertEqual(self.row(run, "B03")["stage"], "可提交")
            self.assertIn("submit --dry-run", self.row(run, "B03")["next"])
            r4 = self.row(run, "B04")
            self.assertEqual((r4["stage"], r4["ok"], r4["short"], r4["rejected"]), ("缺口", 1, 2, 1))
            self.assertIn("topup --dry-run", r4["next"])
            self.assertIn("缺 2 条", r4["next"])
            self.assertEqual(self.row(run, "B05")["stage"], "可交付")

    def test_configuration_only_draft_never_suggests_bulk_topup(self):
        run = make_run([self.brief("B01")], jobs={"B01":{"stage":"script_ready","configuration_only":True,"script_id":"native"}})
        with unittest.mock.patch.object(progress, "approval_problem", lambda b: None):
            row=self.row(run,"B01")
        self.assertEqual(row['stage'],'可提交')
        self.assertIn('submit --dry-run',row['next'])
        self.assertNotIn('topup',row['next'])

    def test_in_flight_means_poll_not_wait(self):
        run = make_run([self.brief("B01")], videos={"B01-V01": {"brief_id": "B01", "stage": "rendering"}},
                       jobs={"B01": {"stage": "done"}})
        with unittest.mock.patch.object(progress, "approval_problem", lambda b: None):
            p = progress.day_progress(self.cfg, run)
        self.assertEqual(p["briefs"][0]["stage"], "生产中")
        self.assertTrue(p["poll_needed"])
        self.assertIn("poll 没在跑", progress.render(p))
        log = run / "clipo" / "poll-qingyang.log"
        log.write_text("x")
        p = progress.day_progress(self.cfg, run)
        self.assertTrue(p["poll_alive"])
        self.assertIn("不用等", progress.render(p))
        os.utime(log, (time.time() - 900, time.time() - 900))
        self.assertFalse(progress.day_progress(self.cfg, run)["poll_alive"])

    def test_demo_gate(self):
        run = make_run([self.brief("B01")], videos={"B01-V01": {"brief_id": "B01", "stage": "done", "ai_review": "pass"}},
                       jobs={"B01": {"stage": "done", "demo_stage": "demo"}})
        with unittest.mock.patch.object(progress, "approval_problem", lambda b: None):
            r = self.row(run, "B01")
        self.assertEqual(r["stage"], "等 demo 人审")
        self.assertIn("approve-demo", r["next"])

    def test_delivered_by_state_pack_or_receipt(self):
        vids = {f"B0{i}-V0{j}": {"brief_id": f"B0{i}", "stage": "done", "ai_review": "pass", "cut_id": f"c{i}{j}"}
                for i in (1, 2, 3) for j in (1, 2, 3)}
        run = make_run([self.brief("B01"), self.brief("B02"), self.brief("B03")], videos=vids,
                       jobs={b: {"stage": "done"} for b in ("B01", "B02", "B03")},
                       shares={"B01": {"id": "s1", "videos": ["B01-V01"]}, "2x-pack-1": {"id": "s2", "videos": ["B02-V01"]}})
        (run / "x").mkdir()
        (run / "x" / "pack-links.json").write_text(json.dumps(
            {"verified": True, "url": "u", "plan": {"entries": [{"cut_id": "c31"}]}}))
        stages = {r["brief"]: r["stage"] for r in progress.day_progress(self.cfg, run)["briefs"]}
        self.assertEqual(stages, {"B01": "已交付", "B02": "已交付", "B03": "已交付"})

    def test_reasons_detail_for_one_brief(self):
        run = make_run([self.brief("B01")], videos={
            "B01-V01": {"brief_id": "B01", "stage": "done", "ai_review": "reject", "ai_reason": "口播用语夸大"},
            "B01-V02": {"brief_id": "B01", "stage": "done", "ai_review": "pass", "copy_issues": ["新增了「最」"]},
            "B01-V03": {"brief_id": "B01", "stage": "failed", "error": "render_failed"}}, jobs={"B01": {"stage": "done"}})
        r = progress.day_progress(self.cfg, run, "B01")["briefs"][0]
        why = {d["video"]: d["why"] for d in r["details"]}
        self.assertIn("文案", why["B01-V01"])
        self.assertTrue(why["B01-V02"].startswith("改写违规"))
        self.assertTrue(why["B01-V03"].startswith("失败"))
        self.assertEqual(r["ok"], 0)

    def test_clipo_batch_status_uses_progress(self):
        import contextlib
        import io
        run = make_run([self.brief("B01", segments=[])])
        cb.CFG, cb.BASE = self.cfg, None
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cb.cmd_status(self.cfg, str(run), type("A", (), {"brief": None})())
        self.assertIn("下一步（照做", buf.getvalue())


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()

class CheckedBatchProgressTest(unittest.TestCase):
    def test_conditional_batch_is_not_reported_as_waiting_for_demo_customer(self):
        b={'brief_id':'B','quantity':10,'status':'draft','segments':[{'id':'1','text':'checked'}],'checked_batch_authorization':{'mode':'check_then_bulk'}}
        counts={'ok':1,'failed':0,'rejected':0,'in_flight':0,'quality_pending':0}
        with unittest.mock.patch.object(progress,'permits_checked_batch',return_value=True):
            stage,_=progress.next_step({},b,{'stage':'generating','demo_stage':'bulk'},counts,0,False,lambda *x:'cmd')
        self.assertEqual(stage,'生产中')
