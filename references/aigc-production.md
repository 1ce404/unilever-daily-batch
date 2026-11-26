# 人物AIGC生产与返工

画面要求（QC 的 AIGC 规定、各品牌人设、禁用画面、提示词怎么写）在 [aigc-standards.md](aigc-standards.md)，写生成计划前先对照那里。本文件只讲流程。

用户要求人物情景剧、素材差异或人物画面修改时使用。传统切片且允许复用时仍用素材池；不能将素材池随机抽样当成“10条使用10套不同人物素材”。

## 三层差异

- 单条视频：分镜 1、2、4 动作各不相同（不要求同一张脸，用户 11-18 删掉了这条）
- 同一脚本的 N 条：用户要求组内不同素材时，N 条用 N 套独立源素材；换配音、BGM、文件名、裁切或调速不算不同素材
- 不同脚本：按各自口播设计场景和动作，不整套复用

人设、外貌、哪些画面不能出现，按 [aigc-standards.md](aigc-standards.md) 里这个品牌那一节。

## 计划与接续

1. 在brief写 `within_folder_material_diversity_required: true`。先准备交付N条的N套人物、场景、动作方案和素材对应关系；已确认demo可保留1条，其余补N−1套，不把demo整套用于批量。
2. `submit`生成平台剪辑后，`poll`将未绑定的人物剪辑置为`aigc_pending`，不先渲染重复默认画面。常规平台建脚本不自动制作独立AIGC源素材，按本节生成并登记。
3. **生成用 `scripts/aigc_gen.py`，不自己写调接口的代码**。先写生成计划 `$R/aigc/<名字>.json`（格式见下），每套源视频一条提示词，一次生成三个不同动作，提示词里写清三个动作的时间（如 0–2 秒 / 2–4 秒 / 4–6 秒），计划里的 `shots` 写同样的起止秒。参考受阻时改用完全原创的文字人物方案。计划和文案一样先给用户看。
4. `submit --dry-run` 报条数（每条单独计费），用户同意后 `submit`，然后 `poll` 放后台跑（nohup），这一轮对话就结束，不在对话里等。`poll` 读任务状态、把成功的素材标签整组换成场景标签（打标规则见 [aigc-standards.md](aigc-standards.md)「打标签」）、被版权检查拦的按 `fallback_prompts` 换方案重试（最多 2 次）、写 `<计划>-report.md`（每个场景成功几条、缺几条）。随时 `report` 看进度，离线。
5. 平台剪辑出来以后（`clipo_batch.py submit`，待绑定的停在 `aigc_pending`），`aigc_gen.py sets --brief <id>` 把这个场景成功的每条源视频按 `shots` 切成三个不重叠片段、上传、登记为 AI 切片，写进 brief 的 `visual_sets`（一条视频一套独立源，带 cut_id）；不够的报缺几套，不拿别的视频或场景的素材顶。然后 `bind-aigc`。成功一批就绑一批，不等整批源素材全部成功。虚构仓库、工厂、实验室追溯画面标注“AI场景示意”。

```bash
python3 -I scripts/aigc_gen.py submit --config $C --run $R --plan $R/aigc/<名字>.json --dry-run
python3 -I scripts/aigc_gen.py submit --config $C --run $R --plan $R/aigc/<名字>.json
nohup python3 -I scripts/aigc_gen.py poll --config $C --run $R --plan $R/aigc/<名字>.json --wait 5400 > $R/aigc/<名字>.log 2>&1 &
python3 -I scripts/aigc_gen.py reject --config $C --run $R --plan $R/aigc/<名字>.json [--dry-run]   # 抽帧/听审不合格：删平台切片，按 plan redo 换提示词，再 submit 补齐
python3 -I scripts/aigc_gen.py report --config $C --run $R --plan $R/aigc/<名字>.json
python3 -I scripts/aigc_gen.py sets --config $C --run $R --plan $R/aigc/<名字>.json --brief <id> [--dry-run]
```

生成计划：

```json
{"product": "2x", "tag": "AI-1124-双人演绎", "duration": 6, "shots": [[0, 2], [2, 4], [4, 6]],
 "scenes": [{"id": "golf", "actions": ["摘帽摸发根", "和同伴说话", "清爽甩发"],
             "prompts": ["版本1：人物、服装、场景、0–2秒…、2–4秒…、4–6秒…", "版本2 …"],
             "fallback_prompts": ["被版权拦时用的重新设计的原创人物方案 …"]}]}
```

`prompts` 一条一个版本（每版人和动作不同）；同一提示词出多版写 `"prompt": "…", "versions": N`。brief 写 `"aigc_scene": "golf"`，`sets` 就从这个场景取素材。场景可以写自己的 `tag`（不写用计划的 `tag`）。可选：`model`、`resolution`、`ratio`、`generate_audio`（默认 false）、`max_inflight`（同时在跑的上限，默认 20）。只要素材池、不逐条绑定时，成功的素材只带场景标签，直接 `clipo_batch.py pool --tags <tag> --allow-risk`。
6. `bind-aigc`保留口播、声音、字幕、产品镜头和其他轨道，只替换1/2/4。PUT后GET现签完整draft再提交render。素材/渲染记录存run内；同一state不同时由两个写入任务操作。失败渲染修复后增加该组render_revision再提交，不能复用旧失败job。

```bash
python3 -I scripts/clipo_batch.py bind-aigc --config $C --run $R [--brief <id>] [--dry-run]
python3 -I scripts/clipo_batch.py poll --config $C --run $R --wait 60
```

visual_sets的每套格式：

```json
{
  "name": "<brief>-A02",
  "video_key": "<brief>-V02",
  "cut_id": "实际剪辑ID",
  "source_generation_id": "实际成功源任务ID",
  "source_object_key": "materials/源视频.mp4",
  "scene": "该条场景",
  "actions": {"1": "开头动作", "2": "承接动作", "4": "效果动作"},
  "segments": [
    {"segment": 1, "object_key": "materials/一.mp4", "source_interval": [0, 3], "duration": 3},
    {"segment": 2, "object_key": "materials/二.mp4", "source_interval": [3, 6], "duration": 3},
    {"segment": 4, "object_key": "materials/四.mp4", "source_interval": [6, 10], "duration": 4}
  ]
}
```

保留用户确认demo可写`retained_user_approved_demo: true`及三镜实际clips.src，保留其已有素材记录；这不是新素材视觉检查通过的标记。

## 失败与等待

- `aigc_gen.py` 在 `<计划>-state.json` 保存每次提交的提示词、任务ID、时间、状态、失败原因，不覆盖尝试历史；pending/running只等待，不重复提交。无取消接口不假装取消。
- 版权/真人参考拦截：停止使用被拒参考，重新设计完全原创的人物和表达，保留年轻帅气、指定人群与已审情境。换模型不能替代解决问题，不将输出检测失败认定为已证实侵权。
- 超时：确认平台终态后才补，按实际调度容量减少在途任务。上传、登记、绑定、渲染失败优先重试对应环节，不重新生成已经成功的源素材。
- 同一素材方案最多自动补生成2次（`aigc_gen.py` 已按这个执行：被拦只用 `fallback_prompts` 里的新方案，没写就直接报缺口；超时或平台错误按原提示词重试）。仍失败时标明该项阻塞与实际缺口，停止原方案追加，给出新的具体原创方案；其他已成功的继续剪片渲染。已有授权覆盖新的同范围方案且用户要求继续时按授权执行；新增预算/范围才需再次确认。不能无限轮换相同描述持续提交。
- 不擅自以重复素材、无关场景或少做条数代替要求。用户要先发完成部分时可阶段交付，明确数量并继续缺口；最终仍按要求够数。
- 进度区分平台排队、源生成、剪片/绑定、渲染与下载，不能把所有等待都称为渲染；只报实际数量和失败，不承诺无法控制的完成时间。

## 检查与交付

默认不逐条抽帧看图；需要demo检查时按用户要求少量检查，不能以AI审核通过代替素材对应关系。用户明确“我来检查”“不用检查，直接给视频”时在该brief或state顶层记录：

```json
{"content_review": {"mode": "user", "instruction": "用户实际原话", "by": "用户"}}
```

`poll`跳过后续内容AI审核并记录review_waived、user_review_pending；不运行额外逐条画面、音色或内容测试。保留已审脚本、生成与素材绑定、任务状态、水印配置、官方下载数量和完整性。不得伪造ai_review=pass、visual-pass或声称全部内容检查通过。

分享前自动检查每条实际draft对应素材组、三镜切片和非重叠源区间、组内/跨脚本独立源记录。这是结构确认，不看帧，也不能证明人物面容和动作视觉质量。仍按官方share --download保存，每组N条与下载清单一致；分享同cut重渲染后使用--refresh。分享封面对象键必须是derivatives/，不能替换成materials/源封面。

## 产品小字被AI改写时

不得把能读LUX当作全包装正确。使用无遮挡真实源帧核对底部小字；不确定的容量不补写。优先让AI只生成无产品布景，再以`product_scene.py`在Clipo原生Remotion保留真实瓶身像素，明确PNG哈希、源片ID/帧时点、裁边轮廓及位置。先每产品1条代表片云渲染和实际检查，再提交其余；全条检查后才以AI素材入库，不代替用户素材人审。原像素不足或边缘含手指时继续找完整参考，不能模型补字掩盖。

Clipo自定义Remotion沙箱不开放OffthreadVideo，真实动态窗用原生SplitScreenClip/VideoClip；只用Img做照片动封。渲染job成功也可能输出红字错误卡，必须查看实际首/中/尾。真实源区间须实看全过程，不能由元数据时长推断无手/产品在场；静态照片动封要明确标注，不冒充动态源。
