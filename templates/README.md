# 追踪初始化与提交：JSON 模板说明

> `tracking_commit.py` 的输入是 JSON，但历史上没给过模板——长篇流程的第一步
> 反而是唯一没有样例的一步。这份文档 + `templates/` 下的两个模板解决它。
>
> **先读这份文档再写 JSON**：schema 是严格白名单，多一个键就报
> `contains unsupported fields`，少一个必填键就报 `must not be empty`。

## 一、最省事的用法：直接用 draft 生成预填单

不要手写 transaction JSON。流程是：

```bash
S=~/.workbuddy/skills/novel-forge/scripts

# 1) 先建追踪（导入已有稿件时用；新书从第 1 章开始可跳过，直接 draft）
python $S/tracking_commit.py init --project <书目录> --input templates/tracking-init.json

# 2) 每写完一章，让脚本按当前状态生成预填单
python $S/tracking_commit.py draft --project <书目录> --chapter N --output 追踪/工作区/N.json

# 3) 你只补 delta 里该改的字段（结果、承诺、角色变化、伏笔、时间线、约束）
# 4) 提交
python $S/tracking_commit.py commit --project <书目录> --input 追踪/工作区/N.json
```

`draft` 会把角色快照、伏笔、时间线的**当前值**预填好——你只写变化，
不用凭空回忆格式。**这是唯一推荐的写法。**

## 二、init 输入字段（templates/tracking-init.json）

init 的语义是「**导入已经发生的事实**」，所以所有章号都不得超前于 `last_chapter`。

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema_version` | ✅ | 固定 `1` |
| `book_title` | ✅ | 书名 |
| `last_chapter` | ✅ | 已写到的章数。伏笔/真相的章号不得大于它 |
| `context` | ✅ | 见下|
| `character_snapshots` | | 角色名 → 状态快照。新书留空 `{}` |
| `foreshadow` | | 伏笔数组。新书留空 `[]` |
| `timeline_events` | | 真相数组。新书留空 `[]` |

### context（只有这 6 个键，多一个都报错）

| 键 | 说明 |
|---|---|
| `position` | **必填**，且 `story_time` 与 `scene` 不能同时为空 |
| `long_term_constraints` | ≤ 6 条 |
| `active_character_names` | ≤ 6 个角色名，跨平台不得重名 |
| `continuity_risks` | ≤ 5 条。门禁会提示「开写前先读这段」 |
| `recent_chapters` | **仅 init**：≤ 3 条，每条 `{chapter, summary}` |
| `next_chapter_commitments` | **仅 init**：≤ 5 条 |

### context.position（只收这 4 个键）

```json
{ "volume": "第一卷", "volume_start_chapter": 1,
  "story_time": "第5章结束的傍晚", "scene": "律所办公室" }
```

### character_snapshots

键是角色名。快照的 8 个基础字段 + 2 个可选死亡字段：

- 字符串：`identity`（身份处境）、`location`（当前所在）、`goal`（当前目标）、`state`（当前处境）
- 字符串数组：`abilities_resources`、`relationships`、`knowledge`、`open_threads`
- 可选死亡字段（2026-10-06 起）：`died_in`（死亡章号）、`revived_in`（复活章号）
  - 角色死亡时**强烈建议登记** `died_in`——`check-factconflicts.py` 靠它拦「死人复活」事故
  - 约束：复活不能早于死亡；写了 `revived_in` 必须写 `died_in`
  - 旧的快照（无这两个字段）完全兼容
  - `state` 文本须与死亡事实一致：已死未复活的角色，state 里要看得出来（如「坠崖身亡」），否则会报 `death-status-mismatch`

### foreshadow

| 字段 | 约束 |
|---|---|
| `id` | 必须形如 `F001`（F + ≥3 位数字） |
| `summary` | ≤ 360 字节 |
| `planted_chapter` | ≤ `last_chapter`，否则报 `cannot be in the future` |
| `planned_resolution_chapter` | 可为 `null`；给了就不能早于埋设章 |
| `status` | `已埋` / `已回收` / `已过期` / `放弃` |
| `importance` | `高` / `中` / `低` |

### timeline_events（双时间线）

| 字段 | 约束 |
|---|---|
| `id` | 必须形如 `E001` |
| `story_time` | 故事内时间，与章号无关 |
| `objective_fact` | 作者侧客观事实，≤ 480 字节 |
| `reader_knowledge` | **读者已知部分**，≤ 480 字节 |
| `reveal_status` | `未揭示` / `部分揭示` / `已揭示` |
| `reveal_chapter` | `未揭示` 时必须为 `null`；其余状态必填且 ≤ `last_chapter` |
| `characters` | ≤ 12 个角色名 |

> **最容易踩的坑**：`reader_knowledge` 写成 `objective_fact` 的复制品。
> 前者是读者看到的谜面，后者是真相全貌。写成一样，`forge.py truth`
> 的泄底检查就全失效了。详见 `references/continuity.md` 第三节。

## 三、init 会生成什么

```
追踪/
├── _tracking-state.json          唯一权威，禁止手改
├── 上下文.md                     派生视图：续写只需读这一个文件
├── 伏笔.md                       派生视图
├── 角色状态/角色名.md            派生视图
└── 时间线/
    ├── 作者真相.md                派生视图：forge.py truth 校验它
    └── 读者已知.md                派生视图：forge.py truth 校验它
```

除 `_tracking-state.json` 外全部是派生视图，**由脚本重渲染，不要手改**。
手改会在下次 commit 时被覆盖。

## 四、commit 的 revision 模式陷阱

修订已提交的章节时 `mode`填 `revision`，此时：

- delta 是**该章的完整记录**，整份替换。只写改动项会静默清掉原有的
  角色变化 / 伏笔变化（脚本会WARNING，但数据已经丢了）。
- `character_snapshots` 必须与 `delta.character_changes` 里的核心角色
  **一一对应**：多带未变更角色会报错，漏带已变更角色也会报错。
- 修订正文后字数要重新计数：走本技能的 `commit`（不要找别的脚本），
  它会重算actual并清理工作目录。

## 五、常见报错对照

| 报错 | 原因 | 怎么改 |
|---|---|---|
| `contains unsupported fields: xxx` | 多写了 schema 不收的键 | 删掉该键。模板里不要加注释键 |
| `context.position contains unsupported fields` | position 只收 4 键 | 只留 volume / volume_start_chapter / story_time / scene |
| `planted_chapter cannot be in the future` | 章号超前于 `last_chapter` | 调小章号，或把 `last_chapter` 调到实际已写章数 |
| `must not put a future reveal chapter` | 未揭示却填了揭示章 | `未揭示` 时 `reveal_chapter` 给 `null` |
| `unclosed` / `必须落成完整记录` | revision 模式 delta 写残了 | 用 `draft` 重新生成预填单再改 |