#!/usr/bin/env python3
"""
[INPUT]: 依赖 common 的人审凭据与 brief 读取、lint_copy 的文案门禁、clipo_batch 的 State / deliverable / picks /
         generating / reject_kind；读取 runs/<日>/clipo 的 state、dry-run 收据与 aigc/*-state.json。
[OUTPUT]: 提供 day_progress()（结构化进度）、render()（一屏文字）与 status 命令（--json 给 agent）。
[POS]: scripts 的唯一进度入口，只读。用户 11-24：agent 约四成工具调用是临时手写代码查「到哪了、缺几条、
       为什么驳回」；这里一次给全，并给出下一条可直接执行的命令。clipo_batch.py status 调用这里。
       退役的本地音色名单不再作为阻塞原因；实际声音质量问题仍单列。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md

    python3 -I scripts/progress.py --config qingyang --run $R            # 一屏：每个 brief 的数和下一步
    python3 -I scripts/progress.py --config qingyang --run $R --json     # 同样内容的 JSON
    python3 -I scripts/progress.py --config qingyang --run $R --brief 1124-B01   # 只看一个 brief，列出驳回原因
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clipo_batch as cb
from common import aigc_problem, approval_problem, demo_first, demo_problem, load_config, out_name
from lint_copy import lint_brief
from demo_authorization import permits_preview

# ---------- 每条视频的阶段 ----------
IN_FLIGHT = ("generated", "rendering", "rendered", "reviewing", "demo_prepare_pending")


def video_bucket(v):
    """把一条视频归到一个桶：合格 / 在途 / 驳回 / 失败 / 待绑 AIGC。"""
    stage = v.get("stage")
    if v.get('customer_rework_required'):
        return 'rejected'
    if stage == "aigc_pending":
        return "aigc_pending"
    if stage in IN_FLIGHT or stage is None:
        return "in_flight"
    if stage == "failed":
        return "failed"
    if cb.deliverable(v):
        return "ok"
    from quality_contract import delivery_issues
    if (v.get('ai_review')=='pass' or v.get('review_waived')) and delivery_issues(v,cb.CFG):
        return 'quality_pending'
    return "rejected"


def why_not(v):
    """不合格的具体原因，一句话。"""
    if v.get('customer_rework_required'):return '用户退回：'+v['customer_rework_required']
    for key, label in (("copy_issues", "改写违规"), ("qc_issues", "交付闸门"), ("bad_footage", "禁用画面"),
                       ("voice_cmd", "念出语音指令"), ("voice_drift", "音色漂移"),
                       ("watermark_missing", "缺 AI 角标")):
        val = v.get(key)
        if val:
            detail = "；".join(map(str, val))[:80] if isinstance(val, list) else ""
            return f"{label}{'：' + detail if detail else ''}"
    if v.get("stage") == "failed":
        return "失败：" + str(v.get("error") or v.get("last_render_error") or v.get("last_error") or "")[:80]
    if v.get("ai_review") == "reject":
        return f"AI 审核驳回（{cb.reject_kind(v)}）：{(v.get('ai_reason') or '')[:80]}"
    if v.get("user_review_pending"):
        return "等用户检查"
    return "无审核结论"


# ---------- 每个 brief 的下一步 ----------
def next_step(cfg, b, job, counts, lint_errs, shared, cmd):
    """(阶段名, 下一条命令或要等的人)。命令可以直接复制执行；⏸ 表示停下等用户。"""
    bid, q = b["brief_id"], b["quantity"]
    if (job or {}).get('customer_rework_hold'):
        return '批量返工', '先核逐条文案与整组人物/来源分配，task_bulk.py budget --dry-run → allocate --dry-run / allocate；未复核前不补产'
    if counts["ok"] >= q:  # 够数优先：成片按当时审过的文案生产，之后新增的 lint 规则不把它打回「改文案」
        return ("已交付", "-") if shared else ("可交付", f"{cmd('qc.py', bid)} → {cmd('clipo_batch.py share', bid)}")
    preview=permits_preview(b)
    if counts.get('quality_pending') and not preview:
        return '成片自查', f"{cmd('qc.py', bid)} → media_qc.py --run <本批目录> --key <待检视频编号> → 实际看图/看片并记录，勿直接补产"
    if not b.get("segments"):
        return "写脚本", f"{cmd('brief_card.py', bid)} → 写 segments → {cmd('lint_copy.py', bid)}"
    if lint_errs:
        return "改文案", f"改 {lint_errs} 个 lint 错误 → {cmd('lint_copy.py', bid)}"
    if preview:
        if counts["failed"]:
            return "demo 制作失败", "只修本条失败环节，不自动补产或新增TTS"
        if counts.get("quality_pending") or counts["ok"]:
            return "demo 待客户检查", "单条demo已完成，按用户原话交客户检查；不自动量产或追加QC"
        if cb.generating(job or {}) or counts["in_flight"]:
            return "demo 制作中", "仅每组1条，完成整篇配音/首渲染；客户审核仍待确认"
        return "可制作单条demo", f"{cmd('clipo_batch.py submit --dry-run', bid)} → 仅首条demo，不能量产"
    if b.get("status") in ("draft", "changes_requested"):
        return "等脚本人审", "⏸ " + ("review_doc.py render 发给用户，等原话" if b.get("status") == "draft" else "按意见改完重新发审")
    if not job or (job.get("configuration_only") and not job.get("generation_id")):
        if approval_problem(b):
            return "等脚本人审", f"⏸ {approval_problem(b)}"
        if aigc_problem(b):
            return "等 AIGC 素材", f"⏸ {aigc_problem(b)}：aigc_gen.py 生成 → 抽帧给用户 → review_doc.py approve-aigc"
        return "可提交", f"{cmd('clipo_batch.py submit --dry-run', bid)} → 报数 → submit"
    if job.get("stage") == "failed":
        return "生成失败", f"{cmd('clipo_batch.py submit --retry-failed', bid)}"
    busy = cb.generating(job) or counts["in_flight"]
    if job.get("demo_stage") == "demo":
        if busy:
            return "demo 生产中", "后台 poll，不用等"
        if not counts["ok"]:
            return "demo 不合格", f"看驳回原因：{cmd('progress.py', bid)}；文案问题改稿重审，素材问题换素材后 submit --retry-failed"
        if demo_problem(b):
            return "等 demo 人审", f"⏸ {cmd('clipo_batch.py share --partial', bid)} 发给用户 → review_doc.py approve-demo"
        return "可量产", f"{cmd('clipo_batch.py submit --dry-run', bid)} → 报数 → submit"
    if busy:
        return "生产中", "后台 poll，不用等"
    if counts["aigc_pending"]:
        return "待绑 AIGC", f"{cmd('aigc_gen.py sets', bid)} → {cmd('clipo_batch.py bind-aigc', bid)}"
    if counts["ok"] < q:
        return "缺口", f"{cmd('clipo_batch.py topup --dry-run', bid)} → 报数 → topup（缺 {q - counts['ok']} 条）"
    if not shared:
        return "可交付", f"{cmd('qc.py', bid)} → {cmd('clipo_batch.py share', bid)}"
    return "已交付", "-"


# ---------- 汇总 ----------
def aigc_plans(run):
    """runs/<日>/aigc/*-state.json：每个生成计划 成功 / 在跑 / 被拦。"""
    out = []
    for p in sorted((Path(run) / "aigc").glob("*-state.json")):
        try:
            st = json.loads(p.read_text())
        except ValueError:
            continue
        c = Counter()
        for s in (st.get("slots") or {}).values():
            if s.get("segment_id"):
                c["ready"] += 1
            elif s.get("gave_up"):
                c["gave_up"] += 1
            elif s.get("attempts") and s["attempts"][-1].get("status") in ("pending", "running", "success"):
                c["running"] += 1
            else:
                c["todo"] += 1
        out.append({"plan": p.name.replace("-state.json", ""), **c, "total": sum(c.values())})
    return out


def receipts(run, cfg):
    """一小时内有效的 dry-run 收据：真正提交时条数必须和它一致。"""
    out = {}
    for name in ("submit", "topup"):
        p = Path(run) / "clipo" / f"dryrun-{name}-{cfg['brand_key']}.json"
        if p.exists():
            rec = json.loads(p.read_text())
            age = time.time() - rec.get("at", 0)
            if age <= cb.DRY_RUN_TTL:
                out[name] = {"plan": rec.get("plan"), "minutes_left": int((cb.DRY_RUN_TTL - age) // 60)}
    return out


def receipt_shares(run):
    """delivery_share.py 在 run 目录里留下的已回读分享（pack / batch / share 收据，verified=true）：
    {cut_id: 分享链接}。这些交付不写进 state 的 shares，按 cut_id 找回来。"""
    out = {}
    for f in Path(run).glob("**/*links*.json"):
        try:
            d = json.loads(f.read_text())
        except ValueError:
            continue
        if not isinstance(d, dict) or not d.get("verified") or not d.get("url"):
            continue
        for e in (d.get("plan") or {}).get("entries") or []:
            if e.get("cut_id"):
                out[e["cut_id"]] = d["url"]
    return out


def poll_alive(run, cfg):
    """后台 poll 在不在跑：看 clipo/ 下 poll*.log 最近 5 分钟有没有写入（poll 每 10~30 秒写一行）。"""
    logs = list((Path(run) / "clipo").glob("poll*.log"))
    if not logs:
        return False, None
    age = time.time() - max(f.stat().st_mtime for f in logs)
    return age < 300, int(age // 60)


def day_progress(cfg, run, only=None):
    cb.CFG = cfg  # clipo_batch status 以 __main__ 运行时，这里导入的是另一份模块：自己设好配置
    cb.BASE = cb.BASE or cfg["clipo"]["base_url"].rstrip("/") + "/api/v1"
    st = cb.State(run)
    briefs = cb.load_briefs(run, only)
    jobs = st.data.get("briefs") or {}
    shares = st.data.get("shares") or {}
    # pack 分享只记视频号：按视频号反查 brief（2x-pack-1 = B03/B04/B30 的视频）
    packed = {v.rsplit("-V", 1)[0] for s in shares.values() for v in s.get("videos") or []}
    by_cut = receipt_shares(run)
    packed |= {v["brief_id"] for v in st.videos.values() if v.get("cut_id") in by_cut}
    def cmd(tool, bid):  # 在技能目录下可直接执行的完整命令
        name, _, flags = tool.partition(" ")
        return f"python3 -I scripts/{name} {flags + ' ' if flags else ''}--config {cfg['brand_key']} --run {run} --brief {bid}"
    rows = []
    for b in briefs:
        bid = b["brief_id"]
        vids = {k: v for k, v in st.videos.items() if v["brief_id"] == bid}
        counts = Counter(video_bucket(v) for v in vids.values())
        for k in ("ok", "in_flight", "rejected", "failed", "aigc_pending", "quality_pending"):
            counts.setdefault(k, 0)
        lint_errs = sum(l == "error" for l, _ in lint_brief(b, cfg)) if b.get("segments") else 0
        job = jobs.get(bid) or {}
        stage, todo = next_step(cfg, b, job, counts, lint_errs, bid in shares or bid in packed, cmd)
        lint_msgs = [m for l, m in lint_brief(b, cfg) if l == "error"] if only and b.get("segments") else []
        reasons = Counter(why_not(v).split("：")[0] for v in vids.values() if video_bucket(v) in ("rejected", "failed"))
        rows.append({
            "brief": bid, "product": b.get("product_key"), "store": b.get("store"), "target": b["quantity"],
            "ok": counts["ok"], "in_flight": counts["in_flight"], "rejected": counts["rejected"],
            "failed": counts["failed"], "aigc_pending": counts["aigc_pending"], "quality_pending": counts["quality_pending"],
            "short": max(b["quantity"] - counts["ok"], 0), "stage": stage, "next": todo,
            "demo_pre_review": permits_preview(b),
            "demo_rendered": sum(bool(v.get("object_key")) and v.get("stage")=="done" for v in vids.values()),
            "reasons": dict(reasons.most_common()),
            "details": ([{"video": "文案", "why": m} for m in lint_msgs] +
                        [{"video": k, "why": why_not(v)} for k, v in sorted(vids.items())
                         if video_bucket(v) in ("rejected", "failed")]) if only else [],
            "share": (shares.get(bid) or {}).get("id") and cb.share_url(shares[bid]["id"]) if bid in shares else None,
        })
    alive, log_age = poll_alive(run, cfg)
    state_age = int((time.time() - st.path.stat().st_mtime) // 60) if st.path.exists() else None
    return {"run": str(run), "brand": cfg["brand_key"], "briefs": rows, "poll_alive": alive,
            "poll_log_age_min": log_age, "state_age_min": state_age, "aigc": aigc_plans(run),
            "receipts": receipts(run, cfg), "group": st.data.get("share_group"),
            "poll_needed": any(r["in_flight"] for r in rows) or any(cb.generating(jobs[b["brief_id"]]) for b in briefs
                                                                     if b["brief_id"] in jobs)}


# ---------- 一屏文字 ----------
def render(p):
    rows = p["briefs"]
    if not rows:
        return f"{p['run']} 还没有 {p['brand']} 的 brief：先跑 fetch_sources.py → parse_schedule.py"
    age = p.get("state_age_min")
    out = [f"state 更新于 {age} 分钟前（本命令离线，只读本地 state）" if age is not None else "还没有平台 state",
           f"{'brief':10} {'产品':8} {'合格/目标':>8} {'在途':>4} {'驳回':>4} {'失败':>4}  阶段"]
    for r in rows:
        out.append(f"{r['brief']:10} {str(r['product'])[:8]:8} {r['ok']:>4}/{r['target']:<3} {r['in_flight']:>4} "
                   f"{r['rejected']:>4} {r['failed']:>4}  {r['stage']}")
    tot = Counter()
    for r in rows:
        tot.update({k: r[k] for k in ("target", "ok", "short", "in_flight", "rejected")})
    out.append(f"\n合计：合格 {tot['ok']}/{tot['target']}，缺 {tot['short']}，在途 {tot['in_flight']}，驳回 {tot['rejected']}")
    previews=sum(r.get('demo_rendered',0) for r in rows if r.get('demo_pre_review'))
    if previews:out.append(f'已完成、待客户检查demo {previews} 条；不计作客户验收通过，不自动补齐量产')
    checks=sum(r.get('quality_pending',0) for r in rows if not r.get('demo_pre_review'))
    if checks:out.append(f"已渲染、待严格成片自查 {checks} 条；这部分先检查，不重复补产")
    why = Counter()
    for r in rows:
        why.update(r["reasons"])
    if why:
        out.append("驳回 / 失败原因：" + "、".join(f"{k} {n}" for k, n in why.most_common()))
    for r in rows:
        for d in r["details"]:
            out.append(f"  {d['video']}：{d['why']}")
    for a in p["aigc"]:
        out.append(f"AIGC {a['plan']}：成功 {a.get('ready', 0)}/{a['total']}，在跑 {a.get('running', 0)}，"
                   f"被拦放弃 {a.get('gave_up', 0)}，未发 {a.get('todo', 0)}")
    for name, rec in p["receipts"].items():
        out.append(f"有效 dry-run 收据 {name}：{rec['plan']}（还剩 {rec['minutes_left']} 分钟）")
    out.append("\n下一步（照做，不用另写代码查）：")
    seen = set()
    for r in rows:
        if r["next"] != "-" and (r["stage"], r["next"]) not in seen:
            seen.add((r["stage"], r["next"]))
            out.append(f"  {r['brief']} {r['stage']}：{r['next']}")
    if p["poll_needed"] and p["poll_alive"]:
        out.append(f"  后台 poll 在跑（日志 {p['poll_log_age_min']} 分钟前更新）：不用等，也不用另开；结束本轮回复")
    elif p["poll_needed"]:
        out.append(f"  后台 poll 没在跑，启动：nohup python3 -I scripts/clipo_batch.py poll --config {p['brand']} --run {p['run']} "
                   f"--wait 3600 > {p['run']}/clipo/poll-{p['brand']}.log 2>&1 &")
    if len(seen) == 0 and not p["poll_needed"]:
        out.append("  今天的活已完成")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--brief")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    cfg = load_config(args.config)
    cb.BASE = cfg["clipo"]["base_url"].rstrip("/") + "/api/v1"
    p = day_progress(cfg, args.run, args.brief)
    print(json.dumps(p, ensure_ascii=False, indent=1) if args.json else render(p))


if __name__ == "__main__":
    main()
