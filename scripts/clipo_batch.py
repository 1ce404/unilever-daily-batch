#!/usr/bin/env python3
# ---------- 文件契约 ----------
# [INPUT]: 依赖 common 的品牌配置、人审凭据与 brief 读取。
#          依赖 lint_copy 的文案规则、aigc_materials 的素材绑定检查；footage_qc 提供源片探测与角标。
#          通过 urllib 调用 Clipo Remix REST，读取并保存批次状态。
# [OUTPUT]: 提供预检、提交、轮询、补量、复审、报告与分享命令；提交与补量只按 1 倍，凭 dry-run 收据才发送。
#           本任务补充逐组包装要求传入建脚本、brief keep_terms保留与inspect-scripts只读配置快照。
#           提供素材池、AIGC 绑定、成片下载与离线状态命令；poll/share 按 --brief 限定写入；画面同步先备份。
#           分享视频附 cut_id，固定状态中已审核输出；线上剪辑只提供封面与规格。
#           状态合并保留批量条目引用；复审绑定最新输出并核对受理收据。
#           按 brief 替换分享先备份，回读新版输出；保留旧独立链接。
#           交付清单分列 AI 审核与用户复核，不以授权伪造通过数。
#           开头可固定或复用同一合格切片；不按交付条数补开头、不自动换开头、不按开头重排交付。
#           素材池可显式只选一条合格来源；素材风险与画面匹配检查保留。
# [POS]: scripts 的批量生产入口；文案与素材检查委托兄弟模块，交付闸门委托 qc；错开钩子时保留合格来源。
#        提交要求文案人审有效；用 AI 素材的须素材人审；所有品牌默认先出 1 条 demo，demo 人审后才量产。
#        submit 并发建脚本；poll 按 brief 并发读平台（同一 brief 顺序处理），素材索引与源片下载加锁只取一次。
#        渲染前 prepare_draft 一次改完草稿（包装、禁用画面、角标），闸门不过不渲染。
#        日常音色沿用产品平台提示词；本地不设音色黑白名单。明确用户要求只在任务级配置留档。
# [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
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
video_script_create), turns on AI rewrite for every segment, verifies it, then generates exactly the scheduled quantity (user 11-19) in rounds of ≤ generate_chunk. Only
briefs a human approved (review_doc.py sync / approve, copy unchanged since) with zero lint errors go out. poll
re-lints every rewritten version. State lives in runs/<day>/clipo/state.json; every step is idempotent and safe to re-run.
"""
import argparse
from collections import Counter
import copy
import hashlib
import json
import math
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import seg_alts, voiced, approval_problem, aigc_problem, demo_first, demo_problem, day_briefs, load_config, normalize_copy, out_name
from aigc_materials import (required as aigc_required, group_for, segments as aigc_segments,
                            validate_selection, validate_draft, render_key,
                            user_review_instruction, release_for_user)
from lint_copy import lint_brief, lint_day, lint_variety, lint_script, lint_text, seg_text

BASE = None
CFG = None


def api(method, path, body=None, timeout=180, tries=5):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    for attempt in range(tries):
        req = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                res = json.load(r)
            break
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            try:
                res = json.loads(raw or "{}")
            except ValueError:  # 11-19：网关 502/504 回的是 HTML 页面，不是 JSON；当服务端错误重试
                res = {"code": f"http_{e.code}", "message": raw[:200]}
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
        self._base = self._snap()

    def _snap(self):
        return {key: {k: json.dumps(v, sort_keys=True) for k, v in (self.data.get(key) or {}).items()}
                for key in ("briefs", "videos")}

    def save(self):
        """11-11: a background poll and a submit share this file; a plain dump made the poll erase the 5 briefs
        submit had just added. 11-19: two writers on one run also overwrote each other's field edits (a poll wiped
        bad_voice flags another session had just set) and two concurrent writes left a corrupt file. Now: under a
        file lock, entries this process did not change since it last loaded / saved are taken from disk; entries it
        changed win; the file is replaced atomically."""
        import fcntl
        lock = open(self.path.with_suffix(".lock"), "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self.path.exists():
                disk = json.loads(self.path.read_text())
                for key in ("briefs", "videos"):
                    mine = self.data.setdefault(key, {})
                    base = self._base.get(key, {})
                    for k, v in (disk.get(key) or {}).items():
                        unchanged = k in mine and json.dumps(mine[k], sort_keys=True) == base.get(k)
                        if (k not in mine and k not in self._seen.get(key, set())) or unchanged:
                            if unchanged and isinstance(mine[k], dict) and isinstance(v, dict):
                                # 原地合并；批量循环持有的条目引用必须保持有效。
                                mine[k].clear()
                                mine[k].update(v)
                            else:
                                mine[k] = v
                for k, v in disk.items():
                    if k not in ("briefs", "videos") and k not in self.data:
                        self.data[k] = v
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2))
            tmp.replace(self.path)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
        self._seen = {key: set(self.data.get(key) or {}) for key in ("briefs", "videos")}
        self._base = self._snap()

    @property
    def videos(self):
        return self.data["videos"]


def load_briefs(run, only=None):
    result=[b for b in day_briefs(run, CFG) if not only or b["brief_id"] == only]
    if any(b.get('checked_batch_authorization') for b in result):
        import hashlib
        st=State(run)
        for b in result:
            if not b.get('checked_batch_authorization'):continue
            bid=b['brief_id'];v=st.videos.get(bid+'-V01',{});evidence={}
            for name,paths in (b.get('checked_batch_evidence_paths') or {}).items():
                evidence[name]=[{'path':str(Path(p).resolve()),'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest()} for p in paths if Path(p).is_file()]
            b['checked_batch_context']={'current_demo':{'video_key':bid+'-V01','render_job':v.get('render_job'),'object_key':v.get('object_key')},'requested_new':b['quantity']-1,'requested_video_keys':[bid+f'-V{i:02d}' for i in range(2,b['quantity']+1)],'current_evidence':evidence}
    return result


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
    catalogs = None
    for key in sorted(used):
        prod = cfg["products"][key]
        pid = prod["clipo_product_id"]
        try:
            p = api("GET", f"/products/{pid}")
        except RuntimeError as e:
            print(f"❌ {key} {prod['full_name']} ({pid}) 打不开：{e}")
            out[key], bad = {"error": str(e)}, True
            continue
        # one call per product (GET /segment-tags, 0.3 s) instead of reading every clip (6 s): user 11-18
        raw = api("GET", f"/products/{pid}/segment-tags")
        raw = {x["tag"]: x["count"] for x in (raw if isinstance(raw, list) else raw.get("items", []))}
        risky, all_clips = raw.get("风险", 0), raw.get("全部", 0)
        counts = {t: n for t, n in raw.items() if t not in ("风险", "未分类", "< 2秒", "< 1秒", "> 10秒", "横屏")}
        counts["全部"] = total = all_clips - risky  # tag counts still include risk clips; the total does not
        empty = [t for t in p["tags"] if counts.get(t, 0) == 0]
        thin = {t: counts[t] for t in p["tags"] if 0 < counts.get(t, 0) < 10}
        print(f"{'✅' if total else '❌'} {key} {p['name']} ({pid}) 可用切片 {total}/{all_clips}（风险 {risky}）")
        print(f"   无可用切片的标签 {empty or '无'}；不足 10 条 {thin or '无'}")
        if risky:
            print(f"   ⚠️ 平台生成不会避开风险切片：带标签的风险切片仍可能被随机选中，建议在平台删除或去掉标签")
        # AI 素材打标规则（scripts/ai_tags.py，用户 11-18）：普通标签里混进 AI 素材，平台按标签配画面会配进 AI 人
        real_tags = [t for t in p["tags"] if not t.startswith("AI")]
        if real_tags:
            hits = [h for i in range(0, len(real_tags), 10)  # 检索接口一次最多 10 个标签（11-19 清扬呼吸瓶 11 个标签报 422）
                    for h in (api("POST", "/materials/segments/search", {"product_id": pid, "tags": real_tags[i:i + 10], "limit": 200}) or {}).get("items", [])]
            mixed = Counter(t for h in hits if h["segment"].get("ai_generated") for t in h["segment"].get("tags") or [] if not t.startswith("AI"))
            if mixed:
                print(f"   ⚠️ 普通标签里混了 AI 素材（最新 200 条里）：{'、'.join(f'{t} {n}' for t, n in mixed.most_common())}。"
                      f"按标签配画面会配进 AI 人：ai_tags.py fix --dry-run 看怎么改，用户同意后改")
        if not desc_ok(p):
            print("   ⚠️ 平台产品描述为空（AI 审核拿不到产品事实）")
        desc = p.get("description") or ""
        if prod["mechanism"]["allowed_numbers"] and not any(n in desc for n in prod["mechanism"]["allowed_numbers"]):
            print(f"   ⚠️ 平台产品描述里没有当前机制 {prod['mechanism']['spoken']}（描述会喂给 AI 审核，旧机制会导致误判）")
        prompts = {x["prompt_type"]: x for x in api("GET", f"/products/{pid}/prompts")}
        create = prompts.get("video_script_create") or {}
        cats = re.findall(r"(?:贴图|音效)[:：][^“”\n]*“([^”]+)”", create.get("prompt") or "")
        if cats:
            if catalogs is None:  # same for every product: fetch once
                catalogs = {c["catalog"] for kind in ("sticker", "sound-effect") for c in api("GET", f"/resource/{kind}/catalog")}
            have = catalogs
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
    # 本任务逐组包装方案也须进入平台，不能只留在本地 brief。
    prod_plan = brief.get("production_plan") or {}
    fields = (("palette", "配色"), ("subtitle", "字幕包装"), ("bgm", "BGM"),
              ("sound_effects", "音效"), ("transitions", "转场"), ("subtitle_rules", "字幕规则"))
    directions = [f"{label}：{prod_plan[key]}" for key, label in fields if prod_plan.get(key)]
    if directions:
        req += "\n本条专属执行要求（只配置包装，不改上方已审口播；从平台实际资源选择可用项）：\n" + "\n".join(directions)
        req += "\n请配置可见但克制的包装、至少两种适当转场，BGM与动作音效分开；音效不得含人声/台词；字幕样式与底色保持对比。"
    if len(req) > 2000:
        raise RuntimeError(f"建脚本需求 {len(req)} 字超过平台2000字，未发送")
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
        pin_decor(ps, sg)
    # Voice choices remain owned by the product platform prompt.
    pin_bgm(script, brief["product_key"])
    script["segments"] = with_silent(script["segments"], brief["segments"], pid)
    body = {k: script[k] for k in ("product_id", "video", "config", "segments") if k in script}
    body["title"] = title[:120]
    rewrite = alt_prompt(segs, CFG["products"][brief["product_key"]])
    if brief.get("keep_terms"):
        rewrite += "\n- 以下本稿核心表达须逐字保留：" + "、".join(brief["keep_terms"])
    body["vo_rewrite_prompt"] = rewrite[:2000]
    api("PUT", f"/video-scripts/{sid}", body)
    allsegs = api("GET", f"/video-scripts/{sid}")["segments"]
    got = [x for x in allsegs if x["type"] != "original"]
    wrong = [i + 1 for i, (x, sg) in enumerate(zip(got, segs)) if (x.get("content") or "") != seg_text(sg)]
    off = [i + 1 for i, x in enumerate(got) if not x.get("ai_rewrite")]
    if [x["type"] for x in allsegs] != ["original" if sg.get("silent") else "dubbing" for sg in brief["segments"]]:
        wrong = wrong or ["无口播原片段没写对"]
    fx = [i + 1 for i, (x, sg) in enumerate(zip(got, segs)) if sg.get("effect") and
          (x.get("effect") or {}).get("template_id") != sg["effect"]["template_id"]]
    if fx:
        wrong = wrong or [f"特效没写进去（段 {fx}）"]
    if wrong or off:
        api("DELETE", f"/video-scripts/{sid}")
        raise RuntimeError(f"平台脚本和审过的文案对不上（不一致的段 {wrong}，没开 AI 改写的段 {off}），已删掉，没有生成")
    if CFG.get('quality_contract'):
        from quality_contract import config_issues
        issues=config_issues(api('GET',f'/video-scripts/{sid}'),brief,CFG,list_segments(pid))
        if issues:raise RuntimeError('真实配置未满足批量要求：'+'；'.join(issues))
    return sid


def silent_segment(sg, pid):
    """A voiceless opening segment on one pinned clip (user 11-11: open on the 爆款 clip, no voiceover; the clip
    keeps its own sound). The platform has no segment-id field: it matches `query` against clip descriptions within
    `tags`, and `score` is the lowest similarity accepted, set so only the chosen clip clears it (checked at
    writing time with POST /materials/segments/search)."""
    f = sg.get("footage_pool") or sg["footage"]  # a pool (user 11-13): one of several clips per video
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


def pin_bgm(script, product_key=None):
    """User 11-19 清扬：BGM 要有节奏感、有激情。config `bgm_categories` 替换平台按提示词随机挑的分类。
    User 11-19 力士：BGM 太小（平台默认 0.08）；config `bgm_volume` 给了就改音量。"""
    cats, vol = CFG.get("bgm_categories"), mix_volumes(product_key)[0]
    if not cats and vol is None:
        return
    bgm = script.setdefault("config", {}).setdefault("bgm", {"mode": "category", "volume": 0.08})
    if cats:
        bgm["mode"] = "category"
        bgm.setdefault("props", {})["categories"] = list(cats)
        bgm["props"]["ids"] = []
    if vol is not None:
        bgm["volume"] = vol


_SEGMENTS = {}
_SEGMENTS_LOCK = threading.Lock()
_FETCH_LOCK = threading.Lock()


def footage_index(product_key):
    """Platform footage segments of a product, by the file name of their video (draft clips point at it)."""
    with _SEGMENTS_LOCK:  # poll 并发：同一产品的素材索引只拉一次
        return _footage_index(product_key)


def _footage_index(product_key):
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


def ai_spans(product_key, draft):
    """[(from, frames)] of the main-track shots cut from AI-generated footage, adjacent shots merged (user 11-19:
    the KP client wants the 内容由AI生成 label only over AI footage, never over real footage)."""
    idx, out = footage_index(product_key), []
    for c in sorted([c for c in draft.get("clips", []) if c.get("trackId") == "main"], key=lambda c: c["from"]):
        seg = idx.get(c["props"]["src"].split("?")[0].rsplit("/", 1)[-1]) or {}
        if not seg.get("ai_generated"):
            continue
        if out and out[-1][0] + out[-1][1] >= c["from"]:
            out[-1] = (out[-1][0], max(out[-1][0] + out[-1][1], c["from"] + c["durationInFrames"]) - out[-1][0])
        else:
            out.append((c["from"], c["durationInFrames"]))
    return out


def banned_words_for(product_key):
    """Footage description words that rule a clip out: the brand's list (头屑、秃顶…) plus the product's own
    (用户 11-20：2x / 呼吸瓶不能用女生形象，AIGC 不能慢动作)."""
    prod = CFG["products"].get(product_key) or {}
    return list(dict.fromkeys((CFG.get("banned_footage_words") or []) + (prod.get("banned_footage_words") or [])))


def bad_footage(product_key, draft):
    """Shots in a cut that the user ruled out (user 11-10: no dandruff footage), found by segment description.
    The platform picks footage by tag, and dandruff shots often carry tags like 头皮出油 / 头发油."""
    words = banned_words_for(product_key)
    banned_files = CFG.get("banned_footage_files") or {}
    if not words and not banned_files:
        return []
    idx, out = footage_index(product_key), []
    for c in draft.get("clips", []):
        if c.get("trackId") != "main":
            continue
        name = (c.get("props", {}).get("src") or "").split("?")[0].rsplit("/", 1)[-1]
        seg = idx.get(name)
        d = (seg or {}).get("description") or ""
        # 用户 11-20：描述命中禁用词但人看过画面没问题的（「女士」指女士款瓶子、杂志封面上的人像），按切片 id 放行
        ok_ids = (CFG["products"].get(product_key) or {}).get("footage_reviewed_ok") or {}
        hit = None if (seg or {}).get("id") in ok_ids else next((w for w in words if w in d), None)
        if name in banned_files:  # user 11-11: deleted from the platform, but cuts made before still use it
            hit, d = "已删素材", banned_files[name]
        if hit:
            out.append(f"{hit}：{d[:30]}")
    return out


def pinned_clips(brief, draft):
    """记录指定段及默认开头的来源，供显式返工查询，不用于交付去重。
    草稿 clip id 为「平台分段 id-镜头」；平台分段按 brief 顺序从 1 编号。"""
    pinned = [str(k + 1) for k, sg in enumerate(brief["segments"]) if sg.get("silent") or sg.get("footage_pool") or sg.get("visual_query")]
    if not pinned:
        pinned = ["1"]  # 留档开头来源，供显式返工查询；不参与去重或交付排序。
    idx, first = footage_index(brief["product_key"]), {}
    for c in sorted((c for c in draft.get("clips", []) if c.get("trackId") == "main"), key=lambda c: c["from"]):
        x = idx.get((c.get("props", {}).get("src") or "").split("?")[0].rsplit("/", 1)[-1])
        first.setdefault(str(c.get("id", "")).split("-")[0], x and x["id"])
    return [first.get(k) for k in pinned]


def pin_visual(platform_seg, seg, product=None):
    """默认只写 visual_tags，平台在标签下全部候选中随机选片（多标签任一匹配）。
    用户指定的 footage_pool / visual_query 才附加检索筛选；未指定时清除旧 query/score。"""
    tags = seg.get("visual_tags") or (seg.get("footage_pool") or {}).get("tags")
    if not tags and product and any(t in seg_text(seg) for t in product.get("ingredient_terms", [])):
        tags = product.get("ingredient_footage")  # user 11-10: a line about an ingredient / tech shows texture
    v = platform_seg.get("visual") or {}
    if tags and v.get("mode") == "material_segment":
        v.setdefault("props", {})["tags"] = list(tags)
        q = seg.get("footage_pool") or seg.get("visual_query")  # user 11-11 one AI scene clip; 11-13 a pool of clips
        if q:
            v["props"]["query"], v["props"]["score"] = q["query"], q.get("score", 0.8)
        else:
            # ---------- 仅指定标签时保留全标签候选，不沿用平台建稿时附加的相似度筛选 ----------
            v["props"].pop("query", None)
            v["props"].pop("score", None)


def pin_decor(platform_seg, seg):
    """User 11-20 力士：包装更丰富。A brief segment may carry `effect` ({template_id, params}: a platform Remotion
    effect template — 金环扫光 / 点阵标注 / 徽章升起 / 悬浮光晕 …, its `text` is the on-screen 花字) and
    `transition` ([{name, type, durationInFrames}] from GET /resource/transition). They replace whatever the platform
    picked for that segment; segments without them keep the platform's choice."""
    if seg.get("effect"):
        platform_seg["effect"] = {"template_id": seg["effect"]["template_id"], "params": dict(seg["effect"].get("params") or {})}
    if seg.get("transition"):
        platform_seg["transition"] = [dict(t) for t in seg["transition"]]


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
    "段落顺序和每段的意思不变：先讲{concern}的问题，再讲产品；产品出现以后只讲用完的好结果，不写用了以后{concern}又变差",
    "不加早上、下午、下班、出门前这类时间线，不编使用场景和前后对比",
]


def banned_for(product):
    """Words a rewrite must never add (user 11-17: the platform's banned list sat only in the script-creation
    prompt, so the per-video rewrite added 「性价比」「一天」 back). Brand list + product list + other SKUs' claims."""
    bw = CFG.get("banned_words") or {}
    words = list(bw.get("error", [])) + list(product.get("banned_words", [])) + list(product.get("foreign_claims", []))
    return [w for w in dict.fromkeys(words) if w]


def alt_prompt(segs, product=None):
    """Rules every rewrite must follow, plus the reviewed phrasings of each line as the range to stay in."""
    rows = [f"「{sg.get('title') or sg.get('role')}」这段可以参考的说法：" + " / ".join([seg_text(sg)] + seg_alts(sg))
            for sg in voiced(segs) if seg_alts(sg)]
    concern = (product or {}).get("concern_word") or "头发"
    rules = [r.format(concern=concern) for r in REWRITE_RULES]
    if product:
        rules.append("不出现这些词：" + "、".join(banned_for(product)) + "；不写「最」「第一」和具体时间（几小时、几天、几周）")
    out = "# 用户要求\n" + "\n".join(f"- {r}" for r in rules)
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
    """How many to generate: exactly the scheduled quantity (用户 11-19/11-20：不双倍产，缺几条 topup 补几条).
    --factor above 1 only when the user asked for it in this turn; config generate_factor is ignored above 1."""
    factor = args.factor if args.factor else 1
    return min(300, math.ceil(b["quantity"] * factor))


DRY_RUN_TTL = 3600


def dry_run_gate(run, cmd, plan, dry):
    """用户 11-20：铁律 2 写进代码。dry-run 把 {brief: 条数} 写成收据并报给用户；真正提交时计划必须和
    一小时内的收据完全一致，否则拒绝。Returns True when the real run may send."""
    path = Path(run) / "clipo" / f"dryrun-{cmd}-{CFG['brand_key']}.json"
    plan = {k: n for k, n in plan.items() if n}
    if dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": time.time(), "plan": plan}, ensure_ascii=False))
        return False
    if not plan:
        return True
    rec = json.loads(path.read_text()) if path.exists() else None
    if not rec or time.time() - rec["at"] > DRY_RUN_TTL or rec["plan"] != plan:
        print(f"⛔ 没有匹配的 dry-run：先跑 {cmd} --dry-run 把条数报给用户（这次要发 {plan}，收据 {rec and rec['plan']}）")
        return False
    path.unlink()
    return True


def _submit_plan(cfg, run, probe):
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        return cmd_submit(cfg, run, probe) or {}


def cmd_submit(cfg, run, args):
    if cfg.get('quality_contract') and not args.scripts_only:
        from quality_contract import audit
        audit_args=argparse.Namespace(run=run,brief=args.brief,demo_pilot=True)
        if any(State(run).data.get('briefs',{}).get(b['brief_id'],{}).get('script_id') for b in load_briefs(run,args.brief)):
            if audit(audit_args,cfg):raise RuntimeError('真实配置未通过客户制作要求，未提交生成')
    if not args.dry_run and not args.scripts_only:  # 铁律 2：先算一遍要发多少，和 dry-run 收据对上才发
        probe = argparse.Namespace(**{**vars(args), "dry_run": True, "_quiet": True})
        if not dry_run_gate(run, "submit", _submit_plan(cfg, run, probe), False):
            return
    st = State(run)
    plan = {}
    launch = []
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
            if job.get("rounds") is not None and job.get("generation_id") and job.get("landed", 0) > 0:  # one task per brief: continue it
                # 11-20：首轮就失败、一条没落地的任务，平台不认 continue（任务 quantity=0、状态停在 failed）：走下面重建新任务
                lost = gen_quantity(cfg, b, args) + sum(t.get("quantity", 0) for t in job.get("topped", [])) \
                    - job.get("landed", 0) - sum(job.get("rounds", []))
                plan[b["brief_id"]] = max(lost, 0)
                total += max(lost, 0)
                if args.dry_run:  # 11-19：dry-run 曾在这里直接 continue 到平台，dry-run 只报数不发请求
                    print(f"🧪 {b['brief_id']} 将在原任务上继续生产 {max(lost, 0)} 条")
                    continue
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
        bulk_after_demo = False
        if demo_first(cfg, b) and job.get("generation_id"):
            # user 11-17: the demo is out; bulk only after a person approved it
            if job.get("demo_stage") != "demo":
                continue  # bulk already started; poll / topup handle it
            why = demo_problem(b)
            if why:
                print(f"⏸  {b['brief_id']} {why}，先不量产")
                skipped += 1
                continue
            if generating(job):
                print(f"⏳ {b['brief_id']} demo 还在跑，先 poll")
                skipped += 1
                continue
            bulk_after_demo = True
        elif job.get("generation_id"):
            continue  # already on the platform; poll / topup handle it
        if not job.get("generation_id") and not args.scripts_only and aigc_problem(b):
            print(f"⏸  {b['brief_id']} {aigc_problem(b)}，先不出 demo")  # 用户 11-22：素材人审在 demo 之前
            skipped += 1
            continue
        n = gen_quantity(cfg, b, args)
        if demo_first(cfg, b) and not job.get("generation_id"):
            n = 1  # one demo per script first (user 11-17)
        if bulk_after_demo:
            n = max(0, gen_quantity(cfg, b, args) - job.get("landed", 0))
        total += 0 if args.scripts_only else n
        plan[b["brief_id"]] = 0 if args.scripts_only else n
        if args.dry_run:
            what = "demo" if demo_first(cfg, b) and not job.get("generation_id") else ("量产" if bulk_after_demo else "")
            print(f"🧪 (dry run，未发送) {title} × {n}{'（' + what + '）' if what else ''}（排期 {b['quantity']}）")
            continue
        launch.append((b, job, pid, title, n, bulk_after_demo))
    # 用户 11-22「生成更快」：各 brief 建平台脚本互不相干（每个要等平台 1~6 分钟），并发建；生成请求仍逐个发
    def build(item):
        b, job, pid, title = item[:4]
        try:
            job["script_id"] = build_script(pid, b, title)
            return None
        except RuntimeError as e:
            return str(e)
    need = [x for x in launch if not x[1].get("script_id")]
    for item, err in zip(need, parallel(build, need, workers=SCRIPT_WORKERS)):
        if err:
            print(f"❌ {item[0]['brief_id']} {err}")
        else:
            print(f"📝 {item[0]['brief_id']} 平台脚本已建好并核对：{item[1]['script_id']}（{item[3]}）")
    if need:
        st.save()
    for b, job, pid, title, n, bulk_after_demo in launch:
        if not job.get("script_id"):
            skipped += 1
            continue
        if args.scripts_only:
            submitted += 1
            continue
        if bulk_after_demo:  # same platform task: continue it with the rest (one task per brief, user 11-06)
            job["rounds"] = split_quantity(n, cfg.get("generate_chunk") or n) if n else []
            job.update(stage="generating", demo_stage="bulk")
            if job["rounds"]:
                next_round(job)
            st.save()
            submitted += 1
            print(f"🚀 {b['brief_id']} demo 已通过（{b['demo_approval']['by']}），量产 {n} 条，接在同一个任务上")
            continue
        if not job.get("generation_id"):
            chunks = split_quantity(n, cfg.get("generate_chunk") or n)
            job["generation_id"] = api("POST", "/video-cut-generations", gen_body(job["script_id"], chunks[0]))["id"]
            job.update(stage="generating", rounds=chunks[1:], landed=0)
            if demo_first(cfg, b):
                job["demo_stage"] = "demo"
            job.pop("topups", None)
            st.save()
            submitted += 1
            if demo_first(cfg, b):
                print(f"🎬 {b['brief_id']} 先出 1 条 demo，poll 跑完后给人看；通过了 review_doc.py approve-demo，再 submit 量产")
            else:
                print(f"🚀 {b['brief_id']} 生成 {n} 条（排期 {b['quantity']}），平台上一个任务，分 {len(chunks)} 轮：{'+'.join(map(str, chunks))}")
    if args.dry_run:
        if getattr(args, "_quiet", False):
            return plan
        dry_run_gate(run, "submit", plan, True)
        print(f"\n(dry run) 将生成 {total} 条视频，跳过 {skipped} 个 brief。把这个条数报给用户，确认后去掉 --dry-run 提交（一小时内有效）")
        return
    if args.scripts_only:
        print(f"\n建好 {submitted} 个平台脚本，跳过 {skipped} 个，没有生成视频。生成前先 submit --dry-run 报条数")
        return
    print(f"\n新提交 {submitted} 个 brief，跳过 {skipped} 个。下一步：clipo_batch.py poll")


# ---------- re-review ----------

def cmd_rereview(cfg, run, args):
    """按最新渲染对象重新审核。先备份状态，刷新画面闸门；受理数量不符则拒绝旧结论。
    不改文案、不生成、不重渲；--all 同时重审原先通过的视频。"""
    st = State(run)
    briefs = {b["brief_id"]: b for b in load_briefs(run)}
    again, relinted = [], 0
    if not args.dry_run:
        backup = st.path.parent / f"rereview-backup-{time.strftime('%Y%m%d-%H%M%S')}.json"
        backup.write_text(json.dumps(st.data, ensure_ascii=False, indent=2))
    for vid, v in sorted(st.videos.items()):
        b = briefs.get(v["brief_id"])
        if v.get('customer_rework_required') or (getattr(args,'video_key',None) and vid not in args.video_key):
            continue
        if v.get("stage") != "done" or not b or (args.brief and v["brief_id"] != args.brief):
            continue
        prod = cfg["products"][b["product_key"]]
        got = v.get("texts") or []
        issues = [m for t in got for l, m in lint_text(t, cfg, prod) if l == "error"]
        issues += [m for l, m in lint_script(got, voiced(b["segments"]), b, cfg, prod) if l == "error"]
        relinted += issues != v.get("copy_issues")
        v["copy_issues"] = issues
        cut = api("GET", f"/video-cuts/{v['cut_id']}")
        if cut.get("output_status") != "success" or not cut.get("output_object_key"):
            raise RuntimeError(f"{vid} 最新输出未成功，不提交复审")
        if b.get("disable_effects") and any(c.get("trackId") == "effect" for c in cut["draft"]["clips"]):
            raise RuntimeError(f"{vid} 草稿仍有花字，不提交复审")
        if v.get("object_key") != cut["output_object_key"]:
            v["previous_object_key"] = v.get("object_key")
            v["object_key"] = cut["output_object_key"]
            v.pop("render_job", None)
        v["ai_review"], v["ai_reason"] = cut.get("ai_review_result"), cut.get("ai_review_reason")
        v["bad_footage"] = bad_footage(b["product_key"], cut["draft"])
        from qc import cut_checks
        v["qc_issues"] = cut_checks(b, cut["draft"], (cut.get("random_result") or {}).get("tts_speaker_id"))
        if args.all or v.get("ai_review") != "pass":
            again.append(v)
    print(f"重新查改写：{relinted} 条结论变了；要重新 AI 审核的：{len(again)} 条")
    if args.dry_run:
        return
    for i in range(0, len(again), 100):
        batch = again[i:i + 100]
        ids = [v["cut_id"] for v in batch]
        intent = backup.with_name(backup.stem + f"-request-{i}.json")
        intent.write_text(json.dumps({"cut_ids": ids, "outputs": [v["object_key"] for v in batch], "state": "submitting"}, ensure_ascii=False))
        receipt = api("POST", "/video-cuts/review", {"cut_ids": ids}, tries=1)
        intent.write_text(json.dumps({"cut_ids": ids, "outputs": [v["object_key"] for v in batch], "receipt": receipt}, ensure_ascii=False))
        if receipt.get("queued") != len(batch) or receipt.get("skipped", 0):
            st.save()
            raise RuntimeError(f"复审受理数不符，不接收旧结论：{receipt}")
        for v in batch:
            v["stage"], v["ai_review"], v["ai_reason"] = "reviewing", None, None
        st.save()
    st.save()
    print("已提交，poll 会读回新结论")


# ---------- top-up ----------

def deliverable(v):
    """Passed AI review, or the user released this video by name (`review_waived` holds their words: user 11-11
    released 爆款-opener videos or explicitly chose to review batch content themselves). Existing known issues
    (copy, voice, footage, watermark) still apply either way."""
    from quality_contract import delivery_issues
    if delivery_issues(v,CFG):return False
    return ((v.get("ai_review") == "pass" or bool(v.get("review_waived"))) and not v.get("copy_issues") and not v.get("watermark_missing")
            and not v.get("voice_cmd") and not v.get("voice_drift")
            and not v.get("bad_footage") and not v.get("qc_issues"))


def _topup_plan(cfg, run, probe):
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        return cmd_topup(cfg, run, probe) or {}


def cmd_topup(cfg, run, args):
    """Make sure every brief ends with `quantity` deliverable videos: regenerate more from the SAME
    approved video-script (no new copy, no new review) for whatever the AI review rejected.
    Asks for exactly the shortfall (user 11-19: no over-generation); --margin M asks for shortfall / pass rate × M."""
    held=State(run).data.get('briefs',{})
    if any(j.get('customer_rework_hold') for bid,j in held.items() if not args.brief or bid==args.brief):
        print('⏸ 用户退回批量：先核逐条文案和人物来源分配，allocate完成前不补产')
        return
    if not args.dry_run:  # 铁律 2
        probe = argparse.Namespace(**{**vars(args), "dry_run": True, "_quiet": True})
        if not dry_run_gate(run, "topup", _topup_plan(cfg, run, probe), False):
            return
    st = State(run)
    jobs = st.data.get("briefs", {})
    total = 0
    plan = {}
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
        if job.get("demo_stage") == "demo":
            print(f"⏸  {bid} 还在 demo 阶段：demo 审过后用 submit 量产，不用 topup")
            continue
        vids = [v for v in st.videos.values() if v["brief_id"] == bid]
        if generating(job) or any(v.get("stage") not in ("done", "failed") for v in vids):
            print(f"⏳ {bid} 还有视频没跑完，先 poll")
            continue
        if cfg.get('quality_contract'):
            from quality_contract import needs_media_review
            pending=[v for v in vids if needs_media_review(v,cfg)]
            if pending:
                print(f"⏸ {bid} 有{len(pending)}条已渲染视频待成片自查，先完成检查，不重复补产")
                continue
        ok = sum(deliverable(v) for v in vids)
        short = b["quantity"] - ok
        if short <= 0:
            print(f"✅ {bid} 可交付 {ok}/{b['quantity']}，够了")
            continue
        rate = max(ok / len(vids), 0.2) if vids else 0.5
        # 用户 11-19：缺几条就补几条，不按通过率放大；要多补时显式传 --margin
        want = min(args.max_per_brief, short if args.margin is None else max(short, math.ceil(short / rate * args.margin)))
        offset = max((int(k.rsplit("V", 1)[1]) for k in st.videos if k.startswith(bid + "-V")), default=0)
        total += want
        plan[bid] = want
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
        if getattr(args, "_quiet", False):
            return plan
        dry_run_gate(run, "topup", plan, True)
        print(f"\n(dry run) 将补生成 {total} 条（消耗额度）。把条数报给用户，确认后去掉 --dry-run（一小时内有效）")
    elif total:
        print(f"\n已补生成 {total} 条。下一步：poll，跑完再 topup，直到每个 brief 都够数")


# ---------- poll ----------

def settled(v):
    """拉取阶段已处理完的条目：已提交渲染、等 AIGC 绑定，或渲染前闸门已判不合格。"""
    return bool(v.get("render_job")) or v.get("stage") in ("aigc_pending", "failed")


def fit_single_line_subtitles(draft):
    """字幕只缩字号、不拆字、不改cue或文字；给描边/字体差异留足宽度。"""
    d=copy.deepcopy(draft);width=d['composition']['width'];cap=(CFG or {}).get('subtitle_font_max',36)
    for clip in d.get('clips',[]):
        props=clip.get('props') or {}
        if clip.get('trackId')!='overlay' or not props.get('text'):continue
        text=props['text'];units=max(1,len(text.replace('\n','').replace('\r','')))
        props['font_size']=max(1,min(props.get('font_size') or cap,cap,int(width*.42/units)))
    return d


def prepare_draft(st, b, v, key, cut):
    """渲染前一次完成 BGM/音效/箭头、去花字、禁用画面替换与右下角角标；不因重复换开头。
    Returns (draft, watermark spans | None 整片 | False 不加角标)。"""
    cut_id = cut.get("id") or v["cut_id"]
    d = with_bgm_volume(cut["draft"], b["product_key"])  # 用户 11-19：箭头盖满最后一句、音效音量、BGM 音量
    if b.get("disable_effects"):
        d = without_effects(d)
    if b.get("replace_banned_footage") or bad_footage(b["product_key"], d):
        try:
            d, v["restyle_replacements"] = replace_banned_shots(b, d, cut_id, st.path.parent / "restyle-media")
        except RuntimeError as e:
            v["replace_error"] = str(e)[:200]  # 换不了的保留，交给下面的闸门拦
    if b['brief_id'] in (CFG.get('task_native_bulk_briefs') or []) and not key.endswith('-V01'):
        import task_bulk
        d = task_bulk.prepare(sys.modules[__name__], st, b, v, key, cut, d)
    elif b['brief_id'] in (CFG.get('lux_bulk_approved_briefs') or []) and not key.endswith('-V01'):
        import lux_bulk_polish
        d = lux_bulk_polish.prepare(sys.modules[__name__], st, b, v, key, cut, d)
    spans = False
    wm = CFG.get("watermark") or {}
    if wm.get("add"):
        # 用户 11-18/11-19：「内容由AI生成」一律右下角；ai_only 时只盖 AI 镜头（KP 客户：非 AI 素材不加角标）
        from footage_qc import with_watermark
        spans = ai_spans(b["product_key"], d) if wm.get("ai_only") else None
        d = with_watermark(d, spans)
    if CFG.get("subtitle_single_line"):
        d=fit_single_line_subtitles(d)
    return d, spans


def check_readback(want, got, spans):
    """一次 PUT 后回读：主画面来源与角标必须和写入一致，否则不渲染。"""
    from footage_qc import WATERMARK_PROPS
    src = lambda dr: [(c["id"], c["props"].get("src", "").split("?")[0]) for c in sorted(
        (c for c in dr["clips"] if c.get("trackId") == "main"), key=lambda c: c["from"])]
    if src(want) != src(got):
        raise RuntimeError("草稿写回后主画面读回不对，不渲染")
    if spans is not False:
        wm = sorted([c for c in got["clips"] if c.get("trackId") == "watermark"], key=lambda c: c["from"])
        exp = spans if spans is not None else [(0, got["composition"]["durationInFrames"])]
        if [(c["from"], c["durationInFrames"]) for c in wm] != exp or any(c["props"] != WATERMARK_PROPS for c in wm):
            raise RuntimeError("右下角水印写回后读回不对，不渲染")


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
            if settled(v):
                continue
            try:
                cut = api("GET", f"/video-cuts/{cut_id}")
                rr = cut.get("random_result") or {}
                raw = [x["content"] for x in rr.get("vo_rewrite", [])]
                got = [re.sub(r"\[#[^\]]*\]", "", t) for t in raw] or [t for t in job["texts"] if t]
                v["voice_cmd"] = any("[#" in t for t in raw)  # user 11-09: sounds like two speakers
                v["bad_footage"] = bad_footage(b["product_key"], cut["draft"])
                v["pinned_clips"] = pinned_clips(b, cut["draft"])
                v["texts"], v["voice"] = got, rr.get("tts_speaker_id")
                v["duration_s"] = round(cut["draft"]["composition"]["durationInFrames"] / 30, 1)
                if (CFG.get("watermark") or {}).get("add") and "水印" not in [t.get("name") for t in cut["draft"]["tracks"]]:
                    v["watermark_missing"] = True
                issues = [m for t in got for l, m in lint_text(t, cfg, prod) if l == "error"]
                issues += [m for l, m in lint_script(got, voiced(b["segments"]), b, cfg, prod) if l == "error"]
                v["copy_issues"] = issues
                if aigc_required(b) and not v.get("aigc_material_set"):
                    v["stage"] = "aigc_pending"
                    continue
                from demo_authorization import permits_preview
                if permits_preview(b) and vids[-1].endswith("-V01") and not v.get("render_job"):
                    v["stage"] = "demo_prepare_pending"
                    v["user_review_pending"] = True
                    continue  # Whole-voice preparation precedes this authorized preview render.
                # ---------- 包装、禁用画面替换与角标在渲染前一次写入并回读；保留平台随机开头 ----------
                styled, wm_spans = prepare_draft(st, b, v, vids[-1], cut)
                if styled != cut["draft"]:
                    api("PUT", f"/video-cuts/{cut_id}", {"script_id": cut["script_id"], "draft": styled})
                    cut = api("GET", f"/video-cuts/{cut_id}")
                    check_readback(styled, cut["draft"], wm_spans)
                if wm_spans is not False:
                    v["ai_label_spans"] = wm_spans
                    v.pop("watermark_missing", None)
                v["bad_footage"] = bad_footage(b["product_key"], cut["draft"])
                v["pinned_clips"] = pinned_clips(b, cut["draft"])
                if CFG.get('quality_contract'):
                    from asset_health import check_sources
                    v['asset_issues']=check_sources(cut['draft'])
                    if v['asset_issues']:
                        v.update(stage='failed',error='失效媒体拦截：'+'；'.join(v['asset_issues']))
                        continue
                from qc import cut_checks  # 用户 11-20：交付闸门，渲染前按草稿查角标、BGM、禁用画面、画面标签、音色
                v["qc_issues"] = cut_checks(b, cut["draft"], rr.get("tts_speaker_id"))
                from quality_contract import cut_facts
                v['quality_facts']=cut_facts(cut['draft'])
                if v["qc_issues"] or v["bad_footage"] or v["copy_issues"] or v.get("voice_cmd"):
                    # 用户 11-22：交付不了的不渲染、不送审，直接记下原因，topup 补位（省一次渲染 + 一次审核）
                    v["stage"], v["error"] = "failed", "渲染前闸门不过：" + "；".join(
                        (v["qc_issues"] or []) + (v["bad_footage"] or []) + (v["copy_issues"] or [])
                        + (["双人声指令"] if v.get("voice_cmd") else []))[:300]
                    continue
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
                elif all(settled(st.videos[x]) for x in vids):
                    gen["stage"] = "generated"
        elif all(settled(st.videos[x]) for x in vids):
            gen["stage"] = "generated"


def next_round(job):
    """Add the brief's next round to its own platform task (POST /video-cut-generations/{id}/continue)."""
    if not job.get("rounds"):  # 11-19：重试时算出来已经够数，没有要补的轮次
        return
    c = job["rounds"][0]
    try:
        api("POST", f"/video-cut-generations/{job['generation_id']}/continue", {"quantity": c})
        job["rounds"] = job["rounds"][1:]
    except RuntimeError as e:  # e.g. previous round still running; retried on the next poll
        job["last_error"] = str(e)[:300]


def generating(job):
    return job.get("stage") == "generating" or any(t.get("stage") == "generating" for t in job.get("topups", []))


def cmd_bind_aigc(cfg, run, args):
    """Bind successful per-video AIGC sets, preserving other tracks; then render each immediately."""
    st = State(run)
    briefs = {b["brief_id"]: b for b in load_briefs(run, args.brief)}
    ready = {}
    for bid, brief in briefs.items():
        ready[bid] = [key for key, v in st.videos.items() if v["brief_id"] == bid and
                      any(g.get("video_key") == key or g.get("cut_id") == v["cut_id"]
                          for g in brief.get("visual_sets", [])
                          if g.get("source_generation_id") or g.get("retained_user_approved_demo"))]
    validate_selection(briefs, st.videos, ready)
    for bid, keys in ready.items():
        for key in keys:
            v = st.videos[key]
            group = group_for(briefs[bid], key, v)
            if group.get("retained_user_approved_demo"):
                continue
            idem = render_key(v["cut_id"], group)
            tracked_legacy = (not v.get("aigc_render_key") and v.get("render_job") and
                              v.get("aigc_material_set") == group["name"] and
                              v.get("aigc_source_generation_id") == group["source_generation_id"] and
                              group.get("render_revision", 1) == 1)
            if (v.get("aigc_render_key") == idem or tracked_legacy) and v.get("stage") in ("rendering", "rendered", "reviewing", "done"):
                continue
            if args.dry_run:
                print(f"{key}: bind {group['name']} and render")
                continue
            before = api("GET", f"/video-cuts/{v['cut_id']}")
            draft = copy.deepcopy(before["draft"])
            fps = draft["composition"].get("fps", 30)
            for n, row in aigc_segments(group).items():
                clips = [c for c in draft["clips"] if c.get("trackId") == "main" and str(c.get("id", "")).split("-")[0] == n]
                if len(clips) != 1 or not row.get("duration"):
                    raise RuntimeError(f"{key} S{n}: 需要单一人物窗口与有效源片时长")
                clip = clips[0]
                need = clip["durationInFrames"] / fps
                rate = min(1.0, (row["duration"] - 1 / fps) / need)
                if rate <= 0:
                    raise RuntimeError(f"{key}: 源片不足有效一帧")
                clip["props"].update(src=row["object_key"], startFrom=0, playbackRate=rate)
                clip["props"].pop("loop", None)
            validate_draft(group, draft)
            api("PUT", f"/video-cuts/{v['cut_id']}", {"script_id": before["script_id"], "draft": draft})
            current = api("GET", f"/video-cuts/{v['cut_id']}")
            validate_draft(group, current["draft"])
            job = api("POST", "/render-jobs", {"spec": current["draft"], "options": {"codec": "h264", "quality": "high", "scale": 1},
                                               "cut_id": v["cut_id"], "idempotency_key": idem})
            v.update(render_job=job["job_id"], stage="rendering", aigc_render_key=idem,
                     aigc_material_set=group["name"], aigc_source_generation_id=group["source_generation_id"])
            for field in ("object_key", "ai_review", "ai_reason", "review_waived", "bad_footage", "error", "last_error"):
                v.pop(field, None)
            st.save()
            print(f"{key}: {group['name']} rendering {job['job_id']}", flush=True)


POLL_WORKERS = 8  # 并发读平台的上限；只读 GET 与互不相干的条目，不改请求语义
SCRIPT_WORKERS = 5  # 并发建平台脚本的上限


def parallel(fn, items, workers=POLL_WORKERS):
    """Run fn over items with a small thread pool (I/O-bound platform reads). Exceptions surface to the caller."""
    if len(items) <= 1:
        return [fn(x) for x in items]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        return list(ex.map(fn, items))


def poll_video(st, briefs, v):
    """One video's render / AI-review progress (GET only). Mutates only this video's entry."""
    stage = v.get("stage")
    user_instruction = user_review_instruction(st.data, briefs.get(v["brief_id"], {}))
    if stage == "reviewing" and user_instruction:
        release_for_user(v, user_instruction)
        return
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
                if v['brief_id'] in (CFG.get('lux_bulk_approved_briefs') or []):
                    import lux_bulk_polish
                    key = next(k for k,z in st.videos.items() if z['cut_id']==v['cut_id'])
                    lux_bulk_polish.release_layout(sys.modules[__name__], st, briefs[v['brief_id']], v, key, cut)
    except RuntimeError as e:
        v["last_error"] = str(e)[:300]


def cmd_poll(cfg, run, args):
    st = State(run)
    deadline = time.time() + args.wait
    briefs = {b["brief_id"]: b for b in load_briefs(run, getattr(args, "brief", None))}
    while True:
        pending = 0
        # 用户 11-22「生成更快」：各 brief 的生成任务互不相干，并发拉取；同一 brief 的条目仍在一个线程里顺序处理
        pulls = [(bid, job, gen) for bid, job in sorted(st.data.get("briefs", {}).items()) if bid in briefs
                 for gen in [job] + job.get("topups", []) if gen.get("stage") == "generating"]
        pending += len(pulls)
        by_brief = {}
        for bid, job, gen in pulls:
            by_brief.setdefault(bid, []).append((job, gen))
        parallel(lambda item: [pull_generation(st, cfg, briefs[item[0]], job, gen) for job, gen in item[1]],
                 list(by_brief.items()))
        rendered = []  # cuts that finished rendering this round: sent to AI review as one batch
        mine = [(vid, v) for vid, v in sorted(st.videos.items()) if v["brief_id"] in briefs]
        parallel(lambda item: poll_video(st, briefs, item[1]), mine)
        for vid, v in mine:
            if v.get("stage") == "rendered":
                user_instruction = user_review_instruction(st.data, briefs.get(v["brief_id"], {}))
                if user_instruction:
                    release_for_user(v, user_instruction)
                else:
                    rendered.append(v)
            pending += v.get("stage") not in ("done", "failed", "aigc_pending", None)
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
        time.sleep(poll_interval(args.interval, pending, by["brief生成中"]))
    sys.exit(0 if not pending else 3)


def poll_interval(base, pending, generating_briefs):
    """用户 11-22：尾巴上只剩几条时少等一轮。生成阶段平台按分钟出片，保持 base；只剩 ≤5 条渲染 / 审核时 10 秒一轮。"""
    return min(base, 10) if not generating_briefs and pending <= 5 else base


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
    """One screen: where every brief and video of the day stands, and what to do next. No network.
    用户 11-24：改由 scripts/progress.py 出（数、原因、下一条命令）；旧实现保留在 cmd_status_legacy。"""
    import progress
    print(progress.render(progress.day_progress(cfg, run, getattr(args, "brief", None))))


def cmd_status_legacy(cfg, run, args):
    """11-22 前的 status（只给阶段名）。"""
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
        elif b.get("status") == "approved" and not job and aigc_problem(b):
            nxt.add("⑦ AIGC 素材：aigc_gen.py 生成 → 抽帧给人审 → review_doc.py approve-aigc")
        elif b.get("status") == "approved" and not job:
            nxt.add("⑧ submit --dry-run → submit（先出 1 条 demo）" if demo_first(cfg, b) else "⑧ submit")
        elif job.get("demo_stage") == "demo" and not generating(job):
            nxt.add("⑨ demo 出来了：share --brief <id> --partial 发给人看 → review_doc.py approve-demo"
                    if demo_problem(b) else "⑩ demo 已通过：submit --dry-run → submit 量产")
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
    summary = [f"共 {total} 条：AI 审核通过 {passed}，审核驳回或改写违规 {len(rejected)}，无通过或驳回结论 {total - passed - len(rejected)}（改写违规 {bad_copy} 条）。", ""]
    user_review = sum(bool(v.get("review_waived")) for v in st.videos.values())
    if user_review:
        summary += [f"按用户原话交付、由用户检查 {user_review} 条；不记作 AI 审核通过。", ""]
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


def opener_clip(v):
    """The footage segment id that opens this video (recorded by poll as pinned_clips[0])."""
    return v.get("hook_clip") or (v.get("pinned_clips") or [None])[0]


def picks(st, bid, quantity):
    """按视频编号选取合格成片；前 quantity 条交付，其余备用，不按开头切片重排。"""
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
    passed = sum(v.get("ai_review") == "pass" for v in st.videos.values())
    user_review = sum(bool(v.get("review_waived")) for v in st.videos.values())
    group = st.data.get("share_group")
    head = [f"# {Path(run).name} 交付清单", ""] + ([f"**今天全部分享链接**：{BASE.replace('/api/v1', '')}/share/group/{group['id']}", ""] if group else []) + [
            f"**交付 {total} / {need} 条**{'，全部够数' if total >= need else f'，还差 {need - total} 条（topup）'}。"
            f"共生成 {gen} 条，AI 审核通过 {passed} 条（{passed / max(gen, 1):.0%}）。"
            + (f"按用户原话交付、由用户检查 {user_review} 条；不记作 AI 审核通过。" if user_review else ""), ""]
    packs = [(k[5:], v) for k, v in st.data.get("shares", {}).items() if k.startswith("pack:")]
    if packs:
        head += ["## 合并交付链接（每个 10 条）", ""]
        head += [f"- {name}（{len(v['videos'])} 条）：{share_url(v['id'])}" for name, v in sorted(packs)] + [""]
    (Path(run) / out_name(CFG, "delivery.md")).write_text("\n".join(head + out))
    print(f"→ {Path(run) / out_name(CFG, 'delivery.md')}（交付 {total}/{need}）")


def pack_links(cfg, briefs, st):
    """User 11-11: deliver in links of `brief_size` (10) even when a product's videos were made as smaller briefs
    (one per 爆款 opener). Briefs of one product and day that set `pack` are pooled and dealt out round-robin, so
    every link mixes the openers. Returns [(name, [video ids], product_key, [brief ids])] or [] when nobody packs."""
    size = cfg.get("brief_size", 10)
    out, groups = [], {}
    for b in sorted(briefs.values(), key=lambda x: x["brief_id"]):
        if b.get("pack"):
            groups.setdefault((b["product_key"], b["date"]), []).append(b)
    for (pk, day), bs in sorted(groups.items()):
        queues = [picks(st, b["brief_id"], b["quantity"])[0] for b in bs]
        need = sum(b["quantity"] for b in bs)
        pool = []
        while any(queues):
            for q in queues:
                if q:
                    pool.append(q.pop(0))
        if len(pool) < need:
            print(f"⏸  {pk} 合并交付可交付 {len(pool)}/{need}，不够数先不发")
            continue
        first = bs[0]
        for i in range(0, need, size):
            name = delivery_name(cfg, dict(first, delivery_no=i // size + 1 + (first.get("pack_offset") or 0)), list(briefs.values()))
            out.append((name, pool[i:i + size], pk, [b["brief_id"] for b in bs]))
    return out


def cmd_share_packed(cfg, run, args, st, briefs, group):
    """Share links for packed briefs (see pack_links); stored under shares["pack:<name>"]."""
    shares = st.data.setdefault("shares", {})
    for name, pick, pk, bids in pack_links(cfg, briefs, st):
        key = f"pack:{name}"
        old = shares.get(key)
        cuts = [st.videos[k]["cut_id"] for k in pick]
        if old and old["cuts"] == cuts and not args.refresh:
            print(f"✅ {name} 已有分享 {share_url(old['id'])}")
            continue
        if args.dry_run:
            print(f"🧪 将{'更新' if old else '新建'}分享「{name}」{len(pick)} 条，来自 {sorted({st.videos[k]['brief_id'] for k in pick})}")
            continue
        payload = {"product_id": cfg["products"][pk]["clipo_product_id"], "group_id": group["id"], "title": name,
                   "video_name_prefix": name + "-", "videos": [share_video(st.videos[k], briefs[st.videos[k]["brief_id"]], k) for k in pick]}
        sh = api("POST", "/video-shares", payload)
        if old:
            g = api("GET", f"/video-share-groups/{group['id']}")
            api("PATCH", f"/video-share-groups/{group['id']}", {"video_share_ids": [x["id"] for x in g["shares"] if x["id"] != old["id"]]})
            api("DELETE", f"/video-shares/{old['id']}")
        shares[key] = {"id": sh["id"], "videos": pick, "cuts": cuts, "title": name, "briefs": bids, "at": time.strftime("%Y-%m-%d %H:%M")}
        st.save()
        print(f"🔗 {name} {len(pick)} 条 → {share_url(sh['id'])}")
    for bid, b in briefs.items():  # a packed brief's own link (from before it was packed) leaves the group
        old = shares.get(bid)
        if b.get("pack") and old:
            if args.dry_run:
                print(f"🧪 将删掉按 brief 建的旧链接「{old.get('title')}」")
                continue
            g = api("GET", f"/video-share-groups/{group['id']}")
            api("PATCH", f"/video-share-groups/{group['id']}", {"video_share_ids": [x["id"] for x in g["shares"] if x["id"] != old["id"]]})
            api("DELETE", f"/video-shares/{old['id']}")
            st.data.setdefault("retired_shares", {})[bid] = shares.pop(bid)
            st.save()
            print(f"🗑  删掉旧链接「{old.get('title')}」（已合并进 10 条一个的链接）")


def share_video(v, brief=None, video_key=None):
    cut = api("GET", f"/video-cuts/{v['cut_id']}")
    if brief and aigc_required(brief):
        validate_draft(group_for(brief, video_key, v), cut["draft"])
    comp = cut["draft"]["composition"]
    cover = re.search(r"\.aliyuncs\.com/([^?]+)", cut["draft"].get("cover_url") or "")
    # ---------- 固定已审核成片；线上重渲不能替换本地交付对象 ----------
    return {"cut_id": v["cut_id"], "video_object_key": v.get("object_key") or cut["output_object_key"], "cover_object_key": cover.group(1) if cover else None,
            "duration_seconds": round(comp["durationInFrames"] / comp.get("fps", 30), 2), "width": comp["width"], "height": comp["height"]}


def share_scope(all_briefs, only=None):
    """限定分享范围；指定 pack brief 时，仅聚合同日、同产品、同店铺的小 brief。"""
    if not only:
        return {b["brief_id"]: b for b in all_briefs}
    selected = next((b for b in all_briefs if b["brief_id"] == only), None)
    if not selected:
        return {}
    if not selected.get("pack"):
        return {only: selected}
    identity = lambda b: (b["date"], b["product_key"], b.get("store"))
    return {b["brief_id"]: b for b in all_briefs if b.get("pack") and identity(b) == identity(selected)}


def cmd_share(cfg, run, args):
    """One share link per brief with its delivered videos (POST /video-shares), all collected into one share
    group for the day (POST /video-share-groups). Only briefs that reached their quantity unless --partial.
    Re-running updates a brief whose picked videos changed: a new share replaces the old one in the group."""
    st = State(run)
    all_briefs = load_briefs(run)
    briefs = share_scope(all_briefs, args.brief)
    validate_selection(briefs, st.videos,
                       {bid: picks(st, bid, b["quantity"])[0] for bid, b in briefs.items()})
    shares = st.data.setdefault("shares", {})
    todo = []
    for bid in sorted(briefs):
        b = briefs[bid]
        if b.get("pack"):
            continue  # delivered in packed links of brief_size (pack_links)
        pick, _ = picks(st, bid, b["quantity"])
        if not pick:
            print(f"⏸  {bid} 还没有可交付的视频")
            continue
        if len(pick) < b["quantity"] and not args.partial:
            print(f"⏸  {bid} 可交付 {len(pick)}/{b['quantity']}，不够数先不发（要先发加 --partial）")
            continue
        from qc import brief_checks
        batch_problems = brief_checks(b, [(k, st.videos[k]) for k in pick])
        if batch_problems:
            print(f"⏸  {bid} 整组质量检查未通过：{batch_problems[:3]}")
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
        if any(b.get("pack") for b in briefs.values()):
            cmd_share_packed(cfg, run, args, st, briefs, None)
        print(f"\n(dry run) 将建 {len(todo)} 个分享链接。确认后去掉 --dry-run")
        return
    day = (all_briefs[0].get("date") or Path(run).name).replace("-", "")
    group = st.data.get("share_group")
    packed = any(b.get("pack") for b in briefs.values())
    if (todo or packed) and not group:
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
                   "videos": [share_video(st.videos[k], b, k) for k in pick]}
        if old:
            record = Path(run) / "clipo" / f"share-replace-{old['id']}-{time.strftime('%Y%m%d-%H%M%S')}.json"
            record.write_text(json.dumps({"group": api("GET", f"/video-share-groups/{group['id']}"),
                                          "old_share": api("GET", f"/video-shares/{old['id']}")}, ensure_ascii=False))
        sh = api("POST", "/video-shares", payload, tries=1)
        got = api("GET", f"/video-shares/{sh['id']}")
        object_key = lambda video: video.get("video_object_key") or (re.search(r"\.aliyuncs\.com/([^?]+)", video.get("video_url") or "") or [None, None])[1]
        if got.get("title") != payload["title"] or [object_key(video) for video in got.get("videos", [])] != [video["video_object_key"] for video in payload["videos"]]:
            raise RuntimeError(f"{bid} 分享回读不一致，停止交付")
        if old:  # 分组替换为新版，保留旧独立链接。
            g = api("GET", f"/video-share-groups/{group['id']}")
            ids = [x["id"] for x in g["shares"] if x["id"] != old["id"]]
            api("PATCH", f"/video-share-groups/{group['id']}", {"video_share_ids": ids})
        shares[bid] = {"id": sh["id"], "videos": pick, "cuts": [v["cut_id"] for v in vids], "title": payload["title"], "at": time.strftime("%Y-%m-%d %H:%M")}
        st.save()
        print(f"🔗 {bid} {payload['title']} {len(pick)} 条 → {share_url(sh['id'])}")
    if packed and group:
        cmd_share_packed(cfg, run, args, st, briefs, group)
    if group:
        print(f"\n今天全部分享（分组）：{BASE.replace('/api/v1', '')}/share/group/{group['id']}")
    write_delivery(st, {b["brief_id"]: b for b in load_briefs(run)}, run)
    if args.download and not args.dry_run:
        print()
        cmd_download(cfg, run, args)


def cmd_rename_shares(cfg, run, args):
    """User 11-18: a share group built by hand has wrong names (b01, b02…). The platform cannot rename a share,
    so each one is rebuilt with the same videos under delivery_name() and put back into the group in brief
    order; the group gets delivery_group_naming. Old shares are kept (their links still open) and a restore
    record is written. The brief comes from the script title (1118-B09-…); the keyword from its first line."""
    gid = args.group.rstrip("/").rsplit("/", 1)[-1]
    g = api("GET", f"/video-share-groups/{gid}")
    briefs = {b["brief_id"]: b for b in load_briefs(run)}
    plan = []
    for s in g["shares"]:
        sh = api("GET", f"/video-shares/{s['id']}")
        sc = api("GET", f"/video-scripts/{sh['script_id']}")
        bid = (re.match(r"\d{4}-[A-Z]\d{2}", sc.get("title") or "") or [None])[0]
        if bid not in briefs:
            raise SystemExit(f"{s['title']}：脚本标题「{sc.get('title')}」对不上本地 brief")
        b = dict(briefs[bid], segments=[{"text": (sc["segments"][0].get("content") or "").split("\n")[0]}])
        plan.append((bid, sh, delivery_name(cfg, b, list(briefs.values()))))
    plan.sort(key=lambda x: x[0])
    day = Path(run).name
    group_name = re.sub(r"--+", "-", cfg.get("delivery_group_naming", "{date}-{brand_tag}").format(
        yy=day[2:4], yymmdd=day[2:8], date=day[4:8], brand_tag=cfg.get("brand_tag", cfg["brand"]), campaign=""))
    print(f"分组：{g['name']} → {group_name}")
    for bid, sh, name in plan:
        print(f"  {bid} {sh['title']} → {name}（{len(sh['videos'])} 条）")
    if args.dry_run:
        print("\n(dry run) 确认后去掉 --dry-run；旧链接保留，不删")
        return
    key = lambda u: (re.search(r"\.aliyuncs\.com/([^?]+)", u or "") or [None, None])[1]
    record = Path(run) / "clipo" / f"share-rename-{time.strftime('%Y%m%d-%H%M%S')}.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({"group": g, "old_shares": [sh for _, sh, _ in plan]}, ensure_ascii=False, indent=1))
    new_ids = []
    for bid, sh, name in plan:
        vids = [{"video_object_key": key(v["video_url"]), "cover_object_key": key(v.get("cover_url")),
                 "duration_seconds": float(v["duration_seconds"]), "width": int(v["width"]), "height": int(v["height"])}
                for v in sh["videos"]]
        new = api("POST", "/video-shares", {"script_id": sh["script_id"], "product_id": sh["product_id"],
                                            "title": name, "video_name_prefix": name + "-", "videos": vids})
        back = api("GET", f"/video-shares/{new['id']}")
        if back["title"] != name or [key(v["video_url"]) for v in back["videos"]] != [x["video_object_key"] for x in vids]:
            raise RuntimeError(f"{bid} 新分享读回不一致")
        new_ids.append(new["id"])
        print(f"🔗 {bid} {name} → {share_url(new['id'])}", flush=True)
    api("PATCH", f"/video-share-groups/{gid}", {"name": group_name, "video_share_ids": new_ids})
    g2 = api("GET", f"/video-share-groups/{gid}")
    if g2["name"] != group_name or sorted(x["id"] for x in g2["shares"]) != sorted(new_ids):  # 平台按新到旧展示
        raise RuntimeError("分组读回不一致")
    data = json.loads(record.read_text()); data["new_share_ids"] = new_ids
    record.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print(f"\n分组已改名并换成新链接：{BASE.replace('/api/v1', '')}/share/group/{gid}\n恢复记录：{record}")


def cmd_split_group(cfg, run, args):
    """User 11-18: one delivery link per store (旗舰店 3 个 brief 一个、洗护号 6 个一个). The shares in a group are
    sorted into new groups by the store of their brief, named delivery_group_naming + store. The old group is
    left as it was (its link may already be with the client); a record of what was built is written."""
    gid = args.group.rstrip("/").rsplit("/", 1)[-1]
    g = api("GET", f"/video-share-groups/{gid}")
    briefs = {b["brief_id"]: b for b in load_briefs(run)}
    stores = {}
    for s in g["shares"]:
        bid = next((b for b in briefs if re.search(rf"brief{int(b[-2:])}(?!\d)", s["title"])
                    and briefs[b]["product_key"] in {k for k, p in cfg["products"].items() if p["clipo_product_id"] == s["product_id"]}), None)
        if not bid:
            raise SystemExit(f"{s['title']}：对不上本地 brief")
        stores.setdefault(briefs[bid]["store"].split("（")[0], []).append((bid, s["id"], s["title"]))
    day = Path(run).name
    base = re.sub(r"--+", "-", cfg.get("delivery_group_naming", "{date}-{brand_tag}").format(
        yy=day[2:4], yymmdd=day[2:8], date=day[4:8], brand_tag=cfg.get("brand_tag", cfg["brand"]), campaign=""))
    plan = [(f"{base}-{store.removeprefix(cfg.get('brand_tag', ''))}", sorted(rows)) for store, rows in stores.items()]
    if getattr(args, "store", None):  # 11-20：只建一个店铺的组，其他店铺的组归各自会话
        plan = [(n, r) for n, r in plan if n.endswith("-" + args.store)]
    for name, rows in plan:
        print(f"{name}：{len(rows)} 个链接\n" + "\n".join(f"  {bid} {t}" for bid, _, t in rows))
    if args.dry_run:
        print("\n(dry run) 原分组保留不动；确认后去掉 --dry-run")
        return
    out = []
    for name, rows in plan:
        ids = [sid for _, sid, _ in rows]
        new = api("POST", "/video-share-groups", {"name": name, "workspace_id": g.get("workspace_id"), "video_share_ids": ids})
        back = api("GET", f"/video-share-groups/{new['id']}")
        if back["name"] != name or sorted(x["id"] for x in back["shares"]) != sorted(ids):
            raise RuntimeError(f"{name} 分组读回不一致")
        out.append({"name": name, "id": new["id"], "shares": ids})
        print(f"🔗 {name}：{BASE.replace('/api/v1', '')}/share/group/{new['id']}", flush=True)
    rec = Path(run) / "clipo" / f"share-split-{time.strftime('%Y%m%d-%H%M%S')}.json"
    rec.write_text(json.dumps({"from_group": gid, "groups": out}, ensure_ascii=False, indent=1))
    print(f"记录：{rec}")


def cmd_refresh_group(cfg, run, args):
    """User 11-18: the cuts behind a group were re-rendered (watermark added), but a share keeps the old video
    files. Each share is rebuilt with the same title, same cuts in the same order and their current renders,
    then the group points at the new shares. Old shares are kept; a record is written."""
    gid = args.group.rstrip("/").rsplit("/", 1)[-1]
    g = api("GET", f"/video-share-groups/{gid}")
    key = lambda u: (re.search(r"\.aliyuncs\.com/([^?]+)", u or "") or [None, None])[1]
    cuts = {}
    for p in cfg["products"].values():
        skip = 0
        while True:
            r = api("GET", f"/video-cuts?product_id={p['clipo_product_id']}&has_output=true&skip={skip}&limit=100")
            items = r if isinstance(r, list) else r.get("items", [])
            cuts.update({c["id"]: c for c in items})
            if len(items) < 100:
                break
            skip += 100
    by_old = {}
    if args.map:  # old output key → cut id, from the backups taken before re-rendering
        for f in Path(args.map).glob("wm-backup-*.json"):
            b = json.loads(f.read_text())
            by_old[b["output_object_key"]] = b["id"]
        for f in Path(args.map).glob("cut-backup-*.json"):  # footage_qc repair 的备份：{"plan", "before"}
            b = json.loads(f.read_text())["before"]
            by_old[b["output_object_key"]] = b["id"]
    plan = []
    for s in g["shares"]:
        sh = api("GET", f"/video-shares/{s['id']}")
        vids = []
        for v in sh["videos"]:
            cid = by_old.get(key(v["video_url"])) or next((c["id"] for c in cuts.values() if c["output_object_key"] == key(v["video_url"])), None)
            c = cuts.get(cid) or (cid and api("GET", f"/video-cuts/{cid}"))  # 剪辑可能挂在别的产品下
            if not c or c["output_status"] != "success":
                raise SystemExit(f"{sh['title']}：有视频找不到对应的新成片")
            vids.append(dict(share_video({"cut_id": cid, "object_key": c["output_object_key"]}), video_object_key=c["output_object_key"]))
        plan.append((sh, vids, sum(1 for v, n in zip(sh["videos"], vids) if key(v["video_url"]) != n["video_object_key"])))
    for sh, vids, changed in plan:
        print(f"  {sh['title']}：{len(vids)} 条，换成新成片 {changed} 条")
    if args.dry_run:
        print("\n(dry run) 旧链接保留；确认后去掉 --dry-run")
        return
    rec = Path(run) / "clipo" / f"share-refresh-{gid}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    rec.write_text(json.dumps({"group": g, "old_shares": [sh for sh, _, _ in plan]}, ensure_ascii=False, indent=1))
    new_ids = []
    for sh, vids, _ in plan:
        new = api("POST", "/video-shares", {"script_id": sh["script_id"], "product_id": sh["product_id"], "title": sh["title"],
                                            "video_name_prefix": sh.get("video_name_prefix") or sh["title"] + "-", "videos": vids})
        back = api("GET", f"/video-shares/{new['id']}")
        if back["title"] != sh["title"] or [key(v["video_url"]) for v in back["videos"]] != [v["video_object_key"] for v in vids]:
            raise RuntimeError(f"{sh['title']} 新分享读回不一致")
        new_ids.append(new["id"])
        print(f"🔗 {sh['title']} → {share_url(new['id'])}", flush=True)
    api("PATCH", f"/video-share-groups/{gid}", {"video_share_ids": list(reversed(new_ids))})
    g2 = api("GET", f"/video-share-groups/{gid}")
    if sorted(x["id"] for x in g2["shares"]) != sorted(new_ids):
        raise RuntimeError("分组读回不一致")
    data = json.loads(rec.read_text()); data["new_share_ids"] = new_ids
    rec.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print(f"分组已换成新成片：{BASE.replace('/api/v1', '')}/share/group/{gid}\n记录：{rec}")


def cmd_rerender(cfg, run, args):
    """11-19: renders that failed on a transient load error (封面图 Error loading image, the file opens fine
    afterwards). Re-render the same draft (fresh signed urls from GET) for failed videos of --brief or all
    briefs; poll then reviews them as usual. Nothing in the draft changes; a new idempotency key per attempt."""
    st = State(run)
    todo = [(k, v) for k, v in sorted(st.videos.items()) if v.get("stage") == "failed" and "渲染失败" in (v.get("error") or "")
            and (not args.brief or v["brief_id"] == args.brief)]
    for k, v in todo:
        print(f"{'🧪' if args.dry_run else '🔁'} {k} {v['error'][:60]}")
        if args.dry_run:
            continue
        n = v.get("render_retries", 0) + 1
        draft = api("GET", f"/video-cuts/{v['cut_id']}")["draft"]
        job = api("POST", "/render-jobs", {"spec": draft, "options": {"codec": "h264", "quality": "high", "scale": 1},
                                           "cut_id": v["cut_id"], "idempotency_key": f"udb-{v['cut_id']}-r{n}"})
        v.update(render_job=job["job_id"], stage="rendering", render_retries=n, last_render_error=v.pop("error"))
        st.save()
    print(f"\n{'(dry run) ' if args.dry_run else ''}{len(todo)} 条渲染失败的重新渲染；接着跑 poll")


def hook_assignments(pool, videos):
    """保留已错开的合格钩子；只给重复或池外钩子分配空缺来源。"""
    assigned, used = {}, set()
    for key, video in videos:
        source = opener_clip(video)
        if source in pool and source not in used:
            assigned[key] = source
            used.add(source)
    remaining = [source for source in pool if source not in used]
    for index, (key, _) in enumerate(videos):
        if key not in assigned:
            assigned[key] = remaining.pop(0) if remaining else pool[index % len(pool)]
    return assigned


def cmd_spread_hooks(cfg, run, args):
    """仅在用户明确要求换开头时调用；默认生产不调用此命令。
    按首段素材池分配来源，只改首镜来源，备份后写入、回读、重渲染并重新送审。
    其他轨道、文案与声音保持原值。"""
    st = State(run)
    for b in load_briefs(run, args.brief):
        seg = (b.get("segments") or [{}])[0]
        pool = [c["segment_id"] for c in (seg.get("footage_pool") or {}).get("clips", [])]
        vids = [(k, v) for k, v in sorted(st.videos.items()) if v["brief_id"] == b["brief_id"] and v.get("stage") == "done"
                and (args.all or deliverable(v))]
        if not pool or not vids:
            continue
        if len(pool) < len(vids):
            print(f"⚠️  {b['brief_id']} 池子 {len(pool)} 条，视频 {len(vids)} 条：会有重复，先补钩子素材")
        r = api("POST", "/materials/segments/search", {"product_id": CFG["products"][b["product_key"]]["clipo_product_id"],
                                                      "tags": (seg["footage_pool"].get("tags") or [])[:10], "limit": 200})
        segs = {h["segment"]["id"]: h["segment"] for h in r.get("items", [])}
        assigned = hook_assignments(pool, vids)
        for i, (k, v) in enumerate(vids):
            sid = assigned[k]
            x = segs.get(sid)
            if not x:
                raise RuntimeError(f"{k}：钩子素材 {sid} 查不到")
            # 平台会另存派生切片；按已记录的素材 ID 判同源，不按文件名重渲。
            if opener_clip(v) == sid:
                print(f"✅ {k} 开头已是 {sid[-6:]}")
                continue
            cut = api("GET", f"/video-cuts/{v['cut_id']}")
            draft = copy.deepcopy(cut["draft"])
            first = min((c for c in draft["clips"] if c.get("trackId") == "main"), key=lambda c: c["from"])
            old = first["props"]["src"].split("?")[0].rsplit("/", 1)[-1]
            if old == x["object_url"].split("?")[0].rsplit("/", 1)[-1]:
                print(f"✅ {k} 开头已是 {sid[-6:]}")
                continue
            print(f"{'🧪' if args.dry_run else '🔁'} {k} 开头 {old[:10]}… → {sid[-6:]} {x.get('name', '')[:24]}")
            if args.dry_run:
                continue
            from footage_qc import backup
            backup(Path(run) / "clipo" / "hook-backups", "cut", cut)
            first["props"].update(src=x["object_url"], startFrom=0)
            api("PUT", f"/video-cuts/{v['cut_id']}", {"script_id": cut["script_id"], "draft": draft})
            got = api("GET", f"/video-cuts/{v['cut_id']}")["draft"]
            g1 = min((c for c in got["clips"] if c.get("trackId") == "main"), key=lambda c: c["from"])
            if g1["props"]["src"].split("?")[0] != x["object_url"].split("?")[0]:
                raise RuntimeError(f"{k} 读回开头不对，不渲染")
            n = v.get("render_retries", 0) + 1
            job = api("POST", "/render-jobs", {"spec": got, "options": {"codec": "h264", "quality": "high", "scale": 1},
                                               "cut_id": v["cut_id"], "idempotency_key": f"udb-{v['cut_id']}-hook{n}"})
            v.update(render_job=job["job_id"], stage="rendering", render_retries=n, hook_clip=sid, ai_review=None, ai_reason=None)
            st.save()


def intro_shots(brief, draft, cut_id):
    """User 11-19: a line that names the product AND an ingredient / tech (「清扬控油蓬松洗发水，有SCA控油蓬松技术」)
    shows the bottle while the name is read and the texture after it. The platform draws a segment's shots from its
    tags in no order, so this rewrites the draft: in such a segment, the shots that start before the subtitle ending
    the product name get product-appearance footage (config `intro_footage`, default 产品外观); at least one texture
    shot stays. Returns (new_draft, changed shot ids)."""
    prod = CFG["products"][brief["product_key"]]
    names = [prod["full_name"]] + [n for n in prod.get("name_aliases") or [] if n != prod["full_name"]]
    terms = prod.get("ingredient_terms") or []
    tags = prod.get("intro_footage") or ["产品外观"]
    idx = footage_index(brief["product_key"])
    banned = banned_words_for(brief["product_key"])
    pool = sorted((x for x in idx.values() if set(tags) & set(x.get("tags") or []) and x.get("review_result") != "reject"
                   and not any(w in (x.get("description") or "") for w in banned)), key=lambda x: x["id"])
    if not pool or not terms:
        return draft, []
    d = copy.deepcopy(draft)
    fps = d["composition"].get("fps", 30)
    main = sorted((c for c in d["clips"] if c.get("trackId") == "main"), key=lambda c: c["from"])
    used = {(c["props"].get("src") or "").split("?")[0].rsplit("/", 1)[-1] for c in main}
    seed = int(hashlib.sha1(cut_id.encode()).hexdigest(), 16)
    changed = []
    for seg in sorted({str(c["id"]).split("-")[0] for c in main}):
        subs = sorted((c for c in d["clips"] if c.get("trackId") == "overlay" and str(c["id"]).startswith(f"s{seg}-")), key=lambda c: c["from"])
        text = "".join(c["props"].get("text") or "" for c in subs)
        name = next((n for n in names if n in text), None)
        if not name or not any(t in text[text.index(name) + len(name):] for t in terms):
            continue
        end_char, pos, name_end = text.index(name) + len(name), 0, None
        for c in subs:
            pos += len(c["props"].get("text") or "")
            if pos >= end_char:
                name_end = c["from"] + c["durationInFrames"]
                break
        shots = [c for c in main if str(c["id"]).split("-")[0] == seg]
        swap = [c for c in shots if c["from"] < name_end][:max(1, len(shots) - 1)]
        for k, c in enumerate(swap):
            need = c["durationInFrames"] / fps
            cands = [x for x in pool if x["object_url"].split("?")[0].rsplit("/", 1)[-1] not in used
                     and (x["end_ms"] - x["begin_ms"]) / 1000 - 1 / fps >= need * 0.6]
            if not cands:
                break
            x = cands[(seed + k) % len(cands)]
            used.add(x["object_url"].split("?")[0].rsplit("/", 1)[-1])
            have = (x["end_ms"] - x["begin_ms"]) / 1000 - 1 / fps
            c["props"].update(src=x["object_url"], startFrom=0, playbackRate=1.0 if have >= need else round(have / need - 0.0005, 3),
                              alt=(x.get("description") or "")[:60])
            c["props"].pop("loop", None)
            changed.append(c["id"])
    return d, changed


def mix_volumes(product_key=None):
    """(bgm, voice) 音量：产品 config 覆盖品牌 config。用户 11-20 Cyber：BGM 偏大、口播偏小，
    实测 0.25 的 BGM 和口播同响（约 -21 LUFS）；平台音量上限 1，二硫化硒降低 BGM 保证口播突出。"""
    prod = (CFG.get("products") or {}).get(product_key) or {}
    return prod.get("bgm_volume", CFG.get("bgm_volume")), prod.get("voice_volume", CFG.get("voice_volume"))


def with_bgm_volume(draft, product_key=None):
    """config `bgm_volume` on the BGM track of an existing draft (user 11-19: 0.08 is inaudible under the voice).
    Also (user 11-19 客户反馈): the 戳直播间 arrow GIF lasts ~2 s and disappeared before the last line ended, so
    every sticker GIF loops to the end of its clip; sound effects play at config `sfx_volume` (礼花声太大).
    产品可单独给 bgm_volume / voice_volume（mix_volumes）。"""
    vol, voice = mix_volumes(product_key)
    sfx = CFG.get("sfx_volume")
    d = copy.deepcopy(draft)
    last = max((c["id"].split("-")[0] for c in d["clips"] if c.get("trackId") == "voiceover"
                and str(c.get("id", "")).split("-")[0][1:].isdigit() and str(c.get("id", "")).startswith("v")), key=lambda x: int(x[1:] or 0), default=None)
    last = last[1:] if last else None
    if CFG.get("no_confetti"):  # 用户 11-19：礼花不要每条都加，太吵；只留最后一句的戳直播间箭头
        d["clips"] = [c for c in d["clips"] if not (c.get("trackId") in ("sticker", "sound_effects")
                      and c.get("component") != "ImageClip" and str(c.get("id", "")).split("-")[0] != last)]
    for c in d["clips"]:
        if c.get("trackId") == "audio" and vol is not None:
            c["props"]["volume"] = vol
        elif c.get("trackId") == "sticker" and c.get("component") == "StickerClip":
            c["props"]["loop"] = True
        elif c.get("trackId") == "sound_effects" and sfx is not None:
            c["props"]["volume"] = sfx
        elif c.get("trackId") == "voiceover" and voice is not None:
            c["props"]["volume"] = voice
    return d


def without_effects(draft):
    """仅移除花字轨道，保留主画面、转场、声音与口播字幕。"""
    d = copy.deepcopy(draft)
    d["clips"] = [c for c in d["clips"] if c.get("trackId") != "effect"]
    return d


def replace_banned_shots(brief, draft, cut_id, cache):
    """按原段画面标签替换禁用源片。源片先下载探测；保留镜头时段，不循环、不慢放。"""
    from footage_qc import probe
    pk = brief["product_key"]
    idx, words = footage_index(pk), banned_words_for(pk)
    banned_files = CFG.get("banned_footage_files") or {}
    name = lambda src: src.split("?")[0].rsplit("/", 1)[-1]
    plan = {str(i + 1): set(sg.get("visual_tags") or (sg.get("footage_pool") or {}).get("tags") or [])
            for i, sg in enumerate(brief["segments"])}
    d, changed = copy.deepcopy(draft), []
    fps = d["composition"].get("fps", 30)
    used = {name(c["props"]["src"]) for c in d["clips"] if c.get("trackId") == "main"}
    seed = int(hashlib.sha1(cut_id.encode()).hexdigest(), 16)
    cache = Path(cache)
    for c in d["clips"]:
        if c.get("trackId") != "main":
            continue
        old = name(c["props"]["src"])
        if old not in banned_files and not any(w in (idx.get(old, {}).get("description") or "") for w in words):
            continue
        tags = plan.get(str(c["id"]).split("-")[0], set())
        need = c["durationInFrames"] / fps
        candidates = sorted((x for key, x in idx.items() if key not in used and key not in banned_files
                     and tags & set(x.get("tags") or []) and x.get("review_result") == "pass"
                     and not any(w in (x.get("description") or "") for w in words)
                     and (x.get("end_ms", 0) - x.get("begin_ms", 0)) / 1000 >= need), key=lambda x: x["id"])
        if candidates:
            shift = seed % len(candidates)
            candidates = candidates[shift:] + candidates[:shift]
        for x in candidates:
            cache.mkdir(parents=True, exist_ok=True)
            local = cache / name(x["object_url"])
            with _FETCH_LOCK:  # poll 并发时两个 brief 可能换同一条源片：同一文件只下载一次
                if not local.exists():
                    fetch_file(x["object_url"], local)
            duration = float(probe(local)["format"]["duration"])
            if duration < need + 1 / fps:
                continue
            c["props"].update(src=x["object_url"], startFrom=0, playbackRate=1.0,
                              alt=(x.get("description") or "")[:60])
            c["props"].pop("loop", None)
            used.add(name(x["object_url"]))
            changed.append({"clip": c["id"], "before": old, "after": name(x["object_url"]),
                            "source_seconds": duration, "required_seconds": need})
            break
        else:
            raise RuntimeError(f'{cut_id} {c["id"]}：没有符合标签 {sorted(tags)} 且足够长的干净源片')
    return d, changed


def cmd_restyle(cfg, run, args):
    """更新包装、可选去花字与禁用画面返工；备份后写入，完整回读后重渲送审。文案与配音保持原字。"""
    from footage_qc import with_watermark, WATERMARK_PROPS
    st = State(run)
    briefs = {b["brief_id"]: b for b in load_briefs(run, args.brief)}
    todo = [(k, v) for k, v in sorted(st.videos.items()) if v["brief_id"] in briefs and v.get("stage") == "done"]
    backup = Path(run) / "clipo" / f"restyle-backup-{time.strftime('%Y%m%d-%H%M%S')}.json"
    saved, jobs, planned = {}, {}, 0
    if (getattr(args, "remove_effects", False) or getattr(args, "replace_banned", False)) and not args.dry_run:
        before = {}
        for bid, b in briefs.items():
            path = Path(run) / "briefs" / f"{bid}.json"
            before[bid] = json.loads(path.read_text())
        backup.with_name(backup.stem + "-briefs.json").write_text(json.dumps(before, ensure_ascii=False))
        for bid, b in briefs.items():
            path = Path(run) / "briefs" / f"{bid}.json"
            current = json.loads(path.read_text())
            if getattr(args, "remove_effects", False):
                current["disable_effects"] = True
            if getattr(args, "replace_banned", False):
                current["replace_banned_footage"] = True
            path.write_text(json.dumps(current, ensure_ascii=False, indent=2))
            b.update(current)
    for k, v in todo:
        cut = api("GET", f"/video-cuts/{v['cut_id']}")
        d, changed = intro_shots(briefs[v["brief_id"]], cut["draft"], v["cut_id"])
        d = with_bgm_volume(d, briefs[v["brief_id"]]["product_key"])
        removed = 0
        if getattr(args, "remove_effects", False) or briefs[v["brief_id"]].get("disable_effects"):
            removed = sum(c.get("trackId") == "effect" for c in d["clips"])
            d = without_effects(d)
        replacements = []
        if getattr(args, "replace_banned", False) or briefs[v["brief_id"]].get("replace_banned_footage"):
            d, replacements = replace_banned_shots(briefs[v["brief_id"]], d, v["cut_id"], Path(run) / "clipo" / "restyle-media")
        if (CFG.get("watermark") or {}).get("add"):
            d = with_watermark(d, ai_spans(briefs[v["brief_id"]]["product_key"], d) if (CFG.get("watermark") or {}).get("ai_only") else None)
        if d == cut["draft"] and not v.get("restyle_pending"):
            continue
        planned += 1
        print(f"{k}：去花字 {removed} 个，换禁用画面 {len(replacements)} 个，产品亮相换外观 {len(changed)} 个，BGM {CFG.get('bgm_volume')}", flush=True)
        if args.dry_run:
            continue
        saved[v["cut_id"]] = cut
        backup.write_text(json.dumps(saved, ensure_ascii=False))
        v["restyle_pending"] = True  # written but not yet rendered: a rerun renders it even though the draft is unchanged
        st.save()
        api("PUT", f"/video-cuts/{v['cut_id']}", {"script_id": cut["script_id"], "draft": d})
        got = api("GET", f"/video-cuts/{v['cut_id']}")["draft"]
        unsign = lambda x: re.sub(r"\?[^\"]*\"", '"', json.dumps(x, sort_keys=True, ensure_ascii=False))  # 现签地址每次 GET 都不同
        if unsign(got) != unsign(d):
            raise RuntimeError(f"{k} 回读草稿不一致，不渲染")
        if (getattr(args, "remove_effects", False) or briefs[v["brief_id"]].get("disable_effects")) and any(c.get("trackId") == "effect" for c in got["clips"]):
            raise RuntimeError(f"{k} 回读仍有花字，不渲染")
        from qc import cut_checks
        v["qc_issues"] = cut_checks(briefs[v["brief_id"]], got, (cut.get("random_result") or {}).get("tts_speaker_id"))
        v["pinned_clips"] = pinned_clips(briefs[v["brief_id"]], got)
        v["restyle_replacements"] = replacements
        r = api("POST", "/render-jobs", {"spec": got, "options": {"codec": "h264", "quality": "high", "scale": 1},
                                        "cut_id": v["cut_id"], "idempotency_key": f"udb-rs-{v['cut_id']}-{backup.stem[-15:]}"})
        jobs[k] = r["job_id"]
        v.update(render_job=r["job_id"], stage="rendering", ai_review=None, ai_reason=None, bad_footage=bad_footage(briefs[v["brief_id"]]["product_key"], got))
        v.pop("restyle_pending", None)
        st.save()
    print(f"\n{'(dry run) ' if args.dry_run else ''}{len(jobs) if not args.dry_run else planned} 条重渲；poll 会读回、送审")


def cmd_add_footage(cfg, run, args):
    """User 11-20: keep the first seconds of a 爆款 reference as the opener of new videos. Upload local clips
    (--files a.mp4,b.mp4) to --product, tag them --tags (added to the product's tag list first), mark them not
    AI-generated unless --ai. Prints each new segment id; use `pool --tags <tag>` or a silent footage segment."""
    pk = args.product or next(iter(cfg["products"]))
    pid = cfg["products"][pk]["clipo_product_id"]
    tags = [t for t in (args.tags or "").split(",") if t]
    files = [Path(f) for f in (args.files or "").split(",") if f]
    if not tags or not files:
        raise SystemExit("要给 --files 和 --tags")
    if args.dry_run:
        print(f"(dry run) 将上传 {len(files)} 条到 {pk}，标签 {tags}")
        return
    have = api("GET", f"/products/{pid}")["tags"]
    new = [t for t in tags if t not in have]
    if new:
        api("PATCH", f"/products/{pid}", {"tags": have + new})
    for f in files:
        tok = api("POST", "/storage/upload-tokens", {"filename": f.name, "content_type": "video/mp4",
                                                     "content_length": f.stat().st_size, "purpose": "material"})
        req = urllib.request.Request(tok["upload_url"], data=f.read_bytes(), method="PUT",
                                     headers=dict(tok.get("headers") or {}, **{"Content-Type": "video/mp4"}))
        urllib.request.urlopen(req, timeout=300).read()
        seg = api("POST", f"/products/{pid}/segments", {"object_key": tok["object_key"], "ai_generated": bool(args.ai)})
        sid = seg.get("id")
        # 11-20：平台理解跑完会用自动标签（CTA、挤洗发水）覆盖掉我们的标签，生成时就找不到这条开头。
        # 等理解完成再打标签，读回确认
        for _ in range(40):
            x = next((y for y in list_segments(pid) if y["id"] == sid), {})
            if x.get("describe_status") in ("success", "failed") and x.get("review_result"):
                break
            time.sleep(10)
        for _ in range(5):
            api("PATCH", f"/materials/segments/{sid}", {"tags": tags})
            time.sleep(5)
            if next((y for y in list_segments(pid) if y["id"] == sid), {}).get("tags") == tags:
                break
        print(f"{f.name} → {sid} {tags}")


def remote_size(url):
    """Size of a signed OSS object. The share URLs are signed for GET only (HEAD gets 403), so ask for one byte."""
    req = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        cr = r.headers.get("Content-Range") or ""
        return int(cr.rsplit("/", 1)[1]) if "/" in cr else int(r.headers.get("Content-Length") or 0)


def cmd_sync_visuals(cfg, run, args):
    """User 11-19: L02's opening drew 爆款 clips with burned-in captions (two-line subtitle rejects). Re-pin a built
    script's footage tags from the brief's `visual_tags`; copy, voice and approval stay as they are."""
    st = State(run)
    for b in load_briefs(run, args.brief):
        job = st.data.get("briefs", {}).get(b["brief_id"]) or {}
        if not job.get("script_id") or not any(sg.get("visual_tags") or sg.get("footage_pool") for sg in b["segments"]):
            continue
        if args.dry_run:
            print(f"🧪 {b['brief_id']} 将按 brief 改画面标签：{[sg.get('visual_tags') for sg in b['segments']]}")
            continue
        from footage_qc import backup
        backup(Path(run) / "clipo" / "visual-sync-backups", "script", api("GET", f"/video-scripts/{job['script_id']}"))
        print(f"🎯 {b['brief_id']} 画面标签已写入并读回：{sync_visuals(job['script_id'], b['segments'])}")


def cmd_pool(cfg, run, args):
    """用户指定素材或限定候选时使用；默认标签随机不调用此命令。
    在 tags 内按 query 检索前 size 条合格切片；风险与禁用素材阻止入池。
    score 区分池内外；--size 1 可限定单条，--brief/--seg 写入分段的 footage_pool。"""
    pk = args.product or next(iter(cfg["products"]))
    pid = cfg["products"][pk]["clipo_product_id"]
    tags = [t for t in (args.tags or "").split(",") if t]
    body = {"query": args.query, "product_id": pid, "tags": tags, "limit": 40}
    r = api("POST", "/materials/segments/search", body)
    hits = r["items"] if isinstance(r, dict) and "items" in r else r.get("hits", r)
    words = banned_words_for(pk)
    # AI scene clips are all risk-flagged by the platform's footage review ("纯讲话，无产品介入") by design;
    # --allow-risk takes them. Banned-footage words (头屑、秃顶…) always stop the pool.
    # 11-19：同一平台产品下混了别的 SKU（二硫化硒库里有绿瓶油脂调控），--exclude 写正则，描述命中的当作不能用，池子在它前面截断
    excl = re.compile(args.exclude) if getattr(args, "exclude", None) else None
    ok_ids = CFG["products"].get(pk, {}).get("footage_reviewed_ok") or {}
    usable = lambda x: (args.allow_risk or x["review_result"] != "reject") and (x["id"] in ok_ids or not any(w in (x.get("description") or "") for w in words)) \
        and not (excl and excl.search((x.get("description") or "") + (x.get("name") or "")))
    pool = []
    for i, h in enumerate(hits):
        if not usable(h["segment"]):
            break  # anything ranked below a bad clip would let the bad clip in too
        pool.append(h)
        if len(pool) == args.size:
            break
    if len(pool) < min(2, args.size):
        bad = hits[len(pool)]["segment"] if len(hits) > len(pool) else None
        why = f"排在第 {len(pool) + 1} 的「{bad['name'][:20]}」是{'风险素材（' + (bad.get('review_reason') or '')[:30] + '）' if bad['review_result'] == 'reject' else '禁用画面'}" if bad else "检索不到更多"
        raise SystemExit(f"只凑到 {len(pool)} 条能用的：{why}。换个 query（写这类素材的共同点）；"
                         "AI 真人场景素材平台都标了风险（无产品介入），确认要用就加 --allow-risk")
    nxt = hits[len(pool)] if len(hits) > len(pool) else None
    score = round((pool[-1]["score"] + nxt["score"]) / 2, 3) if nxt else round(pool[-1]["score"] - 0.01, 3)
    gap = (pool[-1]["score"] - nxt["score"]) if nxt else 1
    print(f"素材池（{len(pool)} 条，score {score}，和池外第一条差 {gap:.3f}）：")
    for h in pool:
        x = h["segment"]
        print(f"  {h['score']:.3f} {(x['end_ms'] - x['begin_ms']) / 1000:4.1f}s {x['id']} {x['name'][:30]}")
    if nxt:
        print(f"  ——池外第一条 {nxt['score']:.3f} {nxt['segment']['review_result']} {nxt['segment']['name'][:30]}")
    if gap < 0.005:
        print("⚠️ 池内外分数太接近，平台可能挑到池外的：换个 query 再试")
    fp = {"query": args.query, "tags": tags, "score": score, "clips": [{"segment_id": h["segment"]["id"], "name": h["segment"]["name"]} for h in pool],
          "_check": f"{time.strftime('%Y-%m-%d')} 检索：池内最低 {pool[-1]['score']:.3f}，池外最高 {nxt['score'] if nxt else 0:.3f}"}
    if args.brief and args.seg:
        p = Path(run) / "briefs" / f"{args.brief}.json"
        b = json.loads(p.read_text())
        sg = next(x for x in b["segments"] if x["id"] == args.seg)
        sg["footage_pool"] = fp
        sg.pop("visual_query", None)
        if sg.get("silent"):
            sg.pop("footage", None)
        p.write_text(json.dumps(b, ensure_ascii=False, indent=2))
        print(f"→ 写进 {args.brief} S{args.seg}（改了画面要重新给人看、重新审）")


def fetch_file(url, dest, tries=3):
    """Download one MP4 to dest via a temp file; returns its size. Retries; never leaves a partial file."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=300) as r, open(tmp, "wb") as f:
                want = int(r.headers.get("Content-Length") or 0)
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            got = tmp.stat().st_size
            if got == 0 or (want and got != want):
                raise RuntimeError(f"文件不完整 {got}/{want} 字节")
            tmp.replace(dest)
            return got
        except Exception as e:  # network hiccup or truncated body: retry, then give up on this file only
            if tmp.exists():
                tmp.unlink()
            if attempt == tries - 1:
                raise RuntimeError(str(e)[:200])
            time.sleep(3 * (attempt + 1))


def cmd_download(cfg, run, args):
    """User 11-12 (PR #2): deliver the final share links as local folders too. One folder per share link, named
    after it, with its videos as <share title>-<n>.mp4 in share order. Default place: <project>/本地交付/YYYYMMDD-品牌/
    (the project holding runs/; --out overrides). Files already there with the right size are kept, so re-running
    only fetches what is missing. Writes 下载清单.json next to the folders. Never regenerates or re-renders."""
    st = State(run)
    shares = [(k, v) for k, v in st.data.get("shares", {}).items()]
    if not shares:
        print("还没有分享链接，先跑 share")
        return
    day = Path(run).name
    # default: the project that holds runs/ (scripts run from the skill dir; MP4s must not land in the skill repo)
    root = Path(args.out) if args.out else Path(run).resolve().parent.parent / "本地交付" / f"{day}-{cfg['brand']}"
    if args.dry_run:
        for _, sh in shares:
            print(f"🧪 {sh['title']}：{len(sh['videos'])} 条 → {root / sh['title']}")
        print(f"\n(dry run) 将下载到 {root.resolve()}")
        return
    root.mkdir(parents=True, exist_ok=True)
    manifest, failed, total = [], [], 0
    for key, sh in sorted(shares, key=lambda x: x[1]["title"]):
        page = api("GET", f"/video-shares/{sh['id']}")  # fresh signed urls, in the order the client sees
        folder = root / page["title"]
        folder.mkdir(exist_ok=True)
        keep = {f"{v['name']}.mp4" for v in page["videos"]}
        for old in folder.glob("*.mp4"):  # the link was rebuilt with other videos: drop files no longer in it
            if old.name not in keep:
                old.unlink()
        rows = []
        for v in page["videos"]:
            dest = folder / f"{v['name']}.mp4"
            try:
                size = remote_size(v["video_url"])
                if dest.exists() and size and dest.stat().st_size == size:
                    got = size
                else:
                    got = fetch_file(v["video_url"], dest)
                rows.append({"file": dest.name, "bytes": got})
            except Exception as e:
                failed.append(f"{page['title']}/{dest.name}：{str(e)[:120]}")
        n = len([r for r in rows])
        total += n
        mark = "✅" if n == len(page["videos"]) else "⚠️"
        print(f"{mark} {page['title']}：{n}/{len(page['videos'])} 条", flush=True)
        manifest.append({"share": page["title"], "url": share_url(sh["id"]), "folder": str(folder.resolve()),
                         "expected": len(page["videos"]), "saved": n, "files": rows})
    (root / "下载清单.json").write_text(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M"), "folders": manifest,
                                                  "failed": failed}, ensure_ascii=False, indent=1))
    want = sum(m["expected"] for m in manifest)
    print(f"\n本地交付：{root.resolve()}（{total}/{want} 条）")
    if failed:
        print("下载失败（再跑一次 download 只补这些）：")
        print("\n".join(f"  - {f}" for f in failed))


def cmd_inspect_scripts(cfg, run, args):
    """只读回读已建平台脚本与包装配置；保存快照，不提交生成或更改共享提示词。"""
    st = State(run)
    out = Path(run) / "clipo" / "script-snapshots"
    out.mkdir(parents=True, exist_ok=True)
    for b in load_briefs(run, args.brief):
        sid = (st.data.get("briefs", {}).get(b["brief_id"]) or {}).get("script_id")
        if not sid:
            print(f"{b['brief_id']} 未建平台脚本")
            continue
        script = api("GET", f"/video-scripts/{sid}")
        (out / f"{b['brief_id']}.json").write_text(json.dumps(script, ensure_ascii=False, indent=2))
        actual = [sg.get("content") or "" for sg in script.get("segments", [])]
        expected = [seg_text(sg) for sg in b.get("segments", [])]
        summary = {"brief": b["brief_id"], "script_id": sid, "copy_exact": actual == expected,
                   "config": script.get("config"),
                   "segments": [{k: sg.get(k) for k in ("id", "title", "visual", "subtitle_style", "transition", "word_art", "sticker", "sound_effects", "effect")} for sg in script.get("segments", [])]}
        (out / f"{b['brief_id']}-inspection.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(json.dumps({"brief": b["brief_id"], "script_id": sid, "copy_exact": summary["copy_exact"],
              "segments": len(actual), "transitions": sum(bool(x.get("transition")) for x in script.get("segments", [])),
              "effects": sum(bool(x.get("effect") or x.get("word_art")) for x in script.get("segments", [])),
              "sfx_segments": sum(bool(x.get("sound_effects")) for x in script.get("segments", [])),
              "snapshot": str(out / f"{b['brief_id']}.json")}, ensure_ascii=False))
        if not summary["copy_exact"]:
            raise SystemExit("平台脚本文案与当前已审主版不符，停止后续生产")


def main():
    global BASE, CFG
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["preflight", "submit", "poll", "topup", "rereview", "report", "share", "download", "pool", "bind-aigc", "status", "rename-shares", "split-group", "refresh-group", "rerender", "sync-visuals", "spread-hooks", "restyle", "add-footage", "inspect-scripts"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--brief")
    ap.add_argument("--video-key",action='append',help='rereview: only these existing video keys; never include customer-rejected versions')
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remove-effects", action="store_true", help="restyle: 去花字但保留转场；本批后续补片也去花字")
    ap.add_argument("--replace-banned", action="store_true", help="restyle: 按原段标签替换禁用画面，下载探测后使用原速足长源片")
    ap.add_argument("--partial", action="store_true", help="share: 不够数的 brief 也先发")
    ap.add_argument("--refresh", action="store_true", help="share: 已有分享也重建（换掉旧链接）")
    ap.add_argument("--group", help="rename-shares / split-group / refresh-group: 分享分组链接或 id")
    ap.add_argument("--store", help="split-group: 只建这个店铺的分组（如 Cyber）")
    ap.add_argument("--map", help="refresh-group: footage_qc 返工的备份目录，按旧成片找到剪辑")
    ap.add_argument("--out", help="download / share --download: 本地交付目录，默认 <项目>/本地交付/YYYYMMDD-品牌/（项目 = runs 所在目录）")
    ap.add_argument("--download", action="store_true", help="share: 建完链接后把成片下载到本地（同 download）")
    ap.add_argument("--query", help="pool: 这类素材的共同点，例如「男士摸发根 头发油」")
    ap.add_argument("--tags", help="pool: 检索范围，逗号隔开的产品素材标签；add-footage: 新素材的标签")
    ap.add_argument("--files", help="add-footage: 逗号隔开的本地 mp4")
    ap.add_argument("--ai", action="store_true", help="add-footage: 标为 AI 生成")
    ap.add_argument("--product", help="pool: 产品 key，默认第一个")
    ap.add_argument("--size", type=int, default=5, help="pool: 池子要几条，默认 5")
    ap.add_argument("--seg", help="pool: 写进 brief 的哪一段（段 id），配 --brief")
    ap.add_argument("--exclude", help="pool: 描述命中这个正则的素材不进池（例：绿色标|油脂调控|绿蓬蓬）")
    ap.add_argument("--allow-risk", action="store_true", help="pool: 平台标了风险的素材也进池（AI 真人场景素材都被标「无产品介入」）")
    ap.add_argument("--all", action="store_true", help="rereview: 通过的也重新审；spread-hooks: 没通过的也换开头")
    ap.add_argument("--scripts-only", action="store_true", help="submit: 只在平台建脚本并核对，不生成视频（不扣额度）")
    ap.add_argument("--retry-failed", action="store_true", help="submit: regenerate briefs whose generation failed")
    ap.add_argument("--factor", type=float, help="submit: 一次生成排期条数的几倍，默认 1（用户 11-19 不双倍产）；大于 1 只在用户这次明确要求时用")
    ap.add_argument("--margin", type=float, default=None, help="topup: 默认缺几条补几条；给了才按通过率 × 这个倍数多补")
    ap.add_argument("--max-per-brief", type=int, default=30, help="topup: 每个 brief 单次最多补几条")
    ap.add_argument("--wait", type=int, default=0, help="poll: keep polling up to N seconds")
    ap.add_argument("--interval", type=int, default=30)
    args = ap.parse_args()
    cfg = load_config(args.config)
    BASE = cfg["clipo"]["base_url"].rstrip("/") + "/api/v1"
    CFG = cfg
    if args.cmd in ('submit','topup') and not args.dry_run:
        from sync_skill import assert_current
        assert_current(Path(__file__).resolve().parent.parent)
    {"preflight": cmd_preflight, "submit": cmd_submit, "poll": cmd_poll, "topup": cmd_topup, "rereview": cmd_rereview, "report": cmd_report, "share": cmd_share,
     "download": cmd_download, "pool": cmd_pool, "bind-aigc": cmd_bind_aigc, "status": cmd_status, "rename-shares": cmd_rename_shares, "split-group": cmd_split_group, "refresh-group": cmd_refresh_group, "rerender": cmd_rerender, "sync-visuals": cmd_sync_visuals, "spread-hooks": cmd_spread_hooks, "restyle": cmd_restyle, "add-footage": cmd_add_footage, "inspect-scripts": cmd_inspect_scripts}[args.cmd](cfg, args.run, args)


if __name__ == "__main__":
    main()
