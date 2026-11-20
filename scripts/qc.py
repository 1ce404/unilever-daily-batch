#!/usr/bin/env python3
"""
[INPUT]: 依赖 clipo_batch 的配置、素材索引与 AI 镜头时段；读取剪辑草稿与审核状态。
[OUTPUT]: 提供 cut_checks（单条机器检查）与 brief_checks（同一链接开头重复）；命令 qc 输出交付闸门报告。
[POS]: scripts 的交付闸门。poll 渲染前写入单条结论，share 前按 brief 复核；不改草稿、不调写接口。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md

用户 11-19/11-20：返工都是交付后看片才发现的（角标在右上、实拍也加角标、开头重复、BGM 听不见、
念产品名配质地、女生形象、慢动作、女声）。这些都能从草稿判断，交付前机器挡住，不靠人看。

    python3 -I scripts/qc.py --config qingyang --run $R [--brief <id>]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import load_config


def _name(clip):
    return (clip.get("props", {}).get("src") or "").split("?")[0].rsplit("/", 1)[-1]


def cut_checks(brief, draft, voice=None):
    """Problems one cut has before it may be delivered: [str]. Empty list = passes the gate."""
    cfg, pk = cb.CFG, brief["product_key"]
    prod = cfg["products"][pk]
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
    vol = cfg.get("bgm_volume")
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
    # ---------- 音色：本 brief 的名单（2x / 呼吸瓶只用男声） ----------
    allowed = cb.stable_voices(brief)
    if voice and allowed and voice not in allowed:
        out.append(f"音色 {voice} 不在本 brief 名单（{prod.get('voice_gender') or cfg.get('tts', {}).get('gender', '')}）")
    return list(dict.fromkeys(out))


def brief_checks(brief, videos):
    """Problems across the videos picked for one link: openers must all differ (用户 11-19)."""
    seen, out = {}, []
    for k, v in videos:
        first = (v.get("pinned_clips") or [None])[0] or v.get("hook_clip")
        if first and first in seen:
            out.append(f"{k} 开头和 {seen[first]} 是同一条素材")
        elif first:
            seen[first] = k
    return out


def main():
    ap = argparse.ArgumentParser(description="交付闸门：读草稿检查角标、BGM、禁用画面、画面标签、音色和开头重复")
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
