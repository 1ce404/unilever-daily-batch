# unilever-daily-batch

Claude Code skill：联合利华（清扬）千川混剪视频每日批量生产的流程串联。

```
飞书排期 → 每 10 条一个 brief 写底稿 → 机器检查 → 人审 → 审过的文案交给 Clipo Remix
→ 一次多生成、渲染、平台 AI 审核 → 凑够可交付条数 → 交付清单
```

这个 skill 只串流程。片子怎么剪（画面、配音、BGM、字幕、贴图、音效、改写规则）由 Clipo Remix 上每个产品的提示词决定。

## 覆盖的产品

| key | 产品 | 排期店铺 |
|---|---|---|
| `2x` | 清扬控油蓬松洗发水 | 清扬官方旗舰店 |
| `huxi` | 清扬控油呼吸瓶 | 洗护号 |
| `selenium` | 清扬二硫化硒蓝C瓶 | Cyber |

## 安装

放进 Claude Code 的 skills 目录：

```bash
git clone https://github.com/1ce404/unilever-daily-batch ~/.claude/skills/unilever-daily-batch
```

依赖：Python 3.9+（只用标准库）、[`lark-cli`](https://github.com/larksuite)（读飞书文档）、能访问 Clipo Remix。

## 用法

在 Claude Code 里说「跑今天的排期」「做 11.5 的清扬」「审完了，开始生产」「今天到哪了」。完整流程见 [SKILL.md](SKILL.md)。

## 目录

| 路径 | 内容 |
|---|---|
| `SKILL.md` | 入口：定位、九步流程、铁律 |
| `config/qingyang.json` | 品牌共用：资料来源、店铺→产品、禁用词、文案长度、生成数量 |
| `config/qingyang/<产品>.json` | 每个产品的卖点、机制、CTA、卡审词、素材画面、固定要求 |
| `gold/qingyang.json` | 范本库 |
| `scripts/` | 拉资料、拆排期、写稿辅助、lint、待审文档、Clipo 批量生产 |
| `references/` | 写文案、平台接口、QC 规则、接入新品牌 |

每天的运行数据写在 skill 目录外的 `runs/YYYYMMDD/`，不进仓库。
