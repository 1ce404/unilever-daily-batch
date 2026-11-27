# 返工与交付修订命令

本文件收成片返工、任务级直接交付及进度沟通规则。主流程见 ../SKILL.md。每条写命令先 `--dry-run` 报数。真正执行前先备份，执行后回读，旧链接不删除。

| 场景 | 命令 |
|---|---|
| 已分享分组质量复核（无原state） | `group_quality_rework.py inventory / snapshot / media / resources / pool / graphics`；本次清扬30条按 `group_rework_apply.py plan → apply --stage pilot --dry-run → apply → status / review / media`，实际看图后 `accept`，余量 `--stage rest`；`finish`回读原分组和每条当前输出。助手不能替用户审批新脚本/新AI。 |
| 不够数 | `clipo_batch.py topup --dry-run` → `topup` → `poll`（缺几条补几条） |
| 生成失败 | `clipo_batch.py submit --retry-failed`（在原任务上继续） |
| 渲染临时失败 | `clipo_batch.py rerender --brief <id>` → `poll` |
| 改了平台审核提示词 | 先停 `poll`，再 `clipo_batch.py rereview`（不重新生成） |
| 去花字 / 换禁用源片 | `clipo_batch.py restyle --remove-effects --replace-banned --dry-run` |
| 用户明确要求换开头 | `clipo_batch.py spread-hooks`；已交付的用 `footage_qc.py repair` 只换第 1 段 → `refresh-group --map <目录>`；默认允许切片重复，不自动执行 |
| 补右下角 AI 角标 | `footage_qc.py watermark --cut <id>… --out <目录> --dry-run` → 去掉 dry-run 加 `--wait` → `refresh-group` |
| 已交付分组换 AIGC 开头并调音 | `opener_refresh.py prepare` → `apply --dry-run` → `apply` → `footage_qc.py review` → `refresh-group` → `opener_refresh.py download` |
| 定点修订续跑 | `footage_qc.py repair --resume-backup <原备份>`；`review --out <收据目录> --wait` |
| 无完整 state 的修订成片交付 | `delivery_share.py share / pack / batch --plan <清单> --out <收据目录>` |
| 合并或替换分享 | `delivery_share.py collect / replace` |
| 手建分组改名 | `clipo_batch.py rename-shares --group <链接> --dry-run` |
| 按店铺拆组 | `clipo_batch.py split-group --group <链接> [--store Cyber] --dry-run` |
| 两号九组断点恢复 | `batch_completion.py status / continue / poll-generations / prepare / poll-reviews` |
| 力士透明特效 | `effects.py inventory → plan → register → apply（试样）→ poll → download → accept → batch` |
| 画面标签重钉 | `clipo_batch.py sync-visuals`（先备份） |

## 用户接管检查或直接出片

先按用户原话区分范围。只接管平台内容审核时，通过skill命令在brief或state顶层记录 `content_review: {mode: "user", instruction: "原话", by: "用户"}`；这不自动免除其他检查。用户明确说“别QC了直接出”时，停止当次范围内新增QC、抽帧和由此展开的新返工，按现有已完成输出直接分享、下载；不把该次指令改为以后默认免检。

直接交付单独保存用户原话、批次/brief、精确视频键、当前render和输出对象清单及链接/下载数量，不修改技术QC、实际看图、平台审核或三道人审的通过字段。渲染尚未完成的只报在途；用户先前退回的旧片不自动复活。直接出片不扩大视频、TTS或AIGC预算；已提交的修正版完成后可更新对应交付，不另起任务凑数。若现有分享入口不能表达该次授权，先扩展有明确范围和独立收据的skill CLI，不手改state或伪造通过。

## 先做demo、稍后客户检查

用户明确委托先选素材/钩子并制作demo、稍后再检查时，按原话在brief的`demo_production_authorization`保存`mode: demo_only_before_review`、`by: 用户`、`quantity: 1`、当前文案指纹与AI标签、`review_pending: true`。brief仍为draft，不填写脚本/AI素材/demo通过。此许可只让每组首条demo进入制作；原生成任务已存在后仍由demo人审拦住续产，新增或改稿使指纹失效。单条AI试样用任务`preview_ai_hook_ids`绑定，不能冒充人审批准池或扩为量产5条。原生剪辑先停在`demo_prepare_pending`，整篇配音与时间轴准备好才渲染；分享标为待客户检查。

导出这类demo时，用`preview_delivery.py plan --config ... --run ... --out <收据目录>`生成精确清单，再以`delivery_share.py share --plan <收据目录>/preview-plan.json --out <收据目录>`先dry-run后分享；最后`preview_delivery.py finish ... --download-to <目录>`核当前输出、登记分享并下载。`progress.py`分列“demo待客户检查”，不要求追加QC或自动补齐量产。

## 交付节奏与进度

- 已批准的demo及时续产；一个组的检查或修复不阻塞其他组独立的素材、demo和已授权批量。已授权范围继续执行，只有新增收费或超出用户已授权的变更范围才需要补充确认；不能重复问同一授权。
- 进度先运行 `progress.py`，按组报“已出片/在途、检查通过、已分享、已下载”，并说明数据更新时间。严格检查数不能代替实际出片数；提交、渲染完成、分享、下载也不是同一阶段。有直接交付时引用其导出收据，与严格通过数分开。
- 完成时间基于当前队列、剩余步骤和实际进度给出；没有可靠依据就报阻塞点，不给“15–25分钟”等猜测。可能赶不上用户时间点时立即说明，并先交已完成组，不让用户反复催问才发现仍缺demo或尚未量产。
- 返工先汇总同一cut的已知修改；未变的render复用现有有效凭据，改动条目一次重检。不要把技术抽帧说成全片听审或抖音UI验证；不要因修后才发现的轻微观感问题无限延后已授权交付。
- 交付分组按本次约定的产品、组号和顺序核对。一个产品/组只保留当前约定的一条链接；修正版替换旧版/单条demo的分组成员，独立旧链接和退回证据保留。不要让旧版/新版同名brief2并列造成重复或缺组误解，也不能仅为去重擅改产品编号。
- 完成时分别回读分享条数和本地下载条数。按用户要求直接导出的写“已导出/已下载”，不能写“全部QC通过”。

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md

## 用户委托检查后量产

用户明确说“你确认满足要求后批量”时，保存原话与精确brief/V02..V10范围。先实际检查当前demo、字体/音色/素材与包装数量、声音资源及时间轴、改写与人物/包装分配计划。检查有未过项就先修；不能将条件授权写成客户亲自看过或伪造approval/demo_approval。`checked_batch_authorization`是独立条件生产许可，绑定当前demo render/output、文案指纹、AI标签及7类检查证据文件的SHA256；`load_briefs`每次从当前State和检查文件现读临时context，变化即失效。每条全篇改写先进入精确key的检查计划，批量回调核该行哈希后合成，保留平台原改写供追溯。它不增加预算，仍执行quantity、dry-run、whole TTS幂等和实际成片复核。默认三道人审流程保持；本分支只适用这份明确委托、已履行检查的范围。
