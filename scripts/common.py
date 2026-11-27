# ---------- 文件契约 ----------
# [INPUT]: 依赖 Python 标准库读取配置、解析表格与计算文案指纹。
#          通过 lark-cli 读取飞书文档；读取品牌、产品配置与金标脚本。
# [OUTPUT]: 提供配置合并、卡审库读取、飞书读取、表格解析、分句与字幕宽度计算。
#           提供 brief 筛选、文案、AIGC 素材与 demo 人审校验、品牌文件命名；demo 默认对所有品牌开启。
# [POS]: scripts 的共享规则底座；排期、文案、人审与批量入口依赖它。
#        人审凭据绑定文案指纹，防止改稿沿用旧批准。
# [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""Shared helpers: config loading, Feishu fetch via lark-cli, HTML-ish table parsing, clause splitting."""
import csv
import functools
import html
import json
import re
import subprocess
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent


def load_config(path_or_key):
    """Brand file config/<brand>.json (sources, store mapping, shared rules) merged with one file per
    product under config/<brand>/<product_key>.json. Scripts see cfg["products"][key] as before."""
    p = Path(path_or_key)
    if not p.exists():
        p = SKILL_DIR / "config" / f"{path_or_key}.json"
    cfg = json.loads(p.read_text())
    cfg.setdefault("products", {})
    for f in sorted((p.parent / p.stem).glob("*.json")):
        cfg["products"][f.stem] = json.loads(f.read_text())
    return cfg


# ---------- 卡审库（用户 11-24）----------
# 产品 config 写 "review_blocks": "<库名>" 时启用 references/review-blocks/<库名>.csv。
# 库是数据，不进 SKILL.md：lint 按它机械拦文案，brief_card 只打一行指引，aigc_gen 按画面条目提醒提示词。
@functools.lru_cache(maxsize=None)
def _review_rows(name):
    path = SKILL_DIR / "references" / "review-blocks" / f"{name}.csv"
    rows = []
    with path.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            phrase = (r.get("违规词/表述") or "").strip()
            if phrase:
                rows.append({"id": r["卡审点"], "name": r["卡审点名称"], "dim": r["维度"], "risk": r["风险等级"],
                             "phrase": phrase, "fix": (r.get("合规替换建议") or "").strip() or "直接删除"})
    return tuple(rows)


def review_blocks(product, kind=None):
    """产品启用的卡审条目。kind="copy" 只要文案相关（维度含 文案 / 医疗混淆 / 资质 / 功效 / 违禁词），
    kind="visual" 只要纯画面条目；产品 allow_words 里的说法不拦。"""
    name = (product or {}).get("review_blocks")
    if not name:
        return []
    allow = set(product.get("allow_words") or [])
    copy_dims = ("文案", "医疗混淆", "资质", "功效", "违禁词")
    out = []
    for r in _review_rows(name):
        if r["phrase"] in allow:
            continue
        is_copy = any(d in r["dim"] for d in copy_dims) and r["dim"] not in ("画面", "画面·资质")
        if kind == "copy" and not is_copy or kind == "visual" and is_copy:
            continue
        out.append(r)
    return out


def lark_fetch(doc_url):
    """Return the document body (Lark XML-ish markdown) of a wiki/docx URL."""
    out = subprocess.run(
        ["lark-cli", "docs", "+fetch", "--doc", doc_url, "--doc-format", "markdown"],
        capture_output=True, text=True, timeout=180,
    )
    if out.returncode != 0:
        raise RuntimeError(f"lark-cli fetch failed for {doc_url}: {out.stderr[-800:]}")
    data = json.loads(out.stdout)
    if not data.get("ok"):
        raise RuntimeError(f"lark-cli fetch error for {doc_url}: {json.dumps(data, ensure_ascii=False)[:800]}")
    return data["data"]["document"]["content"]


def cell_text(raw):
    """Lark cell HTML → plain text, keeping <br/> as newlines."""
    t = re.sub(r"<br\s*/?>", "\n", raw)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).strip()


def parse_table(table_html, spans=False):
    """Expand a Lark <table> (with rowspan/colspan) into a rectangular grid of plain-text cells.
    spans=True returns (text, colspan) per cell: the master schedule writes a weekly total as one colspan="5" cell."""
    rows = re.findall(r"<tr>(.*?)</tr>", table_html, re.S)
    grid, carry = [], {}  # carry[col] = [remaining_rows, text]
    for row in rows:
        cells = re.findall(r"<t([dh])([^>]*)>(.*?)</t[dh]>", row, re.S)
        out, col = [], 0
        pending = list(cells)
        while pending or col in carry:
            if col in carry:
                left, text = carry[col]
                out.append(text)
                if left <= 1:
                    del carry[col]
                else:
                    carry[col] = [left - 1, text]
                col += 1
                continue
            _, attrs, raw = pending.pop(0)
            text = cell_text(raw)
            rs = int((re.search(r'rowspan="(\d+)"', attrs) or [0, 1])[1])
            cs = int((re.search(r'colspan="(\d+)"', attrs) or [0, 1])[1])
            for _ in range(cs):
                out.append((text, cs) if spans else text)
                if rs > 1:
                    carry[col] = [rs - 1, (text, cs) if spans else text]
                col += 1
        grid.append(out)
    return grid


def split_clauses(text):
    """Split copy into short clauses on reading punctuation; decimal points (89.9) don't split."""
    text = re.sub(r"\[#[^\]]*\]", "", text)
    parts = re.split(r"[，。！？；：、…!?;:,]|(?<!\d)\.|\.(?!\d)", text)
    return [p.strip(" 　“”\"'（）()【】") for p in parts if p.strip(" 　“”\"'（）()【】")]


def visible_len(s):
    return len(re.sub(r"\s", "", s))


def display_len(s):
    """Subtitle width: a CJK character is 1, a half-width letter or digit is 0.5 (HONOR X7e = 4)."""
    s = re.sub(r"\s", "", s)
    return sum(0.5 if ord(ch) < 128 else 1 for ch in s)


def normalize_copy(text):
    """For exact-match comparison: drop voice instructions, punctuation and whitespace."""
    t = re.sub(r"\[#[^\]]*\]", "", text)
    t = re.sub(r"[\s，。！？；：、…,.!?;:“”\"'（）()【】~～]", "", t)
    return t.lower()


def seg_alts(seg):
    """Other ways to say this line (team scripts give 2~3 per line). Stored as a list; a string is one alternative."""
    a = seg.get("alt") or []
    return [a] if isinstance(a, str) else [x for x in a if x]


def voiced(segs):
    """The spoken segments. A `silent` segment (user 11-11: open on a pinned 爆款 clip with its own sound, no
    voiceover) has no copy; every copy check and the platform's voiceover rewrite skip it."""
    return [s for s in segs or [] if not s.get("silent")]


def copy_hash(brief):
    """Fingerprint of the copy a reviewer saw. Approval is only valid while this still matches."""
    import hashlib
    body = "\n".join(f"{x.get('id')}|{x.get('role')}|{x.get('text', '')}" + "".join(f"|{a}" for a in seg_alts(x))
                     + (f"|footage:{(x.get('footage') or {}).get('segment_id')}" if x.get("silent") else "")
                     for x in brief.get("segments") or [])
    return hashlib.sha1(body.encode()).hexdigest()[:12]


def approval_problem(brief):
    """Why this brief may not go to Clipo. Demo-only permission never changes approval."""
    from checked_batch_authorization import permits_checked_batch
    if permits_checked_batch(brief):
        return None
    from demo_authorization import permits_preview
    if permits_preview(brief):
        return None
    if brief.get("status") != "approved":
        return f"status={brief.get('status')}（未审核通过）"
    a = brief.get("approval") or {}
    if not a.get("by"):
        return "没有审核记录：status 是手改的，要走 review_doc.py sync 或 approve"
    if a.get("copy_hash") != copy_hash(brief):
        return f"{a['by']} 审过之后文案又改了，要重新给人看"
    return None


def demo_first(cfg, brief):
    """用户 11-22：所有品牌都走 脚本人审 →（AIGC 素材人审）→ 每个脚本 1 条 demo 人审 → 量产。
    默认开启；只有 brief / config 显式写 false 才跳过（须是用户原话授权）。"""
    return bool(brief.get("demo_first", cfg.get("demo_first", True)))


def aigc_tags(brief):
    """brief 画面计划里引用的 AI 素材标签（以「AI」开头）。"""
    return sorted({t for sg in brief.get("segments") or []
                   for t in (sg.get("visual_tags") or []) + ((sg.get("footage_pool") or {}).get("tags") or [])
                   if t.startswith("AI")})


def aigc_problem(brief):
    """用户 11-22：脚本要用 AIGC 素材时，素材先给人审，通过后才出 demo。None = 可以提交。
    审核凭据记住当时审过的标签；画面计划新增未审的 AI 标签要重新给人看。"""
    from checked_batch_authorization import permits_checked_batch
    if permits_checked_batch(brief):
        return None
    from demo_authorization import permits_preview
    if permits_preview(brief):
        return None
    tags = aigc_tags(brief)
    if not tags:
        return None
    a = brief.get("aigc_approval") or {}
    if not a.get("by"):
        return f"AIGC 素材 {tags} 还没人审过（review_doc.py approve-aigc）"
    missing = sorted(set(tags) - set(a.get("tags") or []))
    if missing:
        return f"{a['by']} 审过素材之后画面计划又加了 {missing}，要重新给人看"
    return None


def demo_problem(brief):
    """Why this demo-first brief may not go to bulk yet, or None. Only a person's recorded OK of the demo counts,
    and it must be for the copy the platform script was built from."""
    from checked_batch_authorization import permits_checked_batch
    if permits_checked_batch(brief):
        return None
    a = brief.get("demo_approval") or {}
    if not a.get("by"):
        return "demo 还没人审过（review_doc.py approve-demo）"
    if a.get("copy_hash") != copy_hash(brief):
        return f"{a['by']} 审过 demo 之后文案又改了，要重新出 demo"
    return None


def load_gold(cfg, product_key=None):
    """Gold scripts for the brand; with product_key, only that product's (each script carries `product`)."""
    p = SKILL_DIR / "gold" / f"{cfg['brand_key']}.json"
    scripts = json.loads(p.read_text())["scripts"] if p.exists() else []
    return [g for g in scripts if not product_key or g.get("product", "2x") == product_key]


def day_briefs(run, cfg):
    """All briefs of this brand in a day folder (several brands share runs/<day>/briefs/)."""
    out = []
    for p in sorted((Path(run) / "briefs").glob("*.json")):
        b = json.loads(p.read_text())
        if b.get("brand", cfg["brand_key"]) == cfg["brand_key"]:
            out.append(b)
    return out


def out_name(cfg, name):
    """Per-brand file name inside a day folder. 清扬 keeps the bare names (existing runs)."""
    if cfg["brand_key"] == "qingyang":
        return name
    stem, dot, ext = name.partition(".")
    return f"{stem}-{cfg['brand_key']}{dot}{ext}"
