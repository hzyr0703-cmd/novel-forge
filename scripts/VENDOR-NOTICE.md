# 第三方代码溯源

本目录部分脚本为第三方开源项目原样引入，未作修改或重写。

## check-ai-patterns.js / check-degeneration.js / style-whitelist.js / normalize-punctuation.js

- **来源**：https://github.com/zenstory-ai/oh-story-claudecode/tree/main/skills/story-deslop/scripts
- **项目**：oh-story-claudecode（ZenStory AI）
- **许可**：MIT License，Copyright (c) 2025-2026 oh-story-claudecode
- **引入原因**：AI 味检测规则（现 28 类，其中有 8 条 blocking）与模型退化检测，每条阈值均以真实中文网文语料（七猫 5584 章 + 知乎盐言 3983 篇 + 《万疆》20 章 + demo 20 章）校准，误报率已压至 0.4% 量级。**重写会丢失校准精度**，故原样保留。
- **本地验证**：2026-10-04 于 Windows 11 + Node 运行通过，实测 5 类AI 腔全部命中。

## tracking_commit.py / wordcount_core.py

- **来源**：https://github.com/zenstory-ai/oh-story-claudecode/tree/main/skills/story-long-write/scripts
- **许可**：MIT License，同上
- **引入原因**：追踪状态机与字数口径。`_tracking-state.json` 唯一权威、派生视图禁手改的设计是长篇一致性的核心。
- **本地验证**：2026-10-04 `tracking_commit.py check` / `storyctl.py wordcount measure` 运行通过。

## check-factconflicts.py（判定逻辑移植，非 vendor）

- **来源**：https://github.com/danjdewhurst/story-skills/tree/main/src
- **许可**：MIT License，Copyright (c) 2025 danjdewhurst
- **引入方式**：**移植判定规则，不是 vendor 源码**。
- **为什么不 vendor**：story-skills 的项目模型是 story bible 文件夹 + Markdown
  frontmatter（11000 行，含 scan.js 2074 行 / publishing.js 272 行的文件扫描层）。
  novel-forge 用`_tracking-state.json`，两者数据结构完全不同，硬接要拖入整个
  文件扫描层。移植时只取其确定性判定规则，按 novel-forge 台账结构重写。

移植的判定（逐条对应上游 err(...)）：

| 本地 rule | 上游判定 |
|-----------|---------|
| `revival-before-death` | revival-before-death |
| `revived-without-death` | revived-without-death |
| `death-status-mismatch` | death-status-mismatch |
| `revival-status-mismatch` | revival-status-mismatch |
| `dead-character-appears` | 死亡窗口 `deathWindow()` 的 deadIn 判定 |
| `promise-payoff-before-plant` | promise-payoff-before-plant / clue-payoff-before-plant |
| `promise-payoff-missing` | promise-payoff-missing |
| `plant-not-drafted` | plant-not-drafted |
| `reveal-chapter-missing` | 上游 revIn 相关校验（按台账结构适配） |
| `payoff-overdue`（新增） | chekhovWarning 的「回收已过期」警告思路 |
| `reveal-in-future`（新增） | 上游 outline/planned 区分思路（按台账结构适配） |

**schema 依赖（2026-10-06 修复时确立）**：死亡/复活判定依赖
`characters.<name>.died_in` / `revived_in` 结构化字段。这两个字段
**已加入 `tracking_commit.py` 的 snapshot 白名单**（可选字段，旧台账
不受影响），写入时即受约束：复活不能早于死亡、有复活必须有死亡。
`check-factconflicts.py` 在此之上做跨章一致性判定。

- **本地验证**：2026-10-06 二次修复后 18/18 用例通过（含
  tracking_commit 白名单闭环验证）；`examples/minimal-book` 零误报；
  selftest 与 schema 兼容测试全绿。
- **首次实现的教训**（保留备忘）：第一版按猜想的 schema 编写，
  `open_threads` 按「带章号的 dict 列表」解析——真实 schema 是纯
  字符串列表，且 `died_in`/`revived_in` 根本不在白名单。手搓测试
  数据绕过了 schema 校验，9/9 全绿是假绿。**测试数据必须经过
  tracking_commit 的正规写入路径，或至少对照其白名单构造**。
- **改动范围**：无上游代码被复制，全部为本技能原创实现。

## references/consistency-judge.md（判据移植）

- **来源**：https://github.com/Picrew/ConStory-Bench/tree/main/prompts/timeline_plot.md
- **许可**：MIT License，Copyright (c) 2026 Picrew
- **引入原因**：引入其**四条硬约束**（精确引用+成对定位、空数组合法、
  排除文学手法、存疑偏向文学解读）与错误分类思路。**未 vendor 其数据集与
  benchmark 代码**——那需要付费 API key，与本技能「零 API 调用」的设计红线冲突。
- **本地改动**：分类按中文网文重构为 C1–C6，新增 C6「战力与规则矛盾」
  （原判据没有，这是玄幻/仙侠网文最高频的崩点）；「网文特例」一节把
  H3「排除文学手法」具体化为分身设定豁免、闭关时长算法等网文特有形态。
- **本地验证**：2026-10-06 人工审阅，确认四条约束完整保留、分类无英文残留。

---

## check-crossrepeat.py（本技能原创）

段级 MinHash 跨章自我复述检测，零第三方依赖（仅 `hashlib` + `re`）。
算法参考公开文献（MinHash / Jaccard 估计），实现为本技能原创。

**两条实测得到的工程结论，改动前务必先读 `references/crossrepeat.md`：**

1. **必须段级，不能章级**。真实语料上复述对的汉明距离是 25 和 19，
   而全库最相似的**无关**章节对只有 10——复述段只占全章约 8%，
   指纹信号被正常内容稀释到噪声之下，不可分辨。
2. **必须以段落边界为切点**。早期实现按句子切分再合并，导致「原创段 +
   复述段」混入同一指纹，Jaccard 被稀释到阈值下而完全漏检（实测 9 个
   自然段被压成 2 段）。段级检测的前提是「段是作者写下的完整语块」。

## check-chinese-tone.py（特征移植 + 网文适配，非 vendor）

- **来源**：https://github.com/larashero3-dotcom/lieflat-less-ai-tone
- **许可**：MIT License
- **引入原因**：11 项经 283 万字对照语料（629 篇，AI 300/人类 329）实证成立的
  中文 AI 腔特征，其中 4 项是 check-ai-patterns.js 没有的中文专项：
  段首零回指评论（R=4.4）、冒号滥用（R=9.4）、拟人化喻体（R=7.3）、
  相邻句结构同款（R=2.0）。同一研究证伪了 15 项流行认知（比喻、正文设问、
  句长均匀度等），本技能据此**避免反向操作**。
- **引入方式**：移植特征正则 + R 值数据，**按网文语境重写阈值与豁免**：
  - 对话层豁免（strip_dialogue）：台词是人物口语，只查叙述层
  - 密度阈值取上游 AI 侧均值 × 2（网文修辞密度天然高于论述文）
  - 结构同款句长阈值从上游 10 字降到 6 字（网文短句是常态，
    10 字阈值把「山风从谷底涌上来」这类句子全部滤掉，检测对网文失灵）
  - 翻案腔正则允许「与其说…，不如说」中间含逗号（论述文正则的
    `[^，。]` 把网文口语化变体全部漏掉）
  - 拟人化喻体支持前修饰形态（上游例句「像一位智慧的导师」本身
    就是前修饰，初版正则只匹配后修饰，漏检）
- **本地验证**：2026-10-06 双向验证 15/15——AI 腔注入逐项检出（10 特征 + 2 结构项）、
  正常网文零误报、对话豁免生效、密度门槛防刷屏。
- **改动范围**：无上游代码复制，按上游 RESEARCH.md 数据重写实现。

## check-platform-compliance.py（思路借鉴，全部重写，非 vendor）

- **来源（思路参考，均无 LICENSE）**：
  - https://github.com/guohei/fanqie-plus — `gate_check.py` 的「meta 混入」与
    「平台违规」两个检查**方向**
  - https://github.com/304769384-png/fanqie-novel-skill — `commercial-review.md`
    的平台审查**视角**
- **许可状态**：两仓库 GitHub 元数据均显示无 LICENSE——法律上保留全部权利，
  **未复制其任何代码与正则**。本文件规则集、分级（blocking/warn）、
  组合判定逻辑、「对话层不豁免」原则均为本技能原创。
- **与上游思路的关键分歧（为什么不能照抄）**：
  1. 上游把「微信/QQ/手机号」一律 blocking——那是为**全自动 agent 流水线**
     设计的（正文由机器生成，出现联系方式必是泄漏）。novel-forge 的用户是
     人主导写作，都市文里「加我微信」「打赏」「138 开头的虚构号码」是正当
     情节道具。故改为**组合判定**：平台词 + 联系方式形态（≥6 位数字/≥5 位
     字母数字串）才 blocking；孤立词一律 warn 人工确认。
  2. 上游对对话层同样全扫（这点保留）——平台机审不区分引号内外，台词里
     的联系方式照样触发人工复审。这与 check-chinese-tone.py 的对话豁免
     相反，两层的豁免语义必须分开。
- **教训（保留备忘）**：CONTACT_HANDLE 内含 `|`，拼接进外层组合正则时
  未包非捕获组，`\d{6,}` 被顶层 `|` 拆成独立分支——任何 6 位以上数字
  （包括剧情里的虚构手机号）都命中「导流」。所有内含 `|` 的子模式拼接
  时必须整体包组。
- **本地验证**：2026-10-07 selftest 4 用例（干净零命中 / dirty 全命中 /
  都市文合法词只 warn / 手机号不误判导流）+ 《燃识》9 章零 blocking 误报。

## references/anti-pattern-checklist.md（判据思路借鉴，全部重写）

- **来源（思路参考，无 LICENSE）**：https://github.com/304769384-png/fanqie-novel-skill
  v2.0.1 的「话疗模式检测」「叙事循环检测」两个审计维度
- **为何不写成机检脚本**：话疗（BOSS 被说服）、循环模式（情节骨架重复）
  是**事件层**问题，无稳定文本形态，纯正则误报不可接受；novel-forge 运行时
  即模型在跑，故降级为**模型执行的审稿清单**（advisory），配合机器侧已有的
  代理指标（spec_check 情绪温度连续同档 = 循环的机器信号）。
- **改动范围**：判定步骤、豁免条款（盟友说服不算话疗、有铺垫的退意豁免）、
  同族结构陷阱表均为本技能原创；未复制上游任何文本。
- **本地验证**：2026-10-07 人工审阅。此清单依赖模型判断，无自动用例。

## references/quality-rubric.md（量规移植）

- **来源**：https://github.com/hardikpandya/stop-slop（MIT）的 Scoring 部分
- **引入原因**：五维主观质量评分（直白/节奏/可信/真实/密度，各 1–10，
  总分 <35 建议返工，单维 ≤2 必须重写该维）。补 gate 只有机检、
  无质量维度的空白。
- **引入方式**：转写为中文网文语境。五维锚点全部重写；增加网文豁免
  （对话直白、类型契约、字数压力）；增加评分纪律（证据要求、双 35 线、
  不凑分原则——尤其引用 lieflat 证伪清单防止反向操作）。
  上游 phrases.md/structures.md 英文规则清单**未移植**（中文侧已由
  check-ai-patterns.js + check-chinese-tone.py 覆盖，重复移植是双报）。
- **定位**：主观层，不进 gate 自动流程，交付前人工/自查用。

## 修改约定

若需改动上述文件，必须在文件顶部追加本节说明，注明改动范围与重新校准依据。
**不要静默调整阈值**——每个数字都对应一条语料校准记录，擅自改动会把误报率推回 5% 以上。

---

## 本技能原创部分

以下为本技能新增，非上游内容：

- `forge.py` — 统一命令入口（gate / hitrate / truth / wordcount），含细纲门禁与双时间线一致性校验
- `spec_check.py` — 单章硬规格机检。其中对话占比**上限**（40/50%）与**上行趋势**
  检查为 2026-10-07 新增：阈值思路参考 304769384-png/fanqie-novel-skill 的
  实战审阅经验（无 LICENSE，未复制文本），口径按本技能「引号内字数/全文字数」
  重新换算（上游是段落占比口径，数值不可直接搬用），出处注释见 THRESHOLDS
- `references/hit-formula.md` — 15 维打分与势能曲线（移植自 tomato-viral-novel，MIT）
- `references/packaging.md` — 书名 15 式与简介 5 结构（移植自 tomato-viral-novel，MIT）
- `references/opening-surgery.md` — 黄金三章手术（移植自 tomato-viral-novel，MIT）
- `references/continuity.md` — 作者真相双时间线操作规程（原创）
- 其余 references 与 SKILL.md 为原创整合