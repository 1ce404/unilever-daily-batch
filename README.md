# unilever-daily-batch

Agent skill：联合利华千川 / 逛逛混剪视频批量生产（清扬、力士、AHC、HC、多芬、力士沐浴）。

```
总排期（今天各品线多少条）→ 品牌排期 → 每 10 条一个 brief → Agent 照团队写法写底稿 → 机器检查 → 人审 → Clipo Remix 建脚本
→ 批量生成、渲染、AI 审核 → 凑够条数 → 每 10 条一个分享链接 + 本地文件夹
```

片子怎么剪（画面、配音、BGM、字幕、贴图、音效、审核标准）由 Clipo Remix 上每个产品的提示词决定；skill 管文案、流程和检查。

## 品牌和产品

| 品牌 | 产品 | 条数从哪来 | 先出 demo |
|---|---|---|---|
| 清扬 `qingyang` | 控油蓬松、控油呼吸瓶、二硫化硒 | 飞书排期（按天） | 否 |
| 力士 `lux` | 精油香氛洗发水、护发精油喷雾、小香瓶 | 每天固定条数 | 否 |
| AHC `ahc` | 超水光水乳、黄金绷带面膜 | 飞书排期（按周，按结构） | 否 |
| HC `hc` | 奥妙 9 个、金纺 12 个 | 每批问用户 | 是 |
| 多芬 `dove` | 磨砂膏、沐浴露、小金碗发膜、洗发水、鎏金千金瓶等 13 个 | 每批问用户 | 是 |
| 力士沐浴 `luxpc` | 精油香氛沐浴露、柑橘香氛磨砂沐浴露 7 个 | 每批问用户 | 是 |

## 安装

克隆到你的 agent 读取 skills 的目录：

```bash
git clone https://github.com/1ce404/unilever-daily-batch <你的 skills 目录>/unilever-daily-batch
```

入口是 `SKILL.md`，Claude Code、Codex、Cursor 等支持 skills 的 agent 都能用。更新：在这个目录里 `git pull`。

依赖：Python 3.9+（只用标准库）、[`lark-cli`](https://github.com/larksuite)（读飞书文档，要先登录）、能访问 Clipo Remix；测配音（`scripts/voice_check.py`）另需 ffmpeg 和 numpy。

## 用法

对 agent 说「跑今天的排期」「做 11.18 的清扬」「审完了，开始生产」「今天到哪了」。完整流程见 [SKILL.md](SKILL.md)。

## 目录

| 路径 | 内容 |
|---|---|
| `SKILL.md` | 入口：品牌、九步流程、铁律 |
| `config/lines.json` | 总排期里的品线对到哪个品牌 / 产品、品线脚本文档、没切的素材 |
| `config/<品牌>.json` | 品牌共用：资料来源、条数、禁用词、命名、音色 |
| `config/<品牌>/<产品>.json` | 每个产品的卖点、机制、CTA、禁用词、素材标签、写法要求 |
| `gold/<品牌>.json` | 范本稿 |
| `scripts/` | 看总排期、拉资料、拆排期、任务卡、lint、待审文档、Clipo 批量生产 / 分享 / 下载、AI 素材生成、AI 素材打标、分类复核与画面返工 |
| `references/` | 写法、平台接口、QC、AIGC 素材标准、人物 AIGC 流程、接入新品牌 |
| `tests/` | 单测：`python3 -I -m unittest discover -s tests` |

每天的运行数据写在 skill 目录外的 `runs/YYYYMMDD/`，不进仓库。
