#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ledger.py 与 promise-payoff-actual-before-plant 的离线回归。

纯临时目录 + 直接调函数/CLI 子进程，不联网、不碰真实书稿。
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "ledger.py")
FACT = os.path.join(HERE, "check-factconflicts.py")
PY = sys.executable

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, detail))


def run_ledger(args, cwd=None):
    return subprocess.run([PY, LEDGER] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60, cwd=cwd)


def load_fact():
    spec = importlib.util.spec_from_file_location("nf_fact", FACT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def make_project():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "追踪"))
    return d


def test_add_and_list():
    d = make_project()
    r = run_ledger(["--project", d, "add", "--chapter", "13", "--rule", "dialogue-ratio",
                    "--payback", "15", "--note", "双人局刻意压对话"])
    check("add 成功", r.returncode == 0, r.stderr)
    check("add 输出 ID", "L001" in r.stdout, r.stdout)
    r2 = run_ledger(["--project", d, "list", "--at", "14"])
    check("list 显示挂账", "L001" in r2.stdout and "欠到第 15 章" in r2.stdout, r2.stdout)


def test_reject_payback_le_chapter():
    d = make_project()
    r = run_ledger(["--project", d, "add", "--chapter", "15", "--rule", "x",
                    "--payback", "15"])
    check("payback==chapter 拒绝", r.returncode == 2, r.returncode)
    r2 = run_ledger(["--project", d, "add", "--chapter", "15", "--rule", "x",
                     "--payback", "14"])
    check("payback<chapter 拒绝", r2.returncode == 2, r2.returncode)


def test_overdue_and_fulfill():
    d = make_project()
    run_ledger(["--project", d, "add", "--chapter", "13", "--rule", "dialogue-ratio",
                "--payback", "15"])
    r = run_ledger(["--project", d, "check", "--at", "15"])
    check("at==payback 未逾期", "逾期" not in r.stdout, r.stdout)
    r2 = run_ledger(["--project", d, "check", "--at", "17"])
    check("at>payback 标逾期", "逾期 2 章" in r2.stdout, r2.stdout)
    r3 = run_ledger(["--project", d, "overdue", "--at", "17"])
    check("overdue 退出码 1", r3.returncode == 1, r3.returncode)
    r4 = run_ledger(["--project", d, "fulfill", "--id", "L001"])
    check("fulfill 成功", r4.returncode == 0, r4.stderr)
    r5 = run_ledger(["--project", d, "overdue", "--at", "17"])
    check("销账后无逾期", r5.returncode == 0 and "无逾期" in r5.stdout, r5.stdout)
    data = json.load(open(os.path.join(d, "追踪", "advisory-ledger.json"), encoding="utf-8"))
    check("台账状态落盘", data["entries"][0]["status"] == "fulfilled")


def test_id_reuse_and_corrupt():
    d = make_project()
    run_ledger(["--project", d, "add", "--chapter", "1", "--rule", "a", "--payback", "3"])
    run_ledger(["--project", d, "fulfill", "--id", "L001"])
    r = run_ledger(["--project", d, "add", "--chapter", "5", "--rule", "b", "--payback", "8"])
    check("ID 不复用已占用号（L002）", "L002" in r.stdout, r.stdout)
    # 损坏台账
    d2 = make_project()
    with open(os.path.join(d2, "追踪", "advisory-ledger.json"), "w", encoding="utf-8") as f:
        f.write("{broken")
    r2 = run_ledger(["--project", d2, "list"])
    check("损坏台账退出码 2 且提示", r2.returncode == 2 and "损坏" in r2.stderr,
          (r2.returncode, r2.stderr))


def test_gate_integration():
    """forge.py gate 应显示挂账行，且台账缺失不影响门禁。"""
    import shutil
    src = os.path.join(HERE, "forge.py")
    d = tempfile.mkdtemp()
    # 最小书目录：追踪 + 细纲（gate 的硬前置）
    os.makedirs(os.path.join(d, "追踪"))
    json.dump({"schema_version": "v4", "state_revision": 1, "characters": {},
               "context": {"active_character_names": [], "continuity_risks": []},
               "foreshadow": {}, "timeline": {}},
              open(os.path.join(d, "追踪", "_tracking-state.json"), "w", encoding="utf-8"))
    open(os.path.join(d, "正文第1章细纲.md"), "w", encoding="utf-8").write(
        "字数范围：2000\n- 情节点1\n")
    # 移动到 gate 期望的位置；跨章检测要求正文目录至少有一个 .md
    os.makedirs(os.path.join(d, "正文"), exist_ok=True)
    shutil.move(os.path.join(d, "正文第1章细纲.md"), os.path.join(d, "正文", "第1章细纲.md"))
    os.makedirs(os.path.join(d, "正文", "final"), exist_ok=True)
    with open(os.path.join(d, "正文", "final", "第001章.md"), "w", encoding="utf-8") as f:
        f.write("他睁开眼。\n" * 30)
    env = dict(os.environ)
    r = subprocess.run([PY, src, "gate", "--book", d, "--chapter", "1", "--stage", "outline"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, timeout=90)
    check("gate 无台账也通过", r.returncode == 0 and "门禁通过" in r.stdout,
          (r.returncode, r.stdout[-200:], r.stderr[-200:]))
    # 登记一笔到期的账，再跑 gate（第 3 章也要有细纲，否则被硬门禁拦在挂账视图之前）
    open(os.path.join(d, "正文", "第3章细纲.md"), "w", encoding="utf-8").write(
        "字数范围：2000\n- 情节点1\n")
    run_ledger(["--project", d, "add", "--chapter", "1", "--rule", "dialogue-ratio",
                "--payback", "2"])
    r2 = subprocess.run([PY, src, "gate", "--book", d, "--chapter", "3", "--stage", "outline"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        env=env, timeout=90)
    check("gate 显示挂账+逾期", "挂账" in r2.stdout and "逾期" in r2.stdout,
          r2.stdout[-400:])
    check("挂账不改变退出码", r2.returncode == 0, r2.returncode)


def test_fact_actual_before_plant():
    m = load_fact()
    st = {"foreshadow": {
        "F001": {"summary": "正常：同章埋收", "planted_chapter": 5,
                 "planned_resolution_chapter": 9, "status": "已回收", "updated_chapter": 9},
        "F002": {"summary": "异常：回收章早于埋设章", "planted_chapter": 9,
                 "planned_resolution_chapter": 10, "status": "已回收", "updated_chapter": 7},
        "F003": {"summary": "已埋不受新规则影响", "planted_chapter": 9,
                 "planned_resolution_chapter": 20, "status": "已埋", "updated_chapter": 9},
    }}
    errs = m.check_foreshadow(st, drafted={5, 9}, through=10)
    rules = [e["rule"] for e in errs]
    check("同章埋收不误报", "promise-payoff-actual-before-plant" not in
          [r for r in rules if True] or
          not any(e["who"] == "F001" and e["rule"] == "promise-payoff-actual-before-plant"
                  for e in errs), rules)
    hit = [e for e in errs if e["who"] == "F002"
           and e["rule"] == "promise-payoff-actual-before-plant"]
    check("实际回收早于埋设被报", len(hit) == 1 and "第 7 章" in hit[0]["detail"], hit)
    check("已埋条目不触发新规则",
          not any(e["who"] == "F003"
                  and e["rule"] == "promise-payoff-actual-before-plant" for e in errs), rules)


def main():
    test_add_and_list()
    test_reject_payback_le_chapter()
    test_overdue_and_fulfill()
    test_id_reuse_and_corrupt()
    test_gate_integration()
    test_fact_actual_before_plant()
    print("通过 %d / 失败 %d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
