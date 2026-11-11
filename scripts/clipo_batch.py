#!/usr/bin/env python3
"""Run approved briefs through Clipo Remix: preflight → submit → poll (generate, render, AI review) → topup → report.

    python3 scripts/clipo_batch.py preflight --config qingyang --run runs/20261104
    python3 scripts/clipo_batch.py submit    --config qingyang --run runs/20261104 [--brief 1104-B01] [--dry-run] [--scripts-only] [--retry-failed]
    python3 scripts/clipo_batch.py poll      --config qingyang --run runs/20261104 [--wait 3600]
    python3 scripts/clipo_batch.py topup     --config qingyang --run runs/20261104 [--dry-run]
    python3 scripts/clipo_batch.py rereview  --config qingyang --run runs/20261104 [--dry-run] [--all]   # 改了平台审核提示词后，驳回的重新审
    python3 scripts/clipo_batch.py report    --config qingyang --run runs/20261104   # report.md + delivery.md
    python3 scripts/clipo_batch.py share     --config qingyang --run runs/20261104 [--dry-run] [--partial]   # 每个 brief 一个分享链接
    python3 scripts/clipo_batch.py status    --config qingyang --run runs/20261104   # where is today's run? (offline)

This script only moves the flow along. Each brief is ONE platform generation task (later rounds and top-ups are
added with /continue) and every generation carries the brand's compliance watermark (config `watermark`). The agent writes each brief's copy at step ④ and a human approves it;
submit has the platform build the script from that copy verbatim (packaging from the product prompt
video_script_create), turns on AI rewrite for every segment, verifies it, then generates quantity × generate_factor cuts in rounds of ≤ generate_chunk. Only
briefs a human approved (review_doc.py sync / approve, copy unchanged since) with zero lint errors go out. poll
re-lints every rewritten version. State lives in runs/<day>/clipo/state.json; every step is idempotent and safe to re-run.
"""
import argparse
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import seg_alts, voiced, approval_problem, day_briefs, load_config, normalize_copy, out_name
from lint_copy import lint_brief, lint_day, lint_variety, lint_script, lint_text, seg_text

BASE = None
CFG = None


def api(method, path, body=None, timeout=180, tries=3):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    for attempt in range(tries):
        req = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                res = json.load(r)
            break
        except urllib.error.HTTPError as e:
            res = json.loads(e.read().decode() or "{}")
            if e.code < 500:
                break
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries - 1:
                raise
        time.sleep(2 * (attempt + 1))
    if res.get("code") != "ok":
        raise RuntimeError(f"{method} {path} → {res.get('code')}: {res.get('message')} {json.dumps(res.get('data'), ensure_ascii=False)[:400]}")
    return res["data"]


class State:
    def __init__(self, run):
        self.path = Path(run) / "clipo" / out_name(CFG, "state.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {"videos": {}}
        self._seen = {key: set(self.data.get(key) or {}) for key in ("briefs", "videos")}

    def save(self):
        """11-11: a background poll and a submit share this file; a plain dump made the poll erase the 5 briefs
        submit had just added. Briefs / videos on disk that this process never loaded are kept."""
        if self.path.exists():
            disk = json.loads(self.path.read_text())
            for key in ("briefs", "videos"):
                mine = self.data.setdefault(key, {})
                for k, v in (disk.get(key) or {}).items():
                    if k not in mine and k not in self._seen.get(key, set()):
                        mine[k] = v
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2))
        self._seen = {key: set(self.data.get(key) or {}) for key in ("briefs", "videos")}

    @property
    def videos(self):
        return self.data["videos"]


def load_briefs(run, only=None):
    return [b for b in day_briefs(run, CFG) if not only or b["brief_id"] == only]


# ---------- preflight ----------

def desc_ok(p):
    return bool((p.get("description") or "").strip())


def list_segments(pid):
    out, skip = [], 0
    while True:
        page = api("GET", f"/products/{pid}/segments?skip={skip}&limit=100")
        out += page["items"]
        skip += 100
        if skip >= page["total"]:
            return out


def tag_counts(pid, segments=None):
    """Clips per tag, excluding clips that failed the platform's clip review (风险)."""
    counts = {"全部": 0}
    for s in segments if segments is not None else list_segments(pid):
        if s.get("review_result") == "reject":
            continue
        counts["全部"] += 1
        for t in s.get("tags") or []:
            counts[t] = counts.get(t, 0) + 1
    return counts


def cmd_preflight(cfg, run, args):
    out, bad = {}, False
    used = {b["product_key"] for b in load_briefs(run) if b.get("product_key")} or set(cfg["products"])
    for key in sorted(used):
        prod = cfg["products"][key]
        pid = prod["clipo_product_id"]
        try:
            p = api("GET", f"/products/{pid}")
        except RuntimeError as e:
            print(f"❌ {key} {prod['full_name']} ({pid}) 打不开：{e}")
            out[key], bad = {"error": str(e)}, True
            continue
        segs = list_segments(pid)
        counts = tag_counts(pid, segs)
        total = counts["全部"]
        risky = sum(s.get("review_result") == "reject" for s in segs)
        empty = [t for t in p["tags"] if counts.get(t, 0) == 0]
        thin = {t: counts[t] for t in p["tags"] if 0 < counts.get(t, 0) < 10}
        print(f"{'✅' if total else '❌'} {key} {p['name']} ({pid}) 可用切片 {total}/{len(segs)}（风险 {risky}）")
        print(f"   无可用切片的标签 {empty or '无'}；不足 10 条 {thin or '无'}")
        if risky:
            print(f"   ⚠️ 平台生成不会避开风险切片：带标签的风险切片仍可能被随机选中，建议在平台删除或去掉标签")
        if not desc_ok(p):
            print("   ⚠️ 平台产品描述为空（AI 审核拿不到产品事实）")
        desc = p.get("description") or ""
        if prod["mechanism"]["allowed_numbers"] and not any(n in desc for n in prod["mechanism"]["allowed_numbers"]):
            print(f"   ⚠️ 平台产品描述里没有当前机制 {prod['mechanism']['spoken']}（描述会喂给 AI 审核，旧机制会导致误判）")
        prompts = {x["prompt_type"]: x for x in api("GET", f"/products/{pid}/prompts")}
        create = prompts.get("video_script_create") or {}
        cats = re.findall(r"(?:贴图|音效)[:：][^“”\n]*“([^”]+)”", create.get("prompt") or "")
        if cats:
            have = {c["catalog"] for kind in ("sticker", "sound-effect") for c in api("GET", f"/resource/{kind}/catalog")}
            gone = [c for c in dict.fromkeys(cats) if c not in have]
            if gone:
                print(f"   ⚠️ 建脚本提示词里写的分类平台上没有：{'、'.join(gone)}（平台不报错、直接不配，告诉用户改提示词里的分类名）")
        review = (prompts.get("video_cut_review") or {}).get("prompt") or ""
        m = re.search(r"产品专属标准[｜|]([^】\]\n]+)", review)
        names = [p["name"], prod["full_name"]] + prod["name_aliases"]
        core = lambda x: re.sub(r"^(清扬|力士|lux|LUX)[\d.]*-?|mist|\s", "", x)
        if m and not any(core(n) in core(m.group(1)) or core(m.group(1)) in core(n) for n in names):
            print(f"   ⚠️ 审核提示词的产品专属标准写的是「{m.group(1).strip()}」，不是这个产品，告诉用户改")
        if not (create.get("prompt") or "").strip():
            print("   ❌ 平台产品没有建脚本提示词 video_script_create：片子的配音、画面、贴图音效都靠它，先让用户在平台写好")
            bad = True
        else:
            print(f"   建脚本提示词：{'产品自定义' if create.get('is_custom') else '平台默认'}，"
                  f"{'更新于 ' + create['updated_at'][:10] if create.get('updated_at') else '未改过'}")
            if not create.get("is_custom"):
                print("   ❌ 建脚本提示词是平台默认：会自己搜音效（11.9 喷雾配进了泡泡、竖琴、闪亮声），配音字幕也和别的产品不一样。"
                      "先让用户把别的产品的提示词复制过来再生产")
                bad = True
            if "搜索相关音效" in (create.get("prompt") or ""):
                print("   ❌ 建脚本提示词允许「其他画面搜索相关音效」：会配进怪音效（11.05 定：其他画面不配置音效），告诉用户改")
                bad = True
        out[key] = {"product_id": pid, "name": p["name"], "segments": total, "risky": risky, "tag_counts": counts,
                    "empty_tags": empty, "description": desc}
        bad |= total == 0
    (Path(run) / "clipo").mkdir(parents=True, exist_ok=True)
    (Path(run) / "clipo" / out_name(cfg, "preflight.json")).write_text(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(1 if bad else 0)


# ---------- submit ----------

def build_script(pid, brief, title):
    """Create the platform script from the approved copy. The platform builds it with the product's own
    prompt video_script_create (voices, BGM, subtitles, visuals per segment, stickers, SFX); we pin only the
    words. Then: title it after the schedule, turn on AI rewrite for every segment (user 11-05: CTA too), hand
    the reviewer-approved alternative phrasings to the rewrite as reference, and read it back. Any mismatch:
    delete the script and raise, nothing is generated."""
    segs = voiced(brief["segments"])
    lines = "\n".join(f"第{i + 1}段（{sg.get('title') or sg.get('role')}）：{seg_text(sg)}" for i, sg in enumerate(segs))
    req = (f"按下面已经人审通过的口播文案建脚本：一共 {len(segs)} 段，每段的口播文案逐字照抄，不改字、不加字、不合并、不拆分；"
           f"每段都允许 AI 改写。画面、字幕、配音、BGM、贴图、音效按产品提示词配。\n{lines}")
    g = api("POST", "/video-script-generations", {"product_id": pid, "requirement": req})
    for _ in range(72):
        time.sleep(5)
        g = api("GET", f"/video-script-generations/{g['id']}")
        if g["status"] in ("success", "failed", "error"):
            break
    if g["status"] != "success":
        raise RuntimeError(f"平台建脚本失败 {g.get('error_code')}: {g.get('error_message')}")
    sid = g["script_id"]
    script = api("GET", f"/video-scripts/{sid}")
    if len(script["segments"]) != len(segs):
        api("DELETE", f"/video-scripts/{sid}")
        raise RuntimeError(f"平台建的脚本 {len(script['segments'])} 段，审过的文案 {len(segs)} 段，已删掉，没有生成")
    for ps, sg in zip(script["segments"], segs):
        ps["content"] = seg_text(sg)
        ps["ai_rewrite"] = True
        pin_visual(ps, sg, CFG["products"][brief["product_key"]])
    pin_voices(script, brief)
    script["segments"] = with_silent(script["segments"], brief["segments"], pid)
    body = {k: script[k] for k in ("product_id", "video", "config", "segments") if k in script}
    body["title"] = title[:120]
    body["vo_rewrite_prompt"] = alt_prompt(segs)
    api("PUT", f"/video-scripts/{sid}", body)
    allsegs = api("GET", f"/video-scripts/{sid}")["segments"]
    got = [x for x in allsegs if x["type"] != "original"]
    wrong = [i + 1 for i, (x, sg) in enumerate(zip(got, segs)) if (x.get("content") or "") != seg_text(sg)]
    off = [i + 1 for i, x in enumerate(got) if not x.get("ai_rewrite")]
    if [x["type"] for x in allsegs] != ["original" if sg.get("silent") else "dubbing" for sg in brief["segments"]]:
        wrong = wrong or ["无口播原片段没写对"]
    saved_voices = [x["id"] for x in api("GET", f"/video-scripts/{sid}")["config"]["tts"]["voices"]]
    if stable_voices(brief) and saved_voices != stable_voices(brief):
        wrong = wrong or ["音色没写进去"]
    if wrong or off:
        api("DELETE", f"/video-scripts/{sid}")
        raise RuntimeError(f"平台脚本和审过的文案对不上（不一致的段 {wrong}，没开 AI 改写的段 {off}），已删掉，没有生成")
    return sid


def silent_segment(sg, pid):
    """A voiceless opening segment on one pinned clip (user 11-11: open on the 爆款 clip, no voiceover; the clip
    keeps its own sound). The platform has no segment-id field: it matches `query` against clip descriptions within
    `tags`, and `score` is the lowest similarity accepted, set so only the chosen clip clears it (checked at
    writing time with POST /materials/segments/search)."""
    f = sg["footage"]
    props = {"product_id": pid, "tags": f.get("tags") or ["爆款"], "query": f["query"], "score": f.get("score", 0.8)}
    if f.get("duration_ms_lt"):
        props["duration_ms_lt"] = f["duration_ms_lt"]
    return {"id": "0", "type": "original", "title": sg.get("title") or "开头（原片无口播）", "content": "",
            "ai_rewrite": False, "visual": {"mode": "material_segment", "props": props}, "transition": [],
            "word_art": None, "sticker": None, "sound_effects": None, "subtitle_style": None, "tts": None}


def with_silent(platform_segs, brief_segs, pid):
    """Platform segments (spoken lines only) with the brief's silent segments put back in place; ids renumbered."""
    it, out = iter(platform_segs), []
    for sg in brief_segs:
        out.append(silent_segment(sg, pid) if sg.get("silent") else next(it))
    for i, x in enumerate(out, 1):
        x["id"] = str(i)
    return out


def stable_voices(brief):
    """Voice ids to use for this brief (config `tts`): user 11-09/11-10 heard two speakers in one video because
    the platform voices each segment separately and some voices swing wildly between segments."""
    tts = CFG.get("tts") or {}
    if tts.get("voices"):
        return tts["voices"]
    # user 11-11: a brief whose lines name the partner (「女朋友夸我」) is read by the matching voice only
    g = brief.get("voice_gender") or CFG["products"][brief["product_key"]].get("voice_gender")
    return (tts.get("voices_by_gender") or {}).get(g) or []


def pin_voices(script, brief):
    """Replace the platform's voice picks with the brand's stable voices; keep volume / speech rate."""
    ids = stable_voices(brief)
    if not ids:
        return
    tts = script.setdefault("config", {}).setdefault("tts", {})
    tts["voices"] = [{"id": i, "platform": "volcengine", "voice_type": i, "config": {"resource_id": "seed-tts-2.0"}} for i in ids]


_SEGMENTS = {}


def footage_index(product_key):
    """Platform footage segments of a product, by the file name of their video (draft clips point at it)."""
    if product_key not in _SEGMENTS:
        pid, idx, skip = CFG["products"][product_key]["clipo_product_id"], {}, 0
        while True:
            r = api("GET", f"/products/{pid}/segments?skip={skip}&limit=100")
            items = r if isinstance(r, list) else r.get("items", [])
            for x in items:
                if x.get("object_url"):
                    idx[x["object_url"].split("?")[0].rsplit("/", 1)[-1]] = x
            if len(items) < 100:
                break
            skip += 100
        _SEGMENTS[product_key] = idx
    return _SEGMENTS[product_key]


def bad_footage(product_key, draft):
    """Shots in a cut that the user ruled out (user 11-10: no dandruff footage), found by segment description.
    The platform picks footage by tag, and dandruff shots often carry tags like 头皮出油 / 头发油."""
    words = CFG.get("banned_footage_words") or []
    if not words:
        return []
    idx, out = footage_index(product_key), []
    for c in draft.get("clips", []):
        if c.get("trackId") != "main":
            continue
        seg = idx.get((c.get("props", {}).get("src") or "").split("?")[0].rsplit("/", 1)[-1])
        d = (seg or {}).get("description") or ""
        hit = next((w for w in words if w in d), None)
        if hit:
            out.append(f"{hit}：{d[:30]}")
    return out


def pin_visual(platform_seg, seg, product=None):
    """User 11-06/11-09: the line decides the footage. A brief segment may pin `visual_tags` (product footage
    tags, any of them may match, e.g. ["泡沫丰富", "产品质地"] for a line about using the shampoo); otherwise the
    platform's own choice stays."""
    tags = seg.get("visual_tags")
    if not tags and product and any(t in seg_text(seg) for t in product.get("ingredient_terms", [])):
        tags = product.get("ingredient_footage")  # user 11-10: a line about an ingredient / tech shows texture
    v = platform_seg.get("visual") or {}
    if tags and v.get("mode") == "material_segment":
        v.setdefault("props", {})["tags"] = list(tags)
        q = seg.get("visual_query")  # user 11-11: a 双人演绎 line shows one AI scene clip, pinned like a silent opening
        if q:
            v["props"]["query"], v["props"]["score"] = q["query"], q.get("score", 0.8)


def sync_visuals(script_id, segs):
    """Apply pinned `visual_tags` to an already-built platform script (copy and everything else untouched)."""
    script = api("GET", f"/video-scripts/{script_id}")
    if len(script["segments"]) != len(segs):
        raise RuntimeError("平台脚本段数和 brief 对不上")
    for ps, sg in zip(script["segments"], segs):
        if not sg.get("silent"):
            pin_visual(ps, sg)
    body = {k: script[k] for k in ("product_id", "video", "config", "vo_rewrite_prompt", "segments", "title") if k in script}
    api("PUT", f"/video-scripts/{script_id}", body)
    got = api("GET", f"/video-scripts/{script_id}")["segments"]
    bad = [i + 1 for i, (x, sg) in enumerate(zip(got, segs))
           if sg.get("visual_tags") and x["visual"]["props"].get("tags") != list(sg["visual_tags"])]
    if bad:
        raise RuntimeError(f"画面标签没写进去的段 {bad}")
    return [x["visual"]["props"].get("tags") for x in got]


REWRITE_RULES = [
    # user 11-09: per-segment [#…] voice instructions made one video sound like two different speakers
    "不要加任何 [#…] 语音指令，每段都直接写正文，整条用同一种自然的语气",
    # user 11-09: a rewrite turned 「早上喷一下」→「下班发尾干干的」, which reads as the product not working
    "段落顺序和每段的意思不变：先讲头发的问题，再讲产品；产品出现以后只讲用完的好结果，不写用了以后头发又干、又毛躁、又乱",
    "不加早上、下午、下班、出门前这类时间线，不编使用场景和前后对比",
]


def alt_prompt(segs):
    """Rules every rewrite must follow, plus the reviewed phrasings of each line as the range to stay in."""
    rows = [f"「{sg.get('title') or sg.get('role')}」这段可以参考的说法：" + " / ".join([seg_text(sg)] + seg_alts(sg))
            for sg in voiced(segs) if seg_alts(sg)]
    out = "# 用户要求\n" + "\n".join(f"- {r}" for r in REWRITE_RULES)
    if rows:
        out += "\n每段改写时，在下面这些人审过的说法范围内换措辞，事实和机制不变：\n" + "\n".join(rows)
    return out


def video_title(cfg, brief, n=None):
    prod = cfg["products"][brief["product_key"]]
    fmt = cfg["naming"] if n else cfg["naming"].replace("-V{n:02d}", "")
    return fmt.format(
        date=brief["date"].replace("-", "")[4:], brief_no=brief["brief_id"].split("-")[-1], n=n,
        product=prod["full_name"], selling_point=brief.get("selling_point") or "机制",
        audience=brief.get("audience") or "通用", direction=brief["direction"].replace("/", "-"))


def gen_body(script_id, quantity):
    """Body for POST /video-cut-generations, with the brand's compliance watermark (config `watermark`)."""
    body = {"script_id": script_id, "quantity": quantity}
    wm = CFG.get("watermark") or {}
    if wm.get("add"):
        body.update(add_compliance_watermark=True, compliance_watermark_position=wm.get("position", "bottom_right"),
                    compliance_watermark_mode=wm.get("mode", "global"))  # platform default "segments" skips real footage
    return body


def delivery_name(cfg, brief, briefs):
    """Share (delivery folder) name, user 11-06: TZ-26-1106-LUX-精油香氛-brief1. brief n counts up per date and
    per product in brief-id order; brief["delivery_date"] (MMDD) and brief["delivery_no"] override the run date and
    the number when a batch is split across days or added after an earlier delivery."""
    fmt = cfg.get("delivery_naming")
    if not fmt or brief.get("delivery_legacy_name"):  # delivered before the naming rule; keep its old name
        return video_title(cfg, brief)
    day = lambda b: b.get("delivery_date") or b["date"].replace("-", "")[4:]
    if brief.get("delivery_no"):  # set by hand when a batch has to slot in after earlier deliveries
        n = brief["delivery_no"]
    else:
        same = sorted(x["brief_id"] for x in briefs if x["product_key"] == brief["product_key"] and day(x) == day(brief)
                      and not x.get("delivery_no"))
        n = same.index(brief["brief_id"]) + 1
    prod = cfg["products"][brief["product_key"]]
    tags = direction_tags(cfg, brief)
    camp = cfg.get("campaign")
    in_camp = camp and brief.get("date", "") <= camp["until"]
    clause = re.split(r"[，,。！？!?；;、]", seg_text((voiced(brief.get("segments")) or [{}])[0]))[0]
    first = re.sub(r"[^一-鿿A-Za-z0-9]", "", clause)[:5]
    direction = (brief.get("direction") or "").translate(str.maketrans({"（": "(", "）": ")", "｜": "|", "/": "|"}))
    name = fmt.format(yy=brief["date"][2:4], yymmdd=brief["date"].replace("-", "")[2:], date=day(brief),
                      brand_tag=cfg.get("brand_tag", cfg["brand"]),
                      product_short=prod.get("short_name", brief["product_key"]),
                      product_naming=prod.get("naming_product", prod.get("short_name", brief["product_key"])),
                      grams=prod.get("naming_grams") or "（克数待确认）",
                      naming_code=prod.get("naming_code") or "（产品段待确认）",
                      campaign=camp["name"] if in_camp else "", video_type=tags["video_type"],
                      content_type=tags["content_type"], keyword=first, n=n,
                      brief_no=int(re.sub(r"\D", "", brief["brief_id"].split("-")[-1]) or 0), direction=direction)
    return re.sub(r"--+", "-", name)  # no campaign → drop that segment


def direction_tags(cfg, brief):
    """二级标签 / 十大类型 for the delivery name (user 11-10), from the schedule direction; brief fields override."""
    dt = cfg.get("direction_tags") or {}
    hit = next((r for r in dt.get("rules", []) if any(m in (brief.get("direction") or "") for m in r["match"])),
               dt.get("default") or {"video_type": "", "content_type": ""})
    return {"video_type": brief.get("video_type") or hit["video_type"],
            "content_type": brief.get("content_type") or hit["content_type"]}


def split_quantity(n, chunk):
    """30 with chunk 15 -> [15, 15]; 31 -> [11, 10, 10]: even rounds, none above chunk."""
    k = math.ceil(n / chunk)
    return [n // k + (i < n % k) for i in range(k)]


def gen_quantity(cfg, b, args):
    """How many to generate up front so one round usually yields `quantity` deliverables."""
    factor = args.factor if args.factor else cfg.get("generate_factor", 1)
    return min(300, math.ceil(b["quantity"] * factor))


def cmd_submit(cfg, run, args):
    st = State(run)
    submitted = skipped = total = 0
    all_briefs = load_briefs(run)
    cross = lint_day(all_briefs, cfg)
    for bid, problems in lint_variety(all_briefs, cfg, run).items():
        cross.setdefault(bid, []).extend(problems)
    for b in load_briefs(run, args.brief):
        why = approval_problem(b)
        if why:
            print(f"⏸  {b['brief_id']} {why}，跳过")
            skipped += 1
            continue
        errs = [m for l, m in lint_brief(b, cfg) + cross.get(b['brief_id'], []) if l == "error"]
        if errs:
            print(f"❌ {b['brief_id']} 有 {len(errs)} 个 lint 错误，不提交：{errs[:3]}")
            skipped += 1
            continue
        pid = cfg["products"][b["product_key"]]["clipo_product_id"]
        texts = [seg_text(sg) for sg in b["segments"]]
        title = video_title(cfg, b)
        job = st.data.setdefault("briefs", {}).setdefault(b["brief_id"], {})
        if job.get("stage") == "failed":
            if not args.retry_failed:
                print(f"💥 {b['brief_id']} 上次生成失败（{job.get('error')}），加 --retry-failed 重新生成")
                skipped += 1
                continue
            if job.get("rounds") is not None and job.get("generation_id"):  # one task per brief: continue it
                lost = gen_quantity(cfg, b, args) + sum(t.get("quantity", 0) for t in job.get("topped", [])) \
                    - job.get("landed", 0) - sum(job.get("rounds", []))
                job["rounds"] = ([lost] if lost > 0 else []) + job.get("rounds", [])
                job.update(stage="generating")
                job.pop("error", None)
                next_round(job)
                st.save()
                print(f"🔁 {b['brief_id']} 在原任务上继续生产")
                continue
            for k in ("generation_id", "error", "stage"):
                job.pop(k, None)
        if job.get("texts") and job["texts"] != texts and job.get("script_id"):
            if job.get("generation_id"):
                print(f"⚠️  {b['brief_id']} 提交后文案又改过，平台上是旧版；要按新版重做，先从 state.json 删掉这个 brief 和它的视频")
                skipped += 1
                continue
            job.pop("script_id")  # synced but never generated: sync the new copy again
        job["texts"] = texts
        if job.get("generation_id"):
            continue  # already on the platform; poll / topup handle it
        n = gen_quantity(cfg, b, args)
        total += 0 if args.scripts_only else n
        if args.dry_run:
            print(f"🧪 (dry run，未发送) {title} × {n}（排期 {b['quantity']}）")
            continue
        if not job.get("script_id"):
            try:
                job["script_id"] = build_script(pid, b, title)
            except RuntimeError as e:
                print(f"❌ {b['brief_id']} {e}")
                skipped += 1
                continue
            st.save()
            print(f"📝 {b['brief_id']} 平台脚本已建好并核对：{job['script_id']}（{title}）")
        if args.scripts_only:
            submitted += 1
            continue
        if not job.get("generation_id"):
            chunks = split_quantity(n, cfg.get("generate_chunk") or n)
            job["generation_id"] = api("POST", "/video-cut-generations", gen_body(job["script_id"], chunks[0]))["id"]
            job.update(stage="generating", rounds=chunks[1:], landed=0)
            job.pop("topups", None)
            st.save()
            submitted += 1
            print(f"🚀 {b['brief_id']} 生成 {n} 条（排期 {b['quantity']}），平台上一个任务，分 {len(chunks)} 轮：{'+'.join(map(str, chunks))}")
    if args.dry_run:
        print(f"\n(dry run) 将生成 {total} 条视频，跳过 {skipped} 个 brief。确认后去掉 --dry-run 提交")
        return
    if args.scripts_only:
        print(f"\n建好 {submitted} 个平台脚本，跳过 {skipped} 个，没有生成视频。生成前先 submit --dry-run 报条数")
        return
    print(f"\n新提交 {submitted} 个 brief，跳过 {skipped} 个。下一步：clipo_batch.py poll")


# ---------- re-review ----------

def cmd_rereview(cfg, run, args):
    """After the user changes a platform review prompt (or the copy rules in config): send rendered videos
    that were rejected back to AI review, and re-lint every rewrite with today's rules. Nothing is regenerated
    or re-rendered. --all also resubmits videos that passed."""
    st = State(run)
    briefs = {b["brief_id"]: b for b in load_briefs(run)}
    again, relinted = [], 0
    for vid, v in sorted(st.videos.items()):
        b = briefs.get(v["brief_id"])
        if v.get("stage") != "done" or not b or (args.brief and v["brief_id"] != args.brief):
            continue
        prod = cfg["products"][b["product_key"]]
        got = v.get("texts") or []
        issues = [m for t in got for l, m in lint_text(t, cfg, prod) if l == "error"]
        issues += [m for l, m in lint_script(got, voiced(b["segments"]), b, cfg, prod) if l == "error"]
        relinted += issues != v.get("copy_issues")
        v["copy_issues"] = issues
        v["bad_footage"] = bad_footage(b["product_key"], api("GET", f"/video-cuts/{v['cut_id']}")["draft"])
        if args.all or v.get("ai_review") != "pass":
            again.append(v)
    print(f"重新查改写：{relinted} 条结论变了；要重新 AI 审核的：{len(again)} 条")
    if args.dry_run:
        return
    for i in range(0, len(again), 100):
        batch = again[i:i + 100]
        api("POST", "/video-cuts/review", {"cut_ids": [v["cut_id"] for v in batch]})
        for v in batch:
            v["stage"], v["ai_review"], v["ai_reason"] = "reviewing", None, None
    st.save()
    print("已提交，poll 会读回新结论")


# ---------- top-up ----------

def deliverable(v):
    return (v.get("ai_review") == "pass" and not v.get("copy_issues") and not v.get("watermark_missing")
            and not v.get("voice_cmd") and not v.get("bad_voice") and not v.get("voice_drift")
            and not v.get("bad_footage"))


def cmd_topup(cfg, run, args):
    """Make sure every brief ends with `quantity` deliverable videos: regenerate more from the SAME
    approved video-script (no new copy, no new review) for whatever the AI review rejected.
    Asks Clipo for (shortfall / pass rate so far) × margin, so one round usually covers it."""
    st = State(run)
    jobs = st.data.get("briefs", {})
    total = 0
    for b in load_briefs(run, args.brief):
        bid = b["brief_id"]
        job = jobs.get(bid)
        if not job or not job.get("script_id"):
            continue
        why = approval_problem(b)
        if why or job.get("texts") != [seg_text(s) for s in b["segments"]]:
            print(f"⏸  {bid} {why or '平台上的脚本和现在的文案不一致'}，不补")
            continue
        errs = [m for level, m in lint_brief(b, cfg) if level == 'error']
        if errs:
            print(f"⏸  {bid} 有未解决的文案/整篇审读问题，不补：{errs[:3]}")
            continue
        vids = [v for v in st.videos.values() if v["brief_id"] == bid]
        if generating(job) or any(v.get("stage") not in ("done", "failed") for v in vids):
            print(f"⏳ {bid} 还有视频没跑完，先 poll")
            continue
        ok = sum(deliverable(v) for v in vids)
        short = b["quantity"] - ok
        if short <= 0:
            print(f"✅ {bid} 可交付 {ok}/{b['quantity']}，够了")
            continue
        rate = max(ok / len(vids), 0.2) if vids else 0.5
        want = min(args.max_per_brief, max(short, math.ceil(short / rate * args.margin)))
        offset = max((int(k.rsplit("V", 1)[1]) for k in st.videos if k.startswith(bid + "-V")), default=0)
        total += want
        print(f"{'🧪' if args.dry_run else '🔁'} {bid} 可交付 {ok}/{b['quantity']}，差 {short}；"
              f"通过率 {ok}/{len(vids)}，补生成 {want} 条（V{offset+1:02d} 起）")
        if args.dry_run:
            continue
        if job.get("rounds") is not None and not job.get("topups"):  # one platform task per brief: continue it
            job["rounds"] = job.get("rounds", []) + split_quantity(want, cfg.get("generate_chunk") or want)
            job.setdefault("topped", []).append({"quantity": want, "at": time.strftime("%Y-%m-%d %H:%M")})
            job["stage"] = "generating"
            next_round(job)
        else:  # runs submitted before 11-06 (separate tasks per round)
            for c in split_quantity(want, cfg.get("generate_chunk") or want):
                gid = api("POST", "/video-cut-generations", gen_body(job["script_id"], c))["id"]
                job.setdefault("topups", []).append({"generation_id": gid, "stage": "generating", "offset": offset,
                                                     "quantity": c, "at": time.strftime("%Y-%m-%d %H:%M")})
                offset += c
        st.save()
    if args.dry_run:
        print(f"\n(dry run) 将补生成 {total} 条（消耗额度）。确认后去掉 --dry-run")
    elif total:
        print(f"\n已补生成 {total} 条。下一步：poll，跑完再 topup，直到每个 brief 都够数")


# ---------- poll ----------

def pull_generation(st, cfg, b, job, gen):
    """Collect one cut-generation (the first one or a top-up): lint each rewrite, queue its render.
    `gen` holds generation_id / stage / offset (videos are numbered offset+1 …). Updates gen in place."""
    bid = b["brief_id"]
    try:
        g = api("GET", f"/video-cut-generations/{gen['generation_id']}")
    except RuntimeError as e:
        gen["last_error"] = str(e)[:300]
        return
    single = gen is job and job.get("rounds") is not None  # one task per brief, rounds added via /continue
    landed_more = single and len(g.get("cut_ids") or []) > job.get("landed", 0)
    if g["status"] in ("failed", "error"):
        gen["stage"], gen["error"] = "failed", f"生成失败 {g.get('error_code')}: {g.get('error_message')}"
    if (g["status"] == "success" and g["cut_ids"]) or landed_more:
        prod = cfg["products"][b["product_key"]]
        vids = []
        for k, cut_id in enumerate(g["cut_ids"], 1):
            n = gen.get("offset", 0) + k
            vids.append(f"{bid}-V{n:02d}")
            v = st.videos.setdefault(vids[-1], {"brief_id": bid, "cut_id": cut_id, "title": video_title(cfg, b, n)})
            if v.get("render_job"):
                continue
            try:
                cut = api("GET", f"/video-cuts/{cut_id}")
                rr = cut.get("random_result") or {}
                raw = [x["content"] for x in rr.get("vo_rewrite", [])]
                got = [re.sub(r"\[#[^\]]*\]", "", t) for t in raw] or [t for t in job["texts"] if t]
                v["voice_cmd"] = any("[#" in t for t in raw)  # user 11-09: sounds like two speakers
                v["bad_footage"] = bad_footage(b["product_key"], cut["draft"])
                if rr.get("tts_speaker_id") in ((CFG.get("tts") or {}).get("bad_voices") or []):
                    v["bad_voice"] = rr.get("tts_speaker_id")
                v["texts"], v["voice"] = got, rr.get("tts_speaker_id")
                v["duration_s"] = round(cut["draft"]["composition"]["durationInFrames"] / 30, 1)
                if (CFG.get("watermark") or {}).get("add") and "水印" not in [t.get("name") for t in cut["draft"]["tracks"]]:
                    v["watermark_missing"] = True
                issues = [m for t in got for l, m in lint_text(t, cfg, prod) if l == "error"]
                issues += [m for l, m in lint_script(got, voiced(b["segments"]), b, cfg, prod) if l == "error"]
                v["copy_issues"] = issues
                r = api("POST", "/render-jobs", {"spec": cut["draft"], "options": {"codec": "h264", "quality": "high", "scale": 1},
                                                "cut_id": cut_id, "idempotency_key": f"udb-{cut_id}"})
                v["render_job"], v["stage"] = r["job_id"], "rendering"
            except RuntimeError as e:
                v["last_error"] = str(e)[:300]
        if single:
            job["landed"] = len(g["cut_ids"])
            if g["status"] == "success":
                if job.get("rounds"):
                    next_round(job)
                elif all(st.videos[x].get("render_job") for x in vids):
                    gen["stage"] = "generated"
        elif all(st.videos[x].get("render_job") for x in vids):
            gen["stage"] = "generated"


def next_round(job):
    """Add the brief's next round to its own platform task (POST /video-cut-generations/{id}/continue)."""
    c = job["rounds"][0]
    try:
        api("POST", f"/video-cut-generations/{job['generation_id']}/continue", {"quantity": c})
        job["rounds"] = job["rounds"][1:]
    except RuntimeError as e:  # e.g. previous round still running; retried on the next poll
        job["last_error"] = str(e)[:300]


def generating(job):
    return job.get("stage") == "generating" or any(t.get("stage") == "generating" for t in job.get("topups", []))


def cmd_poll(cfg, run, args):
    st = State(run)
    deadline = time.time() + args.wait
    briefs = {b["brief_id"]: b for b in load_briefs(run)}
    while True:
        pending = 0
        for bid, job in sorted(st.data.get("briefs", {}).items()):
            for gen in [job] + job.get("topups", []):
                if gen.get("stage") == "generating":
                    pending += 1
                    pull_generation(st, cfg, briefs[bid], job, gen)
        rendered = []  # cuts that finished rendering this round: sent to AI review as one batch
        for vid, v in sorted(st.videos.items()):
            stage = v.get("stage")
            try:
                if stage == "rendering":
                    j = api("GET", f"/render-jobs/{v['render_job']}")
                    if j["status"] == "success":
                        v["object_key"] = j.get("object_key")
                        v["stage"] = "rendered"
                    elif j["status"] in ("failed", "canceled", "cancelled"):
                        v["stage"], v["error"] = "failed", f"渲染失败 {j.get('error_code')}: {j.get('error_message')}"
                elif stage == "reviewing":
                    cut = api("GET", f"/video-cuts/{v['cut_id']}")
                    if cut.get("ai_review_status") in ("success", "failed"):
                        v["ai_review"] = cut.get("ai_review_result")
                        v["ai_reason"] = cut.get("ai_review_reason")
                        v["stage"] = "done"
            except RuntimeError as e:
                v["last_error"] = str(e)[:300]
            if v.get("stage") == "rendered":
                rendered.append(v)
            pending += v.get("stage") not in ("done", "failed", None)
        for i in range(0, len(rendered), 100):
            batch = rendered[i:i + 100]
            try:
                api("POST", "/video-cuts/review", {"cut_ids": [v["cut_id"] for v in batch]})
                for v in batch:
                    v["stage"] = "reviewing"
            except RuntimeError as e:
                for v in batch:
                    v["last_error"] = str(e)[:300]  # stays "rendered", resubmitted next round
        st.save()
        by = {"brief生成中": sum(generating(j) for j in st.data.get("briefs", {}).values())}
        for v in st.videos.values():
            by[v.get("stage")] = by.get(v.get("stage"), 0) + 1
        print(time.strftime("%H:%M:%S"), by, flush=True)
        if not pending or time.time() > deadline:
            break
        time.sleep(args.interval)
    sys.exit(0 if not pending else 3)


# ---------- report ----------

REJECT_ACTION = {
    "改写违规": "把新出现的词补进 config.banned_words（自动进改写提示词），再单独重跑这几条",
    "文案": "回 ④ 改这个 brief 的那句（同类问题写进产品 rules），lint 后重发审",
    "画面/素材": "去平台给那条切片打「风险」或改标签，再单独重跑",
    "产品信息过时": "平台产品描述里的机制/卖点过时，提醒用户更新描述；判为误判由用户决定放行",
    "其他": "逐条看原因",
}


def reject_kind(r):
    if r.get("copy_issues"):
        return "改写违规"
    why = r.get("ai_reason") or ""
    if re.search(r"机制|价格|描述|信息不准确", why):
        return "产品信息过时"
    if re.search(r"画面|外观|系列|素材|包装|瓶|字幕遮挡|模糊", why):
        return "画面/素材"
    if re.search(r"广告法|口播|文案|用语|禁用|夸大|承诺", why):
        return "文案"
    return "其他"


def cmd_status(cfg, run, args):
    """One screen: where every brief and video of the day stands, and what to do next. No network."""
    briefs = load_briefs(run)
    if not briefs:
        print(f"{run} 还没有 brief：从 ① fetch_sources / ② parse_schedule 开始")
        return
    st = State(run)
    jobs = st.data.get("briefs", {})
    stages = {}
    for v in st.videos.values():
        stages.setdefault(v["brief_id"], {}).setdefault(v.get("stage"), 0)
        stages[v["brief_id"]][v.get("stage")] += 1
    nxt = set()
    print(f"{'brief':10} {'条':>3} {'状态':18} {'lint':6} 平台")
    for b in briefs:
        errs = sum(l == "error" for l, _ in lint_brief(b, cfg)) if b.get("segments") else None
        job = jobs.get(b["brief_id"], {})
        plat = job.get("stage") or "-"
        if stages.get(b["brief_id"]):
            plat = " ".join(f"{k}:{n}" for k, n in sorted(stages[b["brief_id"]].items(), key=str))
        lint = "未写" if errs is None else ("✅" if not errs else f"❌{errs}")
        print(f"{b['brief_id']:10} {b['quantity']:>3} {b.get('status',''):18} {lint:6} {plat}")
        if errs is None:
            nxt.add("④ brief_card.py 看任务，Agent 写脚本")
        elif errs:
            nxt.add("⑤ 改 lint 错误")
        elif b.get("status") in ("draft", "changes_requested"):
            nxt.add("⑥ 发审 / 等审核" if b.get("status") == "draft" else "⑦ 按意见改完再发审")
        elif approval_problem(b) and not job:
            nxt.add(f"⑥ 重新给人审（{approval_problem(b)}）")
        elif b.get("status") == "approved" and not job:
            nxt.add("⑧ submit")
        elif job.get("stage") == "failed":
            nxt.add("⑧ submit --retry-failed")
        elif job and not generating(job):
            vids = [v for v in st.videos.values() if v["brief_id"] == b["brief_id"]]
            if vids and all(v.get("stage") in ("done", "failed") for v in vids) and \
                    sum(deliverable(v) for v in vids) < b["quantity"]:
                nxt.add("⑧ topup（可交付不够数）")
    if any(v.get("stage") not in ("done", "failed") for v in st.videos.values()) or \
            any(generating(j) for j in jobs.values()):
        nxt.add("⑧ poll（后台）")
    elif st.videos:
        nxt.add("⑨ report")
    print("\n下一步：" + ("、".join(sorted(nxt)) or "今天的活已完成"))


def cmd_report(cfg, run, args):
    st = State(run)
    rows, briefs = [], {b["brief_id"]: b for b in load_briefs(run)}
    lines = [f"# {Path(run).name} 生产结果", ""]
    for bid in sorted({v["brief_id"] for v in st.videos.values()}):
        b = briefs.get(bid, {})
        lines += [f"## {bid} · {b.get('direction','')} · {b.get('selling_point','')} · {b.get('audience','')} · 范本：{b.get('gold_id') or '未填'}", "",
                  "| 视频 | 时长 | 渲染 | AI 审核 | 原因 / 问题 | 预览 |", "|-|-|-|-|-|-|"]
        for vid, v in sorted((k, x) for k, x in st.videos.items() if x["brief_id"] == bid):
            flag = ("⚠️ 改写违规：" + "；".join(v["copy_issues"][:2]) + " ") if v.get("copy_issues") else ""
            reason = flag + (v.get("ai_reason") or v.get("error") or "").replace("|", "/").replace("\n", " ")[:120]
            url = f"{BASE.replace('/api/v1','')}/cut/{v['cut_id']}" if v.get("cut_id") else ""
            lines.append(f"| {vid} | {v.get('duration_s','')} | {v.get('stage')} | {v.get('ai_review') or ''} | {reason} | {url} |")
            rows.append({"video": vid, "gold_id": b.get("gold_id"),
                         **{k: v.get(k) for k in ("title", "cut_id", "stage", "ai_review", "ai_reason", "duration_s", "voice",
                                                  "texts", "copy_issues", "error")}})
        lines.append("")
    total = len(rows)
    bad_copy = sum(bool(r.get("copy_issues")) for r in rows)
    passed = sum(r["ai_review"] == "pass" and not r.get("copy_issues") for r in rows)
    rejected = [r for r in rows if r["ai_review"] == "reject" or r.get("copy_issues")]
    buckets = {}
    for r in rejected:
        buckets.setdefault(reject_kind(r), []).append(r["video"])
    summary = [f"共 {total} 条：可交付 {passed}，需处理 {len(rejected)}，未完成 {total - passed - len(rejected)}（改写违规 {bad_copy} 条也算需处理）。", ""]
    need = {bid: (sum(deliverable(v) for v in st.videos.values() if v["brief_id"] == bid), briefs[bid]["quantity"])
            for bid in sorted(briefs)}  # every brief on today's schedule, including ones with no videos yet
    ok_n = sum(min(a, q) for a, q in need.values())
    q_n = sum(q for _, q in need.values())
    summary += [f"**排期要 {q_n} 条，已够数 {ok_n} 条**（每个 brief 最多算到它的排期条数）：" +
                "，".join(f"{bid} {a}/{q}{' ✅' if a >= q else ''}" for bid, (a, q) in need.items()), ""]
    if ok_n < q_n:
        summary += [f"还差 {q_n - ok_n} 条 → `clipo_batch.py topup` 用同一份审过的脚本补生成", ""]
    for kind, vids in buckets.items():
        summary.append(f"- **{kind}** {len(vids)} 条：{'、'.join(vids[:12])}{' …' if len(vids) > 12 else ''} → {REJECT_ACTION[kind]}")
    lines[2:2] = summary + [""]
    (Path(run) / out_name(cfg, "report.md")).write_text("\n".join(lines))
    (Path(run) / "report.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    print("\n".join(summary))
    print(f"→ {Path(run) / out_name(cfg, 'report.md')}")
    write_delivery(st, briefs, run)


def picks(st, bid, quantity):
    """(delivered, spares): the first `quantity` deliverable videos of a brief by number, and the rest."""
    good = sorted((k for k, v in st.videos.items() if v["brief_id"] == bid and deliverable(v)),
                  key=lambda k: int(k.rsplit("V", 1)[1]))
    return good[:quantity], good[quantity:]


def share_url(share_id):
    return f"{BASE.replace('/api/v1', '')}/share/{share_id}"


def write_delivery(st, briefs, run):
    """Deliverable list: per brief, the share link and the first `quantity` videos that passed; the rest as spares."""
    out, total, need = [], 0, 0
    jobs = st.data.get("briefs", {})
    shares = st.data.get("shares", {})
    for bid in sorted(briefs):
        b = briefs[bid]
        q = b["quantity"]
        pick, spare = picks(st, bid, q)
        total, need = total + len(pick), need + q
        out += [f"## {bid} · {b.get('direction_core') or b['direction']} · {b.get('selling_point', '')} · {b.get('audience', '')} · "
                f"交付 {len(pick)}/{q}" + (" ✅" if len(pick) >= q else f" ⚠️ 差 {q - len(pick)}"), ""]
        sh = shares.get(bid)
        if sh:
            stale = sh["videos"] != pick
            out += [f"**分享链接**：{share_url(sh['id'])}" + ("（⚠️ 挑的视频变了，重跑 share 更新）" if stale else ""), ""]
        out += ["| # | 视频 | 时长 | 预览 |", "|-|-|-|-|"]
        for i, k in enumerate(pick, 1):
            v = st.videos[k]
            out.append(f"| {i} | {v['title']} | {v.get('duration_s', '')}s | {BASE.replace('/api/v1', '')}/cut/{v['cut_id']} |")
        if not pick:
            job = jobs.get(bid, {})
            errs = [g.get("error") for g in [job] + job.get("topups", []) if g.get("error")]
            out.append(f"| | 还没有可交付的视频{('：' + errs[-1][:80]) if errs else '（没提交或还在跑）'} | | |")
        if spare:
            out.append(f"\n备用（也已通过）：{'、'.join(spare)}")
        out.append("")
    gen = len(st.videos)
    passed = sum(deliverable(v) for v in st.videos.values())
    group = st.data.get("share_group")
    head = [f"# {Path(run).name} 交付清单", ""] + ([f"**今天全部分享链接**：{BASE.replace('/api/v1', '')}/share/group/{group['id']}", ""] if group else []) + [
            f"**交付 {total} / {need} 条**{'，全部够数' if total >= need else f'，还差 {need - total} 条（topup）'}。"
            f"共生成 {gen} 条，AI 审核通过 {passed} 条（{passed / max(gen, 1):.0%}）。", ""]
    (Path(run) / out_name(CFG, "delivery.md")).write_text("\n".join(head + out))
    print(f"→ {Path(run) / out_name(CFG, 'delivery.md')}（交付 {total}/{need}）")


def cmd_share(cfg, run, args):
    """One share link per brief with its delivered videos (POST /video-shares), all collected into one share
    group for the day (POST /video-share-groups). Only briefs that reached their quantity unless --partial.
    Re-running updates a brief whose picked videos changed: a new share replaces the old one in the group."""
    st = State(run)
    briefs = {b["brief_id"]: b for b in load_briefs(run, args.brief)}
    all_briefs = load_briefs(run)
    shares = st.data.setdefault("shares", {})
    todo = []
    for bid in sorted(briefs):
        b = briefs[bid]
        pick, _ = picks(st, bid, b["quantity"])
        if not pick:
            print(f"⏸  {bid} 还没有可交付的视频")
            continue
        if len(pick) < b["quantity"] and not args.partial:
            print(f"⏸  {bid} 可交付 {len(pick)}/{b['quantity']}，不够数先不发（要先发加 --partial）")
            continue
        old = shares.get(bid)
        same_name = old and (old.get("title") or video_title(cfg, b)) == delivery_name(cfg, b, all_briefs)
        cuts = [st.videos[k]["cut_id"] for k in pick]  # a redo reuses V01.. numbers, so compare the actual cuts
        if old and old["videos"] == pick and old.get("cuts", cuts) == cuts and same_name and not args.refresh:
            print(f"✅ {bid} 已有分享 {share_url(old['id'])}")
            continue
        todo.append((b, pick, old))
    if args.dry_run:
        for b, pick, old in todo:
            print(f"🧪 {b['brief_id']} 将{'更新' if old else '新建'}分享「{delivery_name(cfg, b, all_briefs)}」，{len(pick)} 条")
        print(f"\n(dry run) 将建 {len(todo)} 个分享链接。确认后去掉 --dry-run")
        return
    day = Path(run).name
    group = st.data.get("share_group")
    if todo and not group:
        camp = cfg.get("campaign")
        in_camp = camp and f"{day[:4]}-{day[4:6]}-{day[6:8]}" <= camp["until"]
        name = re.sub(r"--+", "-", cfg.get("delivery_group_naming", "{date}-{brand_tag}").format(
            yy=day[2:4], yymmdd=day[2:8], date=day[4:8], brand_tag=cfg.get("brand_tag", cfg["brand"]),
            campaign=camp["name"] if in_camp else ""))
        g = api("POST", "/video-share-groups", {"name": name})
        group = st.data["share_group"] = {"id": g["id"], "name": name}
        st.save()
    for b, pick, old in todo:
        bid = b["brief_id"]
        vids = [st.videos[k] for k in pick]
        payload = {"script_id": st.data["briefs"][bid]["script_id"],
                   "product_id": cfg["products"][b["product_key"]]["clipo_product_id"],
                   "group_id": group["id"], "title": delivery_name(cfg, b, all_briefs), "video_name_prefix": delivery_name(cfg, b, all_briefs) + "-",
                   "videos": []}
        for v in vids:
            cut = api("GET", f"/video-cuts/{v['cut_id']}")
            comp = cut["draft"]["composition"]
            cover = re.search(r"\.aliyuncs\.com/([^?]+)", cut["draft"].get("cover_url") or "")
            payload["videos"].append({"video_object_key": cut.get("output_object_key") or v["object_key"],
                                      "cover_object_key": cover.group(1) if cover else None,
                                      "duration_seconds": round(comp["durationInFrames"] / comp.get("fps", 30), 2),
                                      "width": comp["width"], "height": comp["height"]})
        sh = api("POST", "/video-shares", payload)
        if old:  # keep the group pointing at the newest share for this brief
            g = api("GET", f"/video-share-groups/{group['id']}")
            ids = [x["id"] for x in g["shares"] if x["id"] != old["id"]]
            api("PATCH", f"/video-share-groups/{group['id']}", {"video_share_ids": ids})
            api("DELETE", f"/video-shares/{old['id']}")
        shares[bid] = {"id": sh["id"], "videos": pick, "cuts": [v["cut_id"] for v in vids], "title": payload["title"], "at": time.strftime("%Y-%m-%d %H:%M")}
        st.save()
        print(f"🔗 {bid} {payload['title']} {len(pick)} 条 → {share_url(sh['id'])}")
    if group:
        print(f"\n今天全部分享（分组）：{BASE.replace('/api/v1', '')}/share/group/{group['id']}")
    write_delivery(st, {b["brief_id"]: b for b in load_briefs(run)}, run)


def main():
    global BASE, CFG
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["preflight", "submit", "poll", "topup", "rereview", "report", "share", "status"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--brief")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--partial", action="store_true", help="share: 不够数的 brief 也先发")
    ap.add_argument("--refresh", action="store_true", help="share: 已有分享也重建（换掉旧链接）")
    ap.add_argument("--all", action="store_true", help="rereview: 通过的也重新审")
    ap.add_argument("--scripts-only", action="store_true", help="submit: 只在平台建脚本并核对，不生成视频（不扣额度）")
    ap.add_argument("--retry-failed", action="store_true", help="submit: regenerate briefs whose generation failed")
    ap.add_argument("--factor", type=float, help="submit: 一次生成排期条数的几倍，默认取 config.generate_factor")
    ap.add_argument("--margin", type=float, default=1.3, help="topup: 按通过率估算后再多要的倍数")
    ap.add_argument("--max-per-brief", type=int, default=30, help="topup: 每个 brief 单次最多补几条")
    ap.add_argument("--wait", type=int, default=0, help="poll: keep polling up to N seconds")
    ap.add_argument("--interval", type=int, default=30)
    args = ap.parse_args()
    cfg = load_config(args.config)
    BASE = cfg["clipo"]["base_url"].rstrip("/") + "/api/v1"
    CFG = cfg
    {"preflight": cmd_preflight, "submit": cmd_submit, "poll": cmd_poll, "topup": cmd_topup, "rereview": cmd_rereview, "report": cmd_report, "share": cmd_share,
     "status": cmd_status}[args.cmd](cfg, args.run, args)


if __name__ == "__main__":
    main()
