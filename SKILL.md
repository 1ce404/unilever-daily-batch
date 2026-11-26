---
name: unilever-daily-batch
description: 联合利华（清扬、力士、AHC、HC、多芬、力士沐浴）千川 / 逛逛混剪视频批量生产。看排期 → 写脚本 → 人审 →（要 AIGC 素材时）生成素材 → 人审 → 每个脚本出 1 条 demo → 人审 → 量产 → 交付链接并下载。用户说「跑今天的排期」「清扬今天的脚本」「力士今天的」「做 11.4 的」「审完了，开始生产」「看下今天生产结果」「今天到哪了」「联合利华批量视频」时使用。
---

# 联合利华批量混剪

**文案由 Agent 写，成片由平台剪。** 音色只由每个产品当前的平台提示词决定；skill 不覆盖、不设黑白名单、不筛配音性别，保留音量检查和实际音色记录。文案里的事实只来自 `config/`。在技能目录下运行 `python3 -I scripts/<x>.py`。`$R` = `runs/YYYYMMDD`，`$C` = 品牌 key（`qingyang` `lux` `ahc` `hc` `dove` `luxpc`）。**查进度只用 `progress.py --config $C --run $R [--brief <id>]`**（离线，0.2 秒）：每个 brief 的合格 / 目标、在途、驳回原因、后台 poll 在不在跑，以及下一条可直接执行的命令。照它做；不另写代码读 state 或调接口查进度。

品牌速查：清扬 `MMDD-Bxx`（2x / huxi / selenium，飞书日排期）· 力士 `Lxx`（jingyou / spray / mist，`daily_quota`）· AHC `Axx`（周排期，每结构 15 条）· HC `Hxx` / 多芬 `Dxx` / 力士沐浴 `Pxx`（每批问用户）。产品配置在 `config/<品牌>/<key>.json`；开工前先看 `open_questions`。

## 开工版本与LUX自查

每次新开工先执行 `python3 -I scripts/sync_skill.py --apply`，在原安装目录更新，确认已与 GitHub main 同步后重新读本文件。不新增 skill 包，不创建旧版备份；旧版从 GitHub 历史恢复。检测到本地改动时停止，不直接覆盖。提交入口也检查版本，旧版、离线未核验版不默默生产。现有在途任务继续使用其已保存的生产版本，不能中途切换代码。

LUX批量先读 [客户制作与自查要求](references/lux-production-qc.md)，阈值只在 `config/quality/lux.json`。默认发布前回读真实配置、检查每条成片和整组差异；字幕单片统一不等于整批一种，素材/BGM/AI样片展示不等于已配置。默认用 `quality_contract.py audit`、`qc.py`、`media_qc.py` 留证据，未检/失败不写成通过。用户明确要求跳过额外QC直接出片时，按 [返工与直接交付](references/rework.md#用户接管检查或直接出片) 记录本次范围并导出，不把直接交付标成质量通过。

## 流程（用户 11-22）

```
① 看排期 → ② 写脚本 → ③ 人审 ⏸ → ④ AIGC 素材（要用才做）→ 人审 ⏸ → ⑤ 1 条 demo → 人审 ⏸ → ⑥ 量产 → ⑦ 交付
```

**① 看排期**
- `overview.py --run $R --date YYYY-MM-DD`：只报今天各品线条数，不接着写脚本。
- 用户要做时，依次跑 `fetch_sources.py --config $C --run $R` 和 `parse_schedule.py --config $C --run $R --date ...`，拆出 brief。
- HC、多芬、力士沐浴的产品和条数，先问用户。

**② 写脚本**
- 先跑 `brief_card.py --config $C --run $R`，照任务卡写 brief 的 `segments`：`role` / `title` / `text` / `alt` / `visual_tags`。任务卡现读平台提示词 `video_script_create`，终稿照它写。写法见 [writing.md](references/writing.md)。
- 每段都写 `visual_tags`，默认从标签下全部素材随机选片。用户明确指定才固定素材或限定候选池。产品名单独一句，配产品外观；成分另起一段，配质地。
- 热梗写进 `keep_terms`，否则 AI 改写会改掉。
- 写完跑 `lint_copy.py --config $C --run $R`，错误清零。

**③ 脚本人审 ⏸**
- 跑 `review_doc.py render`，把完整脚本给用户看，然后停下等审核。
- 用户通过后，跑 `review_doc.py approve --brief <id> --by 用户 --note "<原话>"`；飞书审完的，跑 `review_doc.py sync --doc <链接>`。

**④ AIGC 素材**（`visual_tags` 有「AI」开头标签时才做）
1. 写生成计划；标签统一「AI-MMDD-素材方向」（如 AI-1124-夫妻对话）。
2. 跑 `aigc_gen.py submit --dry-run` 报条数。
3. 跑 `aigc_gen.py submit`，再在后台跑 `aigc_gen.py poll`。
4. 抽帧给用户看 ⏸。
5. 用户通过后，跑 `review_doc.py approve-aigc --brief <id> --by 用户 --note "<原话>"`。

没有素材人审记录时，`submit` 不出 demo。标准见 [aigc-standards.md](references/aigc-standards.md)，流程见 [aigc-production.md](references/aigc-production.md)。

**⑤ demo ⏸**
1. 跑 `clipo_batch.py submit --dry-run`，再跑 `submit`：每个脚本只出 1 条。
2. 在后台跑 `poll`。LUX渲染完成后先 `qc.py`、`media_qc.py --all --config $C --run $R`，实际看图/看片并用 `media_qc.py --run $R --key <视频编号> --config $C --reviewer Agent --review-note "<已检查项与证据>"` 留当前版本复核记录，复跑QC；未经检查不分享demo。
3. 跑 `share --brief <id> --partial`，把 demo 发给用户。
4. 用户通过后，跑 `review_doc.py approve-demo --brief <id> --by 用户 --note "<原话>"`。

所有品牌都走 demo。只有用户明说「不用 demo」时，才在 brief 写 `"demo_first": false`，并记下原话。用户明确委托先做demo、稍后检查时，按 [单条demo制作许可](references/rework.md#先做demo稍后客户检查) 执行；brief保持待审，不能代填人审通过或据此量产。

**⑥ 量产**
1. 跑 `submit --dry-run` 报条数，再跑 `submit`：在 demo 同一个平台任务上续生成，补齐其余条数。
2. 在后台跑 `nohup ... poll --wait 3600 &`，按用户当前要求简报或持续跟进；不把启动后台任务当成交付完成。
3. 不够数时，跑 `topup --dry-run`，再跑 `topup`：缺几条补几条。

**⑦ 交付**
1. 默认跑 `qc.py --config $C --run $R`（交付闸门）；明确要求直接出片时按上述任务级授权执行。
2. 跑 `clipo_batch.py share --download`：每 10 条一个链接，当天收进一个分组，并下载到 `本地交付/YYYYMMDD-品牌/`。

回复用户：
- 每个 brief 的可交付数 / 排期数、驳回原因和本地目录。
- 最后放一个链接代码块：分组在前，每行写「链接名：链接」。

返工与交付修订命令见 [rework.md](references/rework.md)。

## 提速

- `submit` 并发建平台脚本（5 路）；`poll` 按 brief 并发读平台（8 路），素材索引只拉一次。
- **一次渲对**：渲染前把 BGM / 音效 / 箭头、去花字、禁用画面替换、右下角角标改进同一份草稿，一次 PUT、一次回读。
- **开头切片可复用**：可固定同一条，不要求素材数匹配交付条数，不自动换开头或因重复拦交付。开头文案差异、素材合规与画面匹配仍检查。只有用户要求换开头时才用 `spread-hooks`。
- 渲染前交付闸门（`qc`、禁用画面、语音指令、改写违规）不过的直接记 failed，不渲染、不送审，`topup` 补位。
- 只剩 ≤5 条渲染 / 审核时 10 秒轮询一次，其余 30 秒。
- demo 算在总数里，量产只补其余条数；只按 1 倍生成，缺几条补几条。
- 用户一次授权的范围不重复问。按品牌约定检查当前版本；完整render凭据未变时复用既有证据，只复核改动条目，具体范围见 [交付节奏与进度](references/rework.md#交付节奏与进度)。
- 后台 poll 在跑时，按用户当前要求持续跟进或简报；用户要求完成整批时不以后台已启动代替完成。回复给各组实数、阻塞点和下一步；已出片、检查通过、已分享、已下载分开报，不凭经验承诺完成时间。

## 铁律

1. **三道人审**：脚本人审、AIGC 素材人审、demo 人审。每道都要有用户原话，Agent 不自己标通过。
2. **花钱前报数**：`submit` / `topup` / `aigc_gen.py submit` 先 dry-run。代码里写死：没有一小时内条数一致的收据，就不发送。
3. **事实只来自 config 和当天资料**：AI 改写违规按驳回处理。
4. **不改共享配置和素材**：平台产品、素材、提示词与客户文档，未经授权不改；授权后，改前备份，改后回读。
5. **平台操作只用 skill 命令**：命令做不到时，先改 skill 再操作。不手改 `state.json`。
6. **一个品牌一天只由一个会话写入**。
7. **学到的规则写回去，只写一处**：卡审词进卡审库 `references/review-blocks/` 或 config，写法规则进 config，AIGC 规则进 aigc-standards，客户认可的整条脚本进 `gold/`。SKILL.md 只放流程和铁律，不放规则正文。

规则细节在 config，机器门禁在 `lint_copy.py` 与 `qc.py`，接口见 [clipo-api.md](references/clipo-api.md)，新品牌接入见 [new-brand.md](references/new-brand.md)。
