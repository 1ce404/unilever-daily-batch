#!/usr/bin/env python3
"""AI footage generation for a scene library (user 11-16: before this, every agent wrote its own throwaway code
against POST /material-generations, kept waiting in the chat, and patched blocked scenes by reusing other briefs'
clips).

    python3 -I scripts/aigc_gen.py submit --config qingyang --run $R --plan $R/aigc/golf.json --dry-run   # count + cost
    python3 -I scripts/aigc_gen.py submit --config qingyang --run $R --plan $R/aigc/golf.json             # after the user says yes
    nohup python3 -I scripts/aigc_gen.py poll --config qingyang --run $R --plan $R/aigc/golf.json --wait 5400 > $R/aigc/golf.log 2>&1 &
    python3 -I scripts/aigc_gen.py report --config qingyang --run $R --plan $R/aigc/golf.json             # any time, offline
    python3 -I scripts/aigc_gen.py sets   --config qingyang --run $R --plan $R/aigc/golf.json --brief 1116-B01 [--dry-run]

Plan file (written by the agent, reviewed by the user like a script):
    {"product": "2x", "tag": "AI双人演绎", "duration": 6,
     "scenes": [{"id": "golf", "versions": 4, "prompt": "…", "fallback_prompts": ["…"]},
                {"id": "cat", "tag": "AI小猫", "prompts": ["版本1 …", "版本2 …", "版本3 …"]}]}
Tags follow scripts/ai_tags.py (user 11-18): one AI tag per clip, named after the scene, starting with「AI」, no
dates or brief ids. A scene's `tag` overrides the plan's.
Before submit, check_plan stops prompts that break the brand's config `aigc` look rule (清扬 user 11-18: every
character good-looking, never a stock / influencer face): people prompts must name a look word and a natural word,
and must not ask for mask words.

`prompts` gives one slot per prompt (different person / action each); `prompt` + `versions` submits the same prompt
N times. Optional plan keys: model, resolution, ratio, generate_audio (default false), max_inflight.

submit adds the tags to the product's tag list, fills free slots up to max_inflight and exits; poll keeps going in
the background: it reads each task, REPLACES the tags of each successful clip with its scene tag (the platform's
auto-understanding tags AI clips 使用后 / 头皮出油 too, which put AI people into real-footage segments), checks
them again on later rounds (so `clipo_batch.py pool --tags <tag>` finds them), tops up the queue, and
retries a failed slot at most twice. A slot blocked by the model's copyright / sensitive check is retried only with
the scene's next `fallback_prompts` (a redesigned original character / wording the agent wrote, keeping the
approved look, e.g. 年轻帅气 for 清扬 2X: references/aigc-production.md), never the same prompt again and never by
silently deleting the user's requirements; with no fallback left the slot is reported as blocked. A timeout or
platform error retries the same prompt. When nothing is left it writes <plan>-report.md: per scene how many clips are ready and how many are
missing. It does not look at frames; it does not reuse other scenes' clips to fill a gap.

Two ways to use the clips:
  - footage pool (scene clips, any shot): `clipo_batch.py pool --tags <tag> --allow-risk` picks among them.
  - per-video character sets (PR #3, references/aigc-production.md: shots 1/2/4 = same person, three actions,
    one independent source per delivered video). Write each prompt as three timed actions (e.g. 0-2s / 2-4s /
    4-6s, plan key `shots`); `sets` cuts every ready clip of the brief's scene into those three spans with
    ffmpeg, uploads and registers them (ai_generated), and writes `visual_sets` into the brief, one source per
    video. Run it after `clipo_batch.py submit` made the cuts (they wait as aigc_pending); then
    `clipo_batch.py bind-aigc` swaps the shots in and renders.
    Fewer ready clips than videos: it binds what there is and says how many are missing, never reusing one.
"""
import subprocess
import tempfile
import urllib.request
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clipo_batch as cb
import ai_tags
from common import load_config

MODEL = "doubao-seedance-2-0-260128"
MAX_RETRIES = 2
BLOCKED = ("SensitiveContent", "PolicyViolation", "copyright")
# words that drew copyright / sensitive blocks on 11-12~13; only warned about, never removed (the user may want them)
DEFAULT_RISKY = ["明星", "网红", "演员", "某某同款", "工厂展廊", "广告片"]


def vendor_error(g):
    e = ((g.get("result") or {}).get("error") or {})
    return e.get("code") or g.get("error_code") or "", e.get("message") or g.get("error_message") or ""


def is_blocked(code, msg):
    return any(w in code or w in msg for w in BLOCKED)


class Run:
    def __init__(self, cfg, plan_path):
        self.cfg, self.plan_path = cfg, Path(plan_path)
        self.plan = json.loads(self.plan_path.read_text())
        self.product = cfg["products"][self.plan["product"]]
        self.state_path = self.plan_path.with_name(self.plan_path.stem + "-state.json")
        self.st = json.loads(self.state_path.read_text()) if self.state_path.exists() else {"slots": {}}
        for sc in self.plan["scenes"]:
            prompts = sc.get("prompts") or [sc["prompt"]] * int(sc.get("versions", 1))
            for i, p in enumerate(prompts, 1):
                key = f"{sc['id']}-{i:02d}"
                slot = self.st["slots"].setdefault(key, {"scene": sc["id"], "prompt": p, "attempts": []})
                if not slot["attempts"]:
                    slot["prompt"] = p  # plan edited before anything was sent: take the new prompt

    def save(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.st, ensure_ascii=False, indent=1))
        tmp.replace(self.state_path)

    def scene(self, sid):
        return next(s for s in self.plan["scenes"] if s["id"] == sid)

    def tag(self, sid):
        """The one AI tag a scene's clips carry (scene tag, else the plan tag)."""
        return self.scene(sid).get("tag") or self.plan.get("tag")

    def tags(self):
        return sorted({self.tag(sc["id"]) for sc in self.plan["scenes"]} - {None})

    @staticmethod
    def status(slot):
        if slot.get("segment_id"):
            return "ready"
        if slot.get("gave_up"):
            return "gave_up"
        last = slot["attempts"][-1] if slot["attempts"] else None
        if not last:
            return "todo"
        if last["status"] in ("pending", "running"):
            return "inflight"
        if last["status"] == "success":
            return "tagging"
        return "retry"

    def body(self, prompt):
        p = self.plan
        return {"product_id": self.product["clipo_product_id"], "model": p.get("model", MODEL), "prompt": prompt,
                "resolution": p.get("resolution", "720p"), "ratio": p.get("ratio", "9:16"),
                "duration": int(p.get("duration", 6)), "generate_audio": bool(p.get("generate_audio", False)), "quantity": 1}

    def next_prompt(self, slot):
        """Prompt for a retry. Blocked by the copyright / sensitive check: the scene's next unused fallback prompt
        (None when they are used up). Timeout or platform error: the same prompt again."""
        last = slot["attempts"][-1]
        if not last.get("blocked"):
            return last["prompt"]
        tried = [a["prompt"] for a in slot["attempts"]]
        return next((fp for fp in self.scene(slot["scene"]).get("fallback_prompts", []) if fp not in tried), None)

    def submit_free(self, dry=False):
        cap = int(self.plan.get("max_inflight", 20))
        inflight = sum(self.status(s) == "inflight" for s in self.st["slots"].values())
        sent = []
        for key, slot in sorted(self.st["slots"].items()):
            if inflight >= cap:
                break
            state = self.status(slot)
            if state == "todo":
                prompt = slot["prompt"]
            elif state == "retry":
                if len(slot["attempts"]) > MAX_RETRIES:
                    slot["gave_up"] = "重试 2 次仍失败"
                    continue
                prompt = self.next_prompt(slot)
                if not prompt:
                    slot["gave_up"] = "被版权检查拦，没有可换的提示词：重新设计原创人物写进这个场景的 fallback_prompts 再跑"
                    continue
            else:
                continue
            if dry:
                sent.append(key)
                inflight += 1
                continue
            rows = cb.api("POST", "/material-generations", self.body(prompt))
            g = rows[0] if isinstance(rows, list) else rows
            slot["attempts"].append({"generation_id": g["id"], "prompt": prompt, "status": g["status"],
                                     "at": time.strftime("%Y-%m-%d %H:%M")})
            self.save()
            sent.append(key)
            inflight += 1
        return sent

    def refresh(self):
        """Read every in-flight task; give successful clips exactly their scene tag. Returns how many changed."""
        changed, segs = 0, None
        for key, slot in sorted(self.st["slots"].items()):
            tag = self.tag(slot["scene"])
            state = self.status(slot)
            if state not in ("inflight", "tagging"):
                continue
            a = slot["attempts"][-1]
            g = cb.api("GET", f"/material-generations/{a['generation_id']}")
            if g["status"] != a["status"]:
                a["status"], changed = g["status"], changed + 1
            if g["status"] == "failed":
                a["error"], a["error_message"] = vendor_error(g)
                a["blocked"] = is_blocked(a["error"], a["error_message"])
            if g["status"] != "success":
                continue
            object_key = (g.get("object_url") or "").split("?")[0].split(".com/", 1)[-1]
            if segs is None:
                segs = product_segments(self.product["clipo_product_id"])
            seg = next((x for x in segs if object_key and object_key in (x.get("object_url") or "")), None)
            if not seg:
                continue  # the platform lays the segment row a moment after success; next round
            if tag and (seg.get("tags") or []) != [tag]:
                ai_tags.set_tags(seg["id"], [tag])
            slot.update(segment_id=seg["id"], object_key=object_key, duration_ms=seg["end_ms"] - seg["begin_ms"],
                        description=(seg.get("description") or "")[:200], review_result=seg.get("review_result"))
            changed += 1
        changed += self.retag(segs)
        if changed:
            self.save()
        return changed

    def retag(self, segs=None):
        """The platform may still be writing its own tags after we set ours: check every clip this plan made (whole
        clips and the cut shots) and set the scene tag again where it drifted. A clip counts as settled once its
        understanding is done and its tags are right. Returns how many it fixed."""
        mine = {}
        for slot in self.st["slots"].values():
            tag = self.tag(slot["scene"])
            for sid in [slot.get("segment_id")] + [x.get("segment_id") for x in slot.get("shots") or []]:
                if tag and sid and sid not in self.st.setdefault("tags_settled", []):
                    mine[sid] = tag
        if not mine:
            return 0
        segs = segs or product_segments(self.product["clipo_product_id"])
        fixed = 0
        for seg in segs:
            tag = mine.get(seg["id"])
            if not tag:
                continue
            if (seg.get("tags") or []) != [tag]:
                ai_tags.set_tags(seg["id"], [tag])
                fixed += 1
            elif seg.get("describe_status") in ("success", "failed"):
                self.st["tags_settled"].append(seg["id"])
        return fixed

    def ready(self, scene):
        return [(k, s) for k, s in sorted(self.st["slots"].items())
                if s["scene"] == scene and s.get("segment_id") and not banned(self.cfg, s)]

    def report(self):
        tag = "、".join(self.tags())
        words = self.cfg.get("banned_footage_words") or []
        lines = [f"# AI 素材生成 · {self.plan_path.stem} · {time.strftime('%Y-%m-%d %H:%M')}", "",
                 f"产品 {self.plan['product']}，标签「{tag}」。生成成功的只带场景标签，可直接 `clipo_batch.py pool --tags <标签> --allow-risk` 做素材池。", ""]
        lines += ["| 场景 | 要几条 | 成功 | 还在跑 | 放弃 | 被拦次数 |", "|---|---|---|---|---|---|"]
        total_ok = total = 0
        for sc in self.plan["scenes"]:
            slots = [s for s in self.st["slots"].values() if s["scene"] == sc["id"]]
            ok = [s for s in slots if s.get("segment_id")]
            run = [s for s in slots if self.status(s) in ("todo", "inflight", "tagging", "retry")]
            gave = [s for s in slots if s.get("gave_up")]
            blocked = sum(bool(a.get("blocked")) for s in slots for a in s["attempts"])
            total_ok, total = total_ok + len(ok), total + len(slots)
            lines.append(f"| {sc['id']} | {len(slots)} | {len(ok)} | {len(run)} | {len(gave)} | {blocked} |")
        lines += ["", f"**合计成功 {total_ok}/{total}**。", ""]
        bad = [(k, s) for k, s in sorted(self.st["slots"].items())
               if s.get("segment_id") and any(w in (s.get("description") or "") for w in words)]
        if bad:
            lines += ["⚠️ 平台描述里有禁用画面（不进素材池，已在 pool 里自动排除）：", ""]
            lines += [f"- {k}：{s['description'][:60]}" for k, s in bad] + [""]
        gave = [(k, s) for k, s in sorted(self.st["slots"].items()) if s.get("gave_up")]
        if gave:
            lines += ["**没做出来的（要换方案，不要拿别的场景的素材顶）：**", ""]
            for k, s in gave:
                last = s["attempts"][-1] if s["attempts"] else {}
                lines.append(f"- {k}：{s['gave_up']}；最后一次 {last.get('error') or last.get('status')}")
            lines.append("")
        lines += ["## 每条", "", "| 编号 | 状态 | 切片 | 时长 | 尝试 |", "|---|---|---|---|---|"]
        for k, s in sorted(self.st["slots"].items()):
            lines.append(f"| {k} | {self.status(s)} | {s.get('segment_id', '')} | "
                         f"{round(s['duration_ms'] / 1000, 1) if s.get('duration_ms') else ''} | {len(s['attempts'])} |")
        out = self.plan_path.with_name(self.plan_path.stem + "-report.md")
        out.write_text("\n".join(lines) + "\n")
        return out, total_ok, total


def banned(cfg, slot):
    return any(w in (slot.get("description") or "") for w in cfg.get("banned_footage_words") or [])


def cut_and_register(r, key, slot, spans, workdir):
    """Cut one generated clip into the shot spans, upload each piece, register it as an AI segment of the product.
    Returns [{"segment", "object_key", "source_interval", "duration", "segment_id"}] and caches it on the slot."""
    if slot.get("shots"):
        return slot["shots"]
    g = cb.api("GET", f"/material-generations/{slot['attempts'][-1]['generation_id']}")  # fresh signed url
    src = Path(workdir) / f"{key}.mp4"
    urllib.request.urlretrieve(g["object_url"], src)
    pid, rows = r.product["clipo_product_id"], []
    for seg_no, (a, b) in zip(("1", "2", "4"), spans):
        part = Path(workdir) / f"{key}-{seg_no}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(a), "-to", str(b), "-i", str(src), "-an",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-movflags", "+faststart", str(part)], check=True)
        tok = cb.api("POST", "/storage/upload-tokens", {"filename": part.name, "content_type": "video/mp4",
                                                        "content_length": part.stat().st_size, "purpose": "material"})
        req = urllib.request.Request(tok["upload_url"], data=part.read_bytes(), method="PUT",
                                     headers=dict(tok.get("headers") or {}, **{"Content-Type": "video/mp4"}))
        urllib.request.urlopen(req, timeout=300).read()
        seg = cb.api("POST", f"/products/{pid}/segments", {"object_key": tok["object_key"], "ai_generated": True})
        rows.append({"segment": int(seg_no), "object_key": tok["object_key"], "source_interval": [a, b],
                     "duration": round(b - a, 3), "segment_id": seg.get("id")})
        if r.tag(slot["scene"]) and seg.get("id"):
            ai_tags.set_tags(seg["id"], [r.tag(slot["scene"])])  # understanding may overwrite it: settle_tags rechecks
    slot["shots"] = rows
    r.save()
    return rows


def cmd_sets(r, args):
    """Write per-video character sets (visual_sets) into a brief from this plan's ready clips (see module doc)."""
    bp = Path(args.run) / "briefs" / f"{args.brief}.json"
    brief = json.loads(bp.read_text())
    scene = args.scene or brief.get("aigc_scene")
    if not scene:
        raise SystemExit("brief 没写 aigc_scene，用 --scene 指定用哪个场景的素材")
    sc = r.scene(scene)
    spans = sc.get("shots") or r.plan.get("shots")
    if not spans or len(spans) != 3:
        raise SystemExit("plan 要写 shots：三个镜头在源片里的起止秒，例如 [[0,2],[2,4],[4,6]]（和提示词里三个动作的时间一致）")
    ready = r.ready(scene)
    st_path = Path(args.run) / "clipo" / cb.out_name(r.cfg, "state.json")
    videos = json.loads(st_path.read_text()).get("videos", {}) if st_path.exists() else {}
    keys = sorted(k for k, v in videos.items() if v["brief_id"] == args.brief and v.get("cut_id"))
    if not keys:
        raise SystemExit(f"{args.brief} 还没有平台剪辑：先 clipo_batch.py submit（会停在 aigc_pending 等素材），再跑 sets 绑素材")
    existing = {g.get("source_generation_id") for g in brief.get("visual_sets", [])}
    free = [(k, s) for k, s in ready if s["attempts"][-1]["generation_id"] not in existing]
    have = {g.get("video_key") for g in brief.get("visual_sets", [])}
    todo = [k for k in keys if k not in have]
    pairs = list(zip(todo, free))
    print(f"{args.brief}：{len(keys)} 条视频，已绑 {len(have & set(keys))} 条，场景「{scene}」还能用的素材 {len(free)} 条，这次绑 {len(pairs)} 条")
    if len(pairs) < len(todo):
        print(f"⚠️ 还缺 {len(todo) - len(pairs)} 套：再给这个场景生成，不能拿别的视频或场景的素材顶")
    if args.dry_run or not pairs:
        return
    with tempfile.TemporaryDirectory() as wd:
        for vkey, (skey, slot) in pairs:
            shots = cut_and_register(r, skey, slot, spans, wd)
            group = {"name": f"{args.brief}-{skey}", "video_key": vkey, "cut_id": videos[vkey]["cut_id"],
                     "source_generation_id": slot["attempts"][-1]["generation_id"], "source_object_key": slot["object_key"],
                     "scene": scene, "actions": dict(zip(("1", "2", "4"), sc.get("actions") or ["", "", ""])),
                     "segments": [{k: x[k] for k in ("segment", "object_key", "source_interval", "duration")} for x in shots]}
            brief.setdefault("visual_sets", []).append(group)
            brief["within_folder_material_diversity_required"] = True
            bp.write_text(json.dumps(brief, ensure_ascii=False, indent=2))
            print(f"  {vkey} ← {skey}")
    settle_tags(r)
    print("下一步：clipo_batch.py bind-aigc --brief " + args.brief)


def settle_tags(r, wait=300):
    """Cut shots are new clips: the platform's understanding runs after registration and writes its own tags.
    Re-set the scene tag until every clip of this plan is understood and carries only it."""
    deadline = time.time() + wait
    while True:
        fixed = r.retag()
        r.save()
        pending = [sid for s in r.st["slots"].values() for sid in [s.get("segment_id")] + [x.get("segment_id") for x in s.get("shots") or []]
                   if sid and sid not in r.st.get("tags_settled", [])]
        if not pending:
            return
        if time.time() >= deadline:
            print(f"⚠️ {len(pending)} 条素材平台还没理解完，标签可能还会被改：稍后跑 aigc_gen.py poll 或 ai_tags.py check")
            return
        time.sleep(20)


def product_segments(pid):
    rows, skip = [], 0
    while True:
        r = cb.api("GET", f"/products/{pid}/segments?skip={skip}&limit=100")
        page = r if isinstance(r, list) else r.get("items", [])
        rows += page
        if len(page) < 100:
            return rows
        skip += 100


def check_plan(r):
    """Problems that should stop a submit: tag outside the AI tag rule, prompts too long, banned looks asked for."""
    errs, warns = [], []
    for sc in r.plan["scenes"]:
        tag = r.tag(sc["id"])
        if not tag:
            errs.append(f"场景 {sc['id']} 没有标签：plan 写 tag，或场景写 tag（scripts/ai_tags.py 的规则）")
        elif not ai_tags.is_ai_tag(tag) or re.search(r"\d{4}|[A-Z]\d{2}|分镜", tag):
            errs.append(f"场景 {sc['id']} 的标签「{tag}」不合规：以「AI」开头、按场景起名，不写日期、brief 号、分镜号")
    have = cb.api("GET", f"/products/{r.product['clipo_product_id']}")["tags"]
    new = [t for t in r.tags() if t not in have]
    if new:
        warns.append(f"产品标签里还没有 {'、'.join(new)}：提交时会加进产品标签")
    risky = (r.cfg.get("aigc") or {}).get("risky_words", DEFAULT_RISKY)
    for key, s in sorted(r.st["slots"].items()):
        if len(s["prompt"]) > 500:
            errs.append(f"{key} 提示词 {len(s['prompt'])} 字，平台上限 500")
        hit = [w for w in risky if w in s["prompt"]]
        if hit and not s["attempts"]:
            warns.append(f"{key} 提示词有容易被版权检查拦的词：{'、'.join(hit)}（只提醒，不会自动删）")
    # 品牌禁用画面（config banned_footage_words）：提示词里出现且不是「不 / 没 / 无 / 禁止」否定的，提醒
    banned = r.cfg.get("banned_footage_words") or []
    for key, s_ in sorted(r.st["slots"].items()):
        hit = [w for w in banned if re.search(rf"(?<!不)(?<!没)(?<!无)(?<!别)(?<!禁止){re.escape(w)}", s_["prompt"])
               and not re.search(rf"(不|没有?|无|别|禁止)[^，。,;；]{{0,4}}{re.escape(w)}", s_["prompt"])]
        if hit and not s_["attempts"]:
            warns.append(f"{key} 提示词写了禁用画面：{'、'.join(hit)}（references/aigc-standards.md）")
    # 品牌外貌规则（config aigc.look_words / natural_words / mask_words，清扬用户 11-18：帅哥美女、不要脸谱）
    look = r.cfg.get("aigc") or {}
    if look.get("look_words"):
        for key, s_ in sorted(r.st["slots"].items()):
            if s_["attempts"] or not re.search(r"男|女|人|主角|配角|妈|邻居|同事", s_["prompt"]):
                continue  # 已提交过、或纯空镜不查
            p_ = s_["prompt"]
            if not any(w in p_ for w in look["look_words"]):
                errs.append(f"{key} 有人物但没写外貌：写「帅气 / 好看」等（{'、'.join(look['look_words'][:4])}）")
            if not any(w in p_ for w in look.get("natural_words", [])):
                errs.append(f"{key} 没写自然真实：加「自然、真实生活感」，避免脸谱化")
            mask = [w for w in look.get("mask_words", []) if w in p_ and not re.search(rf"(不|没有?|无|别|禁止)[^，。,;；]{{0,4}}{re.escape(w)}", p_)]
            if mask:
                errs.append(f"{key} 写了脸谱化的词：{'、'.join(mask)}（references/aigc-standards.md 外貌规则）")
    for sc in r.plan["scenes"]:
        if not sc.get("fallback_prompts"):
            warns.append(f"场景 {sc['id']} 没写 fallback_prompts：被版权检查拦了就只能报缺口，不会自动重试")
    return errs, warns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["submit", "poll", "report", "sets"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--plan", required=True, help="$R/aigc/<名字>.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--brief", help="sets: 写进哪个 brief 的 visual_sets")
    ap.add_argument("--scene", help="sets: 用 plan 里哪个场景的素材，默认 brief 的 aigc_scene")
    ap.add_argument("--wait", type=int, default=0, help="poll: 最多跑多少秒")
    ap.add_argument("--interval", type=int, default=60)
    args = ap.parse_args()
    cfg = load_config(args.config)
    cb.BASE, cb.CFG = cfg["clipo"]["base_url"].rstrip("/") + "/api/v1", cfg
    r = Run(cfg, args.plan)
    if args.cmd == "sets":
        cmd_sets(r, args)
        return
    if args.cmd == "report":
        out, ok, total = r.report()
        print(f"成功 {ok}/{total} → {out}")
        return
    if args.cmd == "submit":
        errs, warns = check_plan(r)
        for w in warns:
            print("⚠️ ", w)
        if errs:
            raise SystemExit("❌ " + "\n❌ ".join(errs))
        todo = [k for k, s in r.st["slots"].items() if r.status(s) in ("todo", "retry")]
        if args.dry_run:
            first = r.submit_free(dry=True)
            print(f"\n(dry run) 共 {len(r.st['slots'])} 条，待提交 {len(todo)} 条（每条单独计费；被拦的每条最多再试 {MAX_RETRIES} 次，"
                  f"最多 {len(todo) * (MAX_RETRIES + 1)} 次）。这次先提交 {len(first)} 条，其余 poll 边跑边补。确认后去掉 --dry-run")
            return
        added = ai_tags.add_product_tags(r.product["clipo_product_id"], r.tags())
        if added:
            print(f"产品标签新增：{'、'.join(added)}")
        sent = r.submit_free()
        print(f"已提交 {len(sent)} 条。接着在后台跑：\n  nohup python3 -I scripts/aigc_gen.py poll --config {args.config} "
              f"--run {args.run} --plan {args.plan} --wait 5400 > {Path(args.plan).with_suffix('.log')} 2>&1 &")
        return
    deadline = time.time() + args.wait
    while True:
        r.refresh()
        r.submit_free()
        counts = {}
        for s in r.st["slots"].values():
            counts[r.status(s)] = counts.get(r.status(s), 0) + 1
        print(time.strftime("%H:%M:%S"), counts, flush=True)
        if not any(r.status(s) in ("todo", "inflight", "tagging", "retry") for s in r.st["slots"].values()):
            break
        if time.time() >= deadline:
            print("到时间了，还没跑完；再跑一次 poll 接着跑")
            break
        time.sleep(args.interval)
    out, ok, total = r.report()
    print(f"成功 {ok}/{total} → {out}")


if __name__ == "__main__":
    main()
