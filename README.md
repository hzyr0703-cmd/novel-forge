# novel-forge

中文网文长篇写作引擎（DeepSeek Harness / WorkBuddy skill）。15 维选题打分 → 卷纲细纲 → 分层正文 → 去 AI 味门禁，含对标书拆解库、旧稿导入、连续性台账（伏笔/时间线/人设）、改稿盲评与出版 IP 评估。默认番茄小说平台，支持起点/七猫/言言。

## 当前版本

| 项 | 值 |
| --- | --- |
| 版本 | v4.5.1（见 `CHANGELOG.md` 首条） |
| 手法池 | 141 张手法卡 · 十六层（A 结构 / B 章级 / C 句级 / D 开篇 / E 卷尾 / F 反派 / G 群像 / H 世界观 / I 升级体系 / J 悬念伏笔 / K 战斗博弈 / L 感情线 / M 身份戏 / N 幽默调剂 / O 章题 / P 对话） |
| 对标底料 | 五本头部书（诡秘之主 / 我在精神病院学斩神 / 十日终焉 / 宿命之环 / 我不是戏神）各 15 层拆解 |

## 目录结构

| 目录/文件 | 用途 |
| --- | --- |
| `SKILL.md` | 技能主文档：工作流程、路由表（按写作时刻路由到对应文档）、铁律 |
| `references/` | 分层参考文档 + `bench-craft.md` 手法池（防模板五纪律置顶） |
| `scripts/` | 确定性门禁脚本（forge.py gate、check-doc-consistency.py、check-crossrepeat.py 等） |
| `templates/` | 细纲/台账/拆文报告模板 |
| `examples/` | 范例输出 |
| `CHANGELOG.md` | 版本历史（版本号三处同步：CHANGELOG 首条 / SKILL.md frontmatter / git commit 标题） |

## 安装

把本仓库克隆到 skill 目录即可（DeepSeek Harness / WorkBuddy 均按目录识别 skill）：

```bash
git clone git@github.com:hzyr0703-cmd/novel-forge.git ~/workspace/custom-skills/novel-forge
```

## 更新与同步流程

本仓库既是 skill 本体又是同步载体，约定流程：

1. **改完就提交**：版本号三处同步（`CHANGELOG.md` 首个 `## vX.Y` / `SKILL.md` frontmatter `version:` / commit 标题），跑 `python3 scripts/check-doc-consistency.py` 过了再 commit。
2. **提交就推送**：`git push origin main`。远程走 SSH（`git@github.com:hzyr0703-cmd/novel-forge.git`），本机 `~/.ssh/config` 已把 github.com 路由到 `ssh.github.com:443`（HTTPS 443 阻断期间 SSH 可用）。
3. **换机恢复**：`git pull` 即拿到最新版；本仓库不依赖本机路径（书项目路径、对标语料路径均在各项目侧，不入库）。
4. **identity 用仓库级**：`git config user.name hzyr0703-cmd && git config user.email hzyr0703-cmd@users.noreply.github.com`（不加 `--global`）。

## 纪律要点

- 提交前扫敏感文件：`*.json` 里的 API key、sqlite `data/` 目录必须进 `.gitignore`（本仓库已配置，`api_key` 相关命中仅为环境变量名）。
- 手法池是菜单不是工序：冷却制（A 层 5 章 / B 层 3 章）+ 变体制（2–3 变体）+ 同变体连用两次按复读处理；与 chinese-prose-craft / human-texture / scene-architecture 冲突时以那三份为准。
- 门禁是唯一判据事实源：阈值以 `forge.py gate` 输出为准，不要绕开脚本自算。
