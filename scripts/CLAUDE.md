# scripts/
> L2 | 父级: ../CLAUDE.md

本模块把排期推进为待审 brief，再驱动批量成片。
common 提供共享规则；各命令复用其配置与人审校验。
平台批量入口为 clipo_batch；内容复核不等同结构检查。

成员清单
CLAUDE.md: 本模块地图，记录命令职责与依赖方向。
ai_tags.py: AI 素材标签规则唯一出处；检查、备份修复与恢复标签。
aigc_gen.py: AIGC 场景生成与轮询；本地参考图上传缓存；提交前拦截脸谱化及产品禁用（女生、慢动作）提示词；按独立源切片入库并写 visual_sets。
aigc_materials.py: AIGC 素材组映射、源素材去重与 draft 绑定校验；不审内容。
batch_completion.py: 九组合格数量补齐；只续原任务，复用目检窗口，不改文案或人审。
brief_card.py: 文案任务卡；汇总排期、规则、素材与范本，不写文案。
clipo_batch.py: Clipo Remix 批量入口；校验人审后建脚本、生成、渲染与交付；只按 1 倍生成，无匹配 dry-run 收据不提交；按 config 调 BGM 音量，AI 角标可只盖 AI 镜头；rename-shares 按命名规则重建手建分享，split-group 按店铺拆分组，refresh-group 换成重渲后的成片，rerender 重渲临时失败的成片，sync-visuals 按 brief 重钉已建脚本的画面标签；分享命令都保留旧链接。
common.py: 共享规则底座；配置、飞书表格、文案指纹与人审凭据校验。
delivery_share.py: share 定点修订、batch 整批交付、collect 收组、replace 替换并规范命名；备份与回读；保留原分享。
fetch_sources.py: 飞书源文档拉取；保存排期、团队脚本与 QC 到 run 目录。
footage_qc.py: 分类快照、画面返工与右下角 AI 角标（整片或只盖 AI 镜头）；整数源帧、实测源时长，备份后写入并回读。
resume_batch.py: 原任务缺口续产、终态等待与本地返工契约；review-status 仅 GET，finish 下载送审；不改文案、不建分享。
lint_copy.py: 统一文案机械门禁；检查合规、机制、CTA、开头差异与每段画面计划（产品名配外观、成分配质地、开头池够数）。
overview.py: 每日品线内容总览；按周标题定位日期，不写 brief、不调用平台。
qc.py: 交付闸门；按草稿查角标位置与时段、BGM、禁用画面、画面标签、音色与开头重复；poll 渲染前与 share 前调用。
parse_schedule.py: 品牌日排期拆为 brief 空稿；支持周结构与固定日配额。
review_doc.py: 人审文档生成与飞书结果同步；记录审核者、原话与文案指纹。
voice_check.py: 只读查询分段配音，依赖 NumPy 与 ffmpeg 检查音高比；输出可指定路径，不替代听审。

法则：成员完整、一行一文件、父级链接、技术词前置。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
