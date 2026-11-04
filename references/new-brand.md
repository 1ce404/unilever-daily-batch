# 接入新品牌（力士 / 多芬 / HC / AHC …）

品牌共用的放 `config/<brand>.json`（复制 `config/qingyang.json` 改），每个产品一份放 `config/<brand>/<product_key>.json`（复制 `config/qingyang/2x.json` 改；产品的 key 要和 `store_to_product` 里的一致）。逐项改：

| 字段 | 从哪来 |
|---|---|
| `sources.schedule` | 该品牌的排期文档（清扬的是《【清扬】排期》；其他品牌问用户要） |
| `sources.recent_scripts_section` | 近期脚本合集里的 `#` 一级标题，如 `LUX 1.0`、`HC-逛逛` |
| `sources.qc_custom_sections` | QC 文档「定制化标准」里对应的小节名 |
| `store_to_product` | 排期「店铺」列的写法 → 产品 key |
| `products.*.clipo_product_id` | `GET /api/v1/products?limit=100` 按名字找；跑 `preflight` 确认能打开、有切片 |
| `products.*.selling_points` / `mechanism` / `cta_required` | 近期脚本合集该品牌开头的卖点表与 callout；机制写 `source` 和 `verified_on` |
| `products.*.foreign_claims` | 同品牌其他 SKU 的卖点关键词，防串系列 |
| `banned_words` | 通用三类保留；加上该品牌定制段（例：Lux 1.0「国际」「拉满」；Dove-PC「最」「修护」「提亮」→ 焕亮；Lux「养生」「头皮健康」） |
| `products.*.footage` | 看该产品平台素材（非风险切片）的描述，按段写下有哪些画面，写文案时对照 |
| `naming` | 品牌的命名规则 |
| `daily_quota` / `brief_prefix` | 没有排期表的品牌：每天固定的产品和条数（例 `config/lux.json`），brief 编号前缀区分品牌（清扬 B、力士 L） |

**配音、字幕、BGM、画面、贴图、音效不在 skill 里配**：在平台给该产品写好提示词 `video_script_create`（QC 文档里的限制，如 AHC 只要女声、只要蓝底白字字幕，HC 要氛围感 BGM，都写进去）。这一步由用户在平台做。

QC 文档里只能靠人判断的定制要求（如「Dove-Hair 提到仿生氨基酸时换成分画面」「HC-逛逛 结尾箭头往左下角」）文案相关的写进 `references/writing.md` 的固定要求（按品牌分小节）；画面、贴图、音效相关的写进该产品在平台上的提示词 `video_script_create`（告诉用户去改）。

**范本库**：从该品牌近期脚本合集里每个产品挑几条客户接受过的，按 `gold/qingyang.json` 的格式建 `gold/<brand>.json`，每条写 `product`。团队稿里有违规词的先按规则改干净再放进来。没有范本不要开写。

**加一个产品**（同品牌）：建 `config/<brand>/<key>.json`，在 `store_to_product` 加店铺映射，在 gold 里加几条该产品的范本，跑 `preflight` 确认平台有这个产品、有提示词、有切片。

接完跑一遍：`fetch_sources` → `parse_schedule`（确认店铺都映射上）→ `preflight`（确认平台产品有提示词、有切片）→ 写一个 brief → `lint_copy` → `submit --dry-run`。
