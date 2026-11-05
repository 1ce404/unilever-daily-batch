# unilever-daily-batch

Agent skill：联合利华（清扬、力士）千川混剪视频每日批量生产的流程串联。

```
飞书排期 → 每 10 条一个 brief → 查热点，拼创作要求让 Clipo Remix 写底稿 → 机器检查 → 人审
→ 一次多生成、渲染、平台 AI 审核 → 凑够可交付条数 → 每个 brief 一个分享链接
```

这个 skill 只串流程，平台上只走一条路：AI 建脚本 → 脚本批量生成混剪 → 批量渲染 → 批量 AI 审核。片子怎么剪（画面、配音、BGM、字幕、贴图、音效、改写、审核标准）由 Clipo Remix 上每个产品的提示词决定。接口见 [references/clipo-api.md](references/clipo-api.md)。

## 覆盖的产品

| 品牌 | key | 产品 | 每天条数 |
|---|---|---|---|
| 清扬 | `2x` | 清扬控油蓬松洗发水 | 飞书排期 |
| 清扬 | `huxi` | 清扬控油呼吸瓶 | 飞书排期 |
| 清扬 | `selenium` | 清扬二硫化硒蓝C瓶 | 飞书排期 |
| 力士 | `jingyou` | 力士精油香氛洗发水 | 固定 40 |
| 力士 | `mist` | 力士小香瓶 | 固定 10 |

## 安装

克隆到你的 agent 读取 skills 的目录，比如：

```bash
git clone https://github.com/1ce404/unilever-daily-batch <你的 skills 目录>/unilever-daily-batch
```

入口是 `SKILL.md`，支持 skills 的 agent（Claude Code、Codex、Cursor 等）都能用。

依赖：Python 3.9+（只用标准库）、[`lark-cli`](https://github.com/larksuite)（读飞书文档）、能访问 Clipo Remix。

## 用法

对 agent 说「跑今天的排期」「做 11.5 的清扬」「审完了，开始生产」「今天到哪了」。完整流程见 [SKILL.md](SKILL.md)。

## 目录

| 路径 | 内容 |
|---|---|
| `SKILL.md` | 入口：定位、九步流程、铁律 |
| `config/<品牌>.json` | 品牌共用：资料来源、店铺→产品或每日固定条数、禁用词、文案长度、生成数量 |
| `config/<品牌>/<产品>.json` | 每个产品的卖点、机制、CTA、卡审词、受众、素材画面、固定要求 |
| `gold/<品牌>.json` | 范本库 |
| `scripts/` | 拉资料、拆排期、让平台写底稿（`draft.py`）、lint、待审文档、Clipo 批量生产和分享 |
| `references/` | 创作要求和热点、平台接口、QC 规则、接入新品牌 |

每天的运行数据写在 skill 目录外的 `runs/YYYYMMDD/`，不进仓库。
