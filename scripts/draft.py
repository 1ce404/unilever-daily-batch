#!/usr/bin/env python3
"""④ Let the platform write each brief's script from a creative requirement the skill builds.

    python3 scripts/draft.py --config qingyang --run runs/20261105 [--brief 1105-B01] [--dry-run] [--redo]

The platform already knows how to write copy and package a video (product prompt video_script_create).
What it does not know is today's schedule, this product's hard-won rules, the scripts the user approved,
what ran in the last few days, and today's current memes. This script puts exactly that into the
`requirement` (≤ 2000 chars), calls POST /video-script-generations, reads the script back, lints the copy,
and if lint finds errors asks the platform once to revise (same endpoint with script_id). The result is
written into the brief (segments + script_id) for whole-copy editorial reading, then human review.
Every new brief requires a creative_plan before network calls; editorial evidence is pinned to its text.
The platform script is named after the schedule row (config `naming`, the same name its videos and share page get).

Current memes: runs/<day>/hotspots.json, written by the agent after searching (see references/writing.md):
    [{"topic": "嘻嘻不嘻嘻", "source": "11.4 梗语访问榜",
      "angle": "镜前看到油塌的情绪反转", "products": ["2x", "huxi"]}]
Each brief gets at most one topic; topics rotate across a product's briefs. No file, no topic.
A brief with `template` (a structure the user gave) is written in exactly that structure.
"""
import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import SKILL_DIR, day_briefs, load_config, load_gold, out_name
from lint_copy import earlier_copy, lint_brief, lint_day, lint_variety, skeleton
from copy_logic import plan_issues, plan_batch_issues, plan_lines

MAX_REQ = 2000
ROLE_BY_TITLE = [("cta", ("CTA", "直播间", "下单", "机制")), ("product", ("产品", "全名")),
                 ("selling", ("卖点", "成分", "技术", "酰胺", "玻尿酸", "二硫化硒", "深净澈", "超微分子")),
                 ("effect", ("效果", "结果", "洗完", "泡沫", "香")), ("pain", ("原因", "痛点")), ("hook", ("钩子", "开头", "热点"))]


def guess_role(title, i, n):
    if i == n - 1:
        return "cta"
    if i == 0:
        return "hook"
    for role, keys in ROLE_BY_TITLE:
        if any(k in (title or "") for k in keys):
            return role
    return "effect"


def reviewer_edits(cfg, brief, n=3):
    log = SKILL_DIR / "learned" / f"{cfg['brand_key']}-edits.jsonl"
    if not log.exists():
        return []
    edits = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    edits = [e for e in edits if e.get("product_key", brief["product_key"]) == brief["product_key"]]
    return sorted(edits, key=lambda e: e.get("date") or "", reverse=True)[:n]


def hotspot_for(run, cfg, brief, used):
    p = Path(run) / "hotspots.json"
    if not p.exists():
        return None
    topics = [t for t in json.loads(p.read_text()) if brief["product_key"] in t.get("products", [brief["product_key"]])]
    if not topics:
        return None
    topics.sort(key=lambda t: used[t["topic"]])
    used[topics[0]["topic"]] += 1
    return topics[0]


def requirement(cfg, run, brief, hot, today):
    """Prioritize whole-copy reasoning; never truncate mandatory facts or the creative plan."""
    problems = plan_issues(brief)
    if problems:
        raise ValueError(problems[0][1])
    prod = cfg["products"][brief["product_key"]]
    sp = prod["selling_points"].get(brief.get("selling_point") or "", {})
    mech = "、".join(prod["mechanism"]["spoken"])
    camp = cfg.get("campaign")
    in_campaign = camp and brief.get("date", "") <= camp["until"]
    shape = f"用户指定结构：{brief['template']}。" if brief.get("template") else "段数按意思和素材安排，不给各段固定分工。"
    lines = [f"为「{prod['full_name']}」写一条75~150字口播。{shape}",
             "先把下面的思路写成同一人能一口气讲通的全文，再按意思分段回填脚本。不要先列痛点/产品/成分/效果/优惠槽位再各填一句。",
             "\n".join(plan_lines(brief)),
             "每句承接前句并推进判断，卖点用来回答本条疑问；不用全部卖点，不凑效果句。收尾顺着购买理由进入CTA。",
             "语气来自具体观察和立场，不靠兄弟/姐妹/真的/直接冲制造口语。不编亲测、朋友反馈或未经证实的因果；少用口号、排比和空泛形容词。"]
    task = [f"内容方向：{brief.get('direction_core') or brief['direction']}"]
    if brief.get("selling_point"):
        task.append(f"测试卖点：{brief['selling_point']}")
    if brief.get("audience"):
        task.append(f"人群：{brief['audience']}")
    lines.append("；".join(task) + "。")
    if brief.get("direction_hint"):
        lines.append(f"方向说明：{brief['direction_hint']}")
    if sp.get("say"):
        lines.append(f"卖点怎么说：{sp['say']}")
    end = "、".join(prod["cta_required"])
    lines.append(f"机制：{mech if prod['mechanism']['allowed_numbers'] else '不念机制'}"
                 f"{'，带上「' + camp['name'] + '」' if in_campaign else ''}；最后一段要有「{end}」。")
    lines.append(f"首次引入产品说全「{prod['full_name']}」，随后指代清楚即可，不重复报全名；出现位置服从思路，第一次讲卖点时已交代属于哪个产品。")
    if prod.get("audience_note"):
        lines.append(f"受众：{prod['audience_note']}。")
    lines += [f"- {r}" for r in prod.get("rules", [])]
    if prod.get("footage"):
        shots = "；".join(f"{k}：{v}" for k, v in prod["footage"].items() if not k.startswith("_") and v != "同 hook")
        lines.append(f"素材里只有这些画面，每句口播要有画面接得住，不写素材里没有的场景：{shots}")
    banned = list(dict.fromkeys(cfg["banned_words"]["error"] + prod.get("warn_words", [])))
    banned = [w for w in banned if w not in prod.get("allow_words", [])]
    lines.append(f"不能写：{'、'.join(banned)}、最、第一、具体时长（几小时 / 几天）。")
    # Optional context is admitted as whole blocks; mandatory requirements are never silently sliced.
    out = "\n".join(lines)
    if len(out) > MAX_REQ:
        raise ValueError(f"{brief['brief_id']} 必要要求 {len(out)} 字超过 {MAX_REQ}；先精简创作思路/产品规则，不能截断事实")
    optional = []
    if hot:
        optional.append(f"可选热梗「{hot['topic']}」（{hot.get('source', '')}）：{hot.get('angle', '')}。只在支持本条论点时用一句，不强贴、不编热度。")
    peers = [o for o in today if o['brief_id'] != brief['brief_id'] and o.get('creative_plan')]
    if peers:
        optional.append("同组其它稿已选的切入，请在观点推进上区别：" + "；".join(
            f"{o['brief_id']}：{o['creative_plan'].get('angle', '')} / {'→'.join(o['creative_plan'].get('route', []))}"
            for o in peers if o.get('product_key') == brief['product_key']))
    seen = earlier_copy(run, brief, cfg) + [(o["brief_id"], o["segments"]) for o in today
                                            if o["brief_id"] != brief["brief_id"] and o.get("segments")]
    heads = [h for h, _ in Counter(h for _, segs in seen for h in skeleton(segs)[1] if h).most_common(10)]
    if heads:
        optional.append(f"近期用多的句头：{'、'.join(heads)}；换观察和论点，不只换词。")
    edits = reviewer_edits(cfg, brief)
    if edits:
        optional.append("审核人改法：" + "；".join(f"「{e['before']}」→「{e['after']}」" for e in edits))
    std = [g for g in load_gold(cfg, brief["product_key"]) if g.get("status") == "confirmed"] or load_gold(cfg, brief["product_key"])
    if std:
        # Rotate among equally relevant references instead of feeding every brief the same prefix.
        score = lambda g: (g.get('selling_point') == brief.get('selling_point'), g.get('direction') == brief.get('direction_core'))
        best = max(map(score, std))
        candidates = sorted((g for g in std if score(g) == best), key=lambda g: g['id'])
        ordinal = int(re.search(r"(\d+)$", brief['brief_id']).group(1))
        g = candidates[(ordinal - 1) % len(candidates)]
        label = "确认稿" if g.get('status') == 'confirmed' else "未确认参考"
        rationale = f"{label}{g['id']}：谁在说={g.get('speaker', '')}；为何这样讲={g.get('why', '')}。借鉴判断方式，不照搬句序。"
        # Preserve the voice/reason even when there is no room for the example's full wording.
        optional.insert(0, rationale)
        optional.append("参考全文（不复制句式）：" + " / ".join(b['text'] for b in g['beats']))
    for block in optional:
        if len(out) + len(block) + 1 <= MAX_REQ:
            out += "\n" + block
    return out


def wait_generation(gid):
    for _ in range(72):
        time.sleep(5)
        g = cb.api("GET", f"/video-script-generations/{gid}")
        if g["status"] in ("success", "failed"):
            return g
    raise RuntimeError("平台写脚本超过 6 分钟没结束")


def to_segments(script):
    segs = script["segments"]
    return [{"id": str(i + 1), "role": guess_role(s.get("title"), i, len(segs)), "title": s.get("title") or "",
             "text": s.get("content") or ""} for i, s in enumerate(segs)]


def errors_of(brief, cfg, today):
    issues = lint_brief(brief, cfg, require_editorial=False) + lint_day(today, cfg).get(brief["brief_id"], [])
    issues += lint_variety([brief] + [o for o in today if o["brief_id"] != brief["brief_id"]], cfg, Path(brief["_run"])).get(brief["brief_id"], [])
    return [m for l, m in issues if l == "error"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--brief")
    ap.add_argument("--dry-run", action="store_true", help="只打印创作要求，不调平台")
    ap.add_argument("--redo", action="store_true", help="已经写过的 brief 也重写（会删掉平台上的旧脚本）")
    args = ap.parse_args()
    cfg = load_config(args.config)
    cb.BASE, cb.CFG = cfg["clipo"]["base_url"].rstrip("/") + "/api/v1", cfg
    run = Path(args.run)
    today = day_briefs(run, cfg)
    selected = [b for b in today if (not args.brief or b['brief_id'] == args.brief)
                and (args.redo or (not b.get('segments') and b.get('status') != 'approved'))]
    problems = [(b['brief_id'], m) for b in selected for _, m in plan_issues(b)]
    duplicates = plan_batch_issues(today)
    problems += [(b['brief_id'], m) for b in selected for _, m in duplicates.get(b['brief_id'], [])]
    if problems:
        raise SystemExit("生成前先完成组内创作规划：\n" + "\n".join(f"{bid}: {msg}" for bid, msg in problems))
    # Validate all requirements before starting remote generations or deleting an old draft.
    for b in selected:
        requirement(cfg, run, b, None, today)
    used = Counter()
    for b in today:
        if args.brief and b["brief_id"] != args.brief:
            if b.get("hotspot"):
                used[b["hotspot"]] += 1
            continue
        if b.get("segments") and not args.redo:
            print(f"⏭  {b['brief_id']} 已经有脚本（--redo 重写）")
            continue
        if b.get("status") == "approved" and not args.redo:
            print(f"⏭  {b['brief_id']} 已审核通过，不重写")
            continue
        hot = hotspot_for(run, cfg, b, used)
        req = requirement(cfg, run, b, hot, today)
        if args.dry_run:
            print(f"\n######## {b['brief_id']}（{len(req)} 字）\n{req}")
            continue
        prod = cfg["products"][b["product_key"]]
        if args.redo and b.get("script_id"):
            try:
                cb.api("DELETE", f"/video-scripts/{b['script_id']}")
            except RuntimeError:
                pass
        g = wait_generation(cb.api("POST", "/video-script-generations", {"product_id": prod["clipo_product_id"], "requirement": req})["id"])
        if g["status"] != "success":
            print(f"❌ {b['brief_id']} 平台写脚本失败 {g.get('error_code')}: {g.get('error_message')}")
            continue
        sid = g["script_id"]
        b.update({"script_id": sid, "segments": to_segments(cb.api("GET", f"/video-scripts/{sid}")), "copy_logic_version": 2,
                  "requirement": req, "hotspot": hot["topic"] if hot else None, "status": "draft", "_run": str(run)})
        b.pop("approval", None)
        b.pop("editorial_review", None)
        errs = errors_of(b, cfg, today)
        if errs:  # one revision round on the platform, then leave it for the human
            fix = "按下面的问题修改这条脚本，其它保持不变：\n" + "\n".join(f"- {e}" for e in errs[:8])
            g = wait_generation(cb.api("POST", "/video-script-generations", {"script_id": sid, "requirement": fix[:MAX_REQ]})["id"])
            if g["status"] == "success":
                b["segments"] = to_segments(cb.api("GET", f"/video-scripts/{sid}"))
                errs = errors_of(b, cfg, today)
        b.pop("_run", None)
        b["script_title"] = cb.video_title(cfg, b)
        cb.set_title(sid, b["script_title"])
        (run / "briefs" / f"{b['brief_id']}.json").write_text(json.dumps(b, ensure_ascii=False, indent=2))
        print(f"{'✅' if not errs else '⚠️ '} {b['brief_id']} {b['script_title']} · {len(b['segments'])} 段"
              f"{'，热点「' + hot['topic'] + '」' if hot else ''}{'' if not errs else '，还有 ' + str(len(errs)) + ' 个错误要人改'}")
        for s in b["segments"]:
            print(f"     [{s['role']}] {s['text']}")
        for e in errs:
            print(f"     ✗ {e}")
    if not args.dry_run:
        print("\n下一步：整篇审读与组间比较 → review_doc.py editorial 记录 → lint_copy.py → render 给人审")


if __name__ == "__main__":
    main()
