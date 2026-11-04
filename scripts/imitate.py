#!/usr/bin/env python3
"""Print what a writer needs for one brief: facts, footage, the confirmed scripts as the standard,
and which openings / segment orders were already used recently (so the new script takes another angle).

    python3 scripts/imitate.py --config qingyang --run runs/20261104 --brief 1104-B03

Confirmed scripts set the bar (voice, length, how general to stay); they are not templates. Tracing them
line by line made 11.03/11.04 read like the same video with words swapped, and lint_variety now rejects that.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import day_briefs, load_config, load_gold
from lint_copy import earlier_copy, skeleton
from ref_scripts import print_edits

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--brief", required=True)
    args = ap.parse_args()

    cfg = load_config(args.config)
    run = Path(args.run)
    brief = json.loads((run / "briefs" / f"{args.brief}.json").read_text())
    product = cfg["products"][brief["product_key"]]
    gold = load_gold(cfg, brief["product_key"])
    if not gold:
        raise SystemExit(f"gold/{cfg['brand_key']}.json 里没有 {brief['product_key']} 的范本：先从团队已交付脚本里挑几条放进去")

    today = day_briefs(run, cfg)
    today = [b for b in today if b.get("segments")]

    std = [g for g in gold if g.get("status") == "confirmed"] or gold
    sp = product["selling_points"].get(brief.get("selling_point", ""), {})
    print(f"# 写 {brief['brief_id']} · {brief.get('direction_core') or brief['direction']} · {brief.get('selling_point')} · {brief['quantity']} 条")
    if brief.get("direction_note"):
        print(f"（排期括号「{brief['direction_note']}」是标签，不写进口播）")
    print(f"\n要用的事实（只用这些）：")
    print(f"- 产品全名：{product['full_name']}")
    print(f"- 测试卖点：{brief.get('selling_point')} → {sp.get('say', '')}")
    print(f"- 机制：{' / '.join(product['mechanism']['spoken'])}；结尾：{'/'.join(product['cta_required'])}")
    camp = cfg.get("campaign")
    if camp and brief.get("date", "") <= camp["until"]:
        print(f"- 节点：机制句或 CTA 带「{camp['name']}」")

    if product.get("rules"):
        print("\n这个产品的固定要求：")
        for r in product["rules"]:
            print(f"- {r}")

    if product.get("footage"):
        print("\n每段素材里只有这些画面（口播要接得住）：")
        for role, f in product["footage"].items():
            if not role.startswith("_"):
                print(f"- {role}：{f}")

    print()
    print_edits(brief, 5)

    print(f"\n## 标准：用户确认过的 {len(std)} 条（学口吻、长短、宽泛程度；不要在这些句子上换词）")
    for g in std:
        print(f"\n{g['id']} · {g.get('speaker', '')}")
        for beat in g["beats"]:
            print(f"   [{beat['role']}] {beat['text']}")

    seen = earlier_copy(run, brief, cfg)
    seen += [(o["brief_id"], o["segments"]) for o in today if o["brief_id"] != brief["brief_id"]]
    orders = Counter(" → ".join(skeleton(segs)[0]) for _, segs in seen)
    heads = Counter(h for _, segs in seen for h in skeleton(segs)[1] if h)
    print("\n## 已经用过的（换一个角度）")
    print("段落顺序：" + "；".join(f"{o}（{n} 次）" for o, n in orders.most_common(5)))
    print("句首两个字：" + "、".join(f"{h}×{n}" for h, n in heads.most_common(15)))
    print("\n写法：先定角度（开头怎么切入、段落顺序、几句），再写句子。可选角度见 references/writing.md 第 2 节。"
          "lint 会拦：跟范本 / 近 7 天 / 当天其它稿文字相似度超过 35%，或段落顺序一样且 2 句以上开头一样。"
          "写完 brief 里填 gold_id（最接近的标准稿）。")


if __name__ == "__main__":
    main()
