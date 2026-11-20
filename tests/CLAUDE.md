# tests/
> L2 | 父级: ../CLAUDE.md

本模块以离线测试保护生产状态与返工边界。
测试不把平台内容审核替换为结构校验。

成员清单
- test_ai_tags.py: AI 标签分类与场景覆盖规则。
- test_aigc_gen.py: 生成计划与独立素材绑定回归。
- test_aigc_materials.py: 素材组去重、源区间与播放边界。
- test_overview.py: 总排期日期、跨周与品线解析。
- test_footage_qc.py: 声音与时长不可变；拒绝非整数源帧、空隙、单帧、非有限速率与超源播放。
- test_resume_batch.py: 授权、源时长与原脚本门禁；拒绝重复追加和不确定重试；只读审核禁止写请求。
- test_batch_completion.py: 九组补齐授权、条数、脚本文字、不确定请求防重复；正面干净、干爽与时间顺序误拦回归。
- test_delivery_share.py: 单条兼容与两账号九十条交付；配额、最新审核、对象顺序、完整成员及写入中断离线回归。
- test_qc_gates.py: 不双倍产、dry-run 收据门禁、开头重复、产品名与成分分段、每段必须写画面。
- CLAUDE.md: 测试目录地图。

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
