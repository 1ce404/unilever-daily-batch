# config/
> L2 | 父级: ../CLAUDE.md

品牌配置定义事实、命名与生产参数。
产品配置定义卖点、机制与画面候选标签。
common 合并品牌与产品配置，不写平台共享配置。
JSON 保持标准语法，不加入可执行代码。
音色只在产品平台提示词中选择。
本目录不保存音色名单或配音性别门禁。
口播与 BGM 音量仍属于本地生产参数。

成员清单
- lines.json: 总排期品线映射；标记未接入与待确认项。
- qingyang.json: 清扬排期来源、生产与交付命名。
- qingyang/: 控油蓬松、呼吸瓶与二硫化硒产品配置。
- lux.json、lux/: 力士洗护品牌与产品配置。
- ahc.json、ahc/: AHC 品牌与产品配置。
- hc.json、hc/: 家清品牌与产品配置。
- dove.json、dove/: 多芬品牌与产品配置。
- luxpc.json、luxpc/: 力士沐浴品牌与产品配置。

清扬技术句限定质地；二硫化硒油头句限定油头状态。
产品 rules 只放写稿规则（任务卡打印）；AIGC 画面规则放 aigc_notes，不进任务卡。
selenium 启用卡审库 review_blocks（references/review-blocks/）。
文案事实与已审核脚本文字保持原样。
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md

- quality/lux.json：LUX客户制作要求的唯一阈值源，由quality_contract引用，不作为产品读取。
