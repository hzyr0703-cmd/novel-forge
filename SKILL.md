---
name: novel-forge
version: 2.4.1  
description: "中文网文长篇写作全流程引擎：先算后写，再按工程门禁交付。能做：赛道选择与爆款可行性打分（15维命中率 + 势能曲线）、书名简介标签包装、拆解对标书建立剧情模块库（analyze_book.py 文风基线 + book-teardown 语义层）、存量书冷启动导入（import_manuscript.py 只回填近20章 + 本书文风基线）、生成卷纲细纲与章节正文、正文分初稿/定稿/导出三层（分层降 AI 指纹）、零元数据导出、长篇连续性台账维护、去AI味（22 类可机检规则 + 模型退化检测 + 分布级检测 + 跨章多样性检测）、5 套文风基线（冷硬都市/口语生活/悬疑紧张/抒情古风/幽默吐槽）、三轮视角语义审稿（读者/编辑/对家）、改稿对照盲评与细纲兑现核对（llm_review.py --pair 换位盲评：两轮一致才判胜负；--coverage 情节点逐条判落地/越界加戏）、出版/连载/完本与IP 改编评估。硬规格分层：blocking 只守字数/段长/AI味/章末钩子四项底线，其余全部 advisory。何时用：用户要写中文网络小说，或提出题材想法、书名、草稿章节、后台数据并询问能不能爆、为什么扑街、怎么救盘；或要把已有旧稿接进追踪体系、要拆解某本对标爆款；或要判断两版改稿哪版更强、核验细纲有没有落地。常见说法：写小说、写网文、开书、写文、码字、构思题材、写大纲、出卷纲、写细纲、卡文了想不出接下来怎么写、伏笔忘了怎么回收、人物OOC、这章太AI了、去AI味、过审、黄金三章怎么写、开篇怎么写、取书名、写简介、配标签、书测怎么选、数据不好怎么救、正文导出、导入我的旧书、拆这本书、建对标库、这两版哪版好、改前改后对比、细纲写的东西正文有没有写到。也用于拆解某本对标爆款的结构与爽点配方。何时不用：不适用于非虚构写作（论文、公众号文章、商业报告）；不适用于单句润色或改写一段文字而不涉及长篇结构；不适用于角色对话片段、剧情反转点子、世界观设定问答等单点创作请求（这些直接回答即可，无需载入本技能）；不适用于非中文语种的小说创作。平台默认针对番茄小说，起点/晋江/七猫/知乎盐言需调整包装与节奏口径。"  
agent_created: true
---

# novel-forge · 中文网文写作引擎

**核心信条：爆款不是写出来的，是先算出来、再写出来的；而写出来的东西不能崩。**

本技能解决两个方向的失效：

- **动笔前**：选题靠感觉 → 60% 命运在动笔前已定，必须先算
- **动笔后**：长篇靠记忆 → 几十章后伏笔崩、人设漂、AI 味盖不住，必须靠工程

## 一、三条铁律

1. **先算后写**：15 维总分 < 75 **禁止写正文**。先补短板重打分。
2. **门禁先行**：没有细纲**不许创建正文**。这是硬拦截，不是提醒。
3. **机器规则不替代语感**：lint 只负责清零机械感，好句子靠人读。

> **核心原则 · 门禁越严，正文越像 AI**：blocking 只守四条底线（字数 / 段长 /
> AI 味 / 章末钩子），其余全部降为 advisory。旧版把对话占比、爽点、感官、回报、
> 温度全当 blocking，模型为了过门禁开始「填格子」——每章硬塞感官、硬凑爽点，
> 正文反而越来越像机器。这条反直觉但实测成立（详见第十四节坑位 18）。

## 二、流水线

| 阶段 | 动作                                                                            | 门禁                                         |
| -- | ----------------------------------------------------------------------------- | ------------------------------------------ |
| 0  | **默认假设补齐**：未指定项按基线补齐，交付首行标注                                                   | —                                          |
| 1  | **定赛道**：读 `references/genre-radar.md`，判热度三态（非番茄平台加读 `platform-adaptation.md`） | —                                          |
| 2  | **过命中闸门**：15 维打分 + 势能曲线                                                       | `forge.py hitrate` 退出码 0                   |
| 3  | **做包装**：书名 15 式 + 简介 5 版 + 标签 + 封面钩子                                          | D5/D6/D7 达标                                |
| 4  | **立项目**：核心设定 + 金手指三阶段 + 人设骨架 + 分卷 + 三个势能跃升点 + 每卷势能台阶表                         | 势能点齐                                       |
| 5  | **建追踪**：`tracking_commit.py init`（模板见 `templates/`）+ 双时间线文件                   | `forge.py truth` 退出码 0                     |
| 6  | **写细纲**：每章一份，含字数目标 + 情节点                                                      | `forge.py gate --stage outline`            |
| 7  | **写正文（初稿层）**：1800–3200 字/章，写入 `正文/draft/`，爽点位置不固定              | `spec_check.py --file 正文/draft/第N章.md`（刚写完自查） |
| 8  | **去 AI 味**：归一标点 → 查退化 → 查 AI 味 → 改到 blocking=0，advisory 逐条判断                | `forge.py gate --stage draft` 退出码 0      |
| 9  | **跨章校验**：跨章自我复述 + 事实冲突 + 跨章多样性（全书级，非单章）                                               | 同上，不可用 `--skip-*` 绕过                       |
| 10 | **平台合规**：meta 混入 + 站外导流 + 审核敏感形态，blocking 清零才许导出                              | `forge.py gate`（默认含，`--skip-platform` 调试用） |
| 11 | **语义级审稿**：三轮视角（读者/编辑/对家，见第十一节）——话疗（BOSS 禁止被说服）+ 叙事循环在编辑轮执行；可选 `llm_review.py --coverage`（细纲兑现）/`--pair`（换位盲评）打前站 | `references/anti-pattern-checklist.md`  |
| 12 | **定稿层（人重写）**：用自己的语言重述，保留情节/爽点/钩子，写入 `正文/final/`（≥60% 句子人工重写）                    | `forge.py gate --stage final`              |
| 13 | **导出层**：`export.py`（`forge.py export` 转发调用）从 `final/` 剥离全部注释与工程词写入 `导出/`，校验通过才交付               | `forge.py export` 退出码 0                    |
| 14 | **提交状态**：写回追踪，派生视图由脚本重渲染                                                      | `tracking_commit.py check`                 |

> **v2.0 正文三层**：`正文/draft/`（skill 初稿，标 `[DRAFT]`，不导出）→ `正文/final/`
> （人重写定稿，导出源）→ `导出/`（只交付这个，零元数据零注释）。skill 做工程，
> 人做创作——这是降低 AI 指纹最有效的一步。声明块（温度/爽点/回报/章中/钩子型号）
> 一律进 `meta/第N章.json`，正文文件一个字都不放（见第六节）。
>
> 第一次建书或换平台时，先照 `examples/minimal-book/` 跑一遍——那是可运行的门禁样板，  
> 比读文档快。字段说明在 `templates/README.md`。

### 存量书导入与对标拆文（v2.2）

**存量书冷启动接管**（已有几百章的连载/完本旧稿接进追踪体系）——只回填近
N 章，更早的事实由 recall.py 检索层承接，不重建全量台账：

```bash
S=~/.workbuddy/skills/novel-forge/scripts
# 1) 脚本侧：盘点（章号/断号/字数）+ 本书文风基线 + 回填清单 + init 预填单
python $S/import_manuscript.py prepare --project <书目录> --source <原稿目录|单文件> [--recent 20]
# 2) 模型侧：按 .story/import/回填章节清单.md 精读近 20 章，填 init-skeleton.json
#    （八步协议见 .story/import/init-填写说明.md）
# 3) 提交与校验：
python $S/tracking_commit.py init --project <书目录> --input .story/import/init-skeleton.json
python $S/import_manuscript.py verify --project <书目录>   # TODO 残留/章号对齐/悬置线拦截
python $S/forge.py truth --book <书目录> && python $S/recall.py index --project <书目录>
```

**对标书拆文**（建剧情模块库）——脚本管统计，模型管语义：

```bash
# 确定性层：文风基线.json + 文风.md + 拆文报告.md（含语义层待办清单）
python $S/analyze_book.py --book <对标书目录> --out <书目录>/对标/<书名>
# 语义层（角色卡/剧情线/金手指/爽点抽样）：references/book-teardown.md 七步待办
```

`文风基线.json` 与 human-baseline.json 同 schema。`forge.py dist` 在 `--book`
模式下**自动优先**用 `<书目录>/文风基线.json` 当对比基准（存量书导入时由
prepare 生成）——「像不像这一本」比「像不像人类」灵敏；拆文产物放在
`对标/<书名>/文风基线.json`，需要贴着对标书写时用 `--baseline` 显式指定。

## 三、默认假设基线

信息缺失时按此补齐，并在交付首行标注「本次假设：…」：

- 未指定频道 → 男频默认「都市脑洞」，女频默认「现言脑洞」
- 未指定篇幅 → 男频 150 万字 / 女频 100 万字
- 未指定日更 → 6000 字/天（首秀期最低线）
- 未指定视角 → 第一人称受限（代入感最强）
- 未指定文风 → 按赛道自动匹配（见 `references/style-*.md`），默认「冷硬都市」
- 未指定CP → 男频单女主或无女主，女频 1V1双洁
- 未指定平台 → 番茄主站（免费 + 广告分成）
- 未指定情绪 → 男频「爽 + 优越感」，女频「甜 + 爽」
- 未指定书长节奏 → 单章 1800–3200 字，首章 1500–2000

**零问开工**：用户给一句话（题材/梗/书名/草稿/后台数据）即开工，禁止先问一堆问题。

**文风基线可选值**（v2.0 新增，按赛道匹配，用户可显式覆盖）：

| 键         | 适用赛道        | 一句话特征                      |
| --------- | ----------- | -------------------------- |
| 冷硬都市（默认）  | 男频都市/异能     | 短句为主，动词密，叙述克制              |
| 口语生活      | 都市日常/轻松向    | 允许长句重复废话，节奏松，口癖多           |
| 悬疑紧张      | 悬疑/无限流      | 句长交替明显，长句铺陈短句砸点，留白多，对话少    |
| 抒情古风      | 女频/古言       | 长句为主，意象密，节奏慢，允许排比          |
| 幽默吐槽      | 脑洞/轻喜剧      | 自嘲插科打诨，叙述者跳出来说话，节奏跳脱       |

每套基线独立成文，见 `references/style-cold-urban.md` / `style-colloquial.md` /
`style-suspense.md` / `style-lyrical.md` / `style-humor.md`。

## 四、写前必读（强制）

**任何创建或修改正文的动作前，先读本节。只读本文件不算完成。**

按当前时刻读对应 reference，**读完整份，不做局部摘读**：

| 时刻                            | 必读                                  |
| ----------------------------- | ----------------------------------- |
| 定方向/定赛道                       | `references/genre-radar.md`         |
| 换平台（起点/七猫/盐言）                 | `references/platform-adaptation.md` |
| 过命中闸门                         | `references/hit-formula.md`         |
| 做包装                           | `references/packaging.md`           |
| 写开篇/黄金三章                      | `references/opening-surgery.md`     |
| 建追踪/双时间线                      | `references/continuity.md`          |
| **写正文前捞前文 / 查该还哪笔债**          | `references/recall-and-debt.md`     |
| 去 AI 味                        | `references/deslop.md`              |
| **卡文 / 势能台阶 / 重写已提交章 / 实盘复盘** | `references/plot-recovery.md`       |

**读完立即重读**：当前用户请求 + 本章细纲 + 卷纲，在上下文里**记下本轮约束**（用户明确的字数范围、必发生、禁止发生、精确时间锚、停笔点、章尾新债）。references 只提供技法，不得覆盖这些项目事实。

**声明块从 `meta/第N章.json` 读取，不读正文**（v2.0 起；字段格式见第六节 / `templates/meta-sidecar.json`）。写正文时不把温度/爽点/回报/章中/钩子型号写进正文，写完单独落 `meta/第N章.json`。

**交付前逐项复核**：字数带外按收口流程交用户处置，不自动补字；其余项越界不算完成。

任一必需路径不存在或未读完时立即停止，报告准确路径，**不得先写正文再补读**。

## 五、门禁（WorkBuddy 环境下的执行方式）

oh-story 原设计依赖 Claude Code 的 hook 系统与 7 个专职 agent，WorkBuddy 两者皆无。  
**门禁语义由 `forge.py` 子命令显式调用补齐**，每次写正文前必须实跑，不得跳过。

**运行环境**：本技能脚本依赖 Python ≥ 3.10 与 Node ≥ 18。首次使用或换机后先跑 `python $S/check_environment.py`，输出 `unavailable` 时按 `references/setup-guide.md` 补齐后再继续——环境不可用时禁止绕过门禁直接写正文。

```bash
S=~/.workbuddy/skills/novel-forge/scripts

# 细纲门禁 + 连续性门禁（写正文前）
python $S/forge.py gate --book <书目录> --chapter N --stage outline

# 初稿层门禁（写完初稿，查 正文/draft/）
python $S/forge.py gate --book <书目录> --chapter N --stage draft

# 定稿层门禁（人工重写后，查 正文/final/，交付前跑）
python $S/forge.py gate --book <书目录> --chapter N --stage final

# 含正文全检（兼容旧调用的别名，语义同 --stage draft；如需 final 层显式用 --stage final）
python $S/forge.py gate --book <书目录> --chapter N --stage all

# 双时间线一致性（每章写完）
python $S/forge.py truth --book <书目录>

# 跨章自我复述 + 事实冲突（gate 已默认包含，以下为单独排查用）
python $S/check-crossrepeat.py --book <书目录>
python $S/check-factconflicts.py --book <书目录>

# 跨章复述三档工作流（v2.4.1 起）：
#   日常（每章提交后）：增量模式，只查新章对全历史，~0.2s/章
python $S/check-crossrepeat.py --book <书目录> --since <上一章号>
#   里程碑（每 100 章）：全量扫（>40 章自动启用 banding 剪枝，判定逻辑不变）
python $S/check-crossrepeat.py --book <书目录>
#   排查疑点：强制不剪枝的原始全量
python $S/check-crossrepeat.py --book <书目录> --no-banding

# 平台合规（meta 混入 + 站外导流，gate 已默认包含，以下为单独排查用）
python $S/check-platform-compliance.py --book <书目录>
python $S/check-platform-compliance.py --file 正文/draft/第N章.md

# 调试期可临时跳过全书级检查（正式交付勿用）
python $S/forge.py gate --book <书目录> --chapter N --skip-cross --skip-fact --skip-platform

# 15 维命中率复核（动笔前）
python $S/forge.py hitrate --file 命中卡.md

# 单章规格明细（想单独看哪项超标时；默认查 draft 层）
python $S/spec_check.py --file 正文/draft/第N章.md

# 字数口径
python $S/forge.py wordcount --file 正文/draft/第N章.md --target 2300
```

**`--stage` 取值**：`outline`（只查细纲）| `draft`（查 `正文/draft/`，默认层）|
`final`（查 `正文/final/`）| `all`（兼容旧调用的别名，语义同 `--stage draft`；
如需 final 层显式用 `--stage final`）。也可用 `--layer draft|final` 显式覆盖检查层。

**退出码非 0 = 存在硬错误，必须修到通过才允许交付。**

`hitrate` 的四条硬纪律（均已机器实现）：

1. **单维满分不得超过 3 项**——超出即判虚高，拦截。
2. **每维理由必须含可核查的具体物**——理由若整句只有「感觉不错/挺好/还可以」这类套话，  
   无论前后有没有其他词，一律判不合规。这条此前是一个空循环，等于没生效。
3. **合计行的得分列必须与逐维求和一致**——`合计` 行按表格实际列位解析，  
   不允许用固定列序抓到权重列充数（否则写了 68 分会被读成 100 分放行）。
4. **否决项 / 天花板项**照旧：D4、D12 任一为 0 不得开工；D1/D11/D13 任一 ≤2 总分锁 74。

### 专职 agent 分工（单 agent 代演）

原版由 7 个 agent 协作，单 agent 环境下按此分工顺序自检：

| 原 agent             | 单 agent 代演方式                                           |
| ------------------- | ------------------------------------------------------ |
| story-architect     | 写正文前先产出结构判断（本章目标情绪、阻碍、代价）                              |
| narrative-writer    | 按细纲写正文，遵守字数与钩子型号                                       |
| consistency-checker | 写完对照双时间线 + 伏笔表 + 角色状态                                  |
| character-designer  | 新角色出场时先给设定卡与语言指纹                                       |
| chapter-extractor   | 写完把新事实登记进追踪                                            |
| story-review        | 交付前跑 gate --stage all                                  |
| story-explorer      | 回答「某角色现在什么状态」                                          |
| long-memory-keeper  | 回答「第 30 章埋的线现在什么状态」→ `debt.py due` + `recall.py query` |

## 六、单章规格（分层）

**核心原则：blocking 只守底线，其余全部 advisory。** blocking 越多，模型越倾向于
填格子而不是写故事——给正文留出参差、毛边、不完美，这些才是人味。

**blocking（违反即不合格，由 `spec_check.py` 机检，退出码只看这一层）**：

| 规格    | 值            | 判定       |
| ----- | ------------ | -------- |
| 单章字数  | 1800–3200    | blocking |
| 段落长度  | ≤ 90 字       | blocking |
| AI 味  | = 0          | blocking |
| 章末钩子  | 必须存在（不强制标型号） | blocking |

**advisory（逐条判断，不阻断，只打印清单供人看）**：

| 规格          | 建议值          | 判定       |
| ----------- | ------------ | -------- |
| 对话占比        | 20%–55%      | advisory |
| 句长波动        | 每章至少 3 个 40 字以上长句 | advisory |
| 段长分布        | 每章至少 1 段 70–100 字 | advisory |
| 爽点节点        | 每章 ≥ 2，位置不固定 | advisory |
| 感官/微动作      | 每章 ≥ 2 处     | advisory |
| 三重回报        | 每章 ≥ 1/3     | advisory |
| 情绪温度        | 不可连续 3 章同档   | advisory |
| 章末钩子型号      | 建议标注，不强制不同型  | advisory |
| 章中钩子        | 建议 40%–60% 位置 | advisory |
| 跨章开头/结尾/长度多样性 | 见第八节         | advisory |

**human-only（机器不判，人工审稿，见第九节清单）**：
闲笔、非功能对话、句长波动、伏笔回收自然度、整体节奏松紧。

```bash
# 单看规格明细（写完正文想自查时用；默认查 draft 层）
python $S/spec_check.py --file 正文/draft/第N章.md [--first-chapter] [--prev 追踪/_spec-state.json]
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
{
  "chapter": 7,
  "title": "落锤",
  "temperature": "热",
  "satisfaction": "全押落锤买回自己",
  "returns": ["信息", "情绪", "进展"],
  "mid_hook": {
    "position": 0.5,
    "desc": "当场开罐见门牌"
  },
  "end_hook": {
    "type": "H10",
    "desc": "雨里伸手"
  }
}
```

> 完整实例见 `examples/minimal-book/meta/第001章.json`，模板见 `templates/meta-sidecar.json`。

## 七、写作约束（正向 + 禁止）

### 正向写作约束（与禁止清单并列）

禁止清单只能清零机械感，**不能产生人味**。以下规则为 advisory + 人工审稿项，不做
blocking——违背它们不会被门禁拦，但正文会「对而乏味」，最终过不了人的眼睛。

1. **每章至少一处闲笔**：不推进主线、不服务爽点、不埋伏笔。纯气氛、纯环境、纯人物
   小动作。例：一段天气、一个路人、一次走神、一句无关的对话。
2. **每章至少一句非功能对话**：寒暄、跑题、口癖、答非所问、重复。
   **写完后自查**：把本章所有对话抽出来，看有没有一句是「删了不影响剧情」的。
   如果没有，回补一句再往下走。例：「吃了吗」「今儿风大」「你说啥」「哦哦」
   「辣椒要不要」。机器侧线索见 spec_check 的「非功能对话句（粗判）」计数。
3. **句长要有波动**：连续 5 句以上短句后，允许一个长句；连续 3 个长句后，允许一个
   短句砸下来。**每章至少 3 个 40 字以上的长句**（人类基线 p75=34.6 / p90=53，
   40 字以上句约占 15%）。不要全篇同一个呼吸节奏。
4. **允许 5% 的「不完美」**：一个突然的比喻、一个没回收的细节、一个和主线无关的
   闪回。这些不是缺点，是人类写作的自然噪声。
5. **每 3 章允许 1 章弱钩子**：不是每章都要强悬念，停在一个画面、一句台词、一个
   动作上就够了。强钩子连着用反而会钝。
6. **对话连续不超过 5 轮不插动作**（保留原自律）。
7. **每章允许一到两段长段（70–100 字）**：不是硬写，是「本来可以拆成三段但选择
   不拆」——环境铺陈、心理闪回、场景过渡都是自然落点。全部 30 字以内的短段，
   分布上会显得过于均匀（人类基线段长 p90=66.5 / p99=107.8）。

> 分布级检测见 `scripts/check-distribution.py`（句长/段长分布、长句/长段存在性、
> 对话波动/信息密度/伏笔间隔，全部 advisory）；跨章多样性（含对话风格）见
> `scripts/check-cross-diversity.py`。这两个脚本是
> 「查分布」而非「查词」——AI 指纹不在用了哪个词，在整段文本的统计分布像不像人写的。

### 禁止清单（写作时直接照此约束，不必等 lint 报）

> 负面清单比正面描述管用十倍。以下是**写正文时就避开**的项，不是等门禁报出来才改。
> 每条都有机器规则兜底，违反会被 `check-ai-patterns.js` 判为 blocking。

### 禁止词

**硬禁（出现即判 blocking，机器必拦）**

| 类别    | 禁用的词                               | 机检规则               |
| ----- | ---------------------------------- | ------------------ |
| 转折副词  | 不禁、忽然、蓦然、恍惚                        | `hard-banned-word` |
| 情绪直述词 | 非常、十分、极其、很生气、很高兴、很伤心、很漂亮、内心充满、感到愤怒 | `hard-banned-word` |
| 章尾预告词 | 正朝着、压了过去、才刚刚开始、没人知道、没有人知道、谁也不知道    | `trailer-ending`   |
| 工程引用  | 第 5 章、第十一章、上一章、本章、本卷（章号指代情节）      | `工程引用`          |

> 硬禁词只扫**引号外叙述层**——台词里的「你非常生气」是人物口语，不算叙述腔。>  
> 「十分」作量词时豁免（只剩十分钟、十分之一）。  
> **工程引用豁免文内合法分章**（v2.3.1）：故事世界里真有书/法典/碑文分章时不算残留——
> `他翻开《指南》第九章`、`法典第十一章：不可妄言` 里若前置有《》书名号、引号占位或
> 「卷/册/篇/回/书/典/经/谱/志/录/集/碑/文」载体词，则跳过。真残留（`第 5 章在食堂`）
> 都出现在句子中段、无上述语境，照报不误。半角数字+空格形态也认。

**密度约束（不硬禁，但聚集即报 advisory，须逐条判断）**

| 类别    | 词                | 阈值                         |
| ----- | ---------------- | -------------------------- |
| 套式比喻词 | 仿佛、如同、好像、瞬间      | 比喻标记≥7 处且 ≥3/千字 → advisory |
| 高危套词  | 仿佛、一丝、深吸一口气、平静无波 | ≥8 处且 ≥12/千字 → advisory    |

> 比喻类词不是见一个禁一个——比喻承担画面功能。规则只在**成片复现**时报警。删的时候**不要同义词轮换**，改成具体动作、物件或后果。

### 禁止句式（命中即 blocking）

| 型号                  | 形态                  | 改法                        |
| ------------------- | ------------------- | ------------------------- |
| `not-is-comparison` | 「不是A，而是B」           | 删否定铺垫，直接写后项；或改成动作呈现       |
| `reverse-not-is`    | 「是A，不是B」            | 删后置否定，直接写 A 的表现           |
| `voice-contrast`    | 「声音不大…却…」           | 删音量铺垫，写声音落进现场的具体效果        |
| `negation-parade`   | 「没有X，没有Y」/「没X没Y只是Z」 | 删否定清单，直接写现场实际有什么          |
| `em-dash`           | 破折号 `——`            | 打断→动作 beat；拖长音→省略；插入说明→逗号 |
| `trailer-ending`    | 「正朝着…压了过去」          | 结尾停在具体动作、画面或台词上           |
| `trailer-summary`   | 「这一夜注定…」「命运的齿轮」     | 结尾用最后一个具体动作落地，不替读者盖章      |

### 禁止结构

**硬禁（blocking）**

- 章末收在「事情办完了」「然后他睡着了」——蔡格尼克效应，悬念消失即掉追更
- 章末套话：「欲知后事如何，且听下回分解」/「这一夜，无人入眠」/「命运的齿轮，开始转动」/「未完待续」

**写作约束（无对应 blocking 规则，靠自律）**

- 单段超过 90 字（与第六节 blocking 段长上限一致；手机屏约 3–4 行）
- 对话连续超过 5 轮不插动作或环境（读起来全是引号，节奏碎）
- 连续三段以「他」「她」开头

**密度型（advisory，读上下文判断）**

| 情形               | 规则型号             | 处置               |
| ---------------- | ---------------- | ---------------- |
| 叙述层用 1–4 字引号做强调  | `quote-emphasis` | 只留反讽/转述必要的，其余去引号 |
| 同段摆 5 个以上动作动词    | `action-list`    | 合并琐碎步骤；打斗编排可保留   |
| 长段无镜头切换（> 200 字） | `long-paragraph` | 按动作/视线断段         |


## 八、写正文流程

```
0. recall.py index            ← 重建派生检索索引（台账或正文有变动时）
1. gate --stage outline        ← 硬门禁：细纲不存在则退出 1
2. 读本章细纲 + 卷纲 + 上下文卡（含双时间线防剧透 + 素材库）+ 文风基线
3. debt.py due                ← 本章该偿哪几笔债（含紧迫度与处理建议）
4. recall.py query "<本章要素>" ← 按本章场景检索前文事实，捞回该想起的设定与伏笔
5. 按细纲写正文（初稿）→ 正文/draft/第N章.md，首行标 [DRAFT]
   遵守 blocking 四项 + 尽量满足 advisory；主动埋闲笔/非功能对话/句长波动
5.5 自查对话层与分布盲区：抽一遍本章对话，确认至少一句「删了不影响剧情」的
     非功能对话；再确认长句（≥3 个 40 字以上）与长段（1–2 段 70–100 字）。
     缺哪项补哪项——advisory 不拦机器，但写的时候就要落地，不能只靠人兜底
6. 温度/爽点/回报/章中/钩子型号 → 写入 meta/第N章.json（v2.0 起不进正文）
7. node $S/normalize-punctuation.js  ← 先归一标点，否则 lint 位置漂移
8. node $S/check-degeneration.js      ← 退化必须为 0，否则重生成该章
9. node $S/check-ai-patterns.js       ← blocking 清零，advisory 逐条判断
10. python $S/spec_check.py --file 正文/draft/第N章.md  ← blocking 清零；advisory 清单逐条判断
11. python $S/check-distribution.py    ← 分布级检测（advisory，人审前先看）
12. python $S/check-cross-diversity.py ← 跨章开头/结尾/长度/爽点多样性（advisory）
13. python $S/forge.py gate --stage draft ← 完整门禁（lint + 规格 + 跨章复述 + 事实冲突 + 平台合规）
14. 按 references/anti-pattern-checklist.md 审稿 ← 话疗/循环模式（模型执行，advisory）
15. 人工审稿清单逐条答「是」（第十一节）→ 定稿层重写 → 正文/final/
16. python $S/export.py → 导出/第N章.md  ← 零元数据交付文件
17. tracking_commit.py commit         ← 写回追踪，派生视图自动重渲染
18. 追加爆款命中卡 / 算法对齐卡       ← 每份交付尾部附
```

**第 3、4 步是写章前的强制动作，不是可选优化。** 详见 `references/recall-and-debt.md`——  
没有它们，AI 写第 30 章会"忘了"第 3 章埋的线，这是长篇崩盘的头号原因。

**第 15 步是 v2.0 最关键的改动**：skill 产出的是初稿，人用自己的语言重述一遍（≥60%
句子重写）才能进定稿层。无论元数据怎么处理，AI 直出的正文本身才是最大的指纹来源。

## 九、正文分层与目录约定（v2.0）

**不要把 `SKILL.md`、`templates/`、`examples/`、`references/` 放进书稿目录。**
书稿目录只保留：

```
书目录/
├── 正文/
│   ├── draft/       ← skill 初稿，首行标 [DRAFT]，不导出
│   └── final/       ← 人工定稿，导出源
├── meta/            ← 温度/爽点/钩子型号等元数据 sidecar
│                       命名与正文同名、三位零填充：第001章.json
│                       字段格式见第六节 / templates/meta-sidecar.json
├── 追踪/
├── 细纲/
├── 卷纲/
└── 导出/            ← 只交付这个，由 export.py 生成
```

- **初稿层**（`正文/draft/`）：skill 产出，给作者做素材/骨架/参考，标 `[DRAFT]`。
- **定稿层**（`正文/final/`）：人重写，保留情节/爽点/钩子，换掉句式/节奏/段落结构/
  用词习惯，至少 60% 句子由人重写，保留修改记录。
- **导出层**（`导出/`）：从 `final/` 复制，零元数据、零注释、零工程词，只交付这个。

项目要打包/备份/投稿前，先把 skill 目录移出去。

## 十、汇报纪律

**给用户看的汇报只讲三件事**：

1. 写了/改了什么（章名、发生了什么）
2. 要用户定的事（一句白话问题 + 白话选项 + 推荐默认）
3. 下一步

**禁止**：写脚本名、字段、参数名、状态码、内部清单名。编号必带故事标签，如「伏笔 F057（那封信的去处）」。  
检查结果用一句白话带过：「自查过设定和前文，没发现冲突」。

机器行（回执、Notice、Fallback）只放最末一行「技术备注：」。

## 十一、人工审稿清单（v2.3 三轮视角）

机器跑完 gate 后，先按 `references/anti-pattern-checklist.md` 过**三轮语义审稿**
（读者轮 · 每章 / 编辑轮 · 高潮章+每5章 / 对家轮 · 开书换卷数据下滑时，
逐轮独立给结论），再过以下人工十问。每项答「是/否」，**全部答「是」才可交付**。

**机器辅助（可选，advisory）**：三轮审稿可先用 `llm_review.py` 打前站——
- **编辑轮**：`--coverage` 核细纲情节点有没有真演成场景（`missing`/`summarized` 多的章优先返工）；
- **对家轮 / 改稿决策**：`--pair 旧版 新版` 做换位盲评，**两轮一致才判胜负**，
  避免"我改过所以觉得更好"的自我确认偏差；
- **单章预审**：默认模式跑审稿 1–7 问，人来复核第 8–10 问（作者身份问，机器不代答）。

1. 这章有没有至少一处闲笔？
2. 这章有没有至少一句非功能对话？
3. 这章的句长有没有波动？
4. 这章有没有一个「没用但真实」的细节？
5. 这章的钩子是不是自然落下来的，而不是硬塞的？
6. 这章的对话读起来像不像人说的？
7. 这章如果删掉，读者会不会觉得少了什么？
8. 这章的正文，我能不能用自己的话复述一遍而不改变情节？
9. 这章有没有哪一句，是我自己写的，不是 skill 给的？
10. 这章贴出去，我会不会觉得「这不像我写的」？

> **可选 LLM 语义审稿层（v2.3，默认关闭）**：`scripts/llm_review.py` 三种模式——
> ① **单稿审稿**（默认）对第 1–7 问做预审（非功能对话精判、话疗/循环/反派降智等反模式线索）；
> ② **细纲兑现核对**（`--coverage`）把细纲「分段骨架」的情节点逐条判
> `landed`(演成场景)/`summarized`(一句带过)/`missing`，并列出正文里**细纲没授权、会影响
> 后续章**的新剧情事实——缺的多说明细纲没落地，越界多说明模型在自行加戏；
> ③ **配对换位盲评**（`--pair 稿A 稿B`）同章两版随机分 A/B，**交换顺序各评一次，
> 两次一致才算胜负**（位置偏见被对称抵消，结果可复现），用于改前/改后、v5/v6 总纲、
> 不同模型同章质量的对照——评委须与写手**不同模型家族**（`--model` 换）。
> 边界（红线不破）：核心门禁与确定性检测保持**零 API、零网络**；本层无
> `NOVEL_FORGE_API_KEY` 时自动跳过并打印人工回退清单，任何输出**不参与**门禁
> 退出码；第 8–10 问是作者身份问，永远人工。启用后正文段落会发送到所配
> 端点——书稿敏感，是否启用自行决定。`--dry-run` 可先核对口径不联网。
>
> 用法速查（细纲路径缺省自动推导 `<书根>/细纲/<章名>-细纲.md`）：
> ```bash
> S=~/.workbuddy/skills/novel-forge/scripts
> export NOVEL_FORGE_API_KEY=<key>
> python $S/llm_review.py --provider zhipu --book <书目录> --chapter 12 --coverage  # 细纲兑现核对
> python $S/llm_review.py --provider zhipu --pair 改前.md 改后.md --outline 细纲.md   # 换位盲评
> python $S/llm_review.py --provider zhipu --pair v5版.md v6版.md --model deepseek-v4-flash
> ```
> **`--provider` 预设**（省得记 base_url，避开踩过的坑）：`zhipu`（智谱官方 + glm-4.6）、
> `zhipu-flash`（+ glm-4-flash，更快更省）、`tokenhub`（自有网关）。
> **显式 `--provider` 时端点整组以预设为准**（v2.3.1 修）：此前 base_url 会被
> `NOVEL_FORGE_BASE_URL` 抢先、model 却取预设，造成「端点/model 混搭」——正是坑①的
> 复发形态（用户 export 过 token-hub 后加 `--provider zhipu` 仍然 401）。现在会给
> 「已接管端点」提示；`--model` 仍可单独覆盖评委。
> 三个已修的坑：① **智谱 key 打 token-hub 会 401**，必须走 `open.bigmodel.cn/api/paas/v4`；
> ② **glm-4.6 / glm-4.5-\* 默认开「思考」**，content 会是空的（只剩思维链），表现为
> 「超时 / 回复不是有效 JSON」——本脚本默认发 `thinking={"type":"disabled"}` 关掉，
> `--thinking` 可强制打开；③ 响应里的思维链/围栏/夹带说明都能被正确抠出 JSON
> （v2.3.1 加固：未闭合思维链里的示例 JSON 不再冒充结果、顶层数组不再被降级为对象）。
> 若模型只回思维链，报错会直接说明原因，不会伪装成 JSON 解析失败。

**第 8、9、10 题是关键。** 任何一题答「否」，定稿层需要重写。这份清单是 v2.0
「人做创作、skill 做工程」的最后一道保险——机器能保证这章不违规，只有人能保证
这章像人写的。

## 十二、边界与安全

- **不代写侵犯著作权的内容**。用户提供他人作品原文时，只做结构拆解与启发式改写，不逐句洗稿
- **不鼓励**涉政、色情、赌博、毒品、自杀自残细节、未成年不良描写
- 收入表述一律注明「以作者后台实际结算为准」，禁止承诺具体金额或成功率
- 禁止伪造平台官方文件；不确定处标注「待番茄作家中心公告确认」
- 赛道热度/平台机制知识截止 **2026-09**，跨 3 个月使用标注「建议联网核实」
- **AI 合规**：不得输出未经人工改写的 AI 直出正文并标称为可发布内容

## 十三、失败策略（不允许自行"圆回来"）

模型失败时最容易自由发挥。**禁止一切形式的自作主张修补**，一律停下报告。

| 异常         | 禁止做法       | 正确做法                          |
| ---------- | ---------- | ----------------------------- |
| 新章节与已有设定矛盾 | 自行编过渡情节圆过去 | 停止写入，输出冲突报告（位置/已有设定/新内容/建议方案） |
| 缺少细纲或设定    | 凭记忆续写      | 明确告知缺哪些文件，**不猜不补**            |
| 门禁未通过      | 换个说法解释成已通过 | 报告 exit code 与命中的具体规则         |
| 字数欠字       | 自动加戏凑字数    | 交用户决定是否补（欠字不加戏是硬纪律）           |
| 角色 OOC     | 强行解释角色行为   | 标记 OOC 段落，列出性格冲突点，请求确认        |
| 双时间线冲突     | 改一边让它自洽    | 报告哪条真相与读者认知矛盾，等用户裁定           |
| 伏笔未回收      | 忽略或草草带过    | 列出所有挂起伏笔及计划回收章，提示风险           |

**输出格式**：冲突报告要落到可执行颗粒度——给出**具体位置**（第几章第几段）、**冲突双方**、**2–3 个修复方案**及各自代价，由用户选。**不要给「建议润色」这种空话。**

**stop 优先于 wrong**：宁可不写，不可写错。落盘前任何一项门禁不过就停下来报告，不要先写再补。

## 十四、实操坑位（《燃识》实战反馈 + 2026-10 修复记录）

1. **声明块不进正文（v2.0 起）**：`check-degeneration.js` 会把出现在文件最末尾的 `<!-- ... -->` 型 HTML 注释判为 truncated（blocking）。**v2.0 起声明块（含钩子型号）一律写 `meta/第N章.json`，不写正文**；仅当手写正文里临时保留了注释时才适用「注释放在最后一段正文之后但不做文件最后一行」这条。
2. **声明块的冒号用全角`：`**：仅适用于正文里残留的 HTML 注释声明（v2.0 起改为 meta/ JSON，正常不再出现）。JSON 里用标准 ASCII 冒号。
3. **tracking_commit 快照精确匹配**：transaction 的 `character_snapshots` 必须与`delta.character_changes` 里的 core 角色**一一对应**——多带未变更角色的快照会报错，漏带已变更角色的快照也会报错。
4. **修订 delta 是完整记录**：revision 模式下 delta 会整份替换该章记录，只写改动项会静默清掉原有的角色变化/伏笔变化（脚本会 WARNING）。重写流程见 `references/plot-recovery.md` 第三节。
5. **「部分揭示」语义**：`forge.py truth` 拦截「未揭示却出现在读者已知表」。凡读者在正文里亲眼看到过的谜团（哪怕只看到谜面），登记为「部分揭示」+ 揭示章；完全没露过面的真相才能标「未揭示」。
6. **长篇章数口径**：规划卷章数时先定每章字数（如 2300）再算章数（25 万字≈110 章），不要凭感觉写「每卷 25 章」。
7. **normalize-punctuation 会改写省略号/引号**：对正文做字符串替换后，以最新文件内容为准再做精确匹配编辑。
8. **爽点声明按就近生效计算**：显式爽点标记视为「描述它前面刚写完的那一段」，所以声明集中写在文末也能正确归属，不必塞进正文中间。
9. **模板是严格白名单**：`tracking_commit.py init` 的 JSON 多一个键就报错，`_说明` 这类注释键也不行。字段说明看 `templates/README.md`。
10. **别找不存在的脚本**：早期版本的报错信息指向过 `forge.py plan` 和 `storyctl.py`，两者都不属于本技能，已全部改为指向实际存在的命令。
11. **按段落切分，别按句子**：跨章复述检测最初按句子切分再合并到目标长度，结果「原创段 + 复述段」混进同一指纹，9 个自然段被压成 2 段，注入的复述完全漏检。段是作者写下的完整语块，跨段混合会直接摧毁段级检测的前提。详见 `references/crossrepeat.md` 第二节。
12. **测试脚本别用 `os.system`跑中文路径**：`os.system` 会把参数经 `cmd.exe` 传，中文目录名变成「文件名、目录名或卷标语法不正确」，子进程根本没执行，测试却显示「未检出」——静默通过比失败危险。用 `subprocess.run([...])` 传 argv 列表。
13. **测试数据必须走正规 schema**：事实冲突检测第一版按猜想的 schema 编写，5 条死亡规则依赖台账白名单里不存在的字段，手搓测试数据绕过了 schema 校验，9/9 全绿是假绿。测试数据要么经 `tracking_commit.py` 正规写入，要么对照 `normalize_snapshot` 白名单逐字段构造。修复时已把 `died_in`/`revived_in` 正式加入快照白名单（可选字段，旧台账不受影响）。
14. **MinHash 别用 XOR salt 捷径**：`md5(s) XOR salt_i` 看着像哈希族，实际 XOR 双射≠min-wise independent，各 permutation 强相关，估计有系统偏差。用 Carter-Wegman 2-universal 族（`(h1 + i·h2) mod 2^64`）。同理，salt 派生放 shingle 循环外，否则 O(shingles×64) 次 sha256。
15. **段数门槛会漏掉整段逐字复制**：跨章复述的 `min_flagged_segments=2` 拦「轻度呼应」，但一整段 0.89 相似度的逐字搬运也被它拦了。已加 `single_segment_jaccard=0.8` 出口：单段几乎逐字照样报。改这类阈值时想清楚它防的是什么、会误放什么。
16. **论述文规则直接套网文会集体失灵**：lieflat 的结构同款检测用 10 字句长阈值——网文「山风从谷底涌上来」9 字短句全被滤掉，检测归零。翻案腔正则 `[^，。]` 不容逗号——网文口语化「与其说…，不如说…」全漏。移植任何语料驱动的规则，先拿目标文体的真实文本验证触发，再信它的数据。
17. **正则要先对上游例句自测**：拟人化喻体的上游例句「像一位智慧的导师」是**前修饰**，初版正则只写了后修饰形态，照抄上游数据却漏检上游自己的例句。移植正则后第一件事：拿来源文档的原始例句跑一遍。
18. **blocking 别贪多（v2.0 核心教训）**：旧版把对话占比/爽点/感官/回报/温度全当 blocking，模型为了过门禁开始「填格子」——每章硬塞感官、硬凑爽点，正文反而越来越像机器。v2.0 只留四项 blocking（字数/段长/AI味/章末钩子），其余降 advisory。**门禁越严，正文越像 AI**——这条反直觉但实测成立。
19. **分布检测看的是「像不像人」，不是「对不对」**：`check-distribution.py` 的句长标准差/偏度对比人类基线（`references/human-baseline.json`），判的是「这个分布人类写不写得出来」。基线不是手填的——**由 `scripts/collect-baseline.py` 从本地人类语料采集生成**，跑 `python scripts/collect-baseline.py --show references/human-baseline.json` 可核查来源、书数、章数与采集口径。它是**人工审稿的前置线索**，不是违规判定——所有输出都 advisory，不阻断。
20. **对话窗口方差要够窗口数才判**：`check-distribution.py` 的对话波动检测最初 ≥3 窗就判，2000 字的章只有 3–4 窗，噪声大，人类作品频繁误报。改成 ≥5 窗才判，误报消失。**小样本上的方差没有意义**。
21. **导出层宁可不导，不可带脏**：`export.py` 先清洗再校验——HTML 注释、`[DRAFT]`、skill/脚本名、路径全剥掉，剥完还残留 `agent_created`/`forge`/`tracking` 就退出非 0。平台看到的就是导出文件，一处残留可能就是一次判 AI。
22. **裸声明行会漏**：声明块正常包在 `<!-- -->` 里会被剥离，但作者手写时偶有裸的「温度：冷」独立成行——`export.py` 单独用正则抓这种「短行+键+冒号+短值」的形态，避免误伤正文里正常的冒号句。
23. **advisory 要靠「写完自查」落地（v2.1.1）**：v2.0 把非功能对话、段长波动、句长波动降为 advisory 后，模型优先满足 blocking，advisory 容易被跳过——《燃识》四章实测，闲笔进了叙述层，对话层仍全是功能句。解法不是升回 blocking（那会回到「填格子」），而是第八节 5.5 步的显式自查点：机器不拦，写完必须自己过一遍。
24. **段长分布是 AI 指纹的重灾区**：四章《燃识》实测，闲笔、环境细节都加了，但段长全部在 30 字以内，从不接近人类基线 p90（66.5 字）。短段不是错，**永远短段**才是 AI 腔——人类会在铺陈、闪回、过渡时写长段。check-distribution.py 已加长句/长段存在性检测（1b/2b）补这个盲区；写作侧解法见第七节正向约束第 7 条。

## 十五、资源地图

| 文件                                     | 用途                                                  |
| -------------------------------------- | --------------------------------------------------- |
| `references/genre-radar.md`            | 赛道图谱、热度三态、情绪刚需、代入感、弃书节点、温度五档                        |
| `references/hit-formula.md`            | 15 维模型、判定门槛、打分纪律、势能曲线、九大陷阱、红线校验                     |
| `references/packaging.md`              | 书名造词公式机、简介 5 结构、标签布局、书测决策                           |
| `references/opening-surgery.md`        | 黄金三章手术、五句链、钩子 12 型、爽点桥段、三重回报                        |
| `references/continuity.md`             | **作者真相双时间线**（原创）、伏笔协同、典型事故修法                        |
| `references/recall-and-debt.md`        | **写前检索与债务压力**：捞什么、还哪笔、紧急度怎么算                        |
| `references/deslop.md`                 | 22 类 lint 规则全表、blocking/advisory 分流、操作顺序            |
| `references/plot-recovery.md`          | 卡文素材库、势能台阶表、重写保护流程、实盘数据回流                           |
| `references/crossrepeat.md`            | 跨章复述检测原理、两条硬约束（必须段级/ 必须按段落切）、阈值校准表                  |
| `references/consistency-judge.md`      | 一致性判据（ConStory-Bench 移植）：C1–C6 分类 + 四条硬约束 + 可靠性警告   |
| `references/quality-rubric.md`         | 五维主观质量量规（stop-slop 移植）：直白/节奏/可信/真实/密度，交付前自查用        |
| `references/anti-pattern-checklist.md` | **语义级反模式审稿清单**：话疗（BOSS 禁止被说服）、叙事循环、反派降智等。模型审稿执行，不阻断 |
| `references/platform-adaptation.md`    | 起点/七猫/知乎盐言口径差异与阈值改法                                 |
| `references/book-teardown.md`          | **对标书语义层拆解流程**（v2.2）：七步待办产出剧情模块库（角色/剧情/设定/金手指/爽点抽样）；脚本管统计，模型管语义 |
| `references/human-baseline.json`       | **人类网文分布基线**（句长/段长/对话占比），供 check-distribution.py 对比。**由 `scripts/collect-baseline.py` 从本地人类语料采集生成**，不手填；用 `--show` 核查来源与口径 |
| `references/style-cold-urban.md`       | 文风基线·冷硬都市（默认男频）                                     |
| `references/style-colloquial.md`       | 文风基线·口语生活                                          |
| `references/style-suspense.md`         | 文风基线·悬疑紧张                                          |
| `references/style-lyrical.md`          | 文风基线·抒情古风                                          |
| `references/style-humor.md`            | 文风基线·幽默吐槽                                          |

| `references/setup-guide.md` | Python/Node 运行时配置指引（环境检查 unavailable 时读） |  
| `templates/README.md` | **追踪 JSON 字段说明 + 常见报错对照**（建追踪前必读） |  
| `templates/tracking-init.json` | init 输入模板，复制后改成真实内容 |  
| `templates/meta-sidecar.json` | v2.0 元数据 sidecar 模板（温度/爽点/钩子型号，与正文分离） |  
| `examples/minimal-book/` | **可跑通的最小样例书**，照它建自己的书 |

## 十六、脚本

| 脚本                             | 用途                                                                                                                               |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| `forge.py`                     | **统一入口**（gate/hitrate/truth/wordcount/export/dist/diversity/selftest）。`export`/`dist`/`diversity` 均**转发调用**对应独立脚本，行为一致；`gate --stage outline\|draft\|final\|all` 决定检查哪层 |
| `spec_check.py`                | **单章规格机检（分层）**：blocking（字数/段长/AI味/章末钩子）+ advisory（对话占比/爽点/感官/回报/温度/钩型/章中钩子）+ 非功能对话句粗判计数（只报数，人审参考）。**声明读 `meta/第N章.json`，正文文件不读声明块**；meta 缺失时相关 advisory 跳过。输出 blocking_count / advisory_count 分别汇报，gate 退出码只看 blocking |
| `export.py`                    | **导出层**：从 `正文/final/` 剥离全部 HTML 注释、`[DRAFT]`/`[META]` 标记、skill/脚本/路径名，写入 `导出/`。命中 `<!--`/`agent_created`/`forge`/`tracking` 即退出非 0。与 `forge.py export` 是同一入口（后者转发） |
| `check-distribution.py`        | **分布级检测**（advisory）：句长标准差/偏度/峰度 vs human-baseline、长句（≥40 字）存在性、段长集中度、长段（≥70 字）存在性、对话占比窗口方差、信息密度连续推进、伏笔回收间隔。`--baseline <json>` 可换基线文件。查「分布像不像人写的」而非「用了哪个词」 |
| `check-cross-diversity.py`     | **跨章多样性检测**（advisory）：开头/结尾方式不得连续 3 章同型、章长不得连续 5 章同档、爽点类型不得连续 3 章同类、对话风格（长短句×口癖）不得连续 3 章同型。读 `meta/第N章.json` 取开头/结尾/爽点类型，缺失时回退正文启发式 |
| `collect-baseline.py`          | **人类基线采集器**：从本地语料（每本书一子目录）采集句长/段长/章长/对话占比分布，生成 `references/human-baseline.json`。`--show` 核查现有基线来源、`--dry-run` 先看数字、`--split-chapters` 支持整本单文件 |
| `llm_review.py`                | **可选 LLM 语义审稿层**（默认关闭，无 `NOVEL_FORGE_API_KEY` 即跳过）。三模式：①单稿审稿（审稿 1–7 问预审 + 语义反模式线索）②`--coverage` 细纲兑现核对（情节点逐条判 landed/summarized/missing + 越界加戏检测，细纲路径自动推导）③`--pair A B` 配对换位盲评（换位各评一次，一致才判胜负；`--model` 换评委家族）。纯标准库 urllib 直发 OpenAI 兼容端点（默认 token-hub）。**只产 advisory，不参与门禁退出码**；`--dry-run` 核对口径不联网。书稿会发送到所配端点，自行决定启用 |
| `check-ai-patterns.js`         | AI 味检测（22 类规则 + 硬禁词表，MIT vendored）                                                                                               |
| `check-degeneration.js`        | 模型退化检测（复读/截断/占位/工程词泄漏），**章内**视角                                                                                                  |
| `check-crossrepeat.py`         | **跨章**自我复述检测（段级 MinHash/Jaccard）。补现有门禁的系统性盲区——AI 复述前文章节时每章内部都不重复，单文件视角 100% 漏检。v2.4.1 起支持 `--since N` 增量（日常 0.2s/章）与 banding 候选剪枝（>40 章自动启用，(b,r) 由阈值自动推导，判定逻辑不变）                                                   |
| `check-factconflicts.py`       | 事实冲突检测（story-skills 判定移植）：死人复活、回收早于埋设、状态矛盾。只报告不改数据                                                                               |
| `check-chinese-tone.py`        | 中文 AI 腔密度检测（lieflat 11 项特征移植，283 万字语料背书）：翻案腔、冒号提示语、段首零回指评论、结构同款等。advisory 层不阻断。对话层豁免                                             |
| `check-platform-compliance.py` | **平台合规检测**（gate 最后一道闸）：meta 混入（TODO/本章字数/作者的话/键值行泄漏）+ 站外导流组合（平台词+联系方式）+ 审核敏感形态。**对话层不豁免**——平台机审不区分引号内外，引号内导流命中以 dialogue_divert 规则上报，报告含对话层扫描行数。blocking 阻断，warn 高误报区人工确认 |
| `normalize-punctuation.js`     | 标点归一（去味前必跑）                                                                                                                      |
| `style-whitelist.js`           | `.deslop-whitelist` 加载（依赖项）                                                                                                      |
| `tracking_commit.py`           | 追踪状态机，派生视图唯一生成者                                                                                                                  |
| `import_manuscript.py`         | **存量书冷启动导入**（v2.2）：`prepare` 盘点（章号/断号/字数）+ 近 N 章本书文风基线（`<书目录>/文风基线.json`，dist 自动消费）+ 回填清单 + init 预填单；`verify` 拦 TODO 残留/章号错位/空快照/悬置线缺失。只回填近 20 章，更早事实由 recall.py 检索承接 |
| `analyze_book.py`              | **对标书拆文引擎**（v2.2）：文风基线.json（同 human-baseline schema，check-distribution `--baseline` 直接消费）+ 文风.md（分布/标点指纹/高频用词/章末收尾类型）+ 拆文报告.md（统计摘要 + 语义层七步待办）。纯统计不猜语义 |
| `wordcount_core.py`            | 字数口径（依赖项）                                                                                                                        |
| `recall.py`                    | **派生检索层**（BM25/混合），写章前捞回前文事实。索引可随时重建，不持状态                                                                                        |
| `debt.py`                      | **伏笔债务压力计算**，纯函数派生，不落第二份状态                                                                                                       |
| `ledger.py`                    | **advisory 挂账台账**：故意违背 advisory 时登记「违背章 + 规则 + 承诺偿还章」，之后每次 gate 显示未还的账，超期标 ⚠ 逾期（仍 advisory、不阻断）。`add/list/fulfill/overdue/check` 五个子命令，台账在 `追踪/advisory-ledger.json`，与追踪台账分开（承诺≠事实）          |
| `bench_bm25.py`                | **BM25 召回基准**，量化"要不要开向量层"。跑之前先看它给出的实测缺口，别凭感觉配 key                                                                                |
| `bench_context_cost.py`        | **上下文成本测算**，回答"有了 1M 上下文还要不要检索层"。改字数/章数即可重算                                                                                      |
| `test_schema_compat.py`        | **台账 schema 回归测试**，两套等价写法交叉验证，改台账结构后必跑                                                                                           |
| `test_llm_review_pairing.py`   | **盲评/核对逻辑离线测试**（45 用例，不联网不读 key）：换位投票一致性、tally 统计、细纲路径推导、章节定位、JSON 提取鲁棒性（思维链/围栏/嵌套/未闭合思维链/顶层数组）、thinking payload、空 content 诊断、provider 预设与 env 优先级。改 `llm_review.py` 后必跑                                  |
| `test_llm_review_e2e.py`       | **盲评/核对端到端测试**（10 用例）：起本地假 OpenAI 兼容端点走真实 HTTP，验两轮换位真的换了内容、--model 覆盖、--provider 接管端点、端点不可达时不阻断（只连 127.0.0.1，用假 key）                        |
| `test_ledger.py`               | **挂账台账 + 实际回收时序回归**（19 用例）：add/fulfill/overdue/check 全链路、payback≤违背章拒绝、损坏台账退出码 2、gate 集成显示挂账且不改退出码、`promise-payoff-actual-before-plant`（已回收条目 updated_chapter 早于埋设章被报、同章埋收不误报）                    |

修改任何脚本后跑 `python forge.py selftest`（改动前先看它是否全绿）。  
碰了台账字段（`_pick` / `_as_text` 那几处别名）额外跑  
`python scripts/test_schema_compat.py`（交叉验证两种 schema 结果一致）。  
改了 `llm_review.py` 额外跑  
`python scripts/test_llm_review_pairing.py`（离线逻辑）+ `python scripts/test_llm_review_e2e.py`（真实 HTTP 路径）。  
改了 `ledger.py` 或 factconflicts 伏笔规则额外跑 `python scripts/test_ledger.py`。  
第三方来源与修改约定见 `scripts/VENDOR-NOTICE.md`——**不要静默调整 lint 阈值**，  
每个数字都对应一条语料校准记录；规格阈值与分层改 `spec_check.py` 的 `THRESHOLDS`，  
换平台见 `references/platform-adaptation.md`，分布基线见 `references/human-baseline.json`。
