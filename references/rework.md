# 返工与交付修订命令

本文件只收已交付成片的返工命令。主流程见 ../SKILL.md。每条写命令先 `--dry-run` 报数。真正执行前先备份，执行后回读，旧链接不删除。

| 场景 | 命令 |
|---|---|
| 已分享分组质量复核（无原state） | `group_quality_rework.py inventory / snapshot / media / resources / pool / graphics`；本次清扬30条按 `group_rework_apply.py plan → apply --stage pilot --dry-run → apply → status / review / media`，实际看图后 `accept`，余量 `--stage rest`；`finish`回读原分组和每条当前输出。助手不能替用户审批新脚本/新AI。 |
| 不够数 | `clipo_batch.py topup --dry-run` → `topup` → `poll`（缺几条补几条） |
| 生成失败 | `clipo_batch.py submit --retry-failed`（在原任务上继续） |
| 渲染临时失败 | `clipo_batch.py rerender --brief <id>` → `poll` |
| 改了平台审核提示词 | 先停 `poll`，再 `clipo_batch.py rereview`（不重新生成） |
| 去花字 / 换禁用源片 | `clipo_batch.py restyle --remove-effects --replace-banned --dry-run` |
| 开头重复（新生成的渲染前已自动错开） | `clipo_batch.py spread-hooks`；已交付的用 `footage_qc.py repair` 只换第 1 段 → `refresh-group --map <目录>` |
| 补右下角 AI 角标 | `footage_qc.py watermark --cut <id>… --out <目录> --dry-run` → 去掉 dry-run 加 `--wait` → `refresh-group` |
| 已交付分组换 AIGC 开头并调音 | `opener_refresh.py prepare` → `apply --dry-run` → `apply` → `footage_qc.py review` → `refresh-group` → `opener_refresh.py download` |
| 定点修订续跑 | `footage_qc.py repair --resume-backup <原备份>`；`review --out <收据目录> --wait` |
| 无完整 state 的修订成片交付 | `delivery_share.py share / pack / batch --plan <清单> --out <收据目录>` |
| 合并或替换分享 | `delivery_share.py collect / replace` |
| 手建分组改名 | `clipo_batch.py rename-shares --group <链接> --dry-run` |
| 按店铺拆组 | `clipo_batch.py split-group --group <链接> [--store Cyber] --dry-run` |
| 两号九组断点恢复 | `batch_completion.py status / continue / poll-generations / prepare / poll-reviews` |
| 力士透明特效 | `effects.py inventory → plan → register → apply（试样）→ poll → download → accept → batch` |
| 画面标签重钉 | `clipo_batch.py sync-visuals`（先备份） |

用户说「我来检查」「不用检查」时，在 brief 或 state 顶层写 `content_review: {mode: "user", instruction: "原话", by: "用户"}`。这条记录只豁免平台内容审核，不伪造通过。

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
