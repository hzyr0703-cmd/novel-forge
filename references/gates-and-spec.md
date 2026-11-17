# 门禁与单章规格（明细）

> 本文件是 SKILL.md 第五、六节的下沉版。常驻层只留「跑什么命令 + 4 条 blocking + 一声问句」，
> 所有取值、分层、阈值来源、失败处置都在这里。动笔前读一遍，写完后按退出码自查。
> **数字唯一事实源**：阈值看 `spec_check.py` 顶部 `THRESHOLDS`，平台预设看 `PLATFORM_PRESETS`，
> 规则清单跑 `node scripts/check-ai-patterns.js --list-review-classes`。文档里的数字若与脚本冲突，以脚本为准。

## 一、门禁执行方式

oh-story 原设计依赖 Claude Code 的 hook 系统与 7 个专职 agent，WorkBuddy 两者皆无。
**门禁语义由 `forge.py` 子命令显式调用补齐**，每次写正文前必须实跑，不得跳过。

**运行环境**：本技能脚本依赖 Python ≥ 3.10 与 Node ≥ 18。首次使用或换机后先跑
`python3 $S/check_environment.py`，输出 `unavailable` 时按 `references/setup-guide.md`
补齐后再继续——环境不可用时禁止绕过门禁直接写正文。
（本地适配 2026-11-06：本机安装路径为 `~/workspace/custom-skills/novel-forge`，解释器命令为 `python3`；
上游 WorkBuddy 的路径与 `python` 命令已在本文档及相关文档中全局替换，换平台使用时改回对应路径即可。）

```bash
S=~/workspace/custom-skills/novel-forge/scripts

# 细纲门禁 + 连续性门禁（写正文前）
python3 $S/forge.py gate --book <书目录> --chapter N --stage outline

# 初稿层门禁（写完初稿，查 正文/draft/）
python3 $S/forge.py gate --book <书目录> --chapter N --stage draft

# 定稿层门禁（定稿后，查 正文/final/，交付前跑）
python3 $S/forge.py gate --book <书目录> --chapter N --stage final

# 交付前全检：draft + final 两层都查（v2.8 起；旧版 all 是 draft 的别名，
# 交付检查实际只查了初稿层，定稿层可能一次门禁都没跑过）
python3 $S/forge.py gate --book <书目录> --chapter N --stage all

# 换平台：字数带与钩子间隔随平台变（fanqie 默认 / qidian / qimao / yanyan，也认中文名）
python3 $S/forge.py gate --book <书目录> --chapter N --stage final --platform qidian

# 双时间线一致性（每章写完）
python3 $S/forge.py truth --book <书目录>

# 跨章自我复述 + 事实冲突（gate 已默认包含，以下为单独排查用）
python3 $S/check-crossrepeat.py --book <书目录>
python3 $S/check-factconflicts.py --book <书目录>

# 跨章复述三档工作流（v2.4.1 起）：
#   日常（每章提交后）：增量模式，只查新章对全历史，~0.2s/章
python3 $S/check-crossrepeat.py --book <书目录> --since <上一章号>
#   里程碑（每 100 章）：全量扫（>40 章自动启用 banding 剪枝，判定逻辑不变）
python3 $S/check-crossrepeat.py --book <书目录>
#   排查疑点：强制不剪枝的原始全量
python3 $S/check-crossrepeat.py --book <书目录> --no-banding

# 平台合规（meta 混入 + 站外导流，gate 已默认包含，以下为单独排查用）
python3 $S/check-platform-compliance.py --book <书目录>
python3 $S/check-platform-compliance.py --file 正文/draft/第N章.md

# 调试期可临时跳过全书级检查（正式交付勿用）
python3 $S/forge.py gate --book <书目录> --chapter N --skip-cross --skip-fact --skip-platform

# 内容构成门禁（v4.6 起，gate 的 outline 阶段已默认包含）
python3 $S/check-content-composition.py --book <书目录> --chapter N
python3 $S/check-content-composition.py --book <书目录> --chapter N --layer draft
python3 $S/check-content-composition.py --book <书目录> --volume 1
python3 $S/check-content-composition.py --book <书目录> --audit      # 存量迁移体检，只报告

# 15 维命中率复核（动笔前）
python3 $S/forge.py hitrate --file 命中卡.md

# 单章规格明细（想单独看哪项超标时；默认查 draft 层）
python3 $S/spec_check.py --file 正文/draft/第N章.md [--platform qidian]

# 字数口径
python3 $S/forge.py wordcount --file 正文/draft/第N章.md --target 2300
```

**`--stage` 取值**：`outline`（只查细纲）| `draft`（查 `正文/draft/`，默认层）|
`final`（查 `正文/final/`）| `all`（**draft + final 两层都查**，交付前用）。
也可用 `--layer draft|final` 显式指定只查某一层（显式覆盖优先）。

**`--platform` 取值**：`fanqie`（默认）| `qidian` | `qimao` | `yanyan`，也认中文名
（番茄/起点/七猫/盐言）。它只改「字数带 + 爽点/微钩子间隔 + 首章字数」这几项平台性参数；
AI 味、跨章复述、事实冲突、段长与平台无关，一律不动。平台与阈值的对应表见
`references/platform-adaptation.md` 第一节，脚本侧唯一事实源是 `spec_check.py` 的
`PLATFORM_PRESETS`。

**退出码非 0 = 存在硬错误，必须修到通过才允许交付。**

`hitrate` 的四条硬纪律（均已机器实现）：

1. **单维满分不得超过 3 项**——超出即判虚高，拦截。
2. **每维理由必须含可核查的具体物**——理由若整句只有「感觉不错/挺好/还可以」这类套话，
   无论前后有没有其他词，一律判不合规。这条此前是一个空循环，等于没生效。
3. **合计行的得分列必须与逐维求和一致**——`合计` 行按表格实际列位解析，
   不允许用固定列序抓到权重列充数（否则写了 68 分会被读成 100 分放行）。
4. **否决项 / 天花板项**照旧：D4、D12 任一为 0 不得开工；D1/D11/D13 任一 ≤2 总分锁 74。

## 二、专职 agent 分工（单 agent 代演）

原版由 7 个 agent 协作，单 agent 环境下按此分工顺序自检：

| 原 agent             | 单 agent 代演方式                                           |
| ------------------- | ------------------------------------------------------ |
| story-architect     | 写正文前先产出结构判断（本章目标情绪、阻碍、代价）                              |
| narrative-writer    | 按细纲写正文，遵守字数与钩子型号                                       |
| consistency-checker | 写完对照双时间线 + 伏笔表 + 角色状态                                  |
| character-designer  | 新角色出场时先给设定卡与语言指纹                                       |
| chapter-extractor   | 写完把新事实登记进追踪                                            |
| story-review        | 交付前跑 gate --stage all（draft + final 两层都查）                |
| story-explorer      | 回答「某角色现在什么状态」                                          |
| long-memory-keeper  | 回答「第 30 章埋的线现在什么状态」→ `debt.py due` + `recall.py query` |

## 三、单章规格（分层）

**核心原则：blocking 只守底线，其余全部 advisory。** blocking 越多，模型越倾向于
填格子而不是写故事——给正文留出参差、毛边、不完美，这些才是人味。

**blocking（违反即不合格，由 `spec_check.py` 机检，退出码只看这一层）**：

| 规格 | 值 | 判定 |
| ----- | ------------ | -------- |
| 单章字数 | 1800–3200（`--platform` 可换平台带宽） | blocking |
| **首章字数** | **1500–2000**（首章另有一套更严口径，别按 3200 写到 2500） | blocking |
| 段落长度 | ≤ 90 字；**每章允许 1 段放到 120 字**，>120 或第 2 段长段一律 blocking | blocking |
| AI 味 | = 0（此行是 lint 层 8 类的汇总入口：词族位置规则 1 + 禁止句式 4 + 章尾预告 2 + 条件破折号 1，清零才算过；密度约束、分布级、中文腔均为 advisory，不进退出码） | blocking |
| 章末钩子 | 必须存在（不强制标型号） | blocking |

> **首章为什么要单列**：`spec_check.py` 用 `--first-chapter` 走另一套带宽（1500–2000），
> 而本表此前只写 1800–3200。按表写到 2500 字＝表说合格、机检判死，实测为此返工过 4 轮
> （第 1 章从 2370 删到 1993）。gate 在 `--chapter 1` 时自动切首章口径，不必手动加参数。

上面 5 行是**规格层**的 blocking（首章与常规字数同属「字数」一条规则的两档带宽，故为 4 条规则）；
其中「AI 味」一行与下表 lint 层是同一批规则，只汇总、不另计。gate 去重后的完整 blocking 面是 **12 类**：

| 来源 | 条数 | 逐条 |
|---|---:|---|
| 规格层（本表，`spec_check.py`） | 3 | 字数（含首章带宽）/ 段长 / 章末钩子（「AI 味」行见 lint 层，不重复计） |
| lint 层（`check-ai-patterns.js`） | 8 | `hard-banned-word`（词族位置规则）/ `not-is-comparison` / `reverse-not-is` / `voice-contrast` / `negation-parade` / `em-dash`（条件） / `trailer-ending` / `trailer-summary` |
| 平台层（`check-platform-compliance.py`） | 1 类 | meta 混入 / 站外导流 / 审核敏感形态 |

> 类型名以 `node scripts/check-ai-patterns.js --list-review-classes` 为准（29 类中 8 条 blocking）。
> 合计 3 + 8 + 1 = 12 类。旧版「13 条」把规格层的 AI 味汇总行与 lint 8 类重复计算，已纠正
> （本地修订 2026-11-06）；说「只有四条」同样失实——那只数了规格层的表面行数。

**advisory 不是一个整体：先看 `category`，再决定改不改（v3.0）**

`severity` 说「拦不拦」，`category` 说「**该不该改**」。每条 finding 都带这个字段
（`--list-categories` 可查全表）：

| 分类 | 是什么 | 默认动作 | 典型规则 |
| ----- | ----- | ----- | ----- |
| `ai-tone` | 真 AI 腔：句式模板、推理方式、程度加码 | **改** | `not-is-comparison` / `reasoning-chain-tic` / `intensifier-abuse` |
| `cliche` | 网文水词：眼中闪过、深吸一口气、全场哗然 | **可删可留**（删了只是不那么水） | `cliche-density-tic` / `stock-reaction-tic` / `crowd-sync-tic` |
| `literary` | 文学手法：比喻、引号强调、过滤动词 | **多数保留**，先判断功能 | `metaphor-density-tic` / `quote-emphasis-tic` / `filter-verb-tic` |
| `readability` | 节奏与标点：碎句号、段落呼吸、破折号 | **看这一段该快还是该慢** | `period-stutter` / `em-dash` / `low-connective-density-tic` |

> **为什么要分**：这三四类东西原来的处置方式完全一样——都是一行 advisory，模型看到就改。
> 结果是「眼中闪过」被删对了，「她的眼睛像灯」被当成 AI 比喻删错了。分类之后规则不变、
> 判据不变，变的是**反应**：`ai-tone` 默认改，`cliche` 默认删，`literary` 默认留，
> `readability` 默认看语境。

**advisory（逐条判断，不阻断，只打印清单供人看）**

下表保留原「建议值」仅作**参照基线**，写作时一律换成右侧诊断问句自查——
凡写成「≥N%」「每章 N 个」的目标，模型就会为了达标造假象（P30）。

| 规格 | 参照基线 | 写作时问自己 | 判定 |
| ----------- | ------------ | ----- | -------- |
| 对话占比 | 20%–55% | 「这章谁跟谁说话了？有没有一句是白说的？」 | advisory |
| 句长波动 | 不设配额 | 「整章一个 40 字以上的长句都没有？该铺陈的地方是不是被拆碎了？」 | advisory |
| 段长分布 | 每章 1 段 91–120 | 「全篇都是 30 字以内的短段吗？有没有一段本该长一点？」 | advisory |
| 爽点节点 | ≥2/章 | 「这一章读者拿到什么了？在哪儿拿到的？」 | advisory |
| 感官/微动作 | ≥2 处 | 「这一场有没有一个能闻/能摸/能听见的东西？」 | advisory |
| 三重回报 | ≥1/3 | 「这一章除了推进，还给了什么？」 | advisory |
| 情绪温度 | 不可连 3 章同档 | 「上一章什么温度？连着几章一个温度了？」 | advisory |
| 章末钩子型号 | 建议标注 | 「上一章也是这个型号的钩子吗？」 | advisory |
| 章中钩子 | 建议 40%–60% 位置 | 「这一章的中段有没有一个让人想往下翻的点？」 | advisory |
| 跨章开头/结尾/长度多样性 | 见第八节第 12 步 | 「最近十章的开头是不是都一个样？」 | advisory |

**human-only（机器不判，人工审稿，见 `review-checklist.md` 21 问清单）**：
闲笔、非功能对话、句长波动、伏笔回收自然度、整体节奏松紧。

```bash
# 单看规格明细（写完正文想自查时用；默认查 draft 层）
python3 $S/spec_check.py --file 正文/draft/第N章.md [--first-chapter] [--prev 追踪/_spec-state.json]
# 定稿层自查：把路径换成 正文/final/第N章.md 即可
# 输出分两块：[阻断]（必须清零）与 [建议]（逐条判断）；末尾汇报 blocking / advisory 计数

# 阈值换算与来源：spec_check.py 顶部 THRESHOLDS 字典，每个键都注明出处与层级
# 换平台（起点/七猫/盐言）怎么改：references/platform-adaptation.md
```

> **spec_check.py 与 meta/ 的关系（v2.0）**：爽点 / 回报 / 温度 / 章中钩子 /
> 章末钩子型号这几项机器无法从正文推断，声明写在 `meta/第N章.json`。**spec_check.py
> 读取 meta/第N章.json 中的这些字段做 advisory 判断；若 meta/ 缺失，这些项跳过不报；
> 正文文件不读任何声明块**（早期版本从正文 HTML 注释读，v2.0 起不再读）。
> 门禁会打印一行「声明来源」告诉你它到底读到没有。

> **gate 退出码只由 blocking 决定**：`blocking_count > 0` 才非 0 退出；advisory 打印
> 但不阻断。这是 v2.0 的关键改动——旧版把对话占比/爽点/感官/回报/温度全部当 blocking，
> 逼着模型填格子，反而把正文写得越来越像 AI。

> **声明块怎么写**：爽点/回报/温度/章中/钩子型号这几项机器无法从正文推断，
> 靠 `meta/第N章.json` sidecar 声明。**v2.0 起声明块只写入 `meta/`，不写入
> 正文文件**，导出时由 `export.py` 剥离。字段格式：

```json
{ "chapter": 7, "title": "落锤", "temperature": "热",
  "satisfaction": "全押落锤买回自己", "returns": ["信息", "情绪", "进展"],
  "mid_hook": { "position": 0.5, "desc": "当场开罐见门牌" },
  "end_hook": { "type": "H10", "desc": "雨里伸手" } }
```

> 完整实例见 `examples/minimal-book/meta/第001章.json`，模板见 `templates/meta-sidecar.json`。
