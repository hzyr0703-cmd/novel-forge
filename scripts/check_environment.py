#!/usr/bin/env python3
"""novel-forge 环境检查（只读）：核查 Python 与 Node 运行时是否可用。

输出 JSON：{"status": "ready|unavailable", "missing": [...], "details": {...}}
退出码：0 = ready，1 = unavailable。
本脚本只做版本探测，不安装、不修改任何配置。
"""
import json
import shutil
import subprocess
import sys


def _first_line(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        out = (r.stdout or r.stderr or "").strip()
        return out.splitlines()[0] if out else None
    except Exception:
        return None


def main():
    details = {}
    missing = []

    python_ver = _first_line([sys.executable, "--version"])
    if python_ver:
        details["python"] = python_ver
    else:
        missing.append("python")

    node_path = shutil.which("node")
    if node_path:
        node_ver = _first_line([node_path, "--version"])
        if node_ver:
            details["node"] = {"path": node_path, "version": node_ver}
        else:
            missing.append("node")
    else:
        missing.append("node")

    status = "unavailable" if missing else "ready"
    print(json.dumps(
        {"status": status, "missing": missing, "details": details},
        ensure_ascii=False, indent=2,
    ))
    return 0 if status == "ready" else 1


if __name__ == "__main__":
    sys.exit(main())
