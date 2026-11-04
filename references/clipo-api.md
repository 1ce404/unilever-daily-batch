# Clipo Remix 接口（第 ③⑧ 步）

Base `https://clipo-remix.tezign.com/api/v1`，信封 `{code, message, data}`，`code == "ok"` 才算成功。当前无鉴权（工作空间「特赞」）。完整 OpenAPI 曾抓到 `outputs/jianying-sfx-clipo-upload-20261030/inspection/openapi.json`。

单条精剪用 `clipo-remix-ai` 技能（`npx skills add https://ai.tezign.com/static/clipo/clipo-remix-ai_v20261030_1.zip -y`）；**批量走下面这条「脚本 → 生成」链路**，它就是平台网页上「视频脚本 → 生成 N 条」背后的接口，力士团队每天的 `1029-brief1…5` 也是这么跑的。

## 链路

```
POST /video-script-generations      {product_id, requirement} → 平台按产品提示词建脚本（一个 brief 一条）；轮询到 success 拿 script_id
GET  /video-scripts/{id}            读回脚本，核对每段文案和 ai_rewrite
POST /video-cut-generations         {script_id, quantity:10} → 异步组装：合成口播、按标签随机挑画面/BGM/音色/字幕样式
GET  /video-cut-generations/{id}    status=success 时 cut_ids 就是新剪辑
GET  /video-cuts/{id}               draft + random_result（vo_rewrite / tts_speaker_id / subtitle_style_id）
POST /render-jobs                   {spec: draft, options:{codec:h264,quality:high,scale:1}, cut_id, idempotency_key}
GET  /render-jobs/{job_id}          status=success → object_key / download_url；同时回写剪辑的 output_*
POST /video-cuts/review             {cut_ids:[…]} → 202，异步；结论写回剪辑 ai_review_status/result/reason
POST /video-share-groups            {name}                         交付分组（可选）
POST /video-shares                  {script_id, product_id, group_id, title, video_name_prefix, videos:[{video_object_key, cover_object_key, duration_seconds, width, height}]}
```

分享页：`https://clipo-remix.tezign.com/share/{share_id}`；剪辑预览：`https://clipo-remix.tezign.com/cut/{cut_id}`。

## 脚本谁来建：平台按提示词建，skill 只交文案

片子怎么剪（视频规格、配音池、语速、BGM、字幕样式、每段画面标签、贴图、音效）全写在产品提示词里：`GET /products/{id}/prompts`，`prompt_type`：

| 提示词 | 管什么 |
|---|---|
| `video_script_create` | 建脚本：全局配置 + 每段的画面、贴图、音效 |
| `video_script_rewrite` | 生成时每条视频怎么改写口播 |
| `video_cut_review` | 成片 AI 审核标准 |
| `material_segment_review` | 素材切片审核 |

`clipo_batch.py submit` 调 `POST /video-script-generations`，`requirement` 里写：一共几段、每段文案逐字照抄、哪几段不准改写（CTA 和 `lock` 段）、其它按产品默认规则。建完 `GET /video-scripts/{id}` 核对：段数一样、每段 `content` 和审过的一字不差、锁定段 `ai_rewrite=false`；对不上就删掉这条脚本、不生成，报给用户。

11.04 实测：5 段文案逐字照抄，CTA 段 `ai_rewrite=false`；平台自己配了 6 个男声、BGM 0.08、字幕 78%、产品段彩带+礼炮、效果段起泡音效、CTA 段戳直播间头像箭头+指向音效。

**要改片子的样子，改平台提示词，不改 skill。**

## 和 clipo-remix-ai 的区别

`clipo-remix-ai` 是单条精剪：agent 自己合成口播、切字幕、按句挑镜头、按帧放贴图音效（音效 0.2~0.9 秒、音量 0.2~0.3）。本 skill 是批量：平台按分类随机抽画面和贴图音效，音效时长 = 文件原长、音量 1.0，平台没有参数能改。要逐帧控制的片子用 `clipo-remix-ai`。

## 已踩的坑

| 现象 | 处理 |
|---|---|
| 生成成功后剪辑 `output_status` 一直是 `pending` | 生成不会自动渲染，要自己 `POST /render-jobs`（`poll` 已做，`idempotency_key=udb-<cut_id>` 防重复） |
| `GET /products` 列表里有，但 `GET /products/{id}` 回 `product_not_found` | 产品已被删或迁移，列表缓存未清。体检会拦下，让用户重建或给新 id |
| 「二硫化硒」(6ae402ea) 已被删；现用「清扬-二硫化硒蓝C瓶」(6acb4db5) | 描述为空、标签抄自力士，体检会报 ⚠️；补描述和清扬标签前，生成画面可能不准 |
| 产品 `tags` 里的标签切片数为 0 | 平台提示词让某段用这个标签时画面会落空。`preflight` 会列出来，告诉用户补素材或改提示词 |
| 改写后出现禁用词 / 丢产品名 | `poll` 对每条 `random_result.vo_rewrite` 重跑 lint，报告标「改写违规」；同类问题多了，告诉用户把词加进平台的改写提示词 `video_script_rewrite`，同时补进 config 的 `banned_words`（lint 用） |
| 成片音效乱、混进「队友脸面已永久掉线」「支付宝到账」、幽灵声 | 平台按分类随机抽音效，大分类里常混着怪声（11.02 有 3 条因此被驳回）。只用产品提示词推荐的小分类；要加音效先 `GET /resource/sound-effect?catalog=<分类>` 看完全部再用 |
| 成片里没有音效 / 贴图 | 平台提示词里写的分类在资源库里不存在时，平台不报错、直接不配（11.03 出过）。抽查成片发现没有，告诉用户核对平台提示词里的分类名 |
| 一轮生成整轮失败：`voiceover_rewrite_failed` 502/503，或 `timeout: assembly timed out after 600.0s` | 前者是平台改写模型的上游故障，等一会儿 `submit --retry-failed`；后者是一轮要太多条，组装超过 600 秒。一轮全有全无，失败的那轮不落任何剪辑。所以每轮 ≤ `generate_chunk`（15）条 |
| 渲染并发 | `GET /render-jobs/capability`：同时最多 100 条，超出排队，提交不受限 |

## 改写模式（默认）

一个 brief = 一个 video-script + 几轮 `video-cut-generations`（共 排期 × `generate_factor` 条，每轮 ≤ `generate_chunk` 条）。
- 非 CTA 段 `ai_rewrite=true`；CTA 段 `false`，机制和「戳直播间」原样念
- 改写规则在平台产品提示词 `video_script_rewrite` 里（要求措辞和原文不同、各版本之间不重复），所以改写一定会动，`poll` 读回 `random_result.vo_rewrite` 逐条 lint，结果写进 `copy_issues`
- 每轮生成是全有全无：一条组装失败这轮不落地；`submit --retry-failed` 重新生成（沿用同一个 video-script）
- 需要逐字锁定时（客户指定话术），在该段写 `"lock": true`（`cta` 段默认锁定）

## 素材侧（不在每日流程里，用户要求时做）

- 产品切片列表 `GET /products/{id}/segments`，标签统计 `GET /products/{id}/segment-tags`
- 切片改标签 `PATCH /materials/segments/{segment_id}`，人工复核 `POST /materials/segments/{segment_id}/review`
- 别的产品的切片复制过来 `POST /products/{id}/segments/copy`
- 产品描述 / 标签 `PATCH /products/{id}`；审核提示词 `PUT /products/{id}/prompts/video_cut_review`

这些都会改动平台上团队共用的数据：先说清楚要改什么，用户同意再做。
