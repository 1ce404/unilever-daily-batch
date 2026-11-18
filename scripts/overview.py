#!/usr/bin/env python3
"""
[INPUT]: 依赖 common.lark_fetch（拉飞书）、common.parse_table（展开合并单元格）、config/lines.json（品线 → 品牌 / 产品）
[OUTPUT]: 对外提供 week_rows / day_count 两个函数，以及命令行「今天各品线做多少」
[POS]: 流程 ⓪。看联合利华总排期，回答「今天要做哪些内容」；不写 brief、不调平台。
       各品牌的明细（方向、结构）仍由 parse_schedule.py 按各自排期拆。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md

    python3 -I scripts/overview.py --run runs/20261118 --date 2026-11-18 [--refetch]

总排期每周一张表：`## 1116-1120` 标题 + 品牌 | 类型 | 周一…周五 | 总计 | 执行。
表头的日期常是上周复制来的（11-16 那周表头还写 1102~1106），所以按列的位置算日期：第 1 个日期列 = 标题里的起始日。
一个跨 5 列的格子（colspan="5"）是整周条数，不是每天条数。
"""
import argparse
import json
import re
from datetime import date, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SKILL_DIR, lark_fetch, load_config, parse_table


def week_rows(text, day):
    """总排期里含 day 那一周的每一行：line、kind、owner、daily（{date: 条数或说明}）或 weekly（整周条数或说明）。"""
    for m in re.finditer(r"^#+ \D*(\d{2})(\d{2})-(\d{2})(\d{2})\D*$(.*?)(?=^#+ |\Z)", text, re.M | re.S):
        start = date(day.year, int(m.group(1)), int(m.group(2)))
        end = date(day.year, int(m.group(3)), int(m.group(4)))
        if not start <= day <= end:
            continue
        table = re.search(r"<table>.*?</table>", m.group(5), re.S)
        if not table:
            return start, end, []
        raw_owner = re.findall(r"<tr>(.*?)</tr>", table[0], re.S)
        grid = parse_table(table[0], spans=True)
        head = [h for h, _ in grid[0]]
        days = [i for i, h in enumerate(head) if re.fullmatch(r"\d{3,4}", h.strip())]
        out, prev_owner = [], ("", 1)
        for r, raw in zip(grid[1:], raw_owner[1:]):
            cells = [t for t, _ in r]
            if len(set(cells)) <= 1 or "备注" in cells[0]:
                continue  # 备注行、合计行
            line, kind = cells[0].strip(), cells[1].strip() if len(cells) > 1 else ""
            if not line:
                continue
            if kind and "产出" not in kind:
                line, kind = f"{line} · {kind}", "brief产出"  # HC-MKT 下面合并单元格里的「内衣洗」子行
            owner = "、".join(re.findall(r'user-name="([^"]+)"', raw))
            if not owner and prev_owner[1] > 1:
                owner, prev_owner = prev_owner[0], (prev_owner[0], prev_owner[1] - 1)  # 执行人格子跨两行（PC-lux / PC-dove）
            else:
                rs = re.findall(r'<td[^>]*rowspan="(\d+)"[^>]*>(?:(?!</td>).)*user-name', raw, re.S)
                prev_owner = (owner, int(rs[-1]) if rs else 1)
            row = {"line": line, "kind": kind, "owner": owner}
            first = r[days[0]] if days else ("", 1)
            if days and first[1] >= len(days):
                row["weekly"] = first[0].strip()
            else:
                row["daily"] = {(start + timedelta(days=k)).isoformat(): r[i][0].strip() for k, i in enumerate(days)}
            out.append(row)
        return start, end, out
    return None, None, []


def day_count(row, day):
    """这一行今天的条数：按天的取当天；整周的返回 None（由用户 / 品线文档定哪天做）。"""
    if "daily" in row:
        v = row["daily"].get(day.isoformat(), "")
        n = re.match(r"\d+", v.replace(",", ""))
        return int(n[0]) if n else v or 0
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--refetch", action="store_true", help="重新拉总排期（默认用 run 目录里已拉的）")
    args = ap.parse_args()

    lines = json.loads((SKILL_DIR / "config" / "lines.json").read_text())
    day = date.fromisoformat(args.date)
    src = Path(args.run) / "sources" / "master_schedule.txt"
    if args.refetch or not src.exists():
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(lark_fetch(lines["master_schedule"]))
    start, end, rows = week_rows(src.read_text(), day)
    if not rows:
        raise SystemExit(f"总排期里找不到 {day:%m%d} 所在的那一周：排期可能还没更新")

    names = {}
    print(f"总排期 {start:%m%d}-{end:%m%d}，今天 {day:%m.%d}（{'一二三四五六日'[day.weekday()]}）\n")
    print(f"{'品线':16} {'今天':>6} {'本周':>6}  skill 品牌 / 负责人")
    for r in rows:
        key = lines["aliases"].get(r["line"], r["line"])
        conf = lines["lines"].get(key) or lines["lines"].get(r["line"].split(" · ")[-1]) or {}
        today = day_count(r, day)
        week = r.get("weekly") or str(sum(int(v) for v in r.get("daily", {}).values() if v.isdigit()) or "")
        brand = conf.get("brand")
        if brand and brand not in names:
            names[brand] = load_config(brand)["brand"]
        where = names.get(brand, "⚠️ skill 还没接") if conf else "⚠️ lines.json 没有这条品线"
        today_s = "整周" if today is None else str(today)
        print(f"{r['line']:16} {today_s:>6} {week or '—':>6}  {where} / {r['owner'] or '—'}"
              + (f"\n{'':16} ↳ 待确认：{conf['unconfirmed']}" if conf.get("unconfirmed") else ""))
    print("\n整周的品线：条数是这一周的总数，哪天做、做哪些产品和结构看品线文档（config/lines.json 的 line_doc）。"
          "\n按天的品线：明细按各品牌排期拆（parse_schedule.py）。")


if __name__ == "__main__":
    main()
