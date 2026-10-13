# novel-forge CHANGELOG

> 从 SKILL.md 实操坑位清单与正文版本标注蒸馏而成，不新增发明内容。
> 详细的翻车原理见 SKILL.md 第十四节坑位 1–24，此处只留版本脉络。

## v2.4.2（当前）

- 新增 `check-registry-coverage.py` 登记覆盖检查（advisory-only）：静默章扫描 / 角色提及未登记 / 疑似新角色未建卡三向审计「正文有、台账无」——所有确定性引擎以「事实已登记」为前提，这是该前提的第一个巡检器
- 修 `check-distribution.py --book --chapter` 章号解析：原硬编码「第%03d章.md」精确名，真实书稿的「第041章-标题后缀.md」全找不到；改为与 `forge.py find_chapter_file` 同口径递归搜索（final 优先、兼容零填充与标题后缀）

## v2.4.1

- `check-crossrepeat.py` 新增 `--since N` 增量模式（日常 ~0.2s/章）与 banding 候选剪枝（>40 章自动启用，判定逻辑不变）；`--no-banding` 强制原始全量排查
- 坑位 15：跨章复述加 `single_segment_jaccard=0.8` 出口——单段几乎逐字复制不再被 min_flagged_segments=2 拦住漏报

## v2.4

- 跨章复述检测重构：必须段级、必须按段落切分（坑位 11——按句子切分导致 9 个自然段压成 2 段、注入复述完全漏检）
- MinHash 改用 Carter-Wegman 2-universal 族，弃用 XOR salt 捷径（坑位 14）
- 事实冲突检测加入 `died_in` / `revived_in` 快照白名单可选字段（坑位 13）

## v2.3.1

- `llm_review.py` 显式 `--provider` 时端点整组以预设为准（修 base_url 被 env 抢先、model 取预设的「混搭 401」）
- JSON 提取加固：未闭合思维链里的示例 JSON 不再冒充结果、顶层数组不再被降级为对象
- 工程引用检测豁免文内合法分章（《书》第九章/法典第十一章等载体语境，坑位相关）

## v2.3

- 新增 `llm_review.py` 可选 LLM 语义审稿层（默认关闭、零 key 自动跳过、不参与门禁退出码）：单稿审稿 / `--coverage` 细纲兑现核对 / `--pair` 换位盲评（两轮一致才判胜负）
- 人工审稿清单升级为三轮视角（读者/编辑/对家）
- glm 系模型默认发 `thinking=disabled` 防 content 为空

## v2.2

- 新增 `import_manuscript.py` 存量书冷启动导入（prepare/verify，只回填近 20 章，更早事实由 recall 承接）
- 新增 `analyze_book.py` 对标书拆文引擎（文风基线.json + 文风.md + 拆文报告.md）
- `forge.py dist --book` 自动优先用本书文风基线做对比（「像不像这一本」灵敏于「像不像人类」）

## v2.1.1

- 坑位 23/24：《燃识》四章实测发现 advisory 靠模型自觉必被跳过 → 第八节加 5.5 显式自查点；段长全短段是 AI 指纹重灾区 → check-distribution 加长句/长段存在性检测（1b/2b），写作侧加正向约束第 7 条

## v2.0（分层重构）

- **blocking 只守四项底线**（字数/段长/AI味/章末钩子），对话占比/爽点/感官/回报/温度全部降 advisory——坑位 18：门禁越严正文越像 AI，实测成立
- 正文三层：`正文/draft/`（[DRAFT] 不导出）→ `正文/final/`（人重写 ≥60%，导出源）→ `导出/`（零元数据交付）
- 声明块从正文 HTML 注释迁移到 `meta/第N章.json` sidecar（坑位 1）
- spec_check 声明来源改读 meta/，meta 缺失时相关项跳过不阻断

## v2.2 之前（早期修复，摘要）

- 硬禁词只扫引号外叙述层；「十分」量词豁免
- 命中卡四条硬纪律机器化：单维满分 ≤3 项、理由须含可核查具体物（原为空循环）、合计行按列位解析防 68 分读成 100 分
- 修正报错指向不存在的 `forge.py plan` / `storyctl.py`（坑位 10）
- 测试统一 `subprocess.run` argv 列表，弃 `os.system`（中文路径坑位 12）
- `check-distribution.py` 对话波动 ≥5 窗才判（坑位 20）
- 分布基线由 `collect-baseline.py` 从本地语料采集生成，不手填（坑位 19）
