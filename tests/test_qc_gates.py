"""
[INPUT]: 依赖 clipo_batch 的生产与分享入口；依赖 qc 的交付检查；依赖 lint_copy 的画面计划、素材池与形态问答检查。
[OUTPUT]: 验证提交门禁、开头切片复用与编号选片、分享范围、必填剪辑编号与审核版本固定、画面计划；去花字保留转场，禁用画面只换原速足长源片；状态合并保持条目引用，复审拒绝旧输出与跳过受理；分享替换写前备份，保留旧链接并逐条回读新版对象。
[POS]: tests 的交付闸门与提交门禁回归；不调用平台接口。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""
import sys
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import clipo_batch as cb
import qc
from lint_copy import lint_pools, lint_visual_plan, lint_script

PRODUCT = {'full_name': '清扬控油蓬松洗发水', 'name_aliases': ['清扬控油蓬松洗发水'],
           'ingredient_terms': ['SCA'], 'intro_footage': ['产品外观']}


class GateTest(unittest.TestCase):
    def setUp(self):
        cb.CFG = {'brand_key': 'qingyang', 'generate_factor': 2}
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_state_save_keeps_batch_entry_references(self):
        st = cb.State(self.tmp.name)
        st.videos.update({"a": {"stage": "done"}, "b": {"stage": "done"}})
        st.save()
        batch = list(st.videos.values())
        batch[0]["stage"] = "rendering"
        st.save()
        self.assertIs(batch[1], st.videos["b"])
        batch[1]["stage"] = "rendering"
        st.save()
        disk = json.loads(st.path.read_text())
        self.assertEqual([v["stage"] for v in disk["videos"].values()], ["rendering", "rendering"])

    def test_rereview_binds_latest_output_and_checks_receipt(self):
        for queued in (1, 0):
            with self.subTest(queued=queued), tempfile.TemporaryDirectory() as root:
                cb.CFG = {"brand_key": "qingyang", "products": {"p": {}}}
                st = cb.State(root)
                st.videos["V1"] = {"brief_id": "B1", "cut_id": "cut", "stage": "done",
                                   "object_key": "old.mp4", "render_job": "old-job",
                                   "ai_review": "pass", "bad_footage": ["旧禁用源片"]}
                st.save()
                brief = {"brief_id": "B1", "product_key": "p", "segments": [], "disable_effects": True}
                cut = {"output_status": "success", "output_object_key": "new.mp4",
                       "ai_review_result": None, "ai_review_reason": "", "draft": {"clips": []}}
                def api(method, path, body=None, **kwargs):
                    if method == "GET":
                        return cut
                    self.assertEqual(body, {"cut_ids": ["cut"]})
                    self.assertEqual(kwargs["tries"], 1)
                    return {"queued": queued, "skipped": 1 - queued}
                args = SimpleNamespace(brief=None, all=True, dry_run=False)
                with patch.object(cb, "api", side_effect=api), patch.object(cb, "load_briefs", return_value=[brief]), \
                     patch.object(cb, "lint_script", return_value=[]), patch.object(cb, "bad_footage", return_value=[]), \
                     patch("qc.cut_checks", return_value=[]):
                    if queued:
                        cb.cmd_rereview(cb.CFG, root, args)
                    else:
                        with self.assertRaises(RuntimeError):
                            cb.cmd_rereview(cb.CFG, root, args)
                video = cb.State(root).videos["V1"]
                self.assertEqual(video["object_key"], "new.mp4")
                self.assertEqual(video["previous_object_key"], "old.mp4")
                self.assertEqual(video["bad_footage"], [])
                self.assertNotIn("render_job", video)
                self.assertIsNone(video["ai_review"])
                self.assertEqual(video["stage"], "reviewing" if queued else "done")

    def test_remove_effects_preserves_other_tracks(self):
        draft = {"composition": {"fps": 30}, "tracks": [{"id": "effect"}], "clips": [
            {"id": "e1", "trackId": "effect", "props": {"text": "错误花字"}},
            {"id": "1-1", "trackId": "main", "props": {"transition": "whiteFlash"}},
            {"id": "s1", "trackId": "overlay", "props": {"text": "口播字幕"}},
            {"id": "a1", "trackId": "audio", "props": {"volume": 0.2}}]}
        original = copy.deepcopy(draft)
        out = cb.without_effects(draft)
        self.assertEqual(draft, original)
        self.assertEqual(out["clips"], draft["clips"][1:])
        self.assertEqual(out["tracks"], draft["tracks"])
        self.assertEqual(cb.without_effects(out), out)

    def test_banned_replacement_measures_source_and_keeps_timing(self):
        cb.CFG = {"products": {"p": {}}, "banned_footage_files": {"bad.mp4": "皮屑"}}
        brief = {"product_key": "p", "segments": [{"visual_tags": ["产品质地"]}]}
        draft = {"composition": {"fps": 30}, "clips": [
            {"id": "1-1", "trackId": "main", "from": 3, "durationInFrames": 60,
             "props": {"src": "https://x/bad.mp4", "transition": "whiteFlash", "loop": True}},
            {"id": "s1", "trackId": "overlay", "props": {"text": "原字"}}]}
        idx = {"bad.mp4": {"description": "皮屑"}, "good.mp4": {
            "id": "a", "object_url": "https://x/good.mp4", "review_result": "pass",
            "tags": ["产品质地"], "begin_ms": 0, "end_ms": 5000}}
        def fetch(url, dest):
            Path(dest).write_bytes(b"test")
        with patch.object(cb, "footage_index", return_value=idx), patch.object(cb, "fetch_file", side_effect=fetch), \
             patch("footage_qc.probe", return_value={"format": {"duration": "3.0"}}):
            out, changes = cb.replace_banned_shots(brief, draft, "cut", self.tmp.name)
        self.assertEqual(out["clips"][0]["from"], 3)
        self.assertEqual(out["clips"][0]["durationInFrames"], 60)
        self.assertEqual(out["clips"][0]["props"]["playbackRate"], 1.0)
        self.assertEqual(out["clips"][0]["props"]["transition"], "whiteFlash")
        self.assertEqual(out["clips"][1], draft["clips"][1])
        self.assertEqual(len(changes), 1)
        with patch.object(cb, "footage_index", return_value=idx), \
             patch("footage_qc.probe", return_value={"format": {"duration": "1.0"}}):
            with self.assertRaises(RuntimeError):
                cb.replace_banned_shots(brief, draft, "cut", self.tmp.name)

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

    def test_repeated_openers_keep_video_order_and_quality_gates(self):
        def video(source, **kw):
            return {'brief_id': 'B1', 'ai_review': 'pass', 'pinned_clips': [source], **kw}
        state = SimpleNamespace(videos={'B1-V04': video('b'), 'B1-V02': video('a'),
                                       'B1-V01': video('a'), 'B1-V03': video('c', bad_footage=['禁用'])})
        self.assertEqual(cb.picks(state, 'B1', 2), (['B1-V01', 'B1-V02'], ['B1-V04']))

    def test_opening_pool_allows_reuse_but_requires_valid_candidates(self):
        for size in (1, 2, 3):
            with self.subTest(size=size):
                seg = {'id': '1', 'footage_pool': {'query': '开头', 'tags': ['开头'], 'score': 0.8,
                                                  'clips': [{'segment_id': str(i)} for i in range(size)]}}
                self.assertEqual(lint_pools({'quantity': 10}, [seg]), [])
        seg['footage_pool']['clips'] = []
        self.assertTrue(any('素材池为空' in m for _, m in lint_pools({'quantity': 10}, [seg])))
        seg['footage_pool']['clips'] = [{'segment_id': 'a'}]
        del seg['footage_pool']['query']
        self.assertTrue(any('query' in m for _, m in lint_pools({'quantity': 10}, [seg])))

    def test_explicit_single_openers_need_no_repeat_flags(self):
        for segs in ([{'id': '1', 'visual_query': {'segment_id': 'a', 'query': '指定素材'}}],
                     [{'id': '1', 'silent': True, 'footage': {'segment_id': 'a', 'query': '原片'}},
                      {'id': '2', 'silent': True, 'footage': {'segment_id': 'b', 'query': '原片后半'}}]):
            with self.subTest(segs=segs):
                self.assertEqual(lint_pools({'quantity': 10}, segs), [])
        body = [{'id': '1'}, {'id': '2', 'visual_query': {'segment_id': 'b'}}]
        self.assertTrue(lint_pools({'quantity': 10}, body))

    def test_hook_assignment_keeps_existing_distinct_sources(self):
        videos = [("kept1", {"hook_clip": "b"}), ("new", {"pinned_clips": ["b"]}),
                  ("kept2", {"pinned_clips": ["c"]})]
        self.assertEqual(cb.hook_assignments(["a", "b", "c"], videos),
                         {"kept1": "b", "kept2": "c", "new": "a"})

    def test_one_approved_source_can_pin_a_pain_segment(self):
        cb.CFG = {"products": {"selenium": {"clipo_product_id": "pid"}}}
        hit = {"score": 0.95, "segment": {"id": "oil", "name": "拨油塌发根",
               "description": "油头实拍", "review_result": "pass", "begin_ms": 0, "end_ms": 10060}}
        args = SimpleNamespace(product="selenium", tags="1016-洗头", query="拨油塌发根",
                               allow_risk=False, size=1, brief=None, seg=None)
        with patch.object(cb, "api", return_value={"items": [hit]}), \
             patch.object(cb, "banned_words_for", return_value=[]):
            cb.cmd_pool(cb.CFG, self.tmp.name, args)

    def test_share_replacement_backs_up_and_keeps_old_link(self):
        cases = [{"video_object_key": "outputs/new.mp4"},
                 {"video_url": "https://bucket.aliyuncs.com/outputs/new.mp4?signature=test"},
                 {"video_object_key": "outputs/wrong.mp4"}]
        for returned in cases:
            with self.subTest(returned=returned), tempfile.TemporaryDirectory() as root:
                cfg = {"brand_key": "qingyang", "products": {"p": {"clipo_product_id": "product"}}}
                cb.CFG = cfg
                st = cb.State(root)
                st.data.update(share_group={"id": "group"}, briefs={"B1": {"script_id": "script"}},
                               shares={"B1": {"id": "old", "title": "标题", "videos": ["old-video"]}})
                st.videos["V1"] = {"cut_id": "cut", "object_key": "outputs/new.mp4"}
                st.save()
                brief = {"brief_id": "B1", "product_key": "p", "quantity": 1}
                args = SimpleNamespace(brief=None, partial=False, refresh=False, dry_run=False, download=False)
                requests = []
                def api(method, path, body=None, **kwargs):
                    requests.append((method, path, body))
                    if (method, path) == ("GET", "/video-shares/old"):
                        return {"id": "old", "videos": []}
                    if (method, path) == ("GET", "/video-share-groups/group"):
                        return {"id": "group", "shares": [{"id": "old"}, {"id": "new"}]}
                    if (method, path) == ("POST", "/video-shares"):
                        backups = list((Path(root) / "clipo").glob("share-replace-*.json"))
                        self.assertEqual(len(backups), 1)
                        self.assertEqual(json.loads(backups[0].read_text())["old_share"]["id"], "old")
                        self.assertEqual(kwargs["tries"], 1)
                        return {"id": "new"}
                    if (method, path) == ("GET", "/video-shares/new"):
                        return {"title": "标题", "videos": [returned]}
                    if (method, path) == ("PATCH", "/video-share-groups/group"):
                        self.assertEqual(body, {"video_share_ids": ["new"]})
                        return {}
                    self.fail(f"禁止未声明的平台请求：{method} {path}")
                valid = returned.get("video_object_key") != "outputs/wrong.mp4"
                with patch.object(cb, "BASE", "https://offline.invalid/api/v1"), \
                     patch.object(cb, "api", side_effect=api), patch.object(cb, "load_briefs", return_value=[brief]), \
                     patch.object(cb, "validate_selection"), patch.object(cb, "picks", return_value=(["V1"], [])), \
                     patch.object(cb, "delivery_name", return_value="标题"), \
                     patch.object(cb, "share_video", return_value={"cut_id": "cut", "video_object_key": "outputs/new.mp4"}), \
                     patch.object(cb, "write_delivery"):
                    if valid:
                        cb.cmd_share(cfg, root, args)
                    else:
                        with self.assertRaises(RuntimeError):
                            cb.cmd_share(cfg, root, args)
                self.assertFalse(any(method == "DELETE" for method, _, _ in requests))
                self.assertEqual(cb.State(root).data["shares"]["B1"]["id"], "new" if valid else "old")
                if not valid:
                    self.assertFalse(any(method == "PATCH" for method, _, _ in requests))

    def test_delivery_does_not_count_user_release_as_ai_pass(self):
        st = cb.State(self.tmp.name)
        st.videos["B1-V1"] = {"brief_id": "B1", "title": "成片", "cut_id": "cut",
                              "stage": "done", "ai_review": None, "review_waived": "直接交付"}
        brief = {"brief_id": "B1", "quantity": 1, "direction": "日常"}
        with patch.object(cb, "BASE", "https://offline.invalid/api/v1"):
            cb.write_delivery(st, {"B1": brief}, self.tmp.name)
        text = (Path(self.tmp.name) / cb.out_name(cb.CFG, "delivery.md")).read_text()
        self.assertIn("交付 1 / 1 条", text)
        self.assertIn("AI 审核通过 0 条", text)
        self.assertIn("由用户检查 1 条；不记作 AI 审核通过", text)

    def test_share_video_includes_required_cut_id(self):
        cut = {"output_object_key": "outputs/latest.mp4", "draft": {
            "composition": {"durationInFrames": 810, "fps": 30, "width": 1080, "height": 1920},
            "cover_url": "https://bucket.aliyuncs.com/cover.jpg?signature=test"}}
        with patch.object(cb, "api", return_value=cut) as api:
            payload = cb.share_video({"cut_id": "cut-123", "object_key": "outputs/old.mp4"})
        api.assert_called_once_with("GET", "/video-cuts/cut-123")
        self.assertEqual(payload["cut_id"], "cut-123")
        self.assertEqual(payload["video_object_key"], "outputs/old.mp4")
        self.assertEqual(payload["cover_object_key"], "cover.jpg")
        self.assertEqual(payload["duration_seconds"], 27.0)
        with patch.object(cb, "api", return_value=cut):
            fallback = cb.share_video({"cut_id": "cut-123"})
        self.assertEqual(fallback["video_object_key"], "outputs/latest.mp4")

    def test_share_scope_isolates_one_delivery_family(self):
        def brief(bid, product, store, pack):
            return {"brief_id": bid, "product_key": product, "store": store,
                    "date": "2026-11-20", "pack": pack}
        bs = [brief("star", "selenium", "Cyber", False),
              brief("ai1", "selenium", "Cyber", True),
              brief("ai2", "selenium", "Cyber", True),
              brief("other-product", "2x", "Cyber", True),
              brief("other-store", "selenium", "旗舰店", True)]
        self.assertEqual(set(cb.share_scope(bs, "star")), {"star"})
        self.assertEqual(set(cb.share_scope(bs, "ai1")), {"ai1", "ai2"})
        self.assertEqual(set(cb.share_scope(bs, "missing")), set())
        self.assertEqual(len(cb.share_scope(bs)), 5)

    def test_poll_only_touches_selected_brief(self):
        state = SimpleNamespace(data={"briefs": {"chosen": {"stage": "generating"},
                                               "other": {"stage": "generating"}}, "videos": {}},
                                videos={}, save=lambda: None)
        args = SimpleNamespace(brief="chosen", wait=0, interval=0)
        brief = {"brief_id": "chosen"}
        with patch.object(cb, "State", return_value=state), \
             patch.object(cb, "load_briefs", return_value=[brief]), \
             patch.object(cb, "pull_generation") as pull:
            with self.assertRaises(SystemExit):
                cb.cmd_poll({}, self.tmp.name, args)
        self.assertEqual(pull.call_count, 1)
        self.assertEqual(pull.call_args.args[2]["brief_id"], "chosen")

    def test_qc_allows_repeated_openers_and_still_blocks_cut_issues(self):
        cfg = {'brand_key': 'qingyang', 'clipo': {'base_url': 'https://offline.invalid'}}
        b = {'brief_id': 'B1', 'quantity': 2, 'product_key': 'p', 'segments': []}
        state = SimpleNamespace(videos={f'B1-V0{i}': {'brief_id': 'B1', 'stage': 'done',
                                'cut_id': f'c{i}', 'pinned_clips': ['a'], 'ai_review': 'pass'} for i in (1, 2)}, save=lambda: None)
        for issues, exit_code in (([], 0), (['禁用画面'], 1)):
            with self.subTest(issues=issues), patch.object(sys, 'argv', ['qc.py', '--config', 'q', '--run', self.tmp.name]), \
                 patch.object(qc, 'load_config', return_value=cfg), patch.object(cb, 'State', return_value=state), \
                 patch.object(cb, 'load_briefs', return_value=[b]), patch.object(cb, 'api', return_value={'draft': {}}), \
                 patch.object(qc, 'cut_checks', return_value=issues) as checks:
                with self.assertRaises(SystemExit) as exc:
                    qc.main()
                self.assertEqual(exc.exception.code, exit_code)
                self.assertEqual(checks.call_count, 2)

    def test_tags_only_uses_all_tag_candidates_without_query(self):
        platform = {'visual': {'mode': 'material_segment', 'props': {
            'tags': ['旧标签'], 'query': '平台建稿附加的描述', 'score': 0.95, 'duration': 3}}}
        cb.pin_visual(platform, {'visual_tags': ['指定标签']})
        self.assertEqual(platform['visual']['props'], {'tags': ['指定标签'], 'duration': 3})
        cb.pin_visual(platform, {'visual_tags': ['指定标签'],
                                 'visual_query': {'segment_id': 'a', 'query': '用户指定素材', 'score': 0.9}})
        self.assertEqual(platform['visual']['props']['query'], '用户指定素材')
        self.assertEqual(platform['visual']['props']['score'], 0.9)

    def test_replaced_hook_takes_priority_for_explicit_rework(self):
        self.assertEqual(cb.opener_clip({'pinned_clips': ['a'], 'hook_clip': 'b'}), 'b')

    def test_name_and_ingredient_split(self):
        segs = [{'id': '1', 'role': 'product', 'text': '清扬控油蓬松洗发水，有SCA技术。', 'visual_tags': ['产品外观']}]
        self.assertTrue(any('拆成两段' in m for _, m in lint_visual_plan(segs, PRODUCT)))

    def test_name_line_shows_bottle(self):
        segs = [{'id': '1', 'role': 'product', 'text': '试试清扬控油蓬松洗发水。', 'visual_tags': ['产品质地']}]
        self.assertTrue(any('产品亮相' in m for _, m in lint_visual_plan(segs, PRODUCT)))

    def test_form_question_must_be_immediately_answered_by_named_product(self):
        cfg = {'copy_limits': {'name_not_before': 2, 'combo_chars_min': 1, 'combo_chars_max': 200, 'chars_per_second': 4.2}}
        product = {'full_name': '力士护发精油喷雾', 'name_aliases': ['力士护发精油喷雾'],
                   'selling_points': {'全SKU': {'must_mention': ['喷']}}, 'cta_required': ['直播间']}
        def errors(lines):
            segs = [{'role': role, 'text': text} for role, text in lines]
            return [m for level, m in lint_script([s['text'] for s in segs], segs,
                    {'selling_point': '全SKU'}, cfg, product) if level == 'error' and '第一次讲卖点' in m]
        answered = [('hook', '护发精油，还能用喷的？'),
                    ('product', '力士护发精油喷雾，一喷化雾。'), ('cta', '进直播间看看。')]
        self.assertEqual(errors(answered), [])
        for hook in ['一喷就有效？', '护发精油，还能用喷的？一喷就有效。', '护发精油，一喷化雾。']:
            with self.subTest(hook=hook):
                self.assertTrue(errors([('hook', hook)] + answered[1:]))
        self.assertTrue(errors([answered[0], ('effect', '清爽好用。')] + answered[1:]))
        self.assertEqual(errors([answered[0], ('product', '力士护发精油喷雾。'), answered[2]]), [])

    def test_every_segment_plans_footage(self):
        segs = [{'id': '1', 'role': 'hook', 'text': '头发又塌了。'}]
        self.assertTrue(any('没写画面' in m for _, m in lint_visual_plan(segs, PRODUCT)))


if __name__ == '__main__':
    unittest.main()
