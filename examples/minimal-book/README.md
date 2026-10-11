# 最小可运行样例书

> 用途：**照着这个跑一遍，就知道门禁怎么用、追踪怎么建**。  
> 这不是一个作品，是一本「格式正确的空壳书」，内容为演示而写。
>
> 实测状态（2026-10，v2.1）：三层齐全，全链路可跑通——  
> `gate --stage outline` / `--stage draft` / `--stage final` 均 exit 0，  
> `export` exit 0，导出层零元数据残留。
>
> **v2.1 结构**：本书采用标准三层布局，是正式建书的直接模板：  
> `正文/draft/`（skill 初稿）→ `正文/final/`（人工定稿）→ `导出/`（交付）。  
> 声明块（温度/爽点/回报/章中/钩子型号）全部在 `meta/第001章.json`，正文一个字都不放。

## 怎么跑

```bash
S=~/.workbuddy/skills/novel-forge/scripts
B=~/.workbuddy/skills/novel-forge/examples/minimal-book

# 1. 15 维命中卡（这份是68 分，会被正确拦截——示范"开写前先算"）
python $S/forge.py hitrate --file $B/命中卡.md   # 预期 exit 1

# 2. 双时间线一致性
python $S/forge.py truth --book $B               # 预期 exit 0

# 3. 三层门禁（细纲 → 初稿 → 定稿）
python $S/forge.py gate --book $B --chapter 1 --stage outline   # 预期 exit 0
python $S/forge.py gate --book $B --chapter 1 --stage draft     # 预期 exit 0
python $S/forge.py gate --book $B --chapter 1 --stage final     # 预期 exit 0

# 4. 单章规格明细（blocking / advisory 分层；声明读 meta/）
python $S/spec_check.py --file $B/正文/draft/第001章.md --chapter 1

# 5. 分布级检测 / 跨章多样性（全部 advisory）
python $S/check-distribution.py --file $B/导出/第001章.md
python $S/check-cross-diversity.py --book $B

# 6. 导出（从 final/ 清洗到 导出/，有残留即 exit 1）
python $S/forge.py export --book $B              # 预期 exit 0
```

## 三层目录（正式建书照抄这个）

```
书目录/
├── 正文/
│   ├── draft/       ← skill 初稿，首行标 [DRAFT]，不导出
│   └── final/       ← 人工重写定稿，导出源
├── meta/            ← 温度/爽点/钩子型号 sidecar（第N章.json），不进正文
├── 追踪/
├── 细纲/
├── 卷纲/
└── 导出/            ← 只交付这个，由 export.py 生成
```

导出命令：`python $S/export.py --book <书目录>`（等价于 `forge.py export`，  
从 `final/` 清洗后写 `导出/`，有工程词残留即 exit 1）。

## 目录结构

```
minimal-book/
├── 命中卡.md            15 维评分（示范不合格状态）
├── init.json            tracking_commit.py init 的输入
├── 正文/
│   ├── draft/第001章.md   skill 初稿（首行 [DRAFT]，无任何声明注释）
│   └── final/第001章.md   人工重写定稿（导出源，零声明零工程词）
├── meta/
│   └── 第001章.json      温度/爽点/回报/章中/钩子型号（v2.1 声明块）
├── 细纲/
│   └── 第001章细纲.md     含「字数范围」行，否则 gate 会提示
├── 导出/
│   └── 第001章.md         由 export.py 生成，只交付这个
└── 追踪/                 ← 由 init 生成，不要手改派生视图
    ├── _tracking-state.json
    ├── 上下文.md
    ├── 伏笔.md
    ├── 角色状态/主角名.md
    └── 时间线/
        ├── 作者真相.md
        └── 读者已知.md
```

## 声明块怎么写（v2.1：进 meta/，不进正文）

硬规格里有几项机器无法从正文推断，**必须靠 `meta/第N章.json` 声明**：

```json
{
  "chapter": 1,
  "title": "辞职信",
  "temperature": "热",
  "satisfaction": "当众亮合同逼老王失语",
  "returns": ["信息", "情绪", "进展"],
  "mid_hook": { "position": 0.5, "desc": "门被推开，脚步声停在门外" },
  "end_hook": { "type": "H1", "desc": "手机又亮，指向三年前那个号码" }
}
```

- `temperature`：情绪温度五档（冷/温/热/爆/极爆），用于核对「不可连续同档」。
- `satisfaction`：本章主爽点，写得含蓄时用它替代信号词。
- `returns`：三重回报（信息/情绪/进展）。
- `mid_hook.position`：章中钩子位置（0–1）。
- `end_hook.type`：章末钩子 12 型之一，用于核对「与上章不同型」。

> **不再写正文 HTML 注释**。`spec_check.py` / `gate` 只读 `meta/`；  
> meta 缺失时这些 advisory 项跳过不报，正文照常检查。  
> 模板见 `templates/meta-sidecar.json`。

## 从这个壳开始写自己的书

1. 复制整目录，改名
2. 改 `init.json`：书名、角色、伏笔、真相（字段说明见 `templates/README.md`）
3. 删掉 `正文/`、`meta/`、`导出/`，重跑 `tracking_commit.py init` 重建追踪
4. 照着 `细纲/第001章细纲.md` 的格式写自己的细纲
5. 写初稿 → `正文/draft/` → 声明写 `meta/` → 跑 `gate --stage draft` → 改到 exit 0
6. 人工重写 → `正文/final/` → 跑 `gate --stage final` → `export` → `tracking_commit.py commit`

## 命中卡为什么故意写 68 分

命中卡的价值不是拿高分，是**尽早发现自己还不能开写**。  
这份卡逐维给了真实理由（不是套话），但总分不足 75，`forge.py hitrate` 会拦下来。  
改法示例：把 D5 书名压缩到 ≤12 字、D15 记忆点做排他性，再重打分。
