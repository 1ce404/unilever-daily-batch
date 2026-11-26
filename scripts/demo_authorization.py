#!/usr/bin/env python3
"""Bounded permission to produce a demo before customer review; never an approval."""
def permits_preview(brief):
    from common import copy_hash, aigc_tags
    a = brief.get("demo_production_authorization") or {}
    return (brief.get("status") == "draft" and brief.get("demo_first", True) is True
            and not brief.get("approval") and not brief.get("demo_approval")
            and a.get("mode") == "demo_only_before_review" and a.get("by") == "用户"
            and isinstance(a.get("instruction"), str) and bool(a["instruction"].strip())
            and a.get("quantity") == 1 and type(a.get("quantity")) is int
            and a.get("copy_hash") == copy_hash(brief)
            and sorted(a.get("ai_tags") or []) == aigc_tags(brief)
            and a.get("review_pending") is True)


def preview_ai_ids(brief, cfg, video_key):
    if not permits_preview(brief) or video_key != brief["brief_id"] + "-V01":
        return []
    ids = (cfg.get("preview_ai_hook_ids") or {}).get(brief["brief_id"], [])
    return list(ids) if isinstance(ids, list) and len(ids) == 1 and isinstance(ids[0], str) else []
