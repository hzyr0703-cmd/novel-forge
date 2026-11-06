# 资源地图与脚本索引

> 定位：本文件是 **v3.1 从 SKILL.md 第十五、十六节迁出的全量索引**。
>
> SKILL.md 只保留「什么时候读哪个」的路由表（第四节）；本文件回答「这个仓库里到底有什么、每个文件/脚本负责什么」。
>
> 什么时候读：
> - 想找「某个能力在哪个文件」→ 查第一节
> - 要调某个脚本但忘了参数口径 → 查第二节
> - 新写了一份 reference 或脚本，要登记 → 两个表都要加

---

## 一、references

| 文件 | 用途 |
| -- | -- |
| `genre-radar.md` | 赛道图谱、热度三态、情绪刚需、代入感、弃书节点、温度五档 |
| `hit-formula.md` | 15 维模型、判定门槛、打分纪律、势能曲线、九大陷阱、红线校验 |
| `packaging.md` | 书名造词公式机、简介 5 结构、标签布局、书测决策 |
| `opening-surgery.md` | 黄金三章手术、五句链、钩子 12 型、爽点桥段、三重回报 |
| `continuity.md` | **作者真相双时间线**（原创）、伏笔协同、典型事故修法 |
| `recall-and-debt.md` | **写前检索与债务压力**：捞什么、还哪笔、紧急度怎么算 |
| `deslop.md` | **28 类 lint 规则全表**（与 `--list-review-classes` 一致）、按 `category` 分四类（ai-tone/cliche/literary/readability，改法完全不同）、`--diff` 前后对比用法、操作顺序 |
| `rejected-signals.md` | **X 级已否决清单（v3.0）**：10 条「看起来像 AI 味、配对复测后人类反而更多或无差异」的特征 + 推翻依据 + 落地约束。**加规则前必须先查这里**，否则会把真人写法改坏。守卫脚本 `test-rejected-signals.py` |
| `pitfalls.md` | **实操坑位 P1–P31**（v3.1 从 SKILL.md 迁出）：落盘与追踪、检测算法、判据方向三类翻车记录 + 改完跑什么的回归矩阵。**改规则/改脚本/怀疑判据方向前必读** |
| `plot-recovery.md` | 卡文素材库、势能台阶表、重写保护流程、实盘数据回流 |
| `style-refit.md` | **文笔回改手册**（v2.4.3）：删复述就是补长句、四类动作、验收线、回改坑位 R1–R6 |
| `chinese-prose-craft.md` | **中文文笔原则库**（v1.0）：余光中三美德/欧化病清单、汪曾祺语态论、流水句与节奏、回改映射表 |
| `reader-reward.md` | **读者奖励与失败模式**（v1.0）：五通道诊断坐标系、LLM 深层失败模式十种（蒸馏自 haowjy/creative-writing-skills, Apache-2.0）、心理距离/自由间接引语/感官落地 |
| `crossrepeat.md` | 跨章复述检测原理、两条硬约束（必须段级/必须按段落切）、阈值校准表 |
| `consistency-judge.md` | 一致性判据（ConStory-Bench 移植）：C1–C6 分类 + 四条硬约束 + 可靠性警告 |
| `quality-rubric.md` | 五维主观质量量规（stop-slop 移植）：直白/节奏/可信/真实/密度，交付前自查用 |
| `anti-pattern-checklist.md` | **语义级反模式审稿清单**：话疗（BOSS 禁止被说服）、叙事循环、反派降智等。模型审稿执行，不阻断 |
| `platform-adaptation.md` | 起点/七猫/知乎盐言口径差异与阈值改法 |
| `book-teardown.md` | **对标书语义层拆解流程**（v2.2）：七步待办产出剧情模块库（角色/剧情/设定/金手指/爽点抽样）；脚本管统计，模型管语义 |
| `bench-craft.md` | **对标库手法池·防模板版**（v4.3）：五本头部书拆解蒸馏的 121 张手法卡（结构/章级/句级/开篇/卷尾/反派/群像/世界观/升级体系/悬念伏笔/战斗博弈/感情线/身份戏/幽默调剂十四层），防模板五纪律置顶——手法是菜单不是工序，带冷却制与变体制；与 chinese-prose-craft/human-texture/scene-architecture 冲突时以那三份为准 |
| `human-baseline.json` | **人类网文分布基线**（句长/段长/对话占比），供 check-distribution.py 对比。**由 `collect-baseline.py` 从本地人类语料采集生成**，不手填；用 `--show` 核查来源与口径 |
| `human-reaction.md` | **人物反应真实性·人性六条**（v1.0）：群体分裂、情绪错位、私域优先、反应异步、施害者困境、身体先于判断。**形式基线管「句子像不像人写」，本文件管「人像不像人」**，写人群/围观/大会场景前必读 |
| `human-texture.md` | **人味·失控感**（v1.0，逆向自 MIT 项目）：五种「太平均」病灶、三条失控手法（非理性/不完整表达/不闭环）、情绪外显六通道+11 类对照表、侧面揭示三法、冲突越滚越脏、对话六病五原则、语域一致性。三层人味模型的第三层（个体层） |
| `scene-architecture.md` | **场景结构**（v1.0，逆向自 Calliope/MIT，Swain scene-sequel）：目标/冲突/灾难 + 反应/两难/决定 + 接缝只许「因此／但是」 + 网文四类场灾难形态 + 爽点场三例外 + 自检六问。与 human-texture 的「冲突不能十分钟解决」是同一件事的结构版与手感版 |
| `narrative-distance.md` | **叙述距离**（v1.0，逆向自 Calliope/MIT，Gardner 五度）：五档定义 + 四标记判档法 + 过滤器动词清单与三步改法 + 网文落位表 + 三条禁令（不按人称判距离／不把近当好／同场景不跳档） + 自检五问。三层人味模型之外单独一层：镜头 |
| `style-cold-urban.md` | 文风基线·冷硬都市（v2.0 冷燃版，默认男频） |
| `style-myth-urban.md` | 文风基线·都市传说·冷燃怪谈（v1.0，都市异能+规则怪谈专用） |
| `style-colloquial.md` | 文风基线·口语生活 |
| `style-suspense.md` | 文风基线·悬疑紧张 |
| `style-lyrical.md` | 文风基线·抒情古风 |
| `style-humor.md` | 文风基线·幽默吐槽 |
| `setup-guide.md` | Python/Node 运行时配置指引（环境检查 unavailable 时读） |

## 二、templates / examples 与其他

| 文件 | 用途 |
| -- | -- |
| `templates/README.md` | **追踪 JSON 字段说明 + 常见报错对照**（建追踪前必读） |
| `templates/tracking-init.json` | init 输入模板，复制后改成真实内容 |
| `templates/meta-sidecar.json` | v2.0 元数据 sidecar 模板（温度/爽点/钩子型号，与正文分离） |
| `examples/minimal-book/` | **可跑通的最小样例书**，照它建自己的书 |
| `CHANGELOG.md` | 版本脉络（自坑位清单蒸馏）。与 SKILL.md frontmatter 版本号、git 提交三处必须同步 bump |
| `skill-dependencies.json` | 依赖与**功能降级声明**：某依赖缺失时本技能哪些功能受限、如何降级 |

## 三、scripts

| 脚本 | 用途 |
| -- | -- |
| `forge.py` | **统一入口**（gate/hitrate/truth/wordcount/export/dist/diversity/selftest）。`export`/`dist`/`diversity` 均**转发调用**对应独立脚本，行为一致；`gate --stage outline\|draft\|final\|all` 决定检查哪层 |
| `check_environment.py` | **运行时体检**：Python ≥3.10 / Node ≥18 / 依赖脚本是否齐。首次使用或换机后先跑；输出 `unavailable` 时按 `references/setup-guide.md` 补齐，补齐前禁止绕过门禁写正文 |
| `spec_check.py` | **单章规格机检（分层）**：blocking（字数/段长/AI味/章末钩子）+ advisory（对话占比/爽点/感官/回报/温度/钩型/章中钩子）+ 非功能对话句粗判计数（只报数，人审参考）。**声明读 `meta/第N章.json`，正文文件不读声明块**；meta 缺失时相关 advisory 跳过。输出 blocking_count / advisory_count 分别汇报，gate 退出码只看 blocking |
| `export.py` | **导出层**：从 `正文/final/` 剥离全部 HTML 注释、`[DRAFT]`/`[META]` 标记、skill/脚本/路径名，写入 `导出/`。命中 `<!--`/`agent_created`/`forge`/`tracking` 即退出非 0。与 `forge.py export` 是同一入口（后者转发） |
| `check-distribution.py` | **分布级检测**（advisory）：句长标准差/偏度/峰度 vs human-baseline、长句（≥40 字）存在性、段长集中度、长段（≥70 字）存在性、对话占比窗口方差、信息密度连续推进、伏笔回收间隔。`--baseline <json>` 可换基线文件。查「分布像不像人写的」而非「用了哪个词」 |
| `check-cross-diversity.py` | **跨章多样性检测**（advisory）：开头/结尾方式不得连续 3 章同型、章长不得连续 5 章同档、爽点类型不得连续 3 章同类、对话风格（长短句×口癖）不得连续 3 章同型。读 `meta/第N章.json` 取开头/结尾/爽点类型，缺失时回退正文启发式 |
| `collect-baseline.py` | **人类基线采集器**：从本地语料（每本书一子目录）采集句长/段长/章长/对话占比分布，生成 `human-baseline.json`。`--show` 核查现有基线来源、`--dry-run` 先看数字、`--split-chapters` 支持整本单文件 |
| `llm_review.py` | **可选 LLM 语义审稿层**（默认关闭，无 `NOVEL_FORGE_API_KEY` 即跳过）。三模式：①单稿审稿（审稿 1–7 问预审 + 语义反模式线索）②`--coverage` 细纲兑现核对（情节点逐条判 landed/summarized/missing + 越界加戏检测，细纲路径自动推导）③`--pair A B` 配对换位盲评（换位各评一次，一致才判胜负；`--model` 换评委家族）。纯标准库 urllib 直发 OpenAI 兼容端点（默认 token-hub）。**只产 advisory，不参与门禁退出码**；`--dry-run` 核对口径不联网。书稿会发送到所配端点，自行决定启用 |
| `check-ai-patterns.js` | AI 味检测（**28 类规则**，MIT vendored；`--list-review-classes` 是唯一事实源，文档里的数字必须与它一致）。`filter-verb-tic` 抓过滤器动词（看见/听见/感到/觉得/注意到/发现/意识到…）：一段内 ≥3 **种**即报、整章 ≥6/千字即报，同一动词排比不算，对应 narrative-distance.md。`crowd-sync-tic` 抓「群体同步词」（全场哗然/鸦雀无声/齐刷刷/无人敢 单次即报；所有人/众人/全都/人群 密度型），对应 human-reaction.md |
| `check-degeneration.js` | 模型退化检测（复读/截断/占位/工程词泄漏），**章内**视角 |
| `check-crossrepeat.py` | **跨章**自我复述检测（段级 MinHash/Jaccard）。补现有门禁的系统性盲区——AI 复述前文章节时每章内部都不重复，单文件视角 100% 漏检。v2.4.1 起支持 `--since N` 增量（日常 0.2s/章）与 banding 候选剪枝（>40 章自动启用，(b,r) 由阈值自动推导，判定逻辑不变） |
| `check-factconflicts.py` | 事实冲突检测（story-skills 判定移植）：死人复活、回收早于埋设、状态矛盾。只报告不改数据 |
| `check-chinese-tone.py` | 中文 AI 腔密度检测（lieflat 11 项特征移植，283 万字语料背书）：翻案腔、冒号提示语、段首零回指评论、结构同款等。advisory 层不阻断。对话层豁免 |
| `check-platform-compliance.py` | **平台合规检测**（gate 最后一道闸）：meta 混入（TODO/本章字数/作者的话/键值行泄漏）+ 站外导流组合（平台词+联系方式）+ 审核敏感形态。**对话层不豁免**——平台机审不区分引号内外，引号内导流命中以 dialogue_divert 规则上报，报告含对话层扫描行数。blocking 阻断，warn 高误报区人工确认 |
| `check-registry-coverage.py` | **登记覆盖检查**（v2.4.2 新增，advisory-only 不进退出码）：审计「正文有、台账无」的登记遗漏——①近 N 章时间线零登记的静默章 ②注册角色在末章高频出现但该章无任何登记 ③百家姓形态高频 token 疑似新角色未建卡。`--book <书目录> [--chapter N] [--recent 10]`。全部引擎以「事实已登记」为前提，本脚本是该前提的巡检 |
| `normalize-punctuation.js` | 标点归一（去味前必跑） |
| `style-whitelist.js` | `.deslop-whitelist` 加载（依赖项） |
| `tracking_commit.py` | 追踪状态机，派生视图唯一生成者 |
| `import_manuscript.py` | **存量书冷启动导入**（v2.2）：`prepare` 盘点（章号/断号/字数）+ 近 N 章本书文风基线（`<书目录>/文风基线.json`，dist 自动消费）+ 回填清单 + init 预填单；`verify` 拦 TODO 残留/章号错位/空快照/悬置线缺失。只回填近 20 章，更早事实由 recall.py 检索承接 |
| `analyze_book.py` | **对标书拆文引擎**（v2.2）：文风基线.json（同 human-baseline schema，check-distribution `--baseline` 直接消费）+ 文风.md（分布/标点指纹/高频用词/章末收尾类型）+ 拆文报告.md（统计摘要 + 语义层七步待办）。纯统计不猜语义 |
| `wordcount_core.py` | 字数口径（依赖项） |
| `recall.py` | **派生检索层**（BM25/混合），写章前捞回前文事实。索引可随时重建，不持状态。**v2.9 带别名层**：`characters[].aliases`（人物，≤8）与 `追踪/aliases.json`（物件/设定）在**查询侧**扩词族，查「碑」能捞回写「登记册」的段落；`aliases` 子命令看生效词族，`--no-alias` 排查误召回；表改了不用重建索引 |
| `debt.py` | **伏笔债务压力计算**，纯函数派生，不落第二份状态 |
| `ledger.py` | **advisory 挂账台账**：故意违背 advisory 时登记「违背章 + 规则 + 承诺偿还章」，之后每次 gate 显示未还的账，超期标 ⚠ 逾期（仍 advisory、不阻断）。`add/list/fulfill/overdue/check` 五个子命令，台账在 `追踪/advisory-ledger.json`，与追踪台账分开（承诺≠事实） |
| `bench_bm25.py` | **BM25 召回基准**，量化"要不要开向量层"。跑之前先看它给出的实测缺口，别凭感觉配 key |
| `bench_context_cost.py` | **上下文成本测算**，回答"有了 1M 上下文还要不要检索层"。改字数/章数即可重算 |
| `test_schema_compat.py` | **台账 schema 回归测试**，两套等价写法交叉验证，改台账结构后必跑 |
| `test_llm_review_pairing.py` | **盲评/核对逻辑离线测试**（45 用例，不联网不读 key）：换位投票一致性、tally 统计、细纲路径推导、章节定位、JSON 提取鲁棒性（思维链/围栏/嵌套/未闭合思维链/顶层数组）、thinking payload、空 content 诊断、provider 预设与 env 优先级。改 `llm_review.py` 后必跑 |
| `test_llm_review_e2e.py` | **盲评/核对端到端测试**（10 用例）：起本地假 OpenAI 兼容端点走真实 HTTP，验两轮换位真的换了内容、`--model` 覆盖、`--provider` 接管端点、端点不可达时不阻断（只连 127.0.0.1，用假 key） |
| `check-doc-consistency.py` | **文档 / 代码口径对账**（v2.8）：规则数、段长阈值、字数带、平台预设、版本号、deslop 是否覆盖全部规则——全部从代码读事实再回查文档，不一致退非 0。防的是「文档改一处、脚本改另一处」的漂移 |
| `test-rejected-signals.py` | **X 级已否决信号守卫**（v3.0，11 用例）：源码层拦「把已否决词加回 CLICHE/解释链常量」，行为层验「这意味着/换言之/分散比喻」零命中、「引号内破折号不判 blocking」，并做反向对照（同段 3 个比喻仍报）。文档与脚本的 X-ID 双向核对——加一条 X 级结论要改两处，是刻意的摩擦 |
| `test_recall_alias.py` | **别名层回归**（v2.9，13 用例）：查「碑」召回写「登记册」的段落、角色外号召回角色卡、关掉别名层必须退回零命中（对照）、别名表损坏不拖垮检索、别名含「第N章」字面时不误锁章节 |
| `test_ledger.py` | **挂账台账 + 实际回收时序回归**（19 用例）：add/fulfill/overdue/check 全链路、payback≤违背章拒绝、损坏台账退出码 2、gate 集成显示挂账且不改退出码、`promise-payoff-actual-before-plant`（已回收条目 updated_chapter 早于埋设章被报、同章埋收不误报） |

改完跑什么 → `pitfalls.md` 第四节的回归矩阵。
