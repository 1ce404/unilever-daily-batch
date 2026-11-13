---
name: unilever-daily-batch
description: 联合利华（清扬、力士）千川混剪视频每日批量生产的流程串联。按飞书排期表（力士没有排期表，按每天固定条数）取当天任务 → 每 10 条一个 brief，Agent 照团队写法写底稿（方向定开头，卖点句和机制句用固定说法，每句 2~3 个说法）→ 机器 QC → 人审 → 审过的文案交给 Clipo Remix 建脚本（画面、配音、BGM、字幕、贴图、音效由平台产品提示词决定），一次多生成、渲染、AI 审核 → 每个 brief 一个分享链接，并按文件夹下载到本地，不够数就补。用户说「跑今天的排期」「清扬今天的脚本」「力士今天的」「做 11.4 的」「1030 的 brief」「审完了，开始生产」「看下今天生产结果」「今天到哪了」「联合利华批量视频」时使用。
---

# 联合利华每日批量混剪

排期上每一行 = 店铺 × 内容方向 × 测试卖点 × 测试人群 × 数量。**每 10 条一个 brief，一个 brief = 一条底稿**。Clipo 按底稿每段都改写出多种说法（CTA 也改写）；默认以平台 AI 审核通过作为可交付条件；用户明确自行检查时按该次指令记录审核豁免，不伪造通过结论。

**文案 Agent 自己写，片子平台剪。** 平台上只走一条路：**AI 建脚本 → 脚本批量生成混剪 → 批量渲染 → 批量 AI 审核**。通用包装由平台产品提示词决定（`video_script_create` / `video_script_rewrite` / `video_cut_review`）；用户授权的人物AIGC及本条画面修改按下述人物流程执行，不必改共享提示词；写文案时也要守平台提示词里的文案规则（每段 10~30 字、禁用词、只写正向描述），`brief_card.py` 会列出来。每一步用哪个接口见 [references/clipo-api.md](references/clipo-api.md)。

四条设计：

- **照团队的写法写。** 5~6 句，每句一个任务：开头 → 引产品 → 卖点 → 效果 → 机制 + CTA。内容方向和人群决定开头，开头每条新写；卖点句、机制句用 config 里的固定说法，可以跨 brief 复用；每句写 2~3 个说法给审核人挑、给平台改写当范围。详见 [writing.md](references/writing.md)
- **人只审底稿，必须人审。** lint 查底稿，`poll` 查每条改写，平台 AI 审核查成片
- **文案的事实只有一个来源**：`config/<brand>.json`（品牌共用）+ `config/<brand>/<产品>.json`（每个产品）。产品规则变了只改那个产品的文件
- **按可交付数交付。** 一次生成排期的 2 倍（11.6 实测通过率 80%~95%，小香瓶约 55%），不够再补，直到每个 brief 够数；每个 brief 交付一个分享链接并下载到本地文件夹，每天一个品牌一个分组

不知道做到哪了，先跑 `status`，它会给出下一步（离线）。

**人物AIGC、人物画面返工或批量素材差异**：先读 [人物AIGC生产与返工](references/aigc-production.md)。单条分镜1、2、4同人不同动作；不同脚本按各自口播设计场景动作；用户要求组内不同素材时，交付N条必须绑定N套独立人物素材，不能用3–6条素材池随机复用代替。生成、剪片、绑定、渲染逐套接续，已授权工作不反复问确认。

```
① 拉资料 → ② 拆 brief → ③ 平台体检 → ④ 写底稿 → ⑤ lint
   → ⑥ 待审 ⏸ → ⑦ 记录审核 → ⑧ 生成 + 渲染 + AI 审核（不够就补） → ⑨ 交付清单 + 分享链接 + 本地文件夹
```

在技能目录下跑 `python3 -I scripts/<x>.py`。`$R` = 当前项目目录下的 `runs/YYYYMMDD`（运行数据放在 skill 安装目录外），`$C` = 品牌：`qingyang` 或 `lux`。

**每天两个品牌各跑一遍**，共用同一个 `$R`：清扬 brief 编号 `MMDD-Bxx`，力士 `MMDD-Lxx`，脚本按品牌只读自己的 brief；力士的待审文档、报告、交付清单文件名带 `-lux`。

| 品牌 | 产品（`config/<品牌>/<key>.json`） | 每天多少条 |
|---|---|---|
| 清扬 `qingyang` | `2x` 控油蓬松洗发水、`huxi` 控油呼吸瓶、`selenium` 二硫化硒蓝C瓶 | 看飞书排期表 |
| 力士 `lux` | `jingyou` 精油香氛洗发水、`spray` 护发精油喷雾、`mist` 小香瓶 | 没有排期表，按 `config/lux.json` 的 `daily_quota`（11.9 起这周：精油香氛 40 + 护发精油喷雾 10，小香瓶暂停） |

| 文件 | 用途 |
|---|---|
| [references/writing.md](references/writing.md) | ④ 团队的写法：骨架、开头怎么跟方向、固定说法、每句几个说法、硬性要求 |
| `gold/<品牌>.json` | 范本库，每条标了 `product` 和 `direction`。`brief_card.py` 按产品和方向列出来当参考，lint 拿它查开头撞车。用户说好的脚本加进来标 `confirmed` |
| `config/<品牌>.json` | 品牌共用：资料来源、店铺→产品（或 `daily_quota` 固定条数）、禁用词、文案长度、生成数量、`open_questions` 待确认项 |
| `config/<品牌>/<产品>.json` | 每个产品一份：产品名、平台 id、卖点说法、机制、CTA、卡审词、串系列词、受众、素材画面、固定要求 `rules` |
| [references/aigc-production.md](references/aigc-production.md) | 人物设定、三层素材差异、逐条绑定、分类重试、自行检查模式 |
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

**④ 写底稿** `brief_card.py --config $C --run $R [--brief <id>]`
每个 brief 打一张任务卡：方向、卖点、人群、骨架（哪句跟方向变、哪句用固定说法）、产品要求、素材画面、禁用词、平台提示词里的文案规则、同产品同方向的范本和脚本合集里团队交付过的稿、审核人改过的句子、今天和近几天已经用过的开头。用户要结合热点时，先按 [writing.md](references/writing.md) 第 8 节查热梗写进 `$R/hotspots.json`（不用梗百科，每个梗要有热榜、新闻或年度流行语来源），任务卡会列出。Agent 照卡写，填进 brief 的 `segments`（每段 `role` / `title` / `text`，`alt` 写另外 1~2 个说法）。用户给了结构就照结构写，填 `template`。详见 [writing.md](references/writing.md)。

**传统切片的某一段要固定一类画面**（如爆款原片开头）**用同类素材池**（用户 11-13：只钉一条，10 条视频画面全一样）：`clipo_batch.py pool --config $C --run $R --product <key> --tags <标签> --query "<这类素材的共同点>" --brief <id> --seg <段 id>`，3~6 条同类素材，平台每条视频从里面随机挑。人物AIGC的三个分镜不能分别随机抽池；按人物流程逐条绑定素材组，见 [aigc-production.md](references/aigc-production.md)。素材不够时报告缺口，不以重复素材或少做条数代替用户要求。

**⑤ lint** `lint_copy.py --config $C --run $R`
错误清零才能给人审。查事实、合规、产品名位置、卖点、机制、CTA、短句长度（每句和每个备选说法都查），以及开头撞车：开头两句和范本 / 近 7 天 / 当天其它稿相似度超过 50%，或同一天第一句开头一样。卖点句、机制句复用不算。不查文风。

**⑥ 待审 ⏸** `review_doc.py render --config $C --run $R` → `review.md` / `review.xml`
把每条脚本完整发给用户看（每条展示完整口播、分段和每句的备选说法、开头最像的旧稿；相似度只用于辅助判断）。要发飞书：`lark-cli docs +create --content @$R/review.xml`，**第一次往飞书写、往群里发之前先问用户**放哪。**然后停下等审核**。

**⑦ 记录审核**
- 用户在对话里说通过：`review_doc.py approve --brief <id> --by 用户 --note "<原话>"`
- 飞书审完：`review_doc.py sync --doc <链接>`，审核人改过的句子记进 `learned/`

**只有这两条路能标通过**，都会记下当时的文案。手改 `status`、审过后又改文案，`submit` 都会拦下，要重新给人看。

**⑧ 生产**
```bash
python3 -I scripts/clipo_batch.py submit --config $C --run $R --scripts-only   # 只建平台脚本并核对，不生成、不扣额度（可先给用户看）
python3 -I scripts/clipo_batch.py submit --config $C --run $R --dry-run   # 报总条数给用户确认（消耗额度）
python3 -I scripts/clipo_batch.py submit --config $C --run $R
python3 -I scripts/clipo_batch.py poll   --config $C --run $R --wait 3600  # 后台跑
```
- **AI 建脚本**：`POST /video-script-generations` 把审过的文案逐字交给平台，平台按产品提示词配画面、配音、BGM、字幕、贴图、音效；建完按排期命名、每段开 AI 改写、把审过的备选说法写进这条脚本的改写要求（`vo_rewrite_prompt`），再读回核对每段一字不差，对不上就删掉不生成
- 用户明确自行检查时，在run的state顶层或该brief记录 `content_review: {mode: "user", instruction: "用户原话", by: "用户"}`。`poll` 跳过内容AI审核并记录 `user_review_pending`；继续生成、渲染与本地交付，不另起逐条看图、配音或内容审核。脚本原有批准、生成对应关系、水印配置及下载完整性仍保留。
- **批量生成**：`POST /video-cut-generations`；**批量渲染**：`poll` 给每条剪辑 `POST /render-jobs`；**批量审核**：`poll` 把渲染好的一批 `POST /video-cuts/review`
- 人物AIGC brief 写 `within_folder_material_diversity_required: true`，并准备 `visual_sets`。`poll` 把待绑定剪辑记为 `aigc_pending`，不会先渲染同一套默认素材；素材拆片登记后用 `bind-aigc` 绑定并提交渲染，详见人物流程。
- 每个 brief 生成 排期 × `generate_factor`（2）条。平台上一个 brief 一条生成任务，每轮 ≤ `generate_chunk`（15）条，第二轮起在同一条任务上继续生产
- **配音只用稳定音色**（用户 11-09/11-10：好几条听着像两个人在说）。平台每段单独合成配音，一部分音色段与段之间音高能差 35%~51%，听着就像换了人。建脚本时把音色换成 config `tts` 里实测稳定的那几个（力士女声；清扬按产品 `voice_gender`：2X、呼吸瓶男女声都用（用户 11-10：不能只是男声），二硫化硒女声），`bad_voices` 里的不用；改写要求里也写死了不加语音指令。成片抽到不稳音色或带语音指令的，不算可交付。新加音色先测再加。交付前可以用 `scripts/voice_check.py`（要 numpy + ffmpeg，不加 -I 跑）测每条成片的段间音高差，≥1.35 的在进度记录里标 `voice_drift`，不进链接
- 每条视频右下角都加「AI生成」合规水印（config 的 `watermark`，清扬、力士都开）
- 某轮失败：失败的轮不扣额度，`submit --retry-failed` 在同一条任务上继续。常见原因：平台改写模型 502/503（等一会儿重试）、组装超时（减小每轮条数）、`tts_failed` 音色配不出来（11.6 L07：平台建脚本抽到了坏音色，在平台脚本里删掉这个音色再重试，并提醒用户在音色库停用）
- 改了平台审核提示词，或放宽了 config 的文案规则：`rereview` 把被驳回的成片重新送 AI 审核、按当前规则重查改写，不重新生成。它和后台 `poll` 都写进度记录，先停 `poll` 再跑
- 跑完有 brief 不够数：`topup --dry-run` 报补多少，用户确认后 `topup`，再 `poll`。用同一份审过的脚本，不用重审

**⑨ 交付**
```bash
python3 -I scripts/clipo_batch.py report --config $C --run $R              # report.md（每条结果和驳回原因）+ delivery.md（交付清单）
python3 -I scripts/clipo_batch.py share  --config $C --run $R --dry-run    # 看哪些 brief 要发分享
python3 -I scripts/clipo_batch.py share  --config $C --run $R --download   # 每个够数的 brief 一个分享链接（10 条），当天的收进一个分组；建完下载到本地
python3 -I scripts/clipo_batch.py download --config $C --run $R            # 只下载 / 补下载（链接重建过、上次有失败时）
```
- **清扬命名（用户 11-10）**：`TZ-261109-双十一-tz-cryptic控油樱花1500g-AIGC视频-智能生产-原生种草-双十一福利-特赞-brief7-价格优惠(机制)|大促方向(货架、超市等)`。只有这几段变：日期 YYMMDD、产品段（呼吸瓶 `tz-cryptic控油樱花1500g`，控油蓬松 2X `tz-caliber2x960g`（11-11 客户改正），二硫化硒待定）、十大类型（按排期方向查 `direction_tags`）、第一句前几个字、视频方向，其余固定。这是链接名，要准；视频名无所谓（用户 11-10），平台自动加 `-序号`
- 力士：分享链接（交付文件夹）命名 `TZ-26-1106-LUX-精油香氛-brief1`（config 的 `delivery_naming`：年-日期-品牌-产品简称-brief 序号；清扬写「清扬」，如 `TZ-26-1106-清扬-控油蓬松-brief1`；brief 号按日期、按产品各自从 1 升序）。视频名是文件夹名加序号；当天的分组命名 `TZ-26-1106-LUX` / `TZ-26-1106-清扬`（`delivery_group_naming`）
- 每个 brief 挑够排期条数的可交付视频，建一个平台分享链接（`/share/{id}`）；当天这个品牌的所有分享收进一个分组（`/share/group/{id}`），一个链接看全部
- **交付永远是每个链接 10 条**（用户 11-11：力士 40 条精油香氛交 4 个 10 条、喷雾 1 个 10 条，一共 5 个链接）。一个产品拆成更小的 brief 生产时（比如一个爆款开头一个 brief，7/7/7/7/6/6），给这些 brief 写 `"pack": true`：`share` 把它们的可交付视频轮流混进 10 条一个的链接（每个链接都混着不同开头），按产品从 brief1 起命名，按 brief 建过的旧链接从分组里删掉
- 不够数的 brief 默认不发，要先发加 `--partial`；补生成后挑的视频变了，再跑 `share` 会换成新链接（分组里自动换掉，旧链接删除）
- 链接写进 `delivery.md`，每个 brief 一行

**默认本地交付（用户 11-12）**：本 skill 后续清扬、力士的成片交付，都在最终分享页创建后自动下载到本地，用户无需再次提出下载要求。用户明确指定其他交付方式时，按当次要求执行。

- **用 `share --download` 或 `download`，不要自己写 curl / 下载脚本**（换了 agent 结果也一样，也不会把对话占住）。它按分享页现取签名地址，每个分享页一个同名子文件夹，视频名 = 分享页名称-序号.mp4，和客户在分享页看到的顺序一致
- 位置：默认 `runs` 所在项目目录的 `本地交付/YYYYMMDD-品牌/`（不放进 skill 目录和 Git 仓库）；用户指定位置时加 `--out <目录>`
- 只下载缺的：本地已有、大小和平台一致的文件跳过；分享页重建过，文件夹里不在新链接里的旧视频会删掉。不为下载重新生成或渲染
- 只核对完整性和数量：非空、大小和平台一致、每个文件夹条数和分享页一致；写 `下载清单.json`（分享链接、文件夹、文件、字节数、失败项）
- 有失败时会列出具体文件，再跑一次 `download` 只补这些；没全部保存就不说本地已完成。完成后给用户本地绝对目录和各文件夹条数

回复用户：本地交付目录及已保存数量、每个 brief 可交付 / 排期条数、驳回原因分类、没生产的、要人决定的事；最后给一段**能直接复制**的链接清单（代码块，分组链接在前，每行「交付文件夹名：链接」）。有 brief 不够数就不说「全部够数」。

## 铁律

1. **脚本必须人审**：「直接生成」不等于审过，先给人看脚本；不填审核结论
2. **提交前报条数**：首次或新增预算/范围先dry-run并确认；用户已授权的数量、修改和继续生产按原授权执行，不重复索要同一确认
3. **事实只来自 config 和当天资料**；不编亲测或他人背书；每段都开 AI 改写，改写违规（丢机制、丢 CTA 词、出禁用词）一律当驳回
4. **共享数据按授权修改**：用户明确要求的平台删除或修改可直接按授权对象执行接口并核对返回；不以生成删除脚本替代实际操作。不涉及该授权的共享数据仍遵守以下限制：不改客户共享文档（含「Y26-联合利华脚本合集」），不改 / 删平台上的产品、素材、分类和产品共享提示词（本条脚本的改写要求只放审过的备选说法）。片子的样子要变，告诉用户去改平台提示词
5. **学到的写回去**：卡审词、机制、素材画面、产品的写法要求进 config（任务卡会列出来）；用户说好的脚本进 `gold/`；平台那边的问题（素材、提示词）告诉用户；不只留在聊天里
6. **交付默认下载到本地**：最终分享页与本地文件夹一起交付；只有下载集合全部保存后才报告本地完成，不再等用户另行要求下载。
7. **检查模式按当次指令**：默认只按现有机器检查并少量抽查，不逐条抽帧看图。用户明确“我来检查”“不用检查，直接给视频”时采用用户自行检查模式，跳过额外内容审核与抽查，继续生产交付；只确认任务状态、素材绑定及下载完整性，不声称内容全部检查通过。
