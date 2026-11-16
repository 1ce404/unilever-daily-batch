# unilever-daily-batch

Agent skill：联合利华（清扬、力士）千川混剪视频每日批量生产的流程串联。

```
飞书排期 → 每 10 条一个 brief → Agent 照团队写法写底稿 → 机器检查 → 人审 → 审过的文案交给 Clipo Remix 建脚本
→ 一次多生成、渲染、平台 AI 审核 → 凑够可交付条数 → 每个 brief 一个分享链接 + 本地文件夹（各 10 条 MP4）
```

这个 skill 组织文案创作与批量流程，平台上只走一条路：AI 建脚本 → 脚本批量生成混剪 → 批量渲染 → 批量 AI 审核。片子怎么剪（画面、配音、BGM、字幕、贴图、音效、改写、审核标准）由 Clipo Remix 上每个产品的提示词决定；skill 补充每条文案的思路与承接要求。接口见 [references/clipo-api.md](references/clipo-api.md)。

## 覆盖的产品

| 品牌 | key | 产品 | 每天条数 |
|---|---|---|---|
| 清扬 | `2x` | 清扬控油蓬松洗发水 | 飞书排期 |
| 清扬 | `huxi` | 清扬控油呼吸瓶 | 飞书排期 |
| 清扬 | `selenium` | 清扬二硫化硒蓝C瓶 | 飞书排期 |
| 力士 | `jingyou` | 力士精油香氛洗发水 | 固定条数（这周 40） |
| 力士 | `spray` | 力士护发精油喷雾 | 固定条数（这周 10） |
| 力士 | `mist` | 力士小香瓶 | 这周暂停 |

## 安装

克隆到你的 agent 读取 skills 的目录，比如：

```bash
git clone https://github.com/1ce404/unilever-daily-batch <你的 skills 目录>/unilever-daily-batch
```

入口是 `SKILL.md`，支持 skills 的 agent（Claude Code、Codex、Cursor 等）都能用。

依赖：Python 3.9+（只用标准库）、[`lark-cli`](https://github.com/larksuite)（读飞书文档）、能访问 Clipo Remix；测配音（`scripts/voice_check.py`）另需 ffmpeg 和 numpy。

## 用法

对 agent 说「跑今天的排期」「做 11.5 的清扬」「审完了，开始生产」「今天到哪了」。完整流程见 [SKILL.md](SKILL.md)。

后续交付默认自动下载全部成片到本地（`clipo_batch.py share --download` / `download`）：每个分享页一个同名文件夹、各 10 条 MP4。未指定位置时保存到 `runs` 所在项目目录的 `本地交付/YYYYMMDD-品牌/`，回复同时包含分享链接和本地文件夹路径，无需再单独提出下载要求。

人物AIGC支持单条三镜同人不同动作、跨脚本场景动作差异及组内逐条独立素材。执行前读[人物生产与返工](references/aigc-production.md)；`bind-aigc`按visual_sets绑定并渲染，分享前自动核对素材来源和区间。用户自行检查模式可跳过内容AI审核。

## 目录

| 路径 | 内容 |
|---|---|
| `SKILL.md` | 入口：定位、九步流程、铁律 |
| `config/<品牌>.json` | 品牌共用：资料来源、店铺→产品或每日固定条数、禁用词、文案长度、生成数量 |
| `config/<品牌>/<产品>.json` | 每个产品的卖点、机制、CTA、卡审词、受众、素材画面、固定要求 |
| `gold/<品牌>.json` | 范本库 |
| `scripts/` | 拉资料、拆排期、写稿任务卡（`brief_card.py`）、lint、待审文档、Clipo 批量生产和分享 |
| `references/` | 团队写法、平台接口、QC 规则、接入新品牌 |

每天的运行数据写在 skill 目录外的 `runs/YYYYMMDD/`，不进仓库。
