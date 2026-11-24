#!/usr/bin/env python3
"""
[INPUT]: 依赖 clipo_batch.api（平台接口）、common.load_config（品牌与产品 id）
[OUTPUT]: 对外提供 is_ai_tag / new_tag / name_problem / problem / set_tags / add_product_tags，以及 check / fix / restore 三个命令
[POS]: scripts 里 AI 素材打标规则的唯一出处。aigc_gen 生成后按它改标签，clipo_batch preflight 按它报警。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md

AI 素材打标规则（用户 11-18）：
  1. AI 素材（ai_generated=true）只带一个标签，标签名以「AI」开头，按场景起名（AI双人演绎、AI小猫）
  2. 普通标签（使用后、头皮出油…）只给真实素材；AI 标签不打在真实素材上
  3. 新生成的标签统一命名「AI-MMDD-素材方向」（用户 11-24：日期 + 素材方向，所有产品同一规则），
     例：AI-1124-夫妻对话。不写 brief 号、分镜号、版本号（不再出现 AI-1111-B01-分镜1）。
     11-24 之前的旧标签（AI双人演绎、AI小猫…）保留不改，素材池照旧可用。

为什么要改：平台每入库一条素材都跑一次「智能理解」，从产品标签里挑标签打上去，AI 素材也会被打成
「使用后」「头皮出油」。11-18 清扬控油蓬松「使用后」346 条里 303 条是 AI 素材，平台提示词按标签配
画面时就配进了 AI 人。生成和切段之后要把标签整组换成这一个 AI 标签。

    python3 -I scripts/ai_tags.py check   --config qingyang [--product 2x]
    python3 -I scripts/ai_tags.py fix     --config qingyang --run $R [--product 2x] \
        [--map 超市=AI超市选购 --map 猫=AI小猫] [--default AI未分类] --dry-run
    python3 -I scripts/ai_tags.py restore --config qingyang --backup $R/clipo/ai-tags-backup-<品牌>-<时间>.json

fix 只动 AI 素材：已有 AI 标签的只留 AI 标签；没有 AI 标签的按描述关键词（--map，按顺序第一个命中）
归到场景标签，都不中归 --default。新标签先加进产品标签（平台规定切片标签必须是产品标签）。改前把每条
素材的原标签和产品标签存进备份，restore 按备份原样改回。真实素材带了 AI 标签的只报告，不改（可能是
AI 素材上传时没标 ai_generated，要用户确认）。
"""
import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import load_config

PREFIX = "AI"
NAME_RE = re.compile(r"^AI-(\d{4})-([^\d\-][^\-]*)$")  # AI-MMDD-素材方向


def is_ai_tag(tag):
    return tag.startswith(PREFIX)


def new_tag(date, direction):
    """「AI-MMDD-素材方向」。date 可以是 MMDD、YYYYMMDD 或 YYYY-MM-DD；方向去掉开头的 AI 与空白。"""
    mmdd = re.sub(r"\D", "", str(date))[-4:]
    direction = re.sub(r"\s+", "", str(direction)).removeprefix("AI").strip("-")
    tag = f"{PREFIX}-{mmdd}-{direction}"
    err = name_problem(tag)
    if err:
        raise ValueError(err)
    return tag


def name_problem(tag):
    """新生成素材的标签名是否合规：None 合规，否则返回原因。"""
    m = NAME_RE.match(tag or "")
    if not m:
        return f"标签「{tag}」不合规：写成「AI-MMDD-素材方向」，如 AI-1124-夫妻对话"
    mm, dd = int(m.group(1)[:2]), int(m.group(1)[2:])
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return f"标签「{tag}」日期不对：MMDD"
    if re.search(r"[A-Z]\d{2}|分镜|brief|版本|V\d", m.group(2), re.I):
        return f"标签「{tag}」不写 brief 号、分镜号、版本号，只写素材方向"
    return None


def problem(seg):
    """One clip against the rule: None when it follows it, else a short reason."""
    tags = seg.get("tags") or []
    ai = [t for t in tags if is_ai_tag(t)]
    if not seg.get("ai_generated"):
        return f"真实素材带了 AI 标签 {ai}" if ai else None
    if not tags:
        return "AI 素材没有标签"
    if len(ai) != len(tags):
        return f"AI 素材带了普通标签 {[t for t in tags if not is_ai_tag(t)]}"
    if len(ai) > 1:
        return f"AI 素材带了多个 AI 标签 {ai}"
    return None


def segments(pid):
    rows, skip = [], 0
    while True:
        r = cb.api("GET", f"/products/{pid}/segments?skip={skip}&limit=100")
        page = r if isinstance(r, list) else r.get("items", [])
        rows += page
        if len(page) < 100:
            return rows
        skip += 100


def set_tags(seg_id, tags):
    """Replace a clip's tags (AI clips have no parent material, so the flat endpoint). False when the clip was
    deleted meanwhile (someone cleaning the library on the platform)."""
    try:
        cb.api("PATCH", f"/materials/segments/{seg_id}", {"tags": list(tags)})
        return True
    except RuntimeError as e:
        if "segment_not_found" in str(e):
            return False
        raise


def add_product_tags(pid, tags):
    """Add tags to the product's tag list (a clip may only carry the product's tags). Returns the ones added."""
    have = cb.api("GET", f"/products/{pid}")["tags"]
    new = [t for t in dict.fromkeys(tags) if t not in have]
    if new:
        cb.api("PATCH", f"/products/{pid}", {"tags": have + new})
    return new


def products(cfg, only):
    keys = [only] if only else sorted(cfg["products"])
    return [(k, cfg["products"][k]["clipo_product_id"]) for k in keys if cfg["products"][k].get("clipo_product_id")]


def target(seg, maps, default):
    """Where one AI clip goes: its own AI tags if it has any, else the first --map keyword in its name/description."""
    ai = [t for t in seg.get("tags") or [] if is_ai_tag(t)]
    if ai:
        return ai
    text = (seg.get("name") or "") + (seg.get("description") or "")
    return [next((tag for word, tag in maps if word in text), default)]


# ---------- commands ----------

def cmd_check(cfg, args):
    bad_total = 0
    for key, pid in products(cfg, args.product):
        segs = segments(pid)
        rows = [(s, problem(s)) for s in segs]
        bad = [(s, p) for s, p in rows if p]
        ai = sum(bool(s.get("ai_generated")) for s in segs)
        print(f"{'✅' if not bad else '❌'} {key}：素材 {len(segs)}，AI {ai}，不合规 {len(bad)}")
        if bad:
            kinds = Counter(p.split(" [")[0] for _, p in bad)
            print("   " + "；".join(f"{k} {n}" for k, n in kinds.items()))
            mixed = Counter(t for s, _ in bad if s.get("ai_generated") for t in s.get("tags") or [] if not is_ai_tag(t))
            if mixed:
                print("   被混进 AI 素材的普通标签：" + "、".join(f"{t} {n}" for t, n in mixed.most_common()))
        bad_total += len(bad)
    if bad_total:
        print(f"\n{bad_total} 条不合规：ai_tags.py fix --dry-run 看怎么改")
    sys.exit(1 if bad_total else 0)


def cmd_fix(cfg, args):
    maps = [tuple(m.split("=", 1)) for m in args.map or []]
    if any(not is_ai_tag(t) for _, t in maps) or not is_ai_tag(args.default):
        raise SystemExit(f"--map / --default 的标签要以「{PREFIX}」开头")
    plan, backup = [], {"at": time.strftime("%Y-%m-%d %H:%M"), "brand": cfg["brand_key"], "products": {}, "segments": {}}
    for key, pid in products(cfg, args.product):
        segs = segments(pid)
        todo = [(s, target(s, maps, args.default)) for s in segs if s.get("ai_generated") and problem(s)]
        real = [s for s in segs if problem(s) and not s.get("ai_generated")]
        groups = Counter(tuple(t) for _, t in todo)
        print(f"== {key}：改 {len(todo)} 条")
        for t, n in groups.most_common():
            print(f"   → {'、'.join(t)}：{n}")
        unknown = [s for s, t in todo if t == [args.default]]
        for s in unknown[:8]:
            print(f"     {args.default} 例：{(s.get('name') or '')[:40]}")
        if real:
            print(f"   ⚠️ 真实素材带 AI 标签 {len(real)} 条（不改；可能是 AI 素材上传时没标 ai_generated，问用户）："
                  + "；".join((s.get("name") or "")[:24] for s in real[:5]))
        plan.append((key, pid, todo))
    if args.dry_run:
        print("\n(dry run) 没改平台。确认后去掉 --dry-run；改前会备份")
        return
    out = Path(args.run) / "clipo" / f"ai-tags-backup-{cfg['brand_key']}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    for key, pid, todo in plan:
        backup["products"][pid] = cb.api("GET", f"/products/{pid}")["tags"]
        backup["segments"].update({s["id"]: s.get("tags") or [] for s, _ in todo})
    out.write_text(json.dumps(backup, ensure_ascii=False, indent=1))
    print(f"备份 → {out}")
    for key, pid, todo in plan:
        added = add_product_tags(pid, {t for _, ts in todo for t in ts})
        if added:
            print(f"   {key} 产品标签新增：{'、'.join(added)}")
        gone = sum(not set_tags(s["id"], tags) for s, tags in todo)
        if gone:
            print(f"   {key}：{gone} 条在平台上已被删除，跳过")
        after = {s["id"]: s for s in segments(pid)}
        left = [i for i, (s, _) in enumerate(todo) if problem(after.get(s["id"], s))]
        print(f"   {key}：改了 {len(todo)} 条，读回仍不合规 {len(left)} 条")


def cmd_restore(cfg, args):
    b = json.loads(Path(args.backup).read_text())
    for pid, tags in b["products"].items():
        cb.api("PATCH", f"/products/{pid}", {"tags": tags})
    for sid, tags in b["segments"].items():
        set_tags(sid, tags)
    print(f"已按 {args.backup} 改回：产品 {len(b['products'])} 个，素材 {len(b['segments'])} 条")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "fix", "restore"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--run", help="fix: 备份写进 $R/clipo/")
    ap.add_argument("--product", help="只看这个产品 key，默认品牌下全部产品")
    ap.add_argument("--map", action="append", help="fix: 描述关键词=AI 标签，例：超市=AI超市选购（按顺序第一个命中）")
    ap.add_argument("--default", default="AI未分类", help="fix: 没有 AI 标签、关键词都不中时归到这个标签")
    ap.add_argument("--backup", help="restore: fix 写的备份文件")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = load_config(args.config)
    cb.BASE, cb.CFG = cfg["clipo"]["base_url"].rstrip("/") + "/api/v1", cfg
    if args.cmd == "fix" and not args.run and not args.dry_run:
        raise SystemExit("fix 要 --run $R（备份写进 $R/clipo/）")
    {"check": cmd_check, "fix": cmd_fix, "restore": cmd_restore}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
