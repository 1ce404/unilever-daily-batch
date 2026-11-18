# QC

QC 标准原文：https://tezign.feishu.cn/docx/HrWtd4mowoGeAUxtYeFc91KhnJb（`fetch_sources.py` 拉到 `sources/qc.txt`）。QC 文档新增条目，**先改 config 再写脚本**。

## 四层检查

| 层 | 什么时候 | 谁做 | 拦什么 |
|---|---|---|---|
| 1 脚本 lint | 写完、审核人改完 | `lint_copy.py` | 禁用词、极限词、时间承诺、机制数字、串系列卖点、产品名缺失或被拆开、CTA、过长短句、开头撞车。不查文风 |
| 2 人审脚本 | 每个 brief | 审核人 | 口吻、卖点讲清楚没、lint 的提醒项 |
| 3 平台 AI 审核 | 每条成片渲染完 | `poll` 自动送 `video-cuts/review` | 画面与口播一致、产品外观、画面质量、字幕、音画同步 |
| 4 抽查 | 报告出来后 | 用户 | 只看 AI 驳回的，每个 brief 最多抽 1 条通过的 |

用户说「我来检查」「不用检查」时，记 `content_review`，跳过层 3 和层 4，只确认任务状态、素材绑定和下载完整。

## 规则放在哪

同一条规则只放一处：

| 放在哪 | 放什么 | 例子 |
|---|---|---|
| skill：`config/<品牌>.json` 的 `banned_words`；`config/<品牌>/<产品>.json` 的 `banned_words` / `foreign_claims` / `regex_error` / `rules` | 文案能查的：禁用词、别的产品 / 香型的卖点、数字、产品名写法、CTA。lint 查底稿，`poll` 查每条改写，`submit` 把禁用词写进这条脚本的改写要求 | Dove-PC 不写「修护」「提亮」「限时」、不写数字；Lux-PC 墨兰乌木不写竹林 |
| 平台产品提示词 | 文案查不到的：BGM 类别、字幕样式、贴图位置、音效分类（`video_script_create`）；改写时不准新增的词（`video_script_rewrite`）；成片审核标准（`video_cut_review`） | HC-逛逛结尾箭头左下角放大；Dove 字幕白底浅阴影；Dove 开头是泡沫洗浴画面 |

禁用词两处都要有：建脚本提示词管不到改写，每条视频的口播是改写那一步写的。

改平台提示词要用户授权；改前备份到 `runs/prompt-backup-<时间>.json`，改后读回核对；别人在提示词末尾加的产品信息原样保留。

平台审核还读产品 `description`：描述里的机制过时会让审核误判新机制。`preflight` 有 ⚠️ 时提醒用户更新描述。

## QC 文档条目 → 配置

| QC 条目 | 配置 / 检查 |
|---|---|
| 绝对化用语 | `banned_words.error` + `regex_error`（最…、第一…，放过「最近 / 第一次」） |
| 功效夸大 | `banned_words.error`（根治、治疗、防脱、生发）；其余靠人审 |
| 时间承诺 | `regex_error` 数字 + 小时/天/周；`regex_warn` 中文数字；`warn`「长效」「持久」 |
| 权威背书 | `warn`「临床」「专家」「医生」：资料里有出处才能写 |
| 竞品、价格 | `error` 全网、领先、最低价、历史最低、原价、性价比 |
| 信息与机制准确 | 产品全名完整出现；机制数字只能是 `mechanism.allowed_numbers` |
| 系列不可混淆 | `foreign_claims`：别的 SKU / 香型的卖点出现即报错 |
| 文字规范 | 短句 > 12 提醒、> 20 报错；产品名不跨句；不带 emoji / `[#…]` |
| AIGC 标准 | `banned_footage_words` 按素材描述拦：头屑、明星、白大褂、秃顶；其余靠人审 |

## 驳回怎么处理

- `report` 按原因分好类（改写违规 / 文案 / 画面素材 / 产品信息过时 / 其他），每类附处理办法。分错类的看驳回原因文字，不用打开视频
- 平台 AI 审核偶有误判（例：把真实成分判成「虚构成分」），告诉用户，由用户决定放行，不自己改结论
- 改写违规（`copy_issues` 非空）一律当驳回，并把新出现的违规词补进 `banned_words`
- 不说「全部通过」，除非 `reject` 和未完成都是 0
