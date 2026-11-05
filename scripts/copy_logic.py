"""Whole-copy planning and editorial evidence; this never substitutes for human approval."""
import json
import hashlib
from collections import defaultdict

from common import copy_hash, normalize_copy

PLAN_FIELDS = ("speaker", "audience_question", "angle", "throughline", "ending_bridge")


def plan_hash(brief):
    raw = json.dumps(brief.get("creative_plan") or {}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def plan_issues(brief):
    plan = brief.get("creative_plan") or {}
    if not isinstance(plan, dict):
        return [("error", "creative_plan 必须是对象")]
    missing = [k for k in PLAN_FIELDS if not isinstance(plan.get(k), str) or not plan[k].strip()]
    route = plan.get("route")
    if not isinstance(route, list) or len(route) < 2 or any(not isinstance(s, str) or not s.strip() for s in route):
        missing.append("route（至少两个有先后关系的具体思路，不是镜头/卖点标签）")
    if missing:
        return [("error", "先写这条的创作思路 creative_plan，缺少：" + "、".join(missing))]
    return []


def plan_batch_issues(briefs):
    """Only catch identical declared paths; semantic diversity still needs editorial judgment."""
    groups = defaultdict(list)
    for b in briefs:
        if b.get("template") or plan_issues(b):
            continue
        signature = tuple(normalize_copy(s) for s in b["creative_plan"]["route"])
        groups[(b.get("product_key"), signature)].append(b)
    out = {}
    for group in groups.values():
        if len(group) > 1:
            for b in group:
                others = "、".join(o["brief_id"] for o in group if o is not b)
                out[b["brief_id"]] = [("error", f"和 {others} 使用了相同的整篇思路；换核心疑问/推理路径，不能只换开头")]
    return out


def editorial_issues(brief):
    if brief.get("copy_logic_version", 0) < 2:
        return []  # Do not invalidate historical approvals merely by installing the update.
    issues = plan_issues(brief)
    review = brief.get("editorial_review") or {}
    if not isinstance(review, dict):
        review = {}
    if review.get("verdict") != "pass":
        return issues + [("error", "尚未完成整篇审读：用 review_doc.py editorial 记录判断，之后再交人审")]
    if review.get("copy_hash") != copy_hash(brief) or review.get("plan_hash") != plan_hash(brief):
        issues.append(("error", "文案或创作思路在整篇审读后改过，需要重新审读"))
    for field in ("throughline", "voice", "distinctness"):
        if not isinstance(review.get(field), str) or not review[field].strip():
            issues.append(("error", f"整篇审读缺少 {field} 的具体判断"))
    segs = brief.get("segments") or []
    if not segs:
        issues.append(("error", "还没有实际文案，不能记录整篇审读"))
    expected = [(str(a["id"]), str(b["id"])) for a, b in zip(segs, segs[1:])]
    links = review.get("transitions") or []
    if not isinstance(links, list) or any(not isinstance(x, dict) for x in links):
        links = []
    got = [(str(x.get("from")), str(x.get("to"))) for x in links]
    if got != expected or any(not isinstance(x.get("link"), str) or not x["link"].strip() for x in links):
        issues.append(("error", "整篇审读要逐一解释相邻分段如何承接，包含结尾为何进入 CTA"))
    return issues


def plan_lines(brief):
    p = brief.get("creative_plan") or {}
    if not p or plan_issues(brief):
        return []
    return [f"谁在说：{p['speaker']}", f"观众的疑问：{p['audience_question']}",
            f"本条切入：{p['angle']}", f"整篇主张：{p['throughline']}",
            "思路推进：" + " → ".join(p["route"]), f"收尾理由：{p['ending_bridge']}"]


def rewrite_context(brief):
    """Per-script author instructions, not a mutation of the shared product prompt."""
    if brief.get("copy_logic_version", 0) < 2 or plan_issues(brief):
        return ""
    return ("【本条整篇改写要求】\n" + "\n".join(plan_lines(brief)) +
            "\n先通读全部段落，写成同一人围绕同一个问题讲完的一段话，再按原 id 一一回填。"
            "保留论点、指代和前后承接，前句提出的问题后句要接住；不新增分段，不强行重排。"
            "CTA 也可换说法，但要接住这条的购买理由；不要每段重新开场、重复安利或堆连接词。"
            "变化措辞时保留必要产品事实，不编使用经历、他人反馈、实验数据或因果。")
