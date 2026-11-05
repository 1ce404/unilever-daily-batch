---
name: unilever-daily-batch
description: 联合利华（清扬、力士）千川混剪视频每日批量生产的流程串联。按飞书排期表（力士没有排期表，按每天固定条数）取当天任务 → 每 10 条一个 brief，先为每条确定说话立场、核心疑问和说服路径，再让 Clipo Remix 写完整口播并分段 → 整篇审读与机器 QC → 人审 → 审过的文案一次多生成、渲染、AI 审核（画面、配音、BGM、字幕、贴图、音效由平台产品提示词决定）→ 每个 brief 一个分享链接，不够数就补。用户说「跑今天的排期」「清扬今天的脚本」「力士今天的」「做 11.4 的」「1030 的 brief」「审完了，开始生产」「看下今天生产结果」「今天到哪了」「联合利华批量视频」时使用。
---

# 联合利华每日批量混剪

排期上每一行 = 店铺 × 内容方向 × 测试卖点 × 测试人群 × 数量。**每 10 条一个 brief，一个 brief = 一条底稿**。Clipo 按底稿每段都改写出多种说法（CTA 也改写）；平台 AI 审核通过的才算可交付。

**这个 skill 组织文案创作与批量生产流程。** 平台上只走一条路：**AI 建脚本 → 脚本批量生成混剪 → 批量渲染 → 批量 AI 审核**。画面、配音、BGM、字幕、贴图、音效、通用改写和审核标准由平台产品提示词决定；本条文案思路与承接要求由 skill 补充。产品提示词包括（`video_script_create` / `video_script_rewrite` / `video_cut_review`），要改去平台改。每一步用哪个接口见 [references/clipo-api.md](references/clipo-api.md)。

四条设计：

- **先定整篇思路，平台写全文再分段。** 每条 brief 先写 `creative_plan`：谁在说、回答哪个疑问、为什么推荐、论点如何推进、CTA 如何接上。不同底稿要有不同说服路径，不能只轮换钩子。完整格式、范本使用和审读见 [writing.md](references/writing.md)。产品事实与画面约束保留。
- **人只审底稿，必须人审。** lint 查底稿，`poll` 查每条改写，平台 AI 审核查成片
- **文案的事实只有一个来源**：`config/<brand>.json`（品牌共用）+ `config/<brand>/<产品>.json`（每个产品）。产品规则变了只改那个产品的文件
- **按可交付数交付。** 通过率约 40%，一次生成排期的 2 倍，不够再补，直到每个 brief 够数；每个 brief 交付一个分享链接

不知道做到哪了，先跑 `status`，它会给出下一步（离线）。

```
① 拉资料 → ② 拆 brief → ③ 平台体检 → ④ 创作规划 + 可选热点 + 平台写全文 → ⑤ 整篇审读 + lint
   → ⑥ 待审 ⏸ → ⑦ 记录审核 → ⑧ 生成 + 渲染 + AI 审核（不够就补） → ⑨ 交付清单 + 分享链接
```

在技能目录下跑 `python3 -I scripts/<x>.py`。`$R` = 当前项目目录下的 `runs/YYYYMMDD`（运行数据放在 skill 安装目录外），`$C` = 品牌：`qingyang` 或 `lux`。

**每天两个品牌各跑一遍**，共用同一个 `$R`：清扬 brief 编号 `MMDD-Bxx`，力士 `MMDD-Lxx`，脚本按品牌只读自己的 brief；力士的待审文档、报告、交付清单文件名带 `-lux`。

| 品牌 | 产品（`config/<品牌>/<key>.json`） | 每天多少条 |
|---|---|---|
| 清扬 `qingyang` | `2x` 控油蓬松洗发水、`huxi` 控油呼吸瓶、`selenium` 二硫化硒蓝C瓶 | 看飞书排期表 |
| 力士 `lux` | `jingyou` 精油香氛洗发水、`mist` 小香瓶 | 没有排期表，固定：精油香氛 40、小香瓶 10（`config/lux.json` 的 `daily_quota`） |

| 文件 | 用途 |
|---|---|
| [references/writing.md](references/writing.md) | ④ 怎么查热点、创作要求里写什么、人审看什么、硬性要求、brief 格式 |
| `gold/<品牌>.json` | 范本库，每条标了 `product`。确认过的（`"status": "confirmed"`）塞进创作要求当参考，lint 拿全部范本查照抄。用户说好的脚本加进来标 `confirmed` |
| `config/<品牌>.json` | 品牌共用：资料来源、店铺→产品（或 `daily_quota` 固定条数）、禁用词、文案长度、生成数量、`open_questions` 待确认项 |
| `config/<品牌>/<产品>.json` | 每个产品一份：产品名、平台 id、卖点说法、机制、CTA、卡审词、串系列词、受众、素材画面、固定要求 `rules` |
| [references/clipo-api.md](references/clipo-api.md) | 平台接口：建脚本、批量生成、批量渲染、批量审核、体检，每个接口的参数和状态；踩过的坑 |
| [references/qc-rules.md](references/qc-rules.md) | QC 文档 → config 的对应；驳回怎么处理 |
| [references/new-brand.md](references/new-brand.md) | 接入力士 / 多芬 / HC 等新品牌 |

## 每日流程

**① 拉资料** `fetch_sources.py --config $C --run $R`
QC 文档和昨天比有变化，读清扬段：新卡审词、新机制先改 config（改机制更新 `verified_on`）。

**② 拆 brief** `parse_schedule.py --config $C --run $R --date 2026-11-04`
清扬：核对打印的表，店铺都映射到产品；找不到当天的行就停，告诉用户排期没更新。力士：按 `daily_quota` 直接拆，不读排期。用户只要某个产品时，其它产品的 brief 挪到 `briefs-other/`。

**③ 平台体检** `clipo_batch.py preflight --config $C --run $R`
查平台上每个产品：能不能打开、有没有可用素材、产品描述里机制对不对、有没有建脚本提示词、提示词里写的音效贴图分类平台上有没有、审核提示词的产品名对不对。有问题告诉用户去平台改，不自己改平台。

**④ 创作规划 + 平台写完整口播**
先读 [writing.md](references/writing.md)，为当天每条 brief 写 `creative_plan`，比较整组的观察、核心疑问和推理路径。热点只有服务本条观点时才用。`draft.py --config $C --run $R --dry-run` 检查要求与长度，再去掉 `--dry-run` 让平台写全文后分段。首次引入产品说全名，出现位置服从论点；不固定第 3 段，不把功能标签当作统一结构。用户给了结构则照用户的结构写。

**⑤ 整篇审读 + lint**
Agent 通读每条全文，再放在整组比较：每句是否承接并推进，产品推荐有无依据，CTA 是否接住购买理由，说话立场是否一致，是否只是其它稿换词。出现问题回平台修改完整文案后重读；不是每句加「所以/而且」就算连贯。用 `review_doc.py editorial --config $C --run $R --brief <id> --assessment <JSON>` 留下具体判断（格式见 writing.md）。这只记录作者自检，不代表用户通过。
再跑 `lint_copy.py --config $C --run $R`。新稿缺创作思路/整篇审读，或审读后文案改过，会被拦下。机器还查产品事实、禁用词和文字相似度；同产品相同声明路径会报错，但机器不能识别所有语义相同的结构，也不能证明有活人感。错误清零才能交人审。

**⑥ 待审 ⏸** `review_doc.py render --config $C --run $R` → `review.md` / `review.xml`
把每条脚本完整发给用户看（每条展示创作思路、完整口播/分段、热点和最像的旧稿；相似度只用于辅助判断）。要发飞书：`lark-cli docs +create --content @$R/review.xml`，**第一次往飞书写、往群里发之前先问用户**放哪。**然后停下等审核**。

**⑦ 记录审核**
- 用户在对话里说通过：`review_doc.py approve --brief <id> --by 用户 --note "<原话>"`
- 飞书审完：`review_doc.py sync --doc <链接>`，审核人改过的句子记进 `learned/`

**只有这两条路能标通过**，都会记下当时的文案。手改 `status`、审过后又改文案，`submit` 都会拦下，要重新给人看。

**⑧ 生产**
```bash
python3 -I scripts/clipo_batch.py submit --config $C --run $R --dry-run   # 报总条数给用户确认（消耗额度）
python3 -I scripts/clipo_batch.py submit --config $C --run $R
python3 -I scripts/clipo_batch.py poll   --config $C --run $R --wait 3600  # 后台跑
```
- **同步审过的文案**：脚本是 ④ 平台写的那份（`script_id`）。`submit` 把人审后的每段文案 `PUT /video-scripts/{id}` 写回去（画面、贴图、音效不动），每段都开 AI 改写，把本条说话立场和承接要求加入该脚本的 `vo_rewrite_prompt`（不改产品共享提示词），再读回核对文案及要求，对不上不生成
- **批量生成**：`POST /video-cut-generations`；**批量渲染**：`poll` 给每条剪辑 `POST /render-jobs`；**批量审核**：`poll` 把渲染好的一批 `POST /video-cuts/review`
- 每个 brief 生成 排期 × `generate_factor`（2）条，拆成每轮 ≤ `generate_chunk`（15）条提交
- 某轮失败（平台改写模型 502/503、组装超时）：等一会儿 `submit --retry-failed`，失败的轮不扣额度
- 跑完有 brief 不够数：`topup --dry-run` 报补多少，用户确认后 `topup`，再 `poll`。用同一份审过的脚本，不用重审

**⑨ 交付**
```bash
python3 -I scripts/clipo_batch.py report --config $C --run $R              # report.md（每条结果和驳回原因）+ delivery.md（交付清单）
python3 -I scripts/clipo_batch.py share  --config $C --run $R --dry-run    # 看哪些 brief 要发分享
python3 -I scripts/clipo_batch.py share  --config $C --run $R              # 每个够数的 brief 一个分享链接，当天的收进一个分组
```
- 每个 brief 挑够排期条数的可交付视频，建一个平台分享链接（`/share/{id}`）；当天这个品牌的所有分享收进一个分组（`/share/group/{id}`），一个链接看全部
- 不够数的 brief 默认不发，要先发加 `--partial`；补生成后挑的视频变了，再跑 `share` 会换成新链接（分组里自动换掉，旧链接删除）
- 链接写进 `delivery.md`，每个 brief 一行

回复用户：每个 brief 可交付 / 排期条数和分享链接、当天的分组链接、驳回原因分类、没生产的、要人决定的事。有 brief 不够数就不说「全部够数」。

## 铁律

1. **脚本必须人审**：「直接生成」不等于审过，先给人看脚本；不填审核结论
2. **提交前报条数**：每次 submit / topup 都先 dry-run，告诉用户要生成多少条，确认后再提交
3. **整篇成立才交审**：全文先通，再拆段；同组的疑问与说服路径要有区别。相似度低不代表结构不同，不编亲测或他人背书。
4. **事实只来自 config 和当天资料**；热梗只借表达，不编数据；每段都开 AI 改写，改写违规（丢机制、丢 CTA 词、出禁用词）一律当驳回
5. **不替人改共享的东西**：不改客户共享文档（含「Y26-联合利华脚本合集」），不改 / 删平台上的产品、素材、分类和产品共享提示词；本条脚本的改写要求可随人审后的文案一起同步。片子的样子要变，告诉用户去改平台提示词
6. **学到的写回去**：卡审词、机制、素材画面、产品的写法要求进 config（下次自动进创作要求）；用户说好的脚本进 `gold/`；平台那边的问题（素材、提示词）告诉用户；不只留在聊天里
