#!/usr/bin/env python3
"""Turn one day of the schedule doc into brief stubs (one brief per `brief_size` videos).

    python3 scripts/parse_schedule.py --config qingyang --run runs/20261030 --date 2026-10-30

Reads  runs/<day>/sources/schedule-<brand>.txt (old runs: schedule.txt), or config.daily_quota for brands without a schedule doc (LUX).
       config.schedule_format "weekly" (AHC, user 11-18): one `# MMDD-MMDD` block per week, rows 品类 × 结构 × 数量;
       the block that contains --date is used, each row becomes briefs of that 结构 (config.structures says what it is)
Writes runs/<day>/schedule-<brand>.json every row for that date (incl. 新增 tables) + section notes
       runs/<day>/briefs/<id>.json      one stub per brief; the agent fills segments (brief_card.py shows what to write)
"""
import argparse
import json
import math
import re
from datetime import date
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_config, parse_table

COLS = {"日期": "date", "店铺": "store", "内容方向": "direction", "测试卖点": "selling_point",
        "测试人群": "audience", "数量": "quantity", "总交付数量": "total"}


def sections(text):
    """Yield (heading, notes, table_html) for every ### section that has tables."""
    parts = re.split(r"^### (.*)$", text, flags=re.M)
    for i in range(1, len(parts), 2):
        heading, body = parts[i].strip(), parts[i + 1]
        tables = re.findall(r"<table>.*?</table>", body, re.S)
        notes = re.sub(r"<table>.*?</table>", "", body, flags=re.S)
        notes = " ".join(l.strip() for l in notes.splitlines() if l.strip() and not l.startswith("<"))
        for t in tables:
            yield heading, notes, t


def rows_for(text, day):
    key = f"{day.month}.{day.day}"
    found = []
    for heading, notes, table in sections(text):
        grid = parse_table(table)
        if not grid:
            continue
        header = [COLS.get(h.strip(), h.strip()) for h in grid[0]]
        if "date" not in header:
            continue
        for r in grid[1:]:
            row = dict(zip(header, r))
            if row.get("date", "").strip() != key:
                continue
            qty = re.search(r"\d+(?:\+\d+)?", row.get("quantity", ""))
            row["quantity"] = sum(int(x) for x in qty[0].split("+")) if qty else 0
            row["section"] = heading
            row["section_notes"] = notes
            found.append(row)
    return found


def weekly_rows(text, day, cfg):
    """AHC schedule (user 11-18): `# 1116-1120` headings, each with one table 品类 | 结构 | 脚本 | 数量. Returns the
    rows of the week that contains `day`; 品类 maps to a product through store_to_product, 结构 to config.structures."""
    out = []
    for m in re.finditer(r"^# (\d{2})(\d{2})-(\d{2})(\d{2})\s*$(.*?)(?=^# |\Z)", text, re.M | re.S):
        a = date(day.year, int(m.group(1)), int(m.group(2)))
        b = date(day.year, int(m.group(3)), int(m.group(4)))
        if not a <= day <= b:
            continue
        for t in re.findall(r"<table>.*?</table>", m.group(5), re.S):
            grid = parse_table(t)
            head = [h.strip() for h in grid[0]]
            for r in grid[1:]:
                row = dict(zip(head, r))
                q = re.search(r"\d+", row.get("数量", ""))
                if not q or "总" in row.get("品类", "") or not row.get("结构"):
                    continue
                st = row["结构"].strip()
                out.append({"store": row["品类"].strip(), "direction": st, "selling_point": "", "audience": "",
                            "quantity": int(q[0]), "structure": st, "template": (cfg.get("structures") or {}).get(st, ""),
                            "section": f"{m.group(1)}{m.group(2)}-{m.group(3)}{m.group(4)}", "section_notes": ""})
    return out


def split_direction(direction, cfg):
    """「品牌背书，成分种草（法国人头发发质蓬松溯源）」→ core + note. The bracket is a long-lived schedule label,
    not copy material; config.direction_notes says how the team actually wrote it."""
    m = re.match(r"(.*?)[（(](.*)[）)]\s*$", direction.strip())
    core, note = (m.group(1).strip(), m.group(2).strip()) if m else (direction.strip(), "")
    out = {"direction_core": core, "direction_note": note}
    hint = cfg.get("direction_notes", {}).get(note)
    if hint:
        out["direction_hint"] = hint
    return out


def product_key(cfg, store):
    for k, v in cfg["store_to_product"].items():
        if k in store:
            return v
    return None


def schedule_file(run, cfg):
    """每个品牌一份排期文件；旧的 run 只有共用的 schedule.txt。"""
    own = run / "sources" / f"schedule-{cfg['brand_key']}.txt"
    return own if own.exists() or not (run / "sources" / "schedule.txt").exists() else run / "sources" / "schedule.txt"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--product", help="only write briefs for this product key (numbering stays the same)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    run = Path(args.run)
    day = date.fromisoformat(args.date)
    if cfg.get("daily_quota"):
        # brands without a schedule doc: the same products and counts every day (config.daily_quota)
        rows = [{"store": q.get("store", cfg["brand"]), "product_key": q["product"], "direction": q.get("direction", "日常"),
                 "selling_point": "", "audience": q.get("audience", ""), "quantity": q["quantity"],
                 "section": "daily_quota", "section_notes": q.get("note", "")} for q in cfg["daily_quota"]]
    elif "daily_quota" in cfg and not schedule_file(run, cfg).exists():
        raise SystemExit(f"{cfg['brand']} 没有排期表，config 的 daily_quota 也是空的：先问用户这次做哪些产品、各多少条，"
                         f"填进 config/{cfg['brand_key']}.json 的 daily_quota")
    else:
        src = schedule_file(run, cfg)
        if not src.exists():
            raise SystemExit(f"{src} 不存在：先跑 fetch_sources.py --config {args.config} --run {args.run} 拉排期")
        text = src.read_text()
        rows = weekly_rows(text, day, cfg) if cfg.get("schedule_format") == "weekly" else rows_for(text, day)
    if not rows:
        what = "这一周" if cfg.get("schedule_format") == "weekly" else f"{day.month}.{day.day}"
        raise SystemExit(f"排期里找不到 {what} 的行：排期可能还没更新到这一天")

    (run / f"schedule-{cfg['brand_key']}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    bdir = run / "briefs"
    bdir.mkdir(exist_ok=True)
    size, n = cfg["brief_size"], 0
    for row in rows:
        pk = row.get("product_key") or product_key(cfg, row.get("store", "")) or product_key(cfg, row.get("direction", ""))
        for j in range(math.ceil(row["quantity"] / size)):
            n += 1
            bid = f"{day:%m%d}-{cfg.get('brief_prefix', 'B')}{n:02d}"
            path = bdir / f"{bid}.json"
            if path.exists() or (args.product and pk != args.product):
                continue  # never clobber a brief the agent or reviewer already worked on
            stub = {
                "brief_id": bid,
                "date": args.date,
                "brand": cfg["brand_key"],
                "store": row.get("store", "").replace("\n", " "),
                "product_key": pk,
                "direction": row.get("direction", ""),
                **split_direction(row.get("direction", ""), cfg),
                "selling_point": row.get("selling_point", "") or (cfg["products"].get(pk) or {}).get("default_selling_point", ""),
                "audience": row.get("audience", ""),
                "quantity": min(size, row["quantity"] - j * size),
                "schedule_section": row["section"],
                "schedule_notes": row["section_notes"],
                **({"structure": row["structure"], "template": row["template"]} if row.get("structure") else {}),
                "segments": [],
                "status": "draft",
                "review_notes": "",
            }
            path.write_text(json.dumps(stub, ensure_ascii=False, indent=2))
        print(f"{row.get('store','')!r:28} {row.get('direction','')[:30]:32} {row.get('selling_point',''):14} "
              f"{row.get('audience',''):6} {row['quantity']:>3} -> product={pk} [{row['section']}]")
    print(f"\n{len(rows)} 行排期 → {n} 个 brief（每 {size} 条一个），stub 在 {bdir}")
    missing = [r for r in rows if not (r.get("product_key") or product_key(cfg, r.get('store', '')))]
    if missing:
        print("⚠️ 以下店铺没有映射到产品，需要在 config.store_to_product 补：", {r['store'] for r in missing})


if __name__ == "__main__":
    main()
