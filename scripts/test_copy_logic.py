"""Offline regression checks. Run: python3 -I scripts/test_copy_logic.py. No production APIs."""
import copy
import io
import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import draft
import clipo_batch as cb
import review_doc
from common import approval_problem, copy_hash, load_config, parse_table
from copy_logic import editorial_issues, plan_batch_issues, plan_hash
from lint_copy import lint_brief, lint_day


def brief(bid='1105-B01'):
    return {
        'brief_id': bid, 'date': '2026-11-05', 'brand': 'qingyang', 'store': '清扬',
        'product_key': '2x', 'direction': '成分种草', 'direction_core': '成分种草',
        'selling_point': 'SCA控油蓬松技术', 'audience': '男生', 'quantity': 10,
        'status': 'draft', 'copy_logic_version': 2,
        'creative_plan': {
            'speaker': '解释选购思路的朋友，不假装亲测',
            'audience_question': '怎么把清爽和蓬松两个需求一起考虑？',
            'angle': '从选购标准聊起', 'throughline': '先明确需要什么，再用已核实卖点解释推荐理由',
            'route': ['提出两个在意的头发状态', '按需求解释产品选择依据', '请需求明确的人了解活动'],
            'ending_bridge': '有这个需求再核对活动，不突然喊囤货口号'},
        'segments': [
            {'id': '1', 'role': 'hook', 'title': '选择标准', 'text': '头发看着清爽，和发根有没有蓬起来，是你选洗发水时，在意的两件事吗？'},
            {'id': '2', 'role': 'selling', 'title': '选择依据', 'text': '如果这两点你都在意，可以看看清扬控油蓬松洗发水，搭载SCA控油蓬松技术。'},
            {'id': '3', 'role': 'effect', 'title': '解释判断', 'text': '挑洗发水时，把自己的需求先想明白，再看产品的卖点，就没那么容易挑花眼。'},
            {'id': '4', 'role': 'cta', 'title': '继续了解', 'text': '双十一89.9元拍一发四，想了解套组，戳直播间看看。'}]}


def assessment(b):
    return {'verdict': 'pass',
            'throughline': '开头提出两个选择目标，随后用SCA卖点解释为何考虑这款，未另起无关的香氛话题。',
            'voice': '用“是你在意的两件事吗”邀请对方判断，未声称亲测或编同事评价。',
            'distinctness': '本测试只含一条示例；角度是选购标准，不把它当作已经完成组间质量评审。',
            'transitions': [
                {'from': '1', 'to': '2', 'link': '“如果这两点你都在意”接住前句提出的两个选择目标。'},
                {'from': '2', 'to': '3', 'link': '从产品对应的卖点回到按自己的需求做判断。'},
                {'from': '3', 'to': '4', 'link': '选择依据明确后，引导感兴趣的人核对套组活动。'}]}


class CopyLogicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='unilever-offline-')
        self.run = Path(self.tmp.name)
        (self.run/'briefs').mkdir()
        self.cfg = load_config('qingyang')
        self.b = brief()

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, b):
        p = self.run/'briefs'/f"{b['brief_id']}.json"
        p.write_text(json.dumps(b, ensure_ascii=False))
        return p

    def test_missing_plan_stops_before_remote_calls(self):
        b = copy.deepcopy(self.b)
        b['segments'] = []
        b.pop('creative_plan')
        self.save(b)
        with patch.object(sys, 'argv', ['draft.py', '--config', 'qingyang', '--run', str(self.run)]), patch.object(cb, 'api') as api:
            with self.assertRaisesRegex(SystemExit, '先完成组内创作规划'):
                draft.main()
            api.assert_not_called()

    def test_same_path_is_rejected_but_user_template_is_respected(self):
        other = copy.deepcopy(self.b)
        other['brief_id'] = '1105-B02'
        other['creative_plan']['angle'] = '另一个标签不能冒充另一套思路'
        self.assertEqual(set(plan_batch_issues([self.b, other])), {'1105-B01', '1105-B02'})
        self.assertIn('相同的整篇思路', str(lint_day([self.b, other], self.cfg)))
        for b in [self.b, other]: b['template'] = '用户明确指定的同一结构'
        self.assertFalse(plan_batch_issues([self.b, other]))

    def test_requirements_fit_all_products_without_losing_facts(self):
        for brand in ['qingyang', 'lux']:
            cfg = load_config(brand)
            for key, prod in cfg['products'].items():
                b = copy.deepcopy(self.b)
                b.update(brand=brand, product_key=key, segments=[], selling_point=next(iter(prod['selling_points'])))
                req = draft.requirement(cfg, self.run, b, None, [b])
                self.assertLessEqual(len(req), 2000, (brand, key))
                self.assertIn(prod['full_name'], req)
                self.assertIn(b['creative_plan']['throughline'], req)
                self.assertIn(prod['rules'][-1], req)
                self.assertIn('为何这样讲=', req)
                self.assertNotIn('尽量第 3 段才出现', req)
                if prod['mechanism']['allowed_numbers']:
                    for mechanism in prod['mechanism']['spoken']:
                        self.assertIn(mechanism, req)

    def test_overlong_mandatory_requirements_stop_before_deleting_existing_script(self):
        b = copy.deepcopy(self.b)
        b['creative_plan']['throughline'] = '需要精简的必要信息' * 400
        b['script_id'] = 'existing-script'
        self.save(b)
        with patch.object(sys, 'argv', ['draft.py', '--config', 'qingyang', '--run', str(self.run), '--redo']), patch.object(cb, 'api') as api:
            with self.assertRaisesRegex(ValueError, '不能截断事实'):
                draft.main()
            api.assert_not_called()

    def test_references_include_rationale_and_rotate(self):
        refs = [{'id': f'g{i}', 'status': 'confirmed', 'speaker': f'立场{i}', 'why': f'推理依据{i}',
                 'beats': [{'text': f'示例正文{i}'}]} for i in range(3)]
        reqs = []
        with patch.object(draft, 'load_gold', return_value=refs):
            for i in range(1, 4):
                b = brief(f'1105-B{i:02d}')
                reqs.append(draft.requirement(self.cfg, self.run, b, None, [b]))
        for i, req in enumerate(reqs):
            self.assertIn(f'谁在说=立场{i}；为何这样讲=推理依据{i}', req)

    def test_new_draft_records_plan_but_not_editorial_or_human_approval(self):
        b = copy.deepcopy(self.b)
        b['segments'] = []
        self.save(b)
        remote = {'segments': [{'title': x['title'], 'content': x['text']} for x in self.b['segments']]}
        calls = []
        def api(method, path, body=None, **kwargs):
            calls.append((method, path, body))
            if method == 'POST' and path == '/video-script-generations': return {'id': 'generation'}
            if method == 'GET' and path == '/video-scripts/created': return copy.deepcopy(remote)
            if method == 'PUT' and path == '/video-scripts/created': return {}
            self.fail((method, path))
        with patch.object(sys, 'argv', ['draft.py', '--config', 'qingyang', '--run', str(self.run)]), patch.object(cb, 'api', side_effect=api), patch.object(draft, 'wait_generation', return_value={'status': 'success', 'script_id': 'created'}), redirect_stdout(io.StringIO()):
            draft.main()
        got = json.loads((self.run/'briefs'/'1105-B01.json').read_text())
        self.assertEqual(got['copy_logic_version'], 2)
        self.assertEqual(got['creative_plan'], b['creative_plan'])
        self.assertEqual(got['status'], 'draft')
        self.assertNotIn('approval', got)
        self.assertNotIn('editorial_review', got)
        self.assertIn('尚未完成整篇审读', str(lint_brief(got, self.cfg)))
        self.assertIn('先把下面的思路写成', calls[0][2]['requirement'])

    def test_editorial_record_is_bound_to_copy_and_plan_without_approval(self):
        p = self.save(self.b)
        a = self.run/'assessment.json'
        a.write_text(json.dumps(assessment(self.b), ensure_ascii=False))
        with redirect_stdout(io.StringIO()): review_doc.editorial(self.run, self.b['brief_id'], a)
        b = json.loads(p.read_text())
        self.assertFalse(editorial_issues(b))
        self.assertEqual(b['status'], 'draft')
        self.assertNotIn('approval', b)
        changed = copy.deepcopy(b)
        changed['segments'][0]['text'] += '换一句。'
        self.assertIn('需要重新审读', str(editorial_issues(changed)))
        changed = copy.deepcopy(b)
        changed['creative_plan']['angle'] = '换了写作目的'
        self.assertIn('需要重新审读', str(editorial_issues(changed)))

    def test_incomplete_editorial_boundaries_are_rejected(self):
        p = self.save(self.b)
        before = p.read_bytes()
        a = self.run/'assessment.json'
        review = assessment(self.b)
        review['transitions'].pop()
        a.write_text(json.dumps(review))
        with self.assertRaisesRegex(SystemExit, '相邻分段如何承接'):
            review_doc.editorial(self.run, self.b['brief_id'], a)
        self.assertEqual(p.read_bytes(), before)

    def test_sync_preserves_visuals_and_adds_only_per_script_context(self):
        remote = {'product_id': 'p', 'video': {'width': 1080}, 'config': {'bgm': 'original'},
                  'vo_rewrite_prompt': '原有作者要求',
                  'segments': [{'id': str(i), 'content': 'old', 'visual': {'tag': f'素材{i}'}, 'sticker': {'keep': True}} for i in range(1, 5)]}
        before = copy.deepcopy(remote)
        def api(method, path, body=None, **kwargs):
            self.assertEqual(path, '/video-scripts/s')
            if method == 'PUT': remote.update(copy.deepcopy(body)); return {}
            return copy.deepcopy(remote)
        with patch.object(cb, 'api', side_effect=api):
            cb.sync_script('s', self.b, 'title')
            cb.sync_script('s', self.b, 'title')
        self.assertTrue(remote['vo_rewrite_prompt'].startswith('原有作者要求'))
        self.assertEqual(remote['vo_rewrite_prompt'].count('【本条整篇改写要求】'), 1)
        self.assertIn(self.b['creative_plan']['throughline'], remote['vo_rewrite_prompt'])
        self.assertEqual(remote['video'], before['video'])
        self.assertEqual(remote['config'], before['config'])
        for new, old, approved in zip(remote['segments'], before['segments'], self.b['segments']):
            self.assertEqual(new['visual'], old['visual'])
            self.assertEqual(new['sticker'], old['sticker'])
            self.assertTrue(new['ai_rewrite'])
            self.assertEqual(new['content'], approved['text'])

    def test_sync_stops_if_platform_drops_whole_copy_context(self):
        remote = {'segments': [{'content': s['text'], 'ai_rewrite': True} for s in self.b['segments']]}
        def api(method, path, body=None, **kwargs):
            if method == 'PUT':
                remote.update({k: v for k, v in body.items() if k != 'vo_rewrite_prompt'})
                return {}
            return copy.deepcopy(remote)
        with patch.object(cb, 'api', side_effect=api), self.assertRaisesRegex(RuntimeError, '没有保存本条整篇改写要求'):
            cb.sync_script('s', self.b, 'title')

    def test_review_shows_whole_copy_and_editable_segments(self):
        self.save(self.b)
        with redirect_stdout(io.StringIO()): review_doc.render(self.cfg, self.run)
        md = (self.run/'review.md').read_text()
        xml = (self.run/'review.xml').read_text()
        self.assertIn(''.join(s['text'] for s in self.b['segments']), md)
        self.assertIn(self.b['creative_plan']['throughline'], md)
        table = xml[xml.index('<table>'):]
        rows = parse_table(table)
        self.assertEqual(len(rows[0]), len(review_doc.HEAD))
        self.assertEqual(len(rows[1]), len(review_doc.HEAD))
        cell = rows[1][review_doc.HEAD.index('文案')]
        self.assertEqual([s['text'] for s in review_doc.parse_copy(cell)], [s['text'] for s in self.b['segments']])
        self.assertNotIn('CTA 段不改', xml)

    def test_old_human_approval_is_not_invalidated_by_installation(self):
        b = copy.deepcopy(self.b)
        b.pop('copy_logic_version')
        b.pop('creative_plan')
        b.update(status='approved', approval={'by': '用户', 'copy_hash': copy_hash(b)})
        self.assertIsNone(approval_problem(b))
        self.assertFalse(editorial_issues(b))

    def test_topup_cannot_bypass_stale_whole_copy_review(self):
        b = copy.deepcopy(self.b)
        b.update(status='approved', approval={'by': '用户', 'copy_hash': copy_hash(b)})
        b['editorial_review'] = {**assessment(b), 'copy_hash': copy_hash(b), 'plan_hash': plan_hash(b)}
        b['creative_plan']['angle'] = '审读后修改了创作计划'
        self.save(b)
        (self.run/'clipo').mkdir()
        (self.run/'clipo'/'state.json').write_text(json.dumps({
            'videos': {}, 'briefs': {b['brief_id']: {'script_id': 's', 'texts': [s['text'] for s in b['segments']]}}}))
        args = SimpleNamespace(brief=None, dry_run=False, max_per_brief=20, margin=1.2)
        with patch.object(cb, 'CFG', self.cfg), patch.object(cb, 'api') as api, redirect_stdout(io.StringIO()) as output:
            cb.cmd_topup(self.cfg, self.run, args)
        api.assert_not_called()
        self.assertIn('整篇审读问题', output.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=2)
