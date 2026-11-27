"""Read-only, fail-closed gate for a user's conditional batch authorization.

The caller supplies CURRENT authoritative demo/evidence/request snapshots through
brief['checked_batch_context'] on a temporary copy. This function cannot discover
external state, establish who wrote an authorization, or judge evidence contents.
No human approval/status fields are read or written. No API or State dependency.
"""
import hashlib
import json
from pathlib import Path
import re

REQUIRED_CHECKS = (
    'configuration_and_resource_counts',
    'voice_resources_and_timeline',
    'visual_review',
    'rendered_media_technical',
    'rewrite_plan',
    'people_diversity_plan',
    'packaging_diversity_plan',
)
_SHA256 = re.compile(r'[0-9a-f]{64}')


def _copy_hash(brief):
    """Exact common.copy_hash/seg_alts semantics read on 2026-11-27."""
    def alts(segment):
        values = segment.get('alt') or []
        return [values] if isinstance(values, str) else [x for x in values if x]
    body = '\n'.join(
        f"{x.get('id')}|{x.get('role')}|{x.get('text', '')}"
        + ''.join(f'|{a}' for a in alts(x))
        + (f"|footage:{(x.get('footage') or {}).get('segment_id')}"
           if x.get('silent') else '')
        for x in brief.get('segments') or []
    )
    return hashlib.sha1(body.encode()).hexdigest()[:12]


def _ai_tags(brief):
    """Exact common.aigc_tags semantics read on 2026-11-27."""
    return sorted({
        t for sg in brief.get('segments') or []
        for t in ((sg.get('visual_tags') or [])
                  + ((sg.get('footage_pool') or {}).get('tags') or []))
        if t.startswith('AI')
    })


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _artifact_bytes(reference):
    """Only read an absolute regular file with matching raw-byte SHA256."""
    if not isinstance(reference, dict):
        return None
    name, digest = reference.get('path'), reference.get('sha256')
    if not _text(name) or not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        return None
    path = Path(name)
    if not path.is_absolute() or not path.is_file():
        return None
    data = path.read_bytes()
    if not data or hashlib.sha256(data).hexdigest() != digest:
        return None
    return data


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def permits_checked_batch(brief):
    """Return bool; malformed, missing, changed or failed evidence denies.

    Required local schema is documented in README.txt and exercised by tests.
    requested_new/keys belong to THIS operation, not an old budget snapshot.
    Evidence files must also match fresh caller-supplied context, so an old
    receipt and an old authorization alone cannot attest a new demo/config.
    """
    try:
        return _permits(brief)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return False


def _permits(brief):
    if not isinstance(brief, dict) or not brief.get('segments'):
        return False
    bid, quantity = brief.get('brief_id'), brief.get('quantity')
    if not _text(bid) or type(quantity) is not int or not 2 <= quantity <= 10:
        return False
    # This task authorizes at most V02..V10; larger quotas need another policy.
    count = quantity - 1
    keys = [f'{bid}-V{i:02d}' for i in range(2, quantity + 1)]
    fingerprint, tags = _copy_hash(brief), _ai_tags(brief)
    auth = brief.get('checked_batch_authorization')
    ctx = brief.get('checked_batch_context')
    if not isinstance(auth, dict) or not isinstance(ctx, dict):
        return False
    if (auth.get('mode') != 'check_then_bulk' or auth.get('by') != '用户'
            or not _text(auth.get('instruction'))
            or auth.get('brief_id') != bid
            or auth.get('copy_hash') != fingerprint or auth.get('ai_tags') != tags
            or type(auth.get('max_new')) is not int or auth['max_new'] != count
            or auth.get('exact_video_keys') != keys):
        return False
    if (type(ctx.get('requested_new')) is not int or ctx['requested_new'] != count
            or ctx.get('requested_video_keys') != keys):
        return False
    demo = ctx.get('current_demo')
    if (not isinstance(demo, dict) or set(demo) != {'video_key', 'render_job', 'object_key'}
            or demo.get('video_key') != f'{bid}-V01'
            or not _text(demo.get('render_job')) or not _text(demo.get('object_key'))):
        return False
    data = _artifact_bytes({'path': auth.get('check_receipt_path'),
                            'sha256': auth.get('check_receipt_sha256')})
    if data is None:
        return False
    receipt = json.loads(data, object_pairs_hook=_unique_object)
    if (not isinstance(receipt, dict) or receipt.get('schema_version') != 1
            or type(receipt.get('schema_version')) is not int
            or receipt.get('checked_by') != 'Agent' or receipt.get('brief_id') != bid
            or receipt.get('copy_hash') != fingerprint or receipt.get('ai_tags') != tags
            or receipt.get('current_demo') != demo
            or type(receipt.get('max_new')) is not int or receipt['max_new'] != count
            or receipt.get('exact_video_keys') != keys):
        return False
    checks, current = receipt.get('checks'), ctx.get('current_evidence')
    required = set(REQUIRED_CHECKS)
    if (not isinstance(checks, dict) or set(checks) != required
            or not isinstance(current, dict) or set(current) != required):
        return False
    for name in REQUIRED_CHECKS:
        item = checks[name]
        if not isinstance(item, dict) or item.get('passed') is not True:
            return False
        refs = item.get('evidence')
        if not isinstance(refs, list) or not refs or refs != current[name]:
            return False
        for ref in refs:
            if _artifact_bytes(ref) is None:
                return False
    return True
