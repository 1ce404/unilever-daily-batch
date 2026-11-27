import ast
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))

import checked_batch_authorization as gate


class CheckedBatchAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.brief = {
            'brief_id': '1127-L02', 'quantity': 10, 'status': 'draft',
            'segments': [{'id': '1', 'role': 'hook', 'text': '完整文案',
                          'alt': ['备选一', '', '备选二'],
                          'visual_tags': ['产品外观', 'AI-1127-示例'],
                          'footage_pool': {'tags': ['AI-1127-示例']}}],
        }
        self.keys = [f'1127-L02-V{i:02d}' for i in range(2, 11)]
        self.demo = {'video_key': '1127-L02-V01', 'render_job': 'job-current',
                     'object_key': 'derivatives/demo-current.mp4'}
        self.evidence = {}
        for name in gate.REQUIRED_CHECKS:
            path = self.root / (name + '.json')
            path.write_text(json.dumps({'check': name, 'observed': 'fixture evidence'}))
            self.evidence[name] = [self.ref(path)]
        self.receipt = {
            'schema_version': 1, 'checked_by': 'Agent', 'brief_id': '1127-L02',
            'copy_hash': gate._copy_hash(self.brief), 'ai_tags': gate._ai_tags(self.brief),
            'current_demo': copy.deepcopy(self.demo), 'max_new': 9,
            'exact_video_keys': self.keys.copy(),
            'checks': {name: {'passed': True, 'evidence': copy.deepcopy(refs)}
                       for name, refs in self.evidence.items()},
        }
        self.brief['checked_batch_authorization'] = {
            'mode': 'check_then_bulk', 'by': '用户', 'instruction': '确认所有内容，没问题后批量',
            'brief_id': '1127-L02', 'copy_hash': gate._copy_hash(self.brief),
            'ai_tags': gate._ai_tags(self.brief), 'max_new': 9,
            'exact_video_keys': self.keys.copy(),
        }
        self.brief['checked_batch_context'] = {
            'current_demo': copy.deepcopy(self.demo), 'requested_new': 9,
            'requested_video_keys': self.keys.copy(),
            'current_evidence': copy.deepcopy(self.evidence),
        }
        self.save_receipt()

    def ref(self, path):
        return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    def save_receipt(self):
        path = self.root / 'receipt.json'
        path.write_text(json.dumps(self.receipt, ensure_ascii=False))
        auth = self.brief['checked_batch_authorization']
        auth.update(check_receipt_path=str(path), check_receipt_sha256=self.ref(path)['sha256'])

    def test_success_without_any_human_approval_and_no_mutation(self):
        before = copy.deepcopy(self.brief)
        files_before = {str(p): p.read_bytes() for p in self.root.iterdir()}
        self.assertTrue(gate.permits_checked_batch(self.brief))
        self.assertEqual(self.brief, before)
        self.assertEqual({str(p): p.read_bytes() for p in self.root.iterdir()}, files_before)
        self.assertNotIn('approval', self.brief)
        self.assertNotIn('demo_approval', self.brief)
        self.assertEqual(self.brief['status'], 'draft')

    def test_no_authorization_even_if_ordinary_approval_exists(self):
        del self.brief['checked_batch_authorization']
        self.brief.update(status='approved', approval={'by': '用户'}, demo_approval={'by': '用户'})
        self.assertFalse(gate.permits_checked_batch(self.brief))

    def test_missing_or_false_checks_fail(self):
        for name in gate.REQUIRED_CHECKS:
            for value in [False, None, 'true', 1]:
                with self.subTest(name=name, value=value):
                    self.receipt['checks'][name]['passed'] = value
                    self.save_receipt()
                    self.assertFalse(gate.permits_checked_batch(self.brief))
            self.receipt['checks'][name]['passed'] = True
        for name in gate.REQUIRED_CHECKS:
            item = self.receipt['checks'].pop(name)
            self.save_receipt()
            self.assertFalse(gate.permits_checked_batch(self.brief))
            self.receipt['checks'][name] = item

    def test_missing_evidence_and_empty_list_fail(self):
        for value in [[], None]:
            self.receipt['checks']['visual_review']['evidence'] = value
            self.save_receipt()
            self.assertFalse(gate.permits_checked_batch(self.brief))

    def test_stale_copy_text_alt_role_and_silent_footage_fail(self):
        original = copy.deepcopy(self.brief)
        for field, value in [('text', '修改后的文案'), ('alt', '新备选'), ('role', 'product')]:
            self.brief = copy.deepcopy(original)
            self.brief['segments'][0][field] = value
            self.assertFalse(gate.permits_checked_batch(self.brief))
        self.brief = copy.deepcopy(original)
        self.brief['segments'][0].update(silent=True, footage={'segment_id': 'changed'})
        self.assertFalse(gate.permits_checked_batch(self.brief))

    def test_current_ai_tags_change_even_when_copy_hash_does_not(self):
        old = gate._copy_hash(self.brief)
        self.brief['segments'][0]['visual_tags'].append('AI-1127-新增')
        self.assertEqual(old, gate._copy_hash(self.brief))
        self.assertFalse(gate.permits_checked_batch(self.brief))

    def test_receipt_raw_bytes_tampered(self):
        Path(self.brief['checked_batch_authorization']['check_receipt_path']).write_text('{}')
        self.assertFalse(gate.permits_checked_batch(self.brief))

    def test_evidence_file_tampered_deleted_empty_or_directory(self):
        path = Path(self.evidence['visual_review'][0]['path'])
        old = path.read_bytes()
        path.write_text('modified')
        self.assertFalse(gate.permits_checked_batch(self.brief))
        path.write_bytes(b'')
        self.assertFalse(gate.permits_checked_batch(self.brief))
        path.unlink()
        self.assertFalse(gate.permits_checked_batch(self.brief))
        path.mkdir()
        self.assertFalse(gate.permits_checked_batch(self.brief))
        path.rmdir()
        path.write_bytes(old)
        self.assertTrue(gate.permits_checked_batch(self.brief))

    def test_current_demo_render_or_object_change_denies_old_receipt(self):
        current = self.brief['checked_batch_context']['current_demo']
        for name in ['render_job', 'object_key', 'video_key']:
            old = current[name]
            current[name] = 'changed'
            self.assertFalse(gate.permits_checked_batch(self.brief))
            current[name] = old

    def test_current_evidence_snapshot_change_denies_old_receipt(self):
        path = self.root / 'new_config.json'
        path.write_text('current different configuration')
        self.brief['checked_batch_context']['current_evidence']['configuration_and_resource_counts'] = [self.ref(path)]
        self.assertFalse(gate.permits_checked_batch(self.brief))

    def test_expanded_quantity_or_authorization_or_actual_request_denied(self):
        original = copy.deepcopy(self.brief)
        self.brief['quantity'] = 11
        self.assertFalse(gate.permits_checked_batch(self.brief))
        self.brief = copy.deepcopy(original)
        self.brief['checked_batch_authorization']['max_new'] = 10
        self.assertFalse(gate.permits_checked_batch(self.brief))
        self.brief = copy.deepcopy(original)
        self.brief['checked_batch_context']['requested_new'] = 10
        self.assertFalse(gate.permits_checked_batch(self.brief))
        self.brief = copy.deepcopy(original)
        self.brief['checked_batch_context']['requested_video_keys'].append('1127-L02-V11')
        self.assertFalse(gate.permits_checked_batch(self.brief))

    def test_keys_with_other_brief_or_shifted_offset_or_duplicates_fail(self):
        for keys in [['1127-L03-V02'] + self.keys[1:],
                     [f'1127-L02-V{i:02d}' for i in range(3, 12)],
                     self.keys[:-1] + self.keys[:1]]:
            self.brief['checked_batch_authorization']['exact_video_keys'] = keys
            self.assertFalse(gate.permits_checked_batch(self.brief))

    def test_other_brief_in_receipt_or_authorization_denied(self):
        self.receipt['brief_id'] = '1127-L03'
        self.save_receipt()
        self.assertFalse(gate.permits_checked_batch(self.brief))
        self.receipt['brief_id'] = '1127-L02'
        self.save_receipt()
        self.brief['checked_batch_authorization']['brief_id'] = '1127-L03'
        self.assertFalse(gate.permits_checked_batch(self.brief))

    def test_no_context_or_incomplete_current_demo_denied(self):
        ctx = self.brief.pop('checked_batch_context')
        self.assertFalse(gate.permits_checked_batch(self.brief))
        self.brief['checked_batch_context'] = ctx
        del ctx['current_demo']['object_key']
        self.assertFalse(gate.permits_checked_batch(self.brief))

    def test_agent_cannot_impersonate_user_and_receipt_not_human_approval(self):
        self.brief['checked_batch_authorization']['by'] = 'Agent'
        self.assertFalse(gate.permits_checked_batch(self.brief))
        self.brief['checked_batch_authorization']['by'] = '用户'
        self.receipt['checked_by'] = '用户'
        self.save_receipt()
        self.assertFalse(gate.permits_checked_batch(self.brief))

    def test_missing_user_quote_or_wrong_mode_denied(self):
        auth = self.brief['checked_batch_authorization']
        for field, value in [('instruction', ''), ('instruction', '  '), ('mode', 'demo_only')]:
            old = auth[field]
            auth[field] = value
            self.assertFalse(gate.permits_checked_batch(self.brief))
            auth[field] = old

    def test_bool_float_counts_are_not_integers(self):
        for value in [True, 10.0, '10', 0, 1, -1]:
            self.brief['quantity'] = value
            self.assertFalse(gate.permits_checked_batch(self.brief))

    def test_malformed_inputs_fail_closed(self):
        for value in [None, [], {}, {'segments': [None]}, {'segments': 'bad'}]:
            self.assertFalse(gate.permits_checked_batch(value))
        self.brief['segments'][0]['visual_tags'] = [None]
        self.assertFalse(gate.permits_checked_batch(self.brief))

    def test_invalid_receipt_json_with_valid_hash_fails(self):
        path = Path(self.brief['checked_batch_authorization']['check_receipt_path'])
        for data in [b'not json', b'[]', b'{"schema_version":1,"schema_version":1}']:
            path.write_bytes(data)
            self.brief['checked_batch_authorization']['check_receipt_sha256'] = self.ref(path)['sha256']
            self.assertFalse(gate.permits_checked_batch(self.brief))

    def test_relative_evidence_or_receipt_path_denied(self):
        self.brief['checked_batch_authorization']['check_receipt_path'] = 'receipt.json'
        self.assertFalse(gate.permits_checked_batch(self.brief))
        self.save_receipt()
        for target in [self.receipt['checks']['visual_review']['evidence'][0],
                       self.brief['checked_batch_context']['current_evidence']['visual_review'][0]]:
            target['path'] = 'relative.json'
        self.save_receipt()
        self.assertFalse(gate.permits_checked_batch(self.brief))

    def test_receipt_hash_and_binding_fields_changed_even_with_rehash(self):
        original = copy.deepcopy(self.receipt)
        for field, value in [('copy_hash', 'stale'), ('ai_tags', []), ('max_new', 10),
                             ('exact_video_keys', []), ('schema_version', True)]:
            self.receipt = copy.deepcopy(original)
            self.receipt[field] = value
            self.save_receipt()
            self.assertFalse(gate.permits_checked_batch(self.brief))

    def test_standalone_minimal_fixture(self):
        from build_minimal_fixture import build_fixture
        b = build_fixture(self.root / 'standalone')
        before = copy.deepcopy(b)
        self.assertTrue(gate.permits_checked_batch(b))
        self.assertEqual(b, before)
        b['checked_batch_context']['current_demo']['render_job'] = 'new-render'
        self.assertFalse(gate.permits_checked_batch(b))

    def test_current_common_fingerprint_parity_without_importing_common(self):
        path = Path(__file__).resolve().parents[1]/'scripts/common.py'
        if not path.is_file():
            self.skipTest('Owning source not installed on this host')
        tree = ast.parse(path.read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in {'seg_alts', 'copy_hash', 'aigc_tags'}]
        reference = {}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), reference)
        for alt in [[], ['备选', '', None], '单备选']:
            for silent in [False, True]:
                b = copy.deepcopy(self.brief)
                b['segments'][0].update(alt=alt, silent=silent, footage={'segment_id': 'a'})
                self.assertEqual(gate._copy_hash(b), reference['copy_hash'](b))
                self.assertEqual(gate._ai_tags(b), reference['aigc_tags'](b))


if __name__ == '__main__':
    unittest.main()
