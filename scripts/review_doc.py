#!/usr/bin/env python3
"""Render the day's briefs for human review, and read reviewer edits back.

    # 1) build the review doc body (Lark XML) + a local markdown copy
    python3 scripts/review_doc.py render --config qingyang --run runs/20261030
    #    → runs/<day>/review.xml   (lark-cli docs +create --content @review.xml)
    #    → runs/<day>/review.md

    # 2) after the reviewer edited/approved in Feishu: re-fetch the doc and sync it into briefs/*.json
    python3 scripts/review_doc.py sync --config qingyang --run runs/20261030 --doc <review doc url>

    # or: the reviewer approved outside Feishu (said so in chat) — record who and their words
    python3 scripts/review_doc.py approve --config qingyang --run runs/20261030 --brief 1030-B01 --by 用户 --note "这几条通过"

The 文案 cell lists each segment as one line, 【S1 钩子】 followed by that segment's sentence; reviewers
edit the sentence in place. The 审核 cell takes 通过 / 驳回 / 修改意见.
"""
import argparse
import html
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import seg_alts, SKILL_DIR, copy_hash, day_briefs, lark_fetch, load_config, out_name, parse_table
from lint_copy import lint_brief, lint_day, lint_variety, seg_text
from common import voiced

ROLE_CN = {"hook": "钩子", "pain": "痛点", "product": "产品", "selling": "卖点", "effect": "体感", "cta": "CTA"}
HEAD = ["结构", "店铺 / 产品", "内容方向", "测试卖点", "测试人群", "文案", "和旧稿比", "QC 提醒", "审核"]


def esc(s):
    return html.escape(s or "", quote=False)


def copy_cell(b):
    lines = []
    for s in b["segments"]:
        tag = f"【S{s['id']} {s.get('title') or ROLE_CN.get(s.get('role'), s.get('role', ''))}】"
        if s.get("silent"):  # user 11-11: opens on a pinned 爆款 clip with its own sound
            f = s.get("footage") or {}
            lines.append(f"{tag}（无口播，原片原声）{f.get('name') or f.get('query', '')}" + (f"｜原片里说：{f['says']}" if f.get("says") else ""))
            continue
        lines.append(f"{tag}{seg_text(s)}")
        if seg_alts(s):
            lines.append("备选：" + " / ".join(seg_alts(s)))
    return lines


def open_questions(cfg, used):
    """Only the open questions about products in today's briefs (or about the whole brand)."""
    out = []
    for q in cfg.get("open_questions", []):
        about, text = (q.get("about", "all"), q["q"]) if isinstance(q, dict) else ("all", q)
        if about == "all" or about in used:
            out.append(text)
    return out


def closest_cell(b):
    """The earlier script (gold or recent day) this one is most like, so the reviewer can tell
    a fresh script from the same one with a few words swapped. Filled in by lint_variety."""
    c = b.get("_closest")
    if not c:
        return ["（没有可比的旧稿）"]
    return [f"最像 {c[1]}，相似度 {c[0]:.0%}" + ("（偏高）" if c[0] > 0.3 else "")]


def render(cfg, run):
    briefs = day_briefs(run, cfg)
    day = briefs[0]["date"] if briefs else ""
    rows_xml, md = [], [f"# {cfg['brand']} {day} 脚本待审", ""]
    cross = lint_day(briefs, cfg)
    for k, v in lint_variety(briefs, cfg, run).items():
        cross.setdefault(k, []).extend(v)
    for b in briefs:
        prod = cfg["products"].get(b.get("product_key"), {})
        issues = lint_brief(b, cfg) + cross.get(b["brief_id"], [])
        qc = [("✗ " if l == "error" else "! ") + m for l, m in issues] or ["机器检查通过"]
        cells = [b["brief_id"], f"{b['store']}<br/>{prod.get('full_name', '')}", b["direction"],
                 b.get("selling_point", ""), b.get("audience", ""),
                 "<br/>".join(esc(x) for x in copy_cell(b)) or "（未写）",
                 "<br/>".join(esc(x) for x in closest_cell(b)),
                 "<br/>".join(esc(x) for x in qc),
                 esc(b.get("review_notes") or "")]
        cells[1:5] = [esc(c).replace("&lt;br/&gt;", "<br/>") for c in cells[1:5]]
        rows_xml.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        md += [f"## {b['brief_id']} · {b['store']} · {b['direction']} · {b.get('selling_point','')} · {b.get('audience','')}",
               f"数量：{b['quantity']}", ""]
        md += ["完整口播：", "".join(seg_text(s) for s in voiced(b['segments'])), "", "分段（修改这里，「/」后面是同一句的其它说法）："]
        md += copy_cell(b)
        md += ["", "和旧稿比：" + "；".join(closest_cell(b))]
        md += ["", "QC：" + "；".join(qc), ""]
    notes = sorted({b.get("schedule_notes") for b in briefs if b.get("schedule_notes")})
    used = sorted({b["product_key"] for b in briefs if b.get("product_key") in cfg["products"]})
    xml = (f"<title>{esc(cfg['brand'])} {day} 脚本待审</title>"
           f"<callout emoji=\"📝\"><p>每个 brief 一行，对应 {cfg['brief_size']} 条视频。直接在「文案」里改句子"
           "（保留【S1 …】分段标记；一句的几个说法用「 / 」隔开，第一个是主版）。「和旧稿比」写出这条的开头最像哪条旧稿和相似度（卖点句、机制句本来就复用，不算）。Clipo 按这一版为 10 条视频逐段改写，CTA 也改写；在「审核」填 通过 / 驳回 / 修改意见。审核完回复 Agent「审完了」。</p>"
           + "".join(f"<p>排期备注：{esc(n)}</p>" for n in notes)
           + "".join(f"<p>今天的机制（{esc(cfg['products'][k]['full_name'])}）：{esc(' / '.join(cfg['products'][k]['mechanism']['spoken']))}"
                     f"，{esc(cfg['products'][k]['mechanism'].get('verified_on', '未核对'))} 核对</p>" for k in used)
           + "".join(f"<p>待确认：{esc(q)}</p>" for q in open_questions(cfg, used)) + "</callout>"
           "<table><colgroup><col width=\"90\"/><col width=\"120\"/><col width=\"140\"/><col width=\"110\"/>"
           "<col width=\"80\"/><col width=\"480\"/><col width=\"180\"/><col width=\"180\"/><col width=\"120\"/></colgroup>"
           "<thead><tr>" + "".join(f"<th>{h}</th>" for h in HEAD) + "</tr></thead><tbody>"
           + "".join(rows_xml) + "</tbody></table>")
    (run / out_name(cfg, "review.xml")).write_text(xml)
    (run / out_name(cfg, "review.md")).write_text("\n".join(md))
    print(f"{len(briefs)} 个 brief → {run / out_name(cfg, 'review.xml')} / {run / out_name(cfg, 'review.md')}")


def parse_copy(text):
    segs, cur = [], None
    for line in [l.strip() for l in text.splitlines()]:
        if not line:
            continue
        m = re.match(r"【S(\d+)\s*([^】]*)】\s*(.*)", line)
        if m:
            cur = {"id": m.group(1), "title": m.group(2).strip(), "text": m.group(3).strip()}
            segs.append(cur)
        elif cur is not None and re.match(r"备选[:：]", line):
            cur["alt"] = [x.strip() for x in re.split(r"\s+/\s+|／", re.sub(r"^备选[:：]\s*", "", line)) if x.strip()]
        elif cur is not None:  # reviewer broke a line: keep it as one sentence
            cur["text"] += re.sub(r"^[-•·]\s*", "", line)
    return segs


def sync(cfg, run, doc):
    text = lark_fetch(doc)
    (run / "review_fetched.txt").write_text(text)
    table = re.search(r"<table>.*?</table>", text, re.S)
    if not table:
        raise SystemExit("评审文档里找不到表格")
    grid = parse_table(table.group(0))
    head = [h.strip() for h in grid[0]]
    changed, edits = 0, []
    for r in grid[1:]:
        row = dict(zip(head, r))
        bid = row.get("结构", "").strip()
        p = run / "briefs" / f"{bid}.json"
        if not p.exists():
            print(f"⚠️ 评审表里有未知 brief {bid!r}，忽略")
            continue
        b = json.loads(p.read_text())
        new = parse_copy(row.get("文案", ""))
        old = {s["id"]: s for s in b["segments"]}
        merged = []
        for s in new:
            base = dict(old.get(s["id"], {"id": s["id"], "role": ""}))
            if base.get("silent"):  # the silent 爆款 opening has no copy to edit; keep it as is
                merged.append(base)
                continue
            if seg_text(base) != s["text"]:
                changed += 1
                edits.append({"date": b.get("date"), "brief_id": bid, "direction": b.get("direction"),
                              "product_key": b.get("product_key"),
                              "selling_point": b.get("selling_point"), "role": base.get("role"),
                              "before": seg_text(base), "after": s["text"]})
            base.update(text=s["text"], title=s["title"] or base.get("title"))
            if s.get("alt"):
                base["alt"] = s["alt"]
            merged.append(base)
        verdict = row.get("审核", "").strip()
        b["segments"] = merged or b["segments"]
        b["review_notes"] = verdict
        if verdict.startswith("通过"):
            b["status"] = "approved"
            b["approval"] = {"by": "飞书待审文档", "doc": doc, "note": verdict, "at": time.strftime("%Y-%m-%d %H:%M"),
                             "copy_hash": copy_hash(b)}
        elif verdict.startswith("驳回"):
            b["status"] = "rejected"
            b.pop("approval", None)
        elif verdict:
            b["status"] = "changes_requested"
            b.pop("approval", None)
        p.write_text(json.dumps(b, ensure_ascii=False, indent=2))
        print(f"{bid}: {b['status']:18} {verdict[:40]}")
    print(f"同步完成，{changed} 个分段被审核人改过（已以飞书为准）")
    if edits:
        log_edits(cfg, run, edits)


def log_edits(cfg, run, edits):
    """Reviewer rewrites are the best signal of how the team wants copy to sound: keep them per run and
    in a per-brand log that `brief_card.py` shows before the next day's writing."""
    (run / "review_edits.json").write_text(json.dumps(edits, ensure_ascii=False, indent=2))
    log = SKILL_DIR / "learned" / f"{cfg['brand_key']}-edits.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    seen = {(e["brief_id"], e["before"], e["after"]) for e in map(json.loads, log.read_text().splitlines())} if log.exists() else set()
    with log.open("a") as f:
        for e in edits:
            if (e["brief_id"], e["before"], e["after"]) not in seen:
                f.write(json.dumps({**e, "logged_at": time.strftime("%Y-%m-%d")}, ensure_ascii=False) + "\n")
    print(f"审核人改写 {len(edits)} 处 → {run / 'review_edits.json'}，并追加到 {log}")
    for e in edits:
        print(f"  {e['brief_id']} [{e['role']}]\n    - {e['before']}\n    + {e['after']}")


def approve(cfg, run, ids, by, note):
    """Record a human approval given outside Feishu (e.g. the user said so in chat). Pins the copy they saw."""
    for bid in ids:
        p = run / "briefs" / f"{bid}.json"
        b = json.loads(p.read_text())
        errs = [m for l, m in lint_brief(b, cfg) if l == "error"]
        if errs:
            print(f"❌ {bid} 有 lint 错误，不能标通过：{errs[:2]}")
            continue
        b["status"] = "approved"
        b["approval"] = {"by": by, "note": note, "at": time.strftime("%Y-%m-%d %H:%M"), "copy_hash": copy_hash(b)}
        p.write_text(json.dumps(b, ensure_ascii=False, indent=2))
        print(f"✅ {bid} 已记为 {by} 审核通过")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["render", "sync", "approve"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--doc")
    ap.add_argument("--brief", action="append", help="approve: brief id，可重复")
    ap.add_argument("--by", help="approve: 谁审的（人，不能是 agent）")
    ap.add_argument("--note", help="approve: 审核人原话")
    args = ap.parse_args()
    cfg, run = load_config(args.config), Path(args.run)
    if args.cmd == "render":
        render(cfg, run)
    elif args.cmd == "sync":
        sync(cfg, run, args.doc)
    else:
        if not (args.brief and args.by and args.note):
            raise SystemExit("approve 要写 --brief、--by（审核人）和 --note（审核人原话）")
        approve(cfg, run, args.brief, args.by, args.note)


if __name__ == "__main__":
    main()
