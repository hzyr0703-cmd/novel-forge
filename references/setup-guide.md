# novel-forge 运行环境配置指引

本技能脚本依赖两个运行时（见 `skill-dependencies.json`），任一缺失时核心流程（门禁 / 去 AI 味 / 追踪）不可用，无降级路径。

## 检查方法

```bash
python3 ~/workspace/custom-skills/novel-forge/scripts/check_environment.py
```

- 退出码 0 + `"status": "ready"`：环境就绪，直接使用，无需读本文件其余部分。
- 退出码 1 + `"status": "unavailable"`：按下表补齐缺失项，补齐后重跑检查。

## python-runtime：Python ≥ 3.10（forge.py / tracking_commit.py / wordcount_core.py）

1. 官方入口：https://www.python.org/
2. 文档：https://docs.python.org/3/using/windows.html
3. 下载：https://www.python.org/downloads/ （Windows 安装包）
4. 安装时勾选 **Add python.exe to PATH**，重开终端使 PATH 生效
5. 验证：`python --version` 输出 ≥ 3.10；`python3 scripts/check_environment.py` 返回 ready

安全说明：仅使用官方安装包；本技能脚本不读写凭据、不访问网络。

## node-runtime：Node.js ≥ 18（去 AI 味 lint 链路）

1. 官方入口：https://nodejs.org/
2. 下载：https://nodejs.org/en/download （LTS 版本即可）
3. 安装后**重开终端**使 PATH 生效
4. 验证：`node --version` 输出 ≥ v18；`python3 scripts/check_environment.py` 返回 ready

安全说明：仅使用官方安装包；lint 脚本只读写项目内正文文件，不访问网络。

## 停止条件

- 两个运行时均无法安装或用户拒绝安装：本技能整体 unavailable，停止写正文与交付，不猜结果顶替。
- 环境恢复后重跑 `check_environment.py` 确认 ready，再回到流水线；已通过检查不重复引导。

清单校验日期：2026-10-06（对应 skill-dependencies.json v1.0.0）
