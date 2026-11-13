"""Structural AIGC binding checks; no frame, face, voice or content review."""
import hashlib
import json
import math
from urllib.parse import urlparse


def required(brief):
    return bool(brief.get("within_folder_material_diversity_required") or
                brief.get("aigc_material_sets_per_delivery", 0) > 1)


def object_key(value):
    return urlparse(value).path.lstrip("/") if "://" in value else value.split("?", 1)[0]


def group_for(brief, video_key, video):
    matches = [x for x in brief.get("visual_sets", [])
               if x.get("video_key") == video_key or x.get("cut_id") == video.get("cut_id")]
    if len(matches) != 1:
        raise RuntimeError(f"{video_key}: 需要唯一的visual_sets素材组映射")
    group = matches[0]
    if group.get("cut_id") and group["cut_id"] != video["cut_id"]:
        raise RuntimeError(f"{video_key}: 素材组cut_id与实际剪辑不一致")
    return group


def segments(group):
    rows = group.get("segments") or group.get("clips") or []
    mapped = {str(x["segment"]): x for x in rows}
    if len(rows) != 3 or set(mapped) != {"1", "2", "4"}:
        raise RuntimeError("人物素材组必须唯一对应分镜1、2、4")
    keys = [object_key(x.get("object_key") or x.get("src") or "") for x in mapped.values()]
    if not all(keys) or len(set(keys)) != 3:
        raise RuntimeError("三个分镜必须使用三个不同动作切片，不能重复同一切片")
    if not group.get("retained_user_approved_demo"):
        end = -1.0
        for n in ("1", "2", "4"):
            span = mapped[n].get("source_interval")
            if not span or len(span) != 2 or not all(math.isfinite(float(v)) for v in span):
                raise RuntimeError("新增素材必须记录每镜的有效源区间")
            a, b = map(float, span)
            if a < 0 or b <= a or a < end - 1e-5:
                raise RuntimeError("人物分镜源区间倒序、无效或重叠")
            end = b
    return mapped


def source_identity(group):
    rows = segments(group)
    source = group.get("source_object_key") or group.get("source_url")
    if source:
        return "object:" + object_key(source)
    if group.get("source_generation_id"):
        return "generation:" + group["source_generation_id"]
    if group.get("retained_user_approved_demo"):
        return "approved-demo:" + "|".join(object_key(rows[n].get("object_key") or rows[n]["src"])
                                               for n in ("1", "2", "4"))
    raise RuntimeError("新增素材缺少实际source_generation_id或源对象键，不能只靠素材组名称区分")


def validate_selection(briefs, videos, selection):
    seen = {}
    for bid, keys in selection.items():
        brief = briefs[bid]
        if not required(brief):
            continue
        for key in keys:
            group = group_for(brief, key, videos[key])
            identity = source_identity(group)
            if identity in seen:
                raise RuntimeError(f"{key}与{seen[identity]}复用同一人物源素材；不能作为不同素材交付")
            seen[identity] = key
    return len(seen)


def validate_draft(group, draft):
    fps = draft["composition"].get("fps", 30)
    for n, row in segments(group).items():
        actual = [c for c in draft["clips"] if c.get("trackId") == "main" and
                  str(c.get("id", "")).split("-")[0] == n]
        if len(actual) != 1:
            raise RuntimeError(f"分镜{n}应当只播放一个专属人物切片")
        clip = actual[0]
        props = clip["props"]
        expected = object_key(row.get("object_key") or row["src"])
        if object_key(props.get("src") or "") != expected:
            raise RuntimeError(f"分镜{n}实际draft未绑定计划素材")
        rate = float(props.get("playbackRate", 1))
        start = float(props.get("startFrom", 0)) / fps
        if props.get("loop") or not math.isfinite(rate) or rate <= 0 or start < 0:
            raise RuntimeError(f"分镜{n}存在循环或无效播放区间")
        if row.get("duration") and start + clip["durationInFrames"] / fps * rate > row["duration"] + 2 / fps:
            raise RuntimeError(f"分镜{n}播放超出源时长，可能定格补时长")


def render_key(cut_id, group):
    payload = {"source": source_identity(group), "clips": segments(group),
               "revision": group.get("render_revision", 1)}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]
    return f"udb-aigc-{cut_id}-{digest}"


def user_review_instruction(state, brief):
    policy = brief.get("content_review") or state.get("content_review") or {}
    if policy.get("mode") == "user":
        instruction = (policy.get("instruction") or "").strip()
        if not instruction:
            raise RuntimeError("用户自行检查模式必须记录实际用户原话")
        return instruction
    return None


def release_for_user(video, instruction):
    video.update(stage="done", review_waived=instruction, user_review_pending=True,
                 content_review_status="user_review_pending")
