# Clipo Remix 接口（第 ③④⑧⑨ 步）

Base `https://clipo-remix.tezign.com/api/v1`。所有响应都是信封 `{code, message, data}`，`code == "ok"` 才算成功。当前不需要鉴权。线上接口文档：`GET /api/v1/openapi.json`。

这个 skill 只走一条路：

```
AI 建脚本 → 脚本批量生成混剪 → 批量渲染 → 批量 AI 审核 → 每个 brief 一个分享链接
```

片子怎么剪（视频规格、配音、BGM、字幕、每段画面标签、贴图、音效、改写规则）都写在平台的产品提示词里，接口只负责把这条路跑通。文案由 Agent 写、人审，不让平台写。全部调用在 `scripts/clipo_batch.py`（`brief_card.py` 只读产品提示词）。

## 一、AI 建脚本（`submit`）

文案是 Agent 照团队写法写、人审过的。`submit` 把它逐字交给平台建脚本，平台按产品提示词 `video_script_create` 配画面、配音、BGM、字幕、贴图、音效。

| 接口 | 用途 |
|---|---|
| `POST /video-script-generations` | 建脚本。body：`product_id`、`requirement`（自由文本，≤ 2000 字）。skill 写的是：一共几段、每段逐字照抄不改不并不拆、每段都允许 AI 改写、其余按产品提示词配，后面逐段列出审过的文案 |
| `GET /video-script-generations/{id}` | 轮询。`status`：`pending` / `running` / `success` / `failed`；失败看 `error_code`（`ai_timeout`、`ai_call_failed`…），失败即终态，重试就是再提交一条。一条约 1~2 分钟 |
| `GET /video-scripts/{script_id}` | 读回脚本：`title`、`segments[]`（`title` 段名、`content` 口播、`ai_rewrite` 是否允许改写、`visual` 画面标签、`sticker`、`sound_effects`）、`config`（全局配置：配音、BGM、字幕）、`vo_rewrite_prompt`（这条脚本自己的改写要求） |
| `PUT /video-scripts/{script_id}` | 整份写回（`title`、`segments`、`config`、`vo_rewrite_prompt`） |
| `DELETE /video-scripts/{script_id}` | 删脚本 |

建完 `submit` 再做三件事，`PUT` 回去后 `GET` 核对：

- 标题按 config 的 `naming`（和排期一致，视频和分享页也用这个名字）
- 每段 `content` 换成审过的文案、`ai_rewrite = true`（用户 11-05 定：每段都改写，CTA 也改）；画面、贴图、音效、全局配置原样保留
- 审过的备选说法（brief 每段的 `alt`）写进这条脚本的 `vo_rewrite_prompt`（「# 用户要求」一节，平台改写提示词 `video_script_rewrite` 会优先照做），改写时在这些说法范围内换措辞。不改产品共享提示词

核对：段数一样、每段一字不差、每段都开了改写。对不上就 `DELETE` 掉这条脚本、不生成、报给用户。

## 二、脚本批量生成混剪（`submit` / `topup` / `poll`）

同一份脚本，平台每条视频按 `video_script_rewrite` 提示词改写一种说法（每段都开了改写；`poll` 会查改写后有没有丢机制、丢 CTA 词），画面、BGM、配音各自随机抽。

| 接口 | 用途 |
|---|---|
| `POST /video-cut-generations` | 提交生成。body：`script_id`、`quantity`（1~300）、`add_compliance_watermark`、`compliance_watermark_position`。skill 按 config 的 `watermark` 每次都带上：清扬、力士都是 `true` + `bottom_right`（右下角「AI生成」水印，用户 11-06 定） |
| `GET /video-cut-generations/{id}` | 轮询。`status`：`pending` / `running` / `success` / `failed`；`generated_count / quantity` 是进度；`success` 时 `cut_ids` 是这批剪辑 |
| `POST /video-cut-generations/{id}/continue` | 往同一个任务追加 `quantity` 条（要等上一轮跑完；水印沿用任务的设置）。skill 的第二轮起和补生成都用它，平台上一个 brief 只有一条任务 |
| `GET /video-cuts/{cut_id}` | 读一条剪辑：`draft`（整份时间轴，渲染要用）、`random_result.vo_rewrite`（这条改写后的口播）、`random_result.tts_speaker_id`（音色） |

要点：

- **一个 brief 在平台上只有一条生成任务**（用户 11-06：之前每轮单独提交，平台列表里一个脚本出现两行 10/10，看着奇怪）。第一轮 `POST`，后面的轮次和补生成都 `continue` 到这条任务上，`poll` 每落地一轮就接着提交下一轮
- **每一轮全有全无**：一条没组装成，这一轮一条剪辑都不落，也不扣额度，之前几轮的产出照旧保留。一轮最多 `generate_chunk`（15）条，超过的分几轮 `continue`；某轮失败用 `submit --retry-failed` 在同一条任务上继续
- **一次多生成**：每个 brief 生成 排期 × `generate_factor`（2）条，平台 AI 审核通过率约 40%，不够的用 `topup` 补
- `poll` 读回每条的 `random_result.vo_rewrite`，用 lint 再查一遍，违规的记进 `copy_issues`，按驳回处理
- 失败原因：`voiceover_rewrite_failed`（平台改写模型 502/503，过一会儿 `submit --retry-failed`）、`timeout`（一轮组装超过 600 秒，减小每轮条数）

## 三、批量渲染（`poll`）

生成出来的剪辑**不会自动渲染**，要自己提交。

| 接口 | 用途 |
|---|---|
| `POST /render-jobs` | 提交渲染。body：`spec`（剪辑的 `draft` 原样）、`options`（`{codec: "h264", quality: "high", scale: 1}`）、`cut_id`（成片字段回写到这条剪辑）、`idempotency_key`（skill 用 `udb-<cut_id>`，重复提交拿到同一条任务，不重复渲染也不重复计费） |
| `GET /render-jobs/{job_id}` | 轮询。`status`：`queued` / `running` / `success` / `failed` / `canceled`；`success` 时 `object_key`、`download_url`（现签） |
| `GET /render-jobs/capability` | 服务端渲染是否可用；同时最多约 100 条在渲，超出的排队，提交不受限 |

## 四、批量 AI 审核（`poll`）

| 接口 | 用途 |
|---|---|
| `POST /video-cuts/review` | 提交审核。body：`cut_ids`（一批）。返回 202，`queued` 实际排进去的条数、`skipped` 跳过的（没渲染成功、已在排队）。按产品提示词 `video_cut_review` 审 |
| `GET /video-cuts/{cut_id}` | 读结论：`ai_review_status`（`success` / `failed` 是审完了）、`ai_review_result`（`pass` / `reject`）、`ai_review_reason`（驳回原因） |

`poll` 每轮把这一轮刚渲染成功的剪辑攒成一批提交，已经出过结论的再提交会覆盖旧结论。

**可交付 = `ai_review_result == "pass"` 且 lint 没查出改写违规。** `report` 按每个 brief 的排期条数挑可交付的进交付清单。

## 五、交付分享链接（`share`）

每个 brief 一个分享页，当天一个品牌的分享收进一个分组。

| 接口 | 用途 |
|---|---|
| `POST /video-share-groups` | 建当天的分组。body：`name`（如 `1105-清扬`）。分组页 `https://clipo-remix.tezign.com/share/group/{id}` |
| `POST /video-shares` | 建一个 brief 的分享。body：`title`、`video_name_prefix`（视频名 = 前缀 + 序号）、`group_id`（同时收进分组）、`script_id`、`product_id`、`videos[]`（每条 `video_object_key` 用剪辑的 `output_object_key`、`cover_object_key` 从 `draft.cover_url` 取、`duration_seconds`、`width`、`height`）。分享页 `https://clipo-remix.tezign.com/share/{id}` |
| `GET /video-shares/{id}` | 读分享：`videos[].video_url` / `cover_url` 是现签地址 |
| `GET /video-share-groups/{id}`、`PATCH /video-share-groups/{id}` | 读分组；改分组的分享清单（`video_share_ids` 整组替换） |
| `DELETE /video-shares/{id}` | 撤回分享，旧链接从此 404，成片和剪辑不受影响 |

补生成后某个 brief 挑的视频变了，`share` 会建新分享、在分组里替换掉旧的、再删掉旧分享。

## 六、平台体检用到的（`preflight`）

只读，不改平台。

| 接口 | 用途 |
|---|---|
| `GET /products/{id}` | 产品能不能打开、名称、`description`（AI 审核会读它，机制过时会误判）、`tags` |
| `GET /products/{id}/segments?skip=&limit=100` | 素材切片：可用多少、风险多少（`review_result == "reject"`）、各标签多少条 |
| `GET /products/{id}/prompts` | 产品提示词：`video_script_create`（建脚本）、`video_script_rewrite`（改写）、`video_cut_review`（审核）是否自定义、更新时间 |
| `GET /resource/sticker/catalog`、`GET /resource/sound-effect/catalog` | 贴图 / 音效分类，核对提示词里写的分类名平台上还在（不在时平台不报错、直接不配） |

## 已踩的坑

| 现象 | 处理 |
|---|---|
| `GET /products` 列表里有，但 `GET /products/{id}` 回 `product_not_found` | 产品已被删或迁移，体检会拦下，让用户给新 id |
| 产品某个标签切片数为 0 | 平台提示词让某段用这个标签时画面会落空。体检会列出来，告诉用户补素材或改提示词 |
| 改写后出现禁用词 / 丢产品名 | `poll` 记成「改写违规」。同类多了，告诉用户把词加进平台改写提示词 `video_script_rewrite`，同时补进 config 的 `banned_words` |
| 成片音效乱（幽灵声、「队友脸面已永久掉线」） | 大分类里混着怪声，平台随机抽到（11.02 驳回过 3 条）。提示词里只用小分类 |
| 成片没有音效 / 贴图 | 提示词里的分类名平台上没有（11.03，半角 / 和全角 ／ 也算不一样）。体检会查 |
| 风险切片仍被抽到 | 平台生成不避开风险切片，要在平台删除或去标签 |

## 平台上改素材、提示词（不在每日流程里）

以下都会改动团队共用的数据，**先说清楚要改什么，用户同意再做**：

- 切片改标签 `PATCH /materials/segments/{segment_id}`，人工复核 `POST /materials/segments/{segment_id}/review`
- 别的产品的切片复制过来 `POST /products/{id}/segments/copy`
- 产品描述 / 标签 `PATCH /products/{id}`；提示词 `PUT /products/{id}/prompts/{prompt_type}`


## 人物AIGC逐条绑定

人物三镜一致、组内独立素材按[aigc-production.md](aigc-production.md)执行。素材生成使用POST /material-generations与GET /material-generations/{id}，参数以当前OpenAPI为准。成功源素材剪片后，通过POST /storage/upload-tokens上传、POST /products/{id}/segments登记；写入每条visual_sets，使用clipo_batch.py bind-aigc。

bind-aigc只替换1/2/4主轨道人物窗口，PUT后GET完整现签draft提交render，不重新生成已成功的源素材。修改素材或修复失败渲染需新render_revision；同一素材组/修订的幂等键不重复提交。share会核对每条draft绑定及组内/跨脚本来源，不逐条看帧。用户自行检查模式可跳过内容AI审核，保留结构与下载确认。
