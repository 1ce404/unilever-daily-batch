#!/usr/bin/env python3
"""④ Print one page per brief with everything the agent needs to write it the team's way (references/writing.md).

    python3 scripts/brief_card.py --config qingyang --run runs/20261105 [--brief 1105-B01]

It does not write copy or call the platform. Per brief it shows:
  the task (direction / selling point / audience) and which part of the script should change because of it,
  the fixed lines (selling-point say, mechanism + CTA) that team scripts reuse,
  the product rules, footage, banned words, and the platform's own copy rules (video_script_create),
  the meme assigned to it in runs/<day>/hotspots.json, if any,
  team scripts of the same product and direction (gold, plus rows of the team script collection fetched at ①),
  reviewer edits (learned/),
  and the openings already used today and in the last days (so this one's opening is new).
"""
import argparse
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SKILL_DIR, day_briefs, load_config, load_gold
from lint_copy import earlier_copy, seg_text


def same_direction(g, brief):
    d = brief.get("direction_core") or brief.get("direction") or ""
    gd = g.get("direction") or ""
    return bool(gd) and (gd[:4] in d or d[:4] in gd)


def team_rows(run, cfg, brief, n=3):
    """Newest rows of the team script collection (runs/<day>/sources/recent_scripts*.txt, fetched at ①) for
    this product, preferring rows of the same direction or selling point: what the team actually delivered."""
    prod = cfg["products"][brief["product_key"]]
    labels = prod.get("collection_labels") or [prod["full_name"]]
    keys = [k for k in re.split(r"[：:—\-（）()，,、\s]+", brief.get("direction_core") or brief["direction"]) if len(k) >= 2][:1]
    keys += [k for k in re.split(r"[\s（）()]+", brief.get("selling_point") or "") if len(k) >= 2][:1]
    rows = []
    for f in sorted(Path(run).glob("sources/recent_scripts*.txt")):
        for sec in re.split(r"\n#{1,3} ", f.read_text()):
            day = re.match(r"(\d{2})\.?(\d{2})", sec)
            if not day:
                continue
            raw = re.findall(r"<tr>(.*?)</tr>", sec, re.S) + [l for l in sec.splitlines() if l.startswith("|") and "-|-" not in l]
            for r in raw:
                t = re.sub(r"</t[dh]>", " | ", re.sub(r"<br/?>", " / ", r))
                t = html.unescape(re.sub(r"<[^>]+>", " ", t)).replace("**", "")
                t = re.sub(r"\s*/\s*(/\s*)+", " / ", re.sub(r"[ \t]+", " ", t)).strip(" |")
                if len(t) > 60 and any(l in t for l in labels):
                    rows.append((day.group(1) + day.group(2), any(k in t for k in keys), t))
    seen, out = set(), []
    for d, hit, t in sorted(rows, key=lambda x: (x[1], x[0]), reverse=True):
        if t not in seen:
            seen.add(t); out.append(f"{d}：{t}")
    return out[:n]


def hotspot_for(run, brief):
    """The meme assigned to this brief in runs/<day>/hotspots.json (see references/writing.md §8), or None."""
    p = Path(run) / "hotspots.json"
    if not p.exists():
        return None
    return next((h for h in json.loads(p.read_text()) if brief["brief_id"] in h.get("briefs", [])), None)


def edits(cfg, brief, n=5):
    log = SKILL_DIR / "learned" / f"{cfg['brand_key']}-edits.jsonl"
    if not log.exists():
        return []
    rows = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    rows = [e for e in rows if e.get("product_key", brief["product_key"]) == brief["product_key"]]
    return sorted(rows, key=lambda e: e.get("date") or "", reverse=True)[:n]


def card(cfg, run, brief, today):
    prod = cfg["products"][brief["product_key"]]
    sp = prod["selling_points"].get(brief.get("selling_point") or "", {})
    camp = cfg.get("campaign")
    in_camp = camp and brief.get("date", "") <= camp["until"]
    out = [f"######## {brief['brief_id']} · {prod['full_name']} · {brief['quantity']} 条",
           f"内容方向：{brief['direction']}" + (f"（括号「{brief['direction_note']}」是标签，不写进口播）" if brief.get("direction_note") else ""),
           f"测试卖点：{brief.get('selling_point') or '（无，写机制）'}　人群：{brief.get('audience') or '通用'}（人群是标签，不念）"]
    if brief.get("schedule_notes"):
        out.append(f"排期备注：{brief['schedule_notes']}")
    if brief.get("template"):
        out.append(f"用户给的结构：{brief['template']}（照这个分段）")
    out += ["",
            "骨架（团队写法，5~6 句，每句一个任务）：",
            "  ① 开头：按「方向 + 人群」写，每条新写；同产品当天的几条轮换开头类型（反问 / 判断 / 金句 / 场景引子 / 香味 / 对话 / 热梗 / 直戳头发状态，"
            "同类型最多两次，见 writing.md 第 3 节），第二句落到头发状态",
            f"  ② 引产品：一句话带出全名「{prod['full_name']}」（尽量第 3 段才出现）",
            f"  ③ 卖点：{sp.get('say') or '（config 没写，按产品卖点表）'}",
            "  ④ 效果：洗完 / 用完看得见、摸得到的结果",
            f"  ⑤ 机制 + CTA：{'、'.join(prod['mechanism']['spoken'])}"
            f"{'，带「' + camp['name'] + '」' if in_camp else ''}，结尾「{'/'.join(prod['cta_required'])}」"]
    if sp.get("must_mention"):
        out.append(f"  卖点必须出现：{'、'.join(sp['must_mention'])}（第一次讲卖点那句要带产品全名）")
    out.append("  每句写 2~3 个说法（主版 text + alt），语气各一版：口语带情绪 / 正常叙述 / 偏书面。③⑤ 可以沿用固定说法")
    if prod.get("audience_note"):
        out.append(f"\n受众 / 口吻：{prod['audience_note']}")
    if prod.get("rules"):
        out.append("这个产品的要求：")
        out += [f"  - {r}" for r in prod["rules"]]
    if prod.get("footage"):
        out.append("素材里有的画面（每句要接得住，不写素材里没有的场景）：")
        out += [f"  - {k}：{v}" for k, v in prod["footage"].items() if not k.startswith("_") and v != "同 hook"]
    banned = [w for w in cfg["banned_words"]["error"] + prod.get("warn_words", []) if w not in prod.get("allow_words", [])]
    out.append(f"不能写：{'、'.join(dict.fromkeys(banned))}、具体时长（几小时 / 几天）")
    plat = prod.get("_platform_copy_rules")
    if plat:
        out.append("平台提示词里的文案规则（video_script_create）：")
        out += [f"  - {r}" for r in plat]
    gold = load_gold(cfg, brief["product_key"])
    near = [g for g in gold if same_direction(g, brief)] or [g for g in gold if g.get("status") == "confirmed"] or gold
    out.append(f"\n团队稿（同产品{'、同方向' if any(same_direction(g, brief) for g in gold) else ''}，学骨架和口吻，开头不要照抄）：")
    for g in near[:4]:
        out.append(f"  {g['id']}（{g.get('direction', '')}，{g.get('status')}）")
        out += [f"     [{b['role']}] {b['text']}" for b in g["beats"]]
    hot = hotspot_for(run, brief)
    if hot:
        out.append(f"\n今天的热梗：「{hot['topic']}」（{hot.get('meaning', '')}；来源 {hot.get('source', '')}）"
                   "\n  只用在开头一句，马上转到头发状态；不编数据，不提平台名")
    rows = team_rows(run, cfg, brief)
    if rows:
        out.append("团队交付过的同方向稿（脚本合集，「/」隔开的是同一句的几个说法）：")
        out += [f"  · {r[:900]}" for r in rows]
    ed = edits(cfg, brief)
    if ed:
        out.append("审核人改过的句子（照这个方向）：")
        out += [f"  「{e['before']}」→「{e['after']}」" for e in ed]
    seen = earlier_copy(run, brief, cfg) + [(o["brief_id"], o["segments"]) for o in today
                                            if o["brief_id"] != brief["brief_id"] and o.get("segments")]
    used = [(label, seg_text(segs[0])) for label, segs in seen if segs and not label.startswith("范本")]
    if used:
        out.append("已经用过的开头（这条换一个）：")
        out += [f"  {label}：{t}" for label, t in used[-10:]]
    return "\n".join(out)


def platform_rules(cfg, keys):
    """The copy rules in each product's platform prompt video_script_create (read-only; best effort offline)."""
    try:
        import clipo_batch as cb
        cb.BASE = cfg["clipo"]["base_url"].rstrip("/") + "/api/v1"
        for k in keys:
            prompts = {x["prompt_type"]: x for x in cb.api("GET", f"/products/{cfg['products'][k]['clipo_product_id']}/prompts")}
            p = (prompts.get("video_script_create") or {}).get("prompt") or ""
            i = p.find("### 分段文案")
            if i >= 0:
                cfg["products"][k]["_platform_copy_rules"] = [l.lstrip("- ").strip() for l in p[i:].splitlines()[1:] if l.strip().startswith("-")]
    except Exception as e:  # offline: the card still works, just without the platform's rules
        print(f"（读不到平台提示词：{str(e)[:80]}）\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--brief")
    args = ap.parse_args()
    cfg = load_config(args.config)
    run = Path(args.run)
    today = day_briefs(run, cfg)
    todo = [b for b in today if not args.brief or b["brief_id"] == args.brief]
    platform_rules(cfg, sorted({b["product_key"] for b in todo}))
    for b in todo:
        print(card(cfg, run, b, today) + "\n")
    print("写完填进 brief 的 segments（每段 role / title / text / alt），再跑 lint_copy.py。")


if __name__ == "__main__":
    main()
