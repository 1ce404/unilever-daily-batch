# references/review-blocks/
> L2 | 父级: ../CLAUDE.md

本目录存放各产品线的卡审库。
库是数据，由脚本读取，不进 SKILL.md 与任务卡正文。
产品 config 写 `review_blocks: <库名>` 启用对应库。

成员清单
- qingyang.csv: Cyber（清扬-绿蓬蓬和二硫化硒）卡审词库，21 类、336 条；列为卡审点、名称、维度、风险等级、违规词、替换建议。
- qingyang-guide.md: Cyber 已确认的规避写法；写稿和改稿时人工查阅。

读取规则
- common.review_blocks 读库；维度含文案、医疗混淆、资质、功效、违禁词的给 lint，纯画面的给 aigc_gen。
- lint_copy：high 报错，mid / low 提醒；产品 `review_warn_only` 里的短词只提醒，`allow_words` 不拦。
- brief_card 只打一行高风险类别与指引路径。
- aigc_gen 对提示词里未否定的画面条目提醒。

更新：用户给新卡审记录时，替换 csv 与 guide；不在 config 重复抄词。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
