"""Shared helpers: config loading, Feishu fetch via lark-cli, HTML-ish table parsing, clause splitting."""
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


def parse_table(table_html):
    """Expand a Lark <table> (with rowspan/colspan) into a rectangular grid of plain-text cells."""
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
                out.append(text)
                if rs > 1:
                    carry[col] = [rs - 1, text]
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


def copy_hash(brief):
    """Fingerprint of the copy a reviewer saw. Approval is only valid while this still matches."""
    import hashlib
    body = "\n".join(f"{x.get('id')}|{x.get('role')}|{x.get('text', '')}" + (f"|{x['alt']}" if x.get("alt") else "")
                     for x in brief.get("segments") or [])
    return hashlib.sha1(body.encode()).hexdigest()[:12]


def approval_problem(brief):
    """Why this brief may not go to Clipo, or None. Only a recorded human review counts as approved."""
    if brief.get("status") != "approved":
        return f"status={brief.get('status')}（未审核通过）"
    a = brief.get("approval") or {}
    if not a.get("by"):
        return "没有审核记录：status 是手改的，要走 review_doc.py sync 或 approve"
    if a.get("copy_hash") != copy_hash(brief):
        return f"{a['by']} 审过之后文案又改了，要重新给人看"
    return None


def load_gold(cfg, product_key=None):
    """Gold scripts for the brand; with product_key, only that product's (each script carries `product`)."""
    p = SKILL_DIR / "gold" / f"{cfg['brand_key']}.json"
    scripts = json.loads(p.read_text())["scripts"] if p.exists() else []
    return [g for g in scripts if not product_key or g.get("product", "2x") == product_key]
