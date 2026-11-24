#!/usr/bin/env python3
"""
[INPUT]: 依赖 clipo_batch 的配置、素材索引与 AI 镜头时段；读取剪辑草稿与审核状态。
[OUTPUT]: 提供 cut_checks 与 brief_checks；开头去重尊重用户原话授权，并优先使用换后钩子。
[POS]: scripts 的交付闸门。poll 渲染前写入单条结论，share 前按 brief 复核；不改草稿、不调写接口。
       音色由产品的平台提示词决定；闸门检查音量，不限制音色或配音性别。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md

用户 11-19/11-20：返工都是交付后看片才发现的（角标在右上、实拍也加角标、开头重复、BGM 听不见、
念产品名配质地、女生形象、慢动作）。这些都能从草稿判断，交付前机器挡住，不靠人看。

    python3 -I scripts/qc.py --config qingyang --run $R [--brief <id>]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# clipo_batch 以脚本运行时模块名是 __main__；再 import clipo_batch 会得到第二份、CFG 为 None 的副本
# （11-20 poll 在这里崩：cfg["products"] on None）。被 clipo_batch 调用时用正在跑的那一份
_main = sys.modules.get("__main__")
if getattr(_main, "cmd_poll", None):
    cb = _main
else:
    import clipo_batch as _cb
from common import load_config

# clipo_batch 当脚本跑时是 __main__，它的 CFG/BASE 不在另一份 import 的 clipo_batch 里（11-20 poll 因 CFG=None 崩）
cb = sys.modules.get("__main__") if getattr(sys.modules.get("__main__"), "CFG", None) else _cb


def _name(clip):
    return (clip.get("props", {}).get("src") or "").split("?")[0].rsplit("/", 1)[-1]


def cut_checks(brief, draft, voice=None):
    """返回交付问题；空列表表示通过。voice 参数只为兼容旧调用，不参与音色筛选。"""
    cfg, pk = cb.CFG, brief["product_key"]
    out = []
    clips = draft.get("clips", [])
    main = sorted((c for c in clips if c.get("trackId") == "main"), key=lambda c: c["from"])
    idx = cb.footage_index(pk)
    # ---------- 角标：右下角样式；ai_only 时只盖 AI 镜头 ----------
    wm_cfg = cfg.get("watermark") or {}
    if wm_cfg.get("add"):
        from footage_qc import WATERMARK_PROPS
        wm = sorted((c for c in clips if c.get("trackId") == "watermark"), key=lambda c: c["from"])
        if any(c.get("props") != WATERMARK_PROPS for c in wm):
            out.append("角标不是右下角标准样式")
        want = cb.ai_spans(pk, draft) if wm_cfg.get("ai_only") else [(0, draft["composition"]["durationInFrames"])]
        if [(c["from"], c["durationInFrames"]) for c in wm] != want:
            out.append("角标时段和 AI 镜头不一致（实拍不加、AI 镜头要加）")
    # ---------- BGM 音量 ----------
    vol, voice_vol = cb.mix_volumes(pk)
    if voice_vol is not None and any(abs((c["props"].get("volume") or 0) - voice_vol) > 1e-6
                                     for c in clips if c.get("trackId") == "voiceover"):
        out.append(f"口播音量不是 {voice_vol}")
    if vol is not None:
        bgm = [c for c in clips if c.get("trackId") == "audio"]
        if not bgm:
            out.append("没有 BGM")
        elif any(abs((c["props"].get("volume") or 0) - vol) > 1e-6 for c in bgm):
            out.append(f"BGM 音量不是 {vol}")
    # ---------- 禁用画面：品牌 + 产品（头屑、女生形象、慢动作…）与禁用文件 ----------
    out += [f"禁用画面 {x}" for x in cb.bad_footage(pk, draft)]
    # ---------- 每段画面和 brief 写的标签一致（平台会配错，如爆款带字幕、绿瓶混蓝瓶） ----------
    plan = {sg["id"]: set(sg.get("visual_tags") or (sg.get("footage_pool") or {}).get("tags") or [])
            for sg in brief.get("segments") or []}
    for c in main:
        seg = str(c["id"]).split("-")[0]
        tags, x = plan.get(seg), idx.get(_name(c)) or {}
        if tags and x and not tags & set(x.get("tags") or []):
            out.append(f"第{seg}段画面「{(x.get('name') or '')[:16]}」不在计划标签 {sorted(tags)} 里")
    return list(dict.fromkeys(out))


def brief_checks(brief, videos):
    """Problems across the videos picked for one link: openers must all differ (用户 11-19)."""
    if brief.get("hook_repeat_ok"):
        return []
    seen, out = {}, []
    for k, v in videos:
        first = cb.opener_clip(v)
        if first and first in seen:
            out.append(f"{k} 开头和 {seen[first]} 是同一条素材")
        elif first:
            seen[first] = k
    return out


def main():
    ap = argparse.ArgumentParser(description="交付闸门：读草稿检查角标、音量、禁用画面、画面标签和开头重复")
    ap.add_argument("--config", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--brief")
    args = ap.parse_args()
    cfg = load_config(args.config)
    cb.CFG, cb.BASE = cfg, cfg["clipo"]["base_url"].rstrip("/") + "/api/v1"
    st = cb.State(args.run)
    briefs = {b["brief_id"]: b for b in cb.load_briefs(args.run, args.brief)}
    rows, bad = [], 0
    for bid, b in sorted(briefs.items()):
        vids = [(k, v) for k, v in sorted(st.videos.items()) if v["brief_id"] == bid and v.get("stage") == "done"]
        for k, v in vids:
            cut = cb.api("GET", f"/video-cuts/{v['cut_id']}")
            v["qc_issues"] = cut_checks(b, cut["draft"], (cut.get("random_result") or {}).get("tts_speaker_id"))
            bad += bool(v["qc_issues"])
            rows.append((k, v["qc_issues"]))
        picked, _ = cb.picks(st, bid, b["quantity"])
        for m in brief_checks(b, [(k, st.videos[k]) for k in picked]):
            rows.append((bid, [m]))
            bad += 1
    st.save()
    for k, issues in rows:
        print(("❌ " if issues else "✅ ") + k + ("：" + "；".join(issues) if issues else ""))
    print(f"\n闸门：{bad} 处问题" + ("，不进链接" if bad else "，可以交付"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
