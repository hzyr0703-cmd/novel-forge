# 去 AI 味操作规程

> `check-ai-patterns.js` 有 22 类规则 + 一组硬禁词，每条阈值都以真实中文网文语料校准
> （七猫 5584 章 + 知乎盐言 3983 篇 + 《万疆》20 章 + demo 20 章），误报率已压至 0.4% 量级。
>
> **重要定位**：这是本地风格可读性门禁，**不是 AIGC 检测器评分**。功能性人味文本会被标为待审，
> 而不是被强制改写。没有任何外部检测器参与打分。
>
> **它管什么、不管什么**：`check-ai-patterns.js` 只管「像不像机器写的」；
> 「该有的东西有没有」（对话占比、爽点密度、感官、钩子轮换）由 `spec_check.py` 管。
> 两者一起跑完才算过门禁，单跑任何一个都不算。

## 一、怎么跑

```bash
# 单文件（默认有 finding 即 exit 1）
node scripts/check-ai-patterns.js --check 正文/第001章.md

# 只看 blocking（忽略 advisory），门禁用这个
node scripts/check-ai-patterns.js --json --fail-on=blocking 正文/第001章.md

# 批量
node scripts/check-ai-patterns.js --fail-on=blocking 正文/*.md

# 查看完整分类表
node scripts/check-ai-patterns.js --list-review-classes
```

输出格式：`文件:行:列: [级别] 类型: 改写建议 (原文片段)`

## 二、blocking 规则（必须清零）

blocking = 表面形态确定，**就地改写即可，不需要判断**。

| 类型 | 形态 | 改法 |
|---|---|---|
| `hard-banned-word` | SKILL.md 第七节硬禁词（不禁/忽然/蓦然/恍惚/非常/十分/极其/很X生/内心充满/感到愤怒） | 按提示改；**只扫引号外**，台词里的口语不算 |
| `not-is-comparison` | 「不是A，而是B」 | 删掉否定铺垫，直接写后项；或改成动作呈现 |
| `reverse-not-is` | 「是A，不是B」 | 删掉后置否定，直接写 A 的表现 |
| `voice-contrast` | 「声音不大…却…」 | 删音量铺垫，写声音落进场子的具体效果 |
| `negation-parade` | 「没有X，没有Y」/「没X没Y只是Z」 | 删否定清单，直接写现场实际有什么 |
| `em-dash` | 破折号 `——` | 打断→动作 beat；拖长音→省略；插入说明→逗号 |
| `trailer-ending` | 「没人知道/没有人知道/才刚刚开始/正朝着…压了过去」 | 结尾停在具体动作、画面或台词上 |
| `trailer-summary` | 「这一夜注定…/这一切都结束了/命运的齿轮」 | 结尾设定要落成最后一个具体动作，别替读者盖章 |

> `hard-banned-word` 是 2026-10 补的：此前 SKILL.md 第七节声明这批词「出现即判blocking、
> 机器必拦」，但没有任何规则实现——写「忽然」「非常」零命中。现在逐条落地。
>
> **量词豁免**：「只剩十分钟」「十分之一」里的「十分」是量词不是程度副词，不报。

## 三、advisory 规则（提示，需判断）

advisory = 同一形态**可能承担叙事功能**，要读上下文决定保留还是改。

### 机械类（可改可留）

| 类型 | 触发条件 | 修法 |
|---|---|---|
| `period-stutter` | 连续 6 个 ≤5 字短叙述句 | 合并成中长句，补回画面 |
| `long-paragraph` | 单段 > 200 字 | 按镜头/动作/视线切换断段 |
| `micro-action-tic` | 「了下/了一下」式轻量补语 ≥5 处且 ≥6/千字 | 合并动作 beat，换具体细节 |
| `cliche-density-tic` | 高危套词 ≥8 处且 ≥12/千字 | **不要同义词轮换**，改成具体动作/物件/后果 |

### 语义类（必须读上下文）

| 类型 | 触发条件 | 修法 |
|---|---|---|
| `negation-pair-tic` | 「没A，也没B」≥3 处 | 改成他做了什么，只留真要强调的 |
| `stock-reaction-tic` | 部位+轻微动作等通用反应 ≥4 处且 ≥1.5/千字 | **逐处做删除测试**：只标注情绪、不改变选择/关系/物件/动作结果的删掉 |
| `action-list-tic` | 同段 ≥5 个动作动词 + ≥4 个分隔符 | 合并琐碎步骤；打斗编排可保留 |
| `abstract-summary-tic` | 命运/棋局/这一刻终于明白等 ≥3 处且 ≥4/千字 | 回到角色当下可见的文件、动作、物理后果 |
| `metaphor-density-tic` | 像/好像/仿佛等 ≥7 处且 ≥3/千字 | 留最有叙事功能的少数，**不要换新比喻** |
| `reasoning-chain-tic` | 知道/明白/这意味着/必须等 ≥8 处、≥2 个桶、≥18/千字 | 把判断落到动作、物件、对话、现场反馈 |
| `system-notice-formality-tic` | 方括号规则行 ≥4 行且硬规则词密集 | 只在载体内部白话化部分硬词 |
| `overcompressed-prose-tic` | 叙述段≥45 且 ≤15 字段占 58%+ | **禁止机械注水**，先通读判断 |
| `low-connective-density-tic` | 功能词 <100/千字且长承接句 <8% | 补必要连接和中长句群 |
| `quote-emphasis-tic` | 叙述层 1-4 字引号强调 ≥3 处 | 留真正反讽/转述必要的，其余去引号 |
| `formulaic-parallelism` | 「至于X不X，怎么X」/ 同动词「不V A，不V B」 | 若只是复述细纲，压成一次判断 |

## 四、白名单豁免

在书目录下建 `.deslop-whitelist`，每行一个字面量片段，扫描时整段排除：

```
<!-- 去味:跳过 -->
职业名称（如"律师"）
专有名词
固定搭配
```

只豁免**本书确实需要保留**的字面片段。不要把整个句子塞进去——那是绕过门禁不是解决问题。

## 五、模型退化检测

AI 写到中段会退化且自己意识不到，`check-degeneration.js` 专查这类：

```bash
node scripts/check-degeneration.js --fail-on=blocking 正文/第001章.md
```

| 类型 | 含义 |
|---|---|
| 复读/打转 | 长句重复、连续相同行 |
| 截断 | 文件末尾无终止标点 |
| 占位/拒绝语 | 作为AI / 我无法继续 / 此处省略 / 乱码 |
| 工程词泄漏 | 细纲 / 情节点 / 本章 / 下一章漏进正文 |

blocking 命中后的正确反应：**重新生成该章**，把finding 作为约束喂回去，限制重试次数，然后带着证据交给用户判断。**不要手工逐条修补**——退化是整体性的，逐条改会留下缝。

> 该脚本刻意保守：网文常用的排比/复沓/弹幕刷屏/重复台词是正常节奏，短句与台词重复已豁免。

## 六、标点归一

```bash
node scripts/normalize-punctuation.js 正文/*.md
```

统一全半角与中文标点。**去味前先跑标点归一**，否则 lint 报的位置会因为引号/破折号混用而漂移。

## 七、操作顺序

```
1. node normalize-punctuation.js    先归一标点
2. node check-degeneration.js       查退化（若有 blocking，先重生成）
3. node check-ai-patterns.js        查 AI 味
4. 清 blocking（机械改写）
5. 逐条判断 advisory
6. 重跑 check-ai-patterns.js 直到 blocking = 0
7. forge.py gate --stage all        完整门禁
```

## 八、AI 合规

平台打击「AI 直出」，但**不禁止 AI 辅助**。

- 不要把 AI 一次性生成的正文直接标称为可发布内容
- 每章必须经过：blocking 清零 + advisory 人工判断 + 至少一遍自己的语感通读
- 机器规则只能去掉机械感，**不能替你写出好句子**