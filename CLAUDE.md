# unilever-daily-batch - 联合利华排期到人审批量成片
Python 3 + Clipo Remix REST + lark-cli + ffmpeg + NumPy

本 skill 按品牌配置拆排期、出审核文档并生成成片。
流程三道人审：脚本 →（AIGC 素材）→ 1 条 demo，通过后量产。
平台逐字建脚本并逐段启用改写。
批次输入、状态与交付写入调用方指定的 run 目录。

<directory>
scripts/ - 排期、文案校验、人审与批量命令；见 scripts/CLAUDE.md
references/ - 文案、QC、接口与 AIGC 规则；见 references/CLAUDE.md
config/ - 品牌共用配置、产品配置与品线映射；见 config/CLAUDE.md
gold/ - 按品牌保存的团队金标脚本
tests/ - 离线状态、标签与返工安全回归；见 tests/CLAUDE.md
templates/ - 帧驱动透明特效源码与参数；见 templates/CLAUDE.md
</directory>

<config>
SKILL.md - 主流程、提速要点与铁律入口；返工命令在 references/rework.md
README.md - skill 使用说明入口
.gitignore - Git 文件排除规则
CLAUDE.md - 本 skill 的目录地图与职责边界
</config>

职责边界
- overview 只报每日品线内容，不写 brief、不调用平台。
- common 统一配置读取、文案指纹与人审凭据检查。
- lint_copy 检查底稿与平台改写，不替代人审。
- clipo_batch 驱动批量流程，不代写待审文案。
- 画面默认从标签下全部素材随机选片。
- 用户指定才固定素材或限定候选池。
- 开头切片允许重复，不自动换片或按重复拦交付。
- 音色只由产品当前平台提示词选择。
- 本地不设全局音色名单或配音性别门禁。客户明确提出的本次音色/语速/整篇合成要求，按任务参数留原话与实际合成证据，不回滚其他产品默认策略。
- 实际音色留档；口播与 BGM 音量仍检查。
- aigc_materials 只检查结构绑定，不检查内容。
- AIGC 内容要求见 references/aigc-standards.md。
- AIGC 生产与返工流程见 references/aigc-production.md。
- 平台提示词修改须获用户授权，先备份再回读。

本地图只覆盖本 skill，不扩展工作区其他模块。
法则：极简、稳定、导航、事实可核验。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
