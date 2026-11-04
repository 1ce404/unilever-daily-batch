#!/usr/bin/env python3
"""Lint brief scripts against the QC doc rules that can be checked mechanically.

    python3 scripts/lint_copy.py --config qingyang --run runs/20261030 [--brief 1030-B01]

Checks facts and compliance, plus variety: a script may not read like a gold script or a recent day's
script with a few words swapped (lint_variety). Style itself is not checked; see references/writing.md.
Exit 1 when any brief has errors. Writes runs/<day>/lint.json. Warnings are for the reviewer.
Also importable: lint_text() / lint_brief() / lint_script() are reused by clipo_batch.py on Clipo's rewritten copy.
"""
import argparse
import difflib
import json
import re
import sys
from datetime import date
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import day_briefs, display_len, out_name, load_config, load_gold, normalize_copy, split_clauses, visible_len

PRICE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*元|拍[一二三四五六七八九十\d]+发[一二三四五六七八九十\d]+|\d+\s*[gG克]")


def lint_text(text, cfg, product):
    """Issues for one line of copy: [(level, message)]."""
    issues = []
    bw = cfg["banned_words"]
    scrubbed = text
    for ok in bw.get("allow_exceptions", []):
        scrubbed = scrubbed.replace(ok, "")
    for w in bw["error"]:
        if w in scrubbed:
            issues.append(("error", f"禁用词「{w}」"))
    allowed = set(product.get("allow_words", []))
    for w in bw["warn"] + product.get("warn_words", []):
        if w in scrubbed and w not in allowed:
            issues.append(("warn", f"卡审风险词「{w}」"))
    for rx in bw.get("regex_error", []):
        for m in re.finditer(rx, scrubbed):
            issues.append(("error", f"极限词/时间承诺「{m.group(0)}」"))
    for rx in bw.get("regex_warn", []):
        for m in re.finditer(rx, scrubbed):
            issues.append(("warn", f"可能是时间承诺「{m.group(0)}」"))
    for w in bw.get("scene_warn", []):
        if w in text:
            issues.append(("warn", f"「{w}」是具体场景，素材里没有这类画面（见 config.products.*.footage），换成画面接得住的说法"))
    for w in product.get("speaker_warn", []):
        if w in text:
            issues.append(("warn", f"「{w}」和这个产品的受众 / 说话人对不上（{product.get('audience_note', '')}）"))
    lim = cfg["copy_limits"]
    for c in split_clauses(text):
        n = display_len(c)
        if n > lim["clause_hard_max"]:
            issues.append(("error", f"短句 {n} 字超过 {lim['clause_hard_max']}：「{c}」（字幕放不下，加标点断开）"))
        elif lim.get("clause_warn_max") and n > lim["clause_warn_max"]:
            issues.append(("warn", f"短句 {n} 字偏长（建议 ≤ {lim['clause_warn_max']} 字，单句单屏最佳）：「{c}」"))
        elif lim.get("clause_warn_min") and n <= lim["clause_warn_min"]:
            issues.append(("warn", f"短句「{c}」只有 {n} 字：字幕易一闪而过，建议用逗号连入相邻句"))
    flat = normalize_copy(text)
    for alias in product["name_aliases"]:
        if normalize_copy(alias) in flat and not any(a in text for a in product["name_aliases"]):
            issues.append(("error", f"产品名「{alias}」被标点或空格拆开，字幕会分成两屏"))
    allowed = product["mechanism"]["allowed_numbers"]
    for m in PRICE_RE.finditer(text):
        tok = m.group(0)
        if not any(a in tok or tok in a for a in allowed):
            issues.append(("error", f"机制/价格「{tok}」不在当前机制 {allowed} 里"))
    for w in product.get("foreign_claims", []):
        if w in text:
            issues.append(("error", f"「{w}」是别的产品/系列的卖点，不同系列信息不可混淆"))
    if re.search(r"[\U0001F300-\U0001FAFF]", text):
        issues.append(("error", "含 emoji"))
    if "[#" in text:
        issues.append(("error", "正文里出现 [#…] 语音指令，会被念出来"))
    return issues


def seg_text(seg):
    return seg.get("text", "")


def lint_brief(brief, cfg):
    issues = []
    pk = brief.get("product_key")
    if pk not in cfg["products"]:
        return [("error", f"product_key={pk!r} 不在配置里")]
    product = cfg["products"][pk]
    segs = brief.get("segments") or []
    if not segs:
        return [("error", "还没有写分段")]
    if not brief.get("persona"):
        issues.append(("warn", "没写说话人（persona）"))
    texts = [seg_text(s) for s in segs]
    for s, t in zip(segs, texts):
        if not t.strip():
            issues.append(("error", f"S{s.get('id')} 没有文案"))
            continue
        issues += [(lvl, f"S{s['id']} {msg}") for lvl, msg in lint_text(t, cfg, product)]
        if s.get("alt"):
            issues += [(lvl, f"S{s['id']} 备选句 {msg}") for lvl, msg in lint_text(s["alt"], cfg, product)]
    if any(i[0] == "error" and "没有文案" in i[1] for i in issues):
        return issues
    issues += lint_script(texts, segs, brief, cfg, product)
    if not brief.get("gold_id"):
        issues.append(("warn", "没填 gold_id：先用 imitate.py 挑范本再仿写（references/writing.md）"))
    camp = cfg.get("campaign")
    if camp and brief.get("date", "") <= camp["until"] and not any(camp["name"] in t for t in texts):
        issues.append(("warn", f"{camp['name']}期间没提「{camp['name']}」（放进机制或 CTA 句）"))
    stale = mechanism_age(product, brief.get("date"))
    if stale is not None and stale > STALE_DAYS:
        issues.append(("warn", f"机制 {product['mechanism']['spoken']} 已 {stale} 天没核对（{product['mechanism']['verified_on']}），请审核人确认"))
    return issues


STALE_DAYS = 7


def mechanism_age(product, day):
    """Days since the product's mechanism was last verified against the sources, or None."""
    v = product.get("mechanism", {}).get("verified_on")
    if not v or not day:
        return None
    return (date.fromisoformat(day) - date.fromisoformat(v)).days


def lint_day(briefs, cfg):
    """Cross-brief checks: Clipo's rewrite does not create distance between briefs, so the base copy must.
    Product names and keep_terms are meant to repeat, so they are stripped before comparing.
    Returns {brief_id: [(level, msg)]}."""
    out = {}
    fixed = sorted({t for p in cfg["products"].values()
                    for t in p.get("keep_terms", []) + p["name_aliases"] + p["mechanism"]["allowed_numbers"]}
                   | {(cfg.get("campaign") or {}).get("name", "")} - {""}, key=len, reverse=True)
    def own_words(c):
        for t in fixed:
            c = c.replace(t, "")
        return visible_len(c) >= 7
    def clauses(b):
        return {c for s in b.get("segments", []) if s.get("role") != "cta"
                for c in split_clauses(seg_text(s)) if own_words(c)}
    for a, b in combinations([b for b in briefs if b.get("segments")], 2):
        shared = clauses(a) & clauses(b)
        if shared:
            for x, y in ((a, b), (b, a)):
                out.setdefault(x["brief_id"], []).append(
                    ("warn", f"和 {y['brief_id']} 有相同的句子：{'、'.join(sorted(shared)[:3])}"))
        if a.get("product_key") == b.get("product_key") and a.get("direction") == b.get("direction"):
            ha, hb = (normalize_copy(seg_text(x["segments"][0]))[:6] for x in (a, b))
            if ha and ha == hb:
                for x, y in ((a, b), (b, a)):
                    out.setdefault(x["brief_id"], []).append(
                        ("warn", f"和 {y['brief_id']} 同方向、开头一样「{ha}」：换钩子类型或说话人"))
    return out


def body(segs, cfg):
    """Non-CTA copy with product names, selling-point terms and mechanism stripped: what a writer actually wrote."""
    fixed = sorted({t for p in cfg["products"].values()
                    for t in p.get("keep_terms", []) + p["name_aliases"] + p["mechanism"]["allowed_numbers"]}
                   | {cfg["brand"], (cfg.get("campaign") or {}).get("name", "")} - {""}, key=len, reverse=True)
    t = "".join(seg_text(s) for s in segs if s.get("role") != "cta")
    for f in fixed:
        t = t.replace(f, "")
    return re.sub(r"[^一-鿿A-Za-z0-9]", "", t)


def skeleton(segs):
    """Segment order plus the first two characters of each non-CTA line (很多/其实/试试/后来/别慌…)."""
    return ([s.get("role") for s in segs],
            [re.sub(r"[^一-鿿A-Za-z]", "", seg_text(s))[:2] for s in segs if s.get("role") != "cta"])


def earlier_copy(run, brief, cfg):
    """What this brief must not resemble: every gold script, and the same product's briefs from the
    previous `history_days` days. [(label, segments)]"""
    out = [(f"范本 {g['id']}", [{"role": x["role"], "text": x["text"]} for x in g["beats"]])
           for g in load_gold(cfg, brief.get("product_key")) if brief["brief_id"] not in g["id"]]
    day = brief.get("date", "").replace("-", "")
    keep = cfg["copy_limits"].get("history_days", 7)
    for d in sorted(Path(run).resolve().parent.glob("2*")):
        if not (d.name.isdigit() and len(d.name) == 8 and d.name < day):
            continue
        if (date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}") -
                date.fromisoformat(f"{d.name[:4]}-{d.name[4:6]}-{d.name[6:]}")).days > keep:
            continue
        for p in (d / "briefs").glob("*.json"):
            b = json.loads(p.read_text())
            if b.get("segments") and b.get("product_key") == brief.get("product_key"):
                out.append((b["brief_id"], b["segments"]))
    return out


def lint_variety(briefs, cfg, run):
    """A batch traced from the same gold scripts reads as one video with a few words swapped (11.03/11.04).
    Calibrated on the 7 scripts the user approved on 11.02: they are at most 32% alike in wording and
    share at most one opening word in the same slot. Above that is tracing. Returns {brief_id: [(level, msg)]}."""
    lim = cfg["copy_limits"].get("similar_max", 0.35)
    out = {}
    todays = [b for b in briefs if b.get("segments")]
    for b in todays:
        mine, (roles, heads) = body(b["segments"], cfg), skeleton(b["segments"])
        others = earlier_copy(run, b, cfg) + [(o["brief_id"], o["segments"]) for o in todays if o is not b]
        worst = None
        for label, segs in others:
            r = difflib.SequenceMatcher(None, mine, body(segs, cfg), autojunk=False).ratio()
            if worst is None or r > worst[0]:
                worst = (r, label)
            if b.get("template"):
                continue  # user-given structure: same order is intended, wording similarity still applies
            o_roles, o_heads = skeleton(segs)
            same = [h for h, o in zip(heads, o_heads) if h == o]
            if roles == o_roles and len(same) >= 2:
                out.setdefault(b["brief_id"], []).append(
                    ("error", f"和 {label} 段落顺序一样，{len(same)} 句开头也一样（{'、'.join(same)}）：换切入角度和句子顺序，不要逐句改"))
        if worst and worst[0] > lim:
            out.setdefault(b["brief_id"], []).append(
                ("error", f"和 {worst[1]} 文字相似度 {worst[0]:.0%}（上限 {lim:.0%}）：换说法重写，不要在原句上换词"))
        b["_closest"] = worst  # review doc shows it next to the copy
    return out


def lint_script(texts, segs, brief, cfg, product):
    """Whole-script checks; also used on Clipo's rewritten copy."""
    issues = []
    cta = [t for s, t in zip(segs, texts) if s.get("role") == "cta"] or texts[-1:]
    if not any(p in cta[-1] for p in product["cta_required"]):
        issues.append(("error", f"结尾没有「{'/'.join(product['cta_required'])}」"))
    named = [k for k, t in enumerate(texts) if any(a in t for a in product["name_aliases"])]
    if not named:
        issues.append(("error", f"没有完整说出产品名「{product['full_name']}」"))
    else:
        early = cfg["copy_limits"].get("name_not_before", 0)
        if named[0] < early and not brief.get("template"):
            issues.append(("warn", f"S{named[0] + 1} 就说了产品名：尽量放到第 {early + 1} 段以后，前面先讲头发的状态"))
        prod_seg = next((k for k, s in enumerate(segs) if s.get("role") == "product"), None)
        if prod_seg is not None and prod_seg not in named:
            issues.append(("error", f"S{prod_seg + 1} 是产品段但没说完整产品名「{product['full_name']}」（不能用「这瓶」「它」代替）"))
        sp = product["selling_points"].get(brief.get("selling_point", ""))
        first_sp = next((k for k, t in enumerate(texts) if sp and any(m in t for m in sp["must_mention"])), None)
        if first_sp is not None and first_sp not in named and (not named or named[0] > first_sp):
            issues.append(("error", f"S{first_sp + 1} 第一次讲卖点时没带产品名：这句要说是「{product['full_name']}」的（卖点和产品名要在同一句，或产品名已在前面出现）"))
    for k, t in enumerate(texts):
        if re.search(r"(这|那)(瓶|款|个洗发水)", t) and k not in named and (not named or named[0] > k):
            issues.append(("error", f"S{k + 1}「{re.search(r'(这|那)(瓶|款|个洗发水)', t).group(0)}」指代产品：这一句要说完整产品名「{product['full_name']}」"))
    sp = product["selling_points"].get(brief.get("selling_point", ""))
    if sp and not any(m in t for t in texts for m in sp["must_mention"]):
        issues.append(("error", f"测试卖点「{brief['selling_point']}」没有出现（需要提到 {sp['must_mention']}）"))
    base = " ".join(seg_text(s) for s in segs)
    for term in product.get("keep_terms", []):
        if term in base and not any(term in t for t in texts):
            issues.append(("error", f"术语「{term}」没有逐字保留（可能被改写成近义词）"))
    lim = cfg["copy_limits"]
    n = sum(visible_len("".join(split_clauses(t))) for t in texts)
    if not lim["combo_chars_min"] <= n <= lim["combo_chars_max"]:
        cps = lim["chars_per_second"]
        issues.append(("warn", f"整条 {n} 字（约 {n/cps:.0f} 秒），建议 {lim['combo_chars_min']}~{lim['combo_chars_max']}"))
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--brief")
    args = ap.parse_args()
    cfg = load_config(args.config)
    run = Path(args.run)
    every = day_briefs(run, cfg)
    cross = lint_day(every, cfg)
    for k, v in lint_variety(every, cfg, run).items():
        cross.setdefault(k, []).extend(v)
    report, bad = {}, 0
    for b in every:
        if args.brief and b["brief_id"] != args.brief:
            continue
        iss = lint_brief(b, cfg) + cross.get(b["brief_id"], [])
        report[b["brief_id"]] = [{"level": l, "msg": m} for l, m in iss]
        errs = [m for l, m in iss if l == "error"]
        warns = [m for l, m in iss if l == "warn"]
        bad += bool(errs)
        print(f"{'❌' if errs else ('⚠️ ' if warns else '✅')} {b['brief_id']} {b['direction'][:24]} · {len(errs)} 错误 {len(warns)} 提醒")
        for m in errs:
            print("    ✗", m)
        for m in warns:
            print("    !", m)
    (run / out_name(cfg, "lint.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
