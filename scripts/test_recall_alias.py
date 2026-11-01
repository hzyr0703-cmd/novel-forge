#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""recall.py 别名层（v2.9）的离线回归。

要证明的只有一件事：**换个叫法还能捞回前文**。
所以每个用例都必须有「关掉别名就捞不到」的对照，否则测的可能是 BM25 碰巧命中。

纯临时目录 + CLI 子进程，不联网、不碰真实书稿。
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RECALL = os.path.join(HERE, "recall.py")
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


def run(project, *args):
    return subprocess.run(
        [PY, RECALL, "--project", project, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def build_book(root):
    """第 3 章只出现「名册」，从不出现「碑」——别名要跨过这道字面鸿沟。"""
    write(os.path.join(root, "正文", "final", "第003章-册.md"),
          "他把名册翻到最后一页，上面什么都没写。\n\n"
          "纸角有点卷，像是被人反复捻过。\n")
    state = {
        "book_title": "压测",
        "characters": {
            "林灼": {
                "identity": "零信者",
                "location": "南城学院",
                "goal": "查清评级栏为什么是空的",
                "state": "手背有一道新痕",
                "aliases": ["瞎子"],
            }
        },
        "foreshadow": {},
        "timeline": {},
    }
    write(os.path.join(root, "追踪", "_tracking-state.json"),
          json.dumps(state, ensure_ascii=False, indent=1))


def test_alias_bridge():
    root = tempfile.mkdtemp(prefix="nf-alias-")
    build_book(root)
    r = run(root, "index", "--no-vector", "--quiet")
    check("建索引成功", r.returncode == 0, r.stderr[-200:])

    # 基线：正文里真有「名册」，直接查当然命中
    r = run(root, "query", "名册")
    check("基准：查原文用词能命中第3章", r.returncode == 0 and "第3章" in r.stdout,
          r.stdout[:120])

    # 关键：正文里没有「碑」这个字，不挂别名时必须一条都捞不到
    r = run(root, "query", "碑")
    check("对照：查『碑』在无别名时零命中", r.returncode == 1 and "无命中" in r.stdout,
          r.stdout[:120])

    # 挂上别名表：碑 ＝ 名册
    write(os.path.join(root, "追踪", "aliases.json"),
          json.dumps({"碑": ["名册", "登记册"]}, ensure_ascii=False, indent=1))
    r = run(root, "query", "碑")
    check("别名生效：查『碑』召回第3章", r.returncode == 0 and "第3章" in r.stdout,
          r.stdout[:160])
    check("输出里摊开了别名扩展", "别名扩展" in r.stdout and "名册" in r.stdout,
          r.stdout[:160])

    # 反向对照：同一个查询关掉别名层就该退回零命中，证明是别名在起作用
    r = run(root, "query", "碑", "--no-alias")
    check("关掉别名层后退回零命中", r.returncode == 1,
          r.stdout[:120])

    # 人物外号：台账里的 aliases 也要生效。
    # 外号必须与正名**零共同字**（瞎子 vs 林灼），否则单字 token 会让 BM25 自己命中，
    # 对照就失去意义——这正是第一版用例写「灼哥」时被证伪的地方。
    r = run(root, "query", "瞎子")
    check("角色外号召回角色卡", r.returncode == 0 and "林灼" in r.stdout, r.stdout[:160])
    r = run(root, "query", "瞎子", "--no-alias")
    check("关掉别名层后外号零命中", r.returncode == 1, r.stdout[:120])

    # 别名命令要能把生效的词族列出来，否则没法排查「为什么召回了这条」
    r = run(root, "aliases")
    check("aliases 子命令列出物件词族", r.returncode == 0 and "碑" in r.stdout and "名册" in r.stdout,
          r.stdout[:160])
    check("aliases 子命令同时列出角色外号", "瞎子" in r.stdout, r.stdout[:160])


def test_broken_alias_file():
    root = tempfile.mkdtemp(prefix="nf-alias-bad-")
    build_book(root)
    run(root, "index", "--no-vector", "--quiet")
    write(os.path.join(root, "追踪", "aliases.json"), "{ 这不是 json")
    r = run(root, "query", "名册")
    check("别名表损坏不拖垮检索", r.returncode == 0 and "第3章" in r.stdout, r.stdout[:120])
    check("损坏时给出提示且不崩", "解析失败" in r.stderr or "跳过" in r.stderr, r.stderr[-160:])


def test_chapter_ref_guard():
    """别名里若含「第3章」这类字面，扩展后会被章节锁定逻辑误当显式引用。

    宁可少召回，不可误置顶——所以这种情况整次扩展作废。
    """
    root = tempfile.mkdtemp(prefix="nf-alias-ch-")
    build_book(root)
    run(root, "index", "--no-vector", "--quiet")
    write(os.path.join(root, "追踪", "aliases.json"),
          json.dumps({"碑": ["名册", "第3章"]}, ensure_ascii=False))
    r = run(root, "query", "碑")
    check("别名含章节字面时放弃扩展（不误锁章节）",
          r.returncode == 1 and "[1]" not in r.stdout, r.stdout[:160])


def main():
    test_alias_bridge()
    test_broken_alias_file()
    test_chapter_ref_guard()
    print("通过 %d / 失败 %d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
