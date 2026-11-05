# Clipo Remix 接口（第 ③⑧ 步）

Base `https://clipo-remix.tezign.com/api/v1`。所有响应都是信封 `{code, message, data}`，`code == "ok"` 才算成功。当前不需要鉴权。线上接口文档：`GET /api/v1/openapi.json`。

这个 skill 只走一条路：

```
AI 建脚本 → 脚本批量生成混剪 → 批量渲染 → 批量 AI 审核
```

片子怎么剪（视频规格、配音、BGM、字幕、每段画面标签、贴图、音效、改写规则）都写在平台的产品提示词里，接口只负责把这条路跑通。全部调用在 `scripts/clipo_batch.py`。

## 一、AI 建脚本（`submit`）

把审过的文案交给平台，平台按产品提示词 `video_script_create` 把脚本建出来，配音、画面、贴图音效都由它配。

| 接口 | 用途 |
|---|---|
| `POST /video-script-generations` | 提交建脚本任务。body：`product_id`、`requirement`（创作要求，自由文本）。响应里的 `script_id` 提交时就给了（预建的空脚本） |
| `GET /video-script-generations/{id}` | 轮询。`status`：`pending` / `running` / `success` / `failed`；失败看 `error_code`（`ai_timeout`、`ai_call_failed`…），失败即终态，重试就是再提交一条 |
| `GET /video-scripts/{script_id}` | 读回脚本：`segments[].content`（口播）、`segments[].ai_rewrite`（是否允许改写）、`config`（平台配好的全局配置） |
| `DELETE /video-scripts/{script_id}` | 删脚本 |

`requirement` 里写清：一共几段、每段文案逐字照抄、哪几段不准改写（CTA 段和 `lock` 段）、其它按产品默认规则、脚本标题。

建完必须核对，对不上就 `DELETE` 掉这条脚本、不生成、报给用户：

- 段数和审过的一样
- 每段 `content` 和审过的一字不差
- CTA / `lock` 段 `ai_rewrite = false`

## 二、脚本批量生成混剪（`submit` / `topup` / `poll`）

同一份脚本，平台每条视频按 `video_script_rewrite` 提示词改写一种说法（`ai_rewrite=false` 的段原样），画面、BGM、配音各自随机抽。

| 接口 | 用途 |
|---|---|
| `POST /video-cut-generations` | 提交生成。body：`script_id`、`quantity`（1~300）；可选 `add_compliance_watermark`、`compliance_watermark_position`（「AI生成」合规水印） |
| `GET /video-cut-generations/{id}` | 轮询。`status`：`pending` / `running` / `success` / `failed`；`generated_count / quantity` 是进度；`success` 时 `cut_ids` 是这批剪辑 |
| `POST /video-cut-generations/{id}/continue` | 往同一个任务追加 `quantity` 条（要等上一轮跑完）。skill 补生成时另提交一条新任务，效果一样 |
| `GET /video-cuts/{cut_id}` | 读一条剪辑：`draft`（整份时间轴，渲染要用）、`random_result.vo_rewrite`（这条改写后的口播）、`random_result.tts_speaker_id`（音色） |

要点：

- **每一轮全有全无**：一条没组装成，这一轮一条剪辑都不落，也不扣额度。所以一个 brief 要的条数拆成每轮 ≤ `generate_chunk`（15）条分别提交，一轮失败不连累别的轮
- **一次多生成**：每个 brief 生成 排期 × `generate_factor`（3）条，因为平台 AI 审核通过率约 40%
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

## 五、平台体检用到的（`preflight`）

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
