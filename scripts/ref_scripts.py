#!/usr/bin/env python3
"""Find reference scripts in the Feishu script collections for one brief.

    python3 scripts/ref_scripts.py --run runs/20261030 --brief 1030-B01 [--top 6] [--source recent_scripts|history_scripts]

Scores every script row (tables whose header has 文案) by overlap with the brief's store / direction /
selling point / audience, and prints the best rows with their date heading, so the agent reads a few
hundred lines instead of a 500KB doc.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SKILL_DIR, parse_table


def script_rows(text, brand_section=None):
    """Yield dict rows from every table that has a 文案 column, tagged with the nearest headings."""
    h1 = h3 = ""
    for m in re.finditer(r"^(#{1,5}) ([^\n]*)$|<table>.*?</table>", text, re.M | re.S):
        if m.group(1):
            level, title = len(m.group(1)), m.group(2).strip()
            if level == 1:
                h1 = title
            elif level in (2, 3, 4, 5):
                h3 = title
            continue
        if brand_section and brand_section not in h1:
            continue
        grid = parse_table(m.group(0))
        if not grid:
            continue
        header = [h.strip() for h in grid[0]]
        if "文案" not in header:
            continue
        for r in grid[1:]:
            row = dict(zip(header, r))
            if row.get("文案", "").strip():
                yield {"brand": h1, "section": h3, **row}


def tokens(s):
    s = re.sub(r"[（）()——\-：:，,。\s<>/]+", " ", s or "")
    out = set()
    for w in s.split():
        out.add(w)
        out.update(w[i:i + 2] for i in range(len(w) - 1))  # CJK bigrams
    return out


def print_edits(brief, n):
    """Reviewer rewrites from earlier days (review_doc.py sync logs them): the closest thing to the team's taste."""
    log = SKILL_DIR / "learned" / f"{brief.get('brand')}-edits.jsonl"
    if not log.exists():
        return
    edits = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    rank = lambda e: (e.get("direction") == brief.get("direction"), e.get("selling_point") == brief.get("selling_point"), e.get("date") or "")
    edits = sorted(edits, key=rank, reverse=True)[:n]
    if edits:
        print(f"### 审核人以前怎么改的（{log.name}，同方向优先）")
        for e in edits:
            print(f"- {e.get('date')} {e.get('direction','')[:16]} [{e.get('role')}]\n    原：{e['before']}\n    改：{e['after']}")
        print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--brief", required=True)
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--source", default="recent_scripts")
    ap.add_argument("--brand-section", default=None, help="only tables under this # heading, e.g. 清扬")
    ap.add_argument("--edits", type=int, default=8, help="also print the N most relevant past reviewer rewrites (0 = off)")
    args = ap.parse_args()

    run = Path(args.run)
    brief = json.loads((run / "briefs" / f"{args.brief}.json").read_text())
    if brief.get("direction_note"):
        print(f"### 内容方向括号里的「{brief['direction_note']}」是排期标签，不是口播素材")
        print(f"    {brief.get('direction_hint') or '先看下面团队同方向的脚本怎么写，不要照字面写进文案'}\n")
    if args.edits:
        print_edits(brief, args.edits)
    text = (run / "sources" / f"{args.source}.txt").read_text()
    want = {
        "store": tokens(brief["store"]),
        # score on the core direction only: bracketed schedule notes (法国人…溯源) would otherwise pull the one
        # script that happened to quote them to the top and get copied
        "direction": tokens(brief.get("direction_core") or brief["direction"]),
        "sp": tokens(brief["selling_point"]),
        "aud": tokens(brief["audience"]),
    }
    scored = []
    for row in script_rows(text, args.brand_section):
        hay = tokens(" ".join(str(v) for k, v in row.items() if k != "文案"))
        body = tokens(row.get("文案", ""))
        score = (3 * len(want["direction"] & hay) + 3 * len(want["sp"] & (hay | body))
                 + 2 * len(want["store"] & hay) + len(want["aud"] & (hay | body)))
        scored.append((score, row))
    scored.sort(key=lambda x: -x[0])
    for score, row in scored[: args.top]:
        meta = " | ".join(f"{k}={str(v).replace(chr(10), ' ')[:40]}" for k, v in row.items() if k not in ("文案",))
        print(f"\n=== score {score} · {meta}")
        print(row["文案"])
    if not scored:
        print("没有找到带「文案」列的脚本表")


if __name__ == "__main__":
    main()
