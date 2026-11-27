"""Build a SYNTHETIC unit-test fixture; never a production authorization."""
import hashlib
import json
from pathlib import Path

from checked_batch_authorization import REQUIRED_CHECKS, _copy_hash, _ai_tags


def build_fixture(directory):
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    b = {'brief_id': 'FIXTURE-L02', 'quantity': 10, 'status': 'draft',
         'segments': [{'id': '1', 'role': 'hook', 'text': '仅供单元测试的虚构文案',
                       'visual_tags': ['AI-FIXTURE-仅测试']}]}
    demo = {'video_key': 'FIXTURE-L02-V01', 'render_job': 'FIXTURE-FAKE-RENDER',
            'object_key': 'FIXTURE/NOT-A-REAL-VIDEO.mp4'}
    keys = [f'FIXTURE-L02-V{i:02d}' for i in range(2, 11)]
    evidence = {}
    for name in REQUIRED_CHECKS:
        path = root / f'{name}.json'
        path.write_text(json.dumps({'fixture_only': True, 'check': name,
                                    'note': 'Synthetic test data; no real check or authorization.'}))
        evidence[name] = [{'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]
    receipt = {'schema_version': 1, 'checked_by': 'Agent', 'brief_id': b['brief_id'],
               'copy_hash': _copy_hash(b), 'ai_tags': _ai_tags(b),
               'current_demo': demo, 'max_new': 9, 'exact_video_keys': keys,
               'checks': {k: {'passed': True, 'evidence': v} for k, v in evidence.items()}}
    path = root / 'receipt.json'
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    b['checked_batch_authorization'] = {
        'mode': 'check_then_bulk', 'by': '用户',
        'instruction': 'FIXTURE ONLY: 测试用户条件授权，非真实生产指令。',
        'brief_id': b['brief_id'], 'copy_hash': _copy_hash(b), 'ai_tags': _ai_tags(b),
        'max_new': 9, 'exact_video_keys': keys, 'check_receipt_path': str(path),
        'check_receipt_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    b['checked_batch_context'] = {'current_demo': demo, 'requested_new': 9,
                                  'requested_video_keys': keys, 'current_evidence': evidence}
    (root / 'brief.json').write_text(json.dumps(b, ensure_ascii=False, indent=2))
    return b


if __name__ == '__main__':
    from checked_batch_authorization import permits_checked_batch
    brief = build_fixture(Path(__file__).parent / 'example-fixture')
    print('Synthetic fixture only; permits_checked_batch =', permits_checked_batch(brief))
