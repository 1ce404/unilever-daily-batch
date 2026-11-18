---
name: unilever-daily-batch
description: 联合利华（清扬、力士、AHC、HC、多芬、力士沐浴）千川 / 逛逛混剪视频批量生产。按排期或指定条数拆 brief → Agent 照团队写法写底稿 → 机器 QC → 人审 → 审过的文案交给 Clipo Remix 建脚本、批量生成、渲染、AI 审核 → 每 10 条一个分享链接并下载到本地，不够数就补。用户说「跑今天的排期」「清扬今天的脚本」「力士今天的」「做 11.4 的」「审完了，开始生产」「看下今天生产结果」「今天到哪了」「联合利华批量视频」时使用。
---

# 联合利华批量混剪

**文案 Agent 写，片子平台剪。** 平台只走一条路：AI 建脚本 → 批量生成 → 批量渲染 → 批量 AI 审核。画面、配音、BGM、字幕、贴图、音效由平台产品提示词决定；文案的事实只来自 `config/`。接口见 [references/clipo-api.md](references/clipo-api.md)。

用户问「今天做哪些」「跑今天的排期」：先跑 ⓪ `overview.py`，报各品线今天 / 本周的条数，**只报内容，不接着写底稿**；用户说写再写。

不知道做到哪了，先跑 `status`（离线），它给出下一步。

```
⓪ 总排期 → ① 拉资料 → ② 拆 brief → ③ 平台体检 → ④ 写底稿 → ⑤ lint → ⑥ 待审 ⏸ → ⑦ 记录审核
  → ⑧ 生产（先 demo 的品牌多一轮 ⏸）→ ⑨ 交付：分享链接 + 本地文件夹
```

在技能目录下跑 `python3 -I scripts/<x>.py`。`$R` = 项目目录下的 `runs/YYYYMMDD`，`$C` = 品牌 key。多个品牌共用同一个 `$R`，每个品牌只读自己的 brief，产出文件名带品牌后缀（清扬不带）。

## 品牌

| 品牌 `$C` | brief 编号 | 产品（`config/<品牌>/<key>.json`） | 条数从哪来 | 先出 demo |
|---|---|---|---|---|
| 清扬 `qingyang` | `MMDD-Bxx` | `2x` 控油蓬松、`huxi` 控油呼吸瓶、`selenium` 二硫化硒 | 飞书排期表（按天） | 否 |
| 力士 `lux` | `Lxx` | `jingyou` 精油香氛洗发水、`spray` 护发精油喷雾、`mist` 小香瓶 | `daily_quota` | 否 |
| AHC `ahc` | `Axx` | `shuiguang` 超水光水乳、`bandage` 黄金绷带面膜 | 飞书排期表（按周，品类 × 结构，每个结构 15 条） | 否 |
| HC `hc` | `Hxx` | 奥妙 9 个 `omo_*`、金纺 12 个 `cf_*` | 每批问用户，填 `daily_quota` | 是 |
| 多芬 `dove` | `Dxx` | 磨砂膏、双重精华、海盐白柚、泡泡沐浴露、小金碗发膜、深层修护洗发水、鎏金千金瓶（13 个） | 每批问用户 | 是 |
| 力士沐浴 `luxpc` | `Pxx` | 精油香氛沐浴露 3 香型、柑橘香氛磨砂沐浴露 4 款 | 每批问用户 | 是 |

每个品牌 config 的 `open_questions` 是待用户确认的事，开工前看一眼。

| 文件 | 内容 |
|---|---|
| `config/lines.json` | 总排期的品线（clear、PC-lux、HC-MKT…）→ skill 的品牌和产品、每条品线的脚本文档、没切的素材网盘链接 |
| `config/<品牌>.json` | 品牌共用：资料来源、条数、禁用词、命名、音色、水印、`structures`（AHC 结构） |
| `config/<品牌>/<产品>.json` | 产品名、平台 id、卖点 `say`、机制、CTA、禁用词、串系列词、素材标签、写法要求 `rules` |
| `gold/<品牌>.json` | 客户认可过的范本稿，任务卡按产品和方向列出，lint 拿来查开头撞车 |
| [references/writing.md](references/writing.md) | 写底稿：骨架、开头、每句几个说法、硬性要求、热梗、素材池 |
| [references/aigc-standards.md](references/aigc-standards.md) | **AIGC 素材标准，按品牌 / 产品分节**：QC 的 AIGC 规定、人设、禁用画面、提示词怎么写。生成前对照；AIGC 素材的新问题、新要求都更新在这里 |
| [references/aigc-production.md](references/aigc-production.md) | 人物 AIGC 的生成流程：生成计划、逐条绑定、生成失败怎么办 |
| [references/qc-rules.md](references/qc-rules.md) | QC 规则放 skill 还是放平台；驳回怎么处理 |
| [references/new-brand.md](references/new-brand.md) | 接入新品牌 / 新产品 |

## 流程

**⓪ 总排期** `overview.py --run $R --date YYYY-MM-DD [--refetch]`：读联合利华总排期（`config/lines.json` 的 `master_schedule`），列出每条品线今天 / 本周多少条、对应哪个品牌、谁负责。按天的品线（clear、LUX）今天的条数照表；跨 5 天写的是整周总数，哪天做、做哪些产品看品线文档 `line_doc`。标 ⚠️ 的品线 skill 还没接，告诉用户。

**① 拉资料** `fetch_sources.py --config $C --run $R`：排期、脚本合集、QC 文档。QC 有新卡审词、新机制，先改 config。

**② 拆 brief** `parse_schedule.py --config $C --run $R --date YYYY-MM-DD`：每 `brief_size` 条一个 brief。清扬按天找排期行；AHC 找这一天所在的那一周，每个结构一个 brief，结构写进 brief 的 `template`；力士按 `daily_quota`；HC / 多芬 / 力士沐浴先问用户做哪些产品、各多少条。找不到当天的行就停，告诉用户排期没更新。

**③ 平台体检** `clipo_batch.py preflight --config $C --run $R`：产品能不能打开、可用素材、风险素材、普通标签里有没有混进 AI 素材、提示词是不是自定义、提示词里的分类平台上有没有、审核提示词的产品名。有 ❌ 告诉用户去平台改。

**④ 写底稿** `brief_card.py --config $C --run $R [--brief <id>]`：每个 brief 一张任务卡（方向、卖点、结构、产品要求、素材画面、禁用词、范本、团队交付过的稿、已用过的开头）。照卡写进 brief 的 `segments`（每段 `role` / `title` / `text`，`alt` 写 1~2 个别的说法）。写法见 [writing.md](references/writing.md)。某段要固定一类画面，用素材池：`clipo_batch.py pool`。

**⑤ lint** `lint_copy.py --config $C --run $R`：错误清零才给人审。

**⑥ 待审 ⏸** `review_doc.py render --config $C --run $R`：把每条完整脚本给用户看，然后停下等审核。第一次往飞书写、往群里发之前先问放哪。

**⑦ 记录审核**：用户在对话里说通过 → `review_doc.py approve --brief <id> --by 用户 --note "<原话>"`；飞书审完 → `review_doc.py sync --doc <链接>`。只有这两条路能标通过；审过后又改文案，`submit` 会拦下。

**⑧ 生产**
```bash
python3 -I scripts/clipo_batch.py submit --config $C --run $R --dry-run   # 报条数，用户确认
python3 -I scripts/clipo_batch.py submit --config $C --run $R
nohup python3 -I scripts/clipo_batch.py poll --config $C --run $R --wait 3600 > $R/clipo/poll-$C.log 2>&1 &
```
- `submit` 把审过的文案逐字交给平台建脚本，每段开 AI 改写，把备选说法和禁用词写进这条脚本的改写要求，读回核对；再生成 排期 × 2 条。`poll` 放后台跑，这一轮对话就结束，不在对话里等
- **先出 demo 的品牌**（config `demo_first`）：第一次 `submit` 每个脚本只出 1 条 → `poll` 跑完 → `share --partial` 发给用户看 → 用户说可以 → `review_doc.py approve-demo --brief <id> --by <人> --note "<原话>"` → 再 `submit --dry-run` / `submit` 量产（同一个平台任务接着生成，demo 算一条）。demo 没审过不量产、不补
- 不够数：`topup --dry-run` 报补多少，确认后 `topup`，再 `poll`
- 某轮失败：`submit --retry-failed` 在同一任务上继续
- 改了平台审核提示词或放宽了文案规则：先停 `poll`，再 `rereview` 重新送审，不重新生成
- 可交付 = 平台 AI 审核通过 + 改写没违规 + 音色稳定 + 有水印 + 没用禁用画面。用户点名放行的写 `review_waived`（原话）；用户说「我来检查」「不用检查」时记 `content_review: {mode: "user", instruction: "原话", by: "用户"}`，跳过内容审核，不伪造通过
- 音色只用 config `tts` 里的；`voice_check.py` 测段间音高差，≥1.35 不交付（要 numpy + ffmpeg，不加 -I 跑）

**⑨ 交付**
```bash
python3 -I scripts/clipo_batch.py report --config $C --run $R                # 每条结果、驳回原因、交付清单
python3 -I scripts/clipo_batch.py share  --config $C --run $R --download     # 每 10 条一个链接，当天收进一个分组，并下载到本地
```
- 链接名按 config 的 `delivery_naming`，分组名按 `delivery_group_naming`
- 按小 brief 生产、要合成 10 条一个链接的，brief 写 `"pack": true`
- 不够数的不发，要先发加 `--partial`；挑的视频变了再跑 `share` 会换新链接
- 本地文件夹默认在项目目录 `本地交付/YYYYMMDD-品牌/`，`--out` 改位置；`download` 只补缺的，写 `下载清单.json`

回复用户：每个 brief 可交付 / 排期条数、驳回原因分类、要用户决定的事、本地目录和条数；最后一段能直接复制的链接清单（代码块，分组链接在前，每行「链接名：链接」）。有 brief 不够数就不说「全部够数」。

## 铁律

1. **脚本必须人审**：没有用户原话的通过，不提交；不自己标通过
2. **提交前报条数**：新的生成、补生成先 dry-run 告诉用户条数；用户已授权的范围不重复问
3. **事实只来自 config 和当天资料**：不编亲测、背书、数据；改写违规一律当驳回
4. **不改共享的东西**：客户共享文档、平台上的产品 / 素材 / 分类 / 提示词，没有用户明确授权不改；授权了就改，改前备份、改后读回核对
5. **学到的写回去**：卡审词、机制、写法要求进 config；AIGC 素材的问题和要求进 `references/aigc-standards.md` 对应品牌那一节；用户认可的整条脚本进 `gold/`；不只留在聊天里
6. **交付默认下载到本地**：全部保存后才说本地完成
7. **默认不逐条看成片和素材**：只靠机器检查和少量抽查（每个 brief 最多 1 条）；用户说不用检查就连抽查也跳过
8. **平台操作只用 skill 的命令，不自己写代码调接口**：建链接只用 `share`，下载只用 `download`，看进度只用 `status` / `poll`，AI 素材只用 `aigc_gen.py`，改 AI 素材标签只用 `ai_tags.py`。命令做不到的，先改 skill 再用
