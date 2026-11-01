#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""文档与代码口径一致性检查 —— 防「文档一套、脚本一套」复发。

背景（2026-11 质检报告 根因 2）：段长有过 90 / 100 / 200 三套数字、字数有过 2000 / 3200
两套、lint 规则数有过 22 / 26 / 29 四种说法、版本号在 frontmatter / CHANGELOG / git 三处不同步。
根因不是笔误，是**每个数字都没有唯一事实源，也没有任何机制去核对**。

本脚本就是那个机制：把「可由机器判定的事实」从代码里读出来，再回头查文档有没有说错。
它不判断文案好坏，只判断数字与名字对不对得上。

检查项：
  1. lint 规则总数 / blocking 数 / advisory 数        事实源：check-ai-patterns.js --list-review-classes
  2. 段长阈值（常态上限 / 豁免上限 / 豁免段数）        事实源：spec_check.THRESHOLDS
  3. 字数带与首章带宽、平台预设键                     事实源：spec_check.PLATFORM_PRESETS
  4. 章节字数上限与 spec_check 是否一致                事实源：THRESHOLDS['words_max']
  5. 版本号三处一致（frontmatter / CHANGELOG 首条 / 本文件） 事实源：SKILL.md frontmatter
  6. deslop.md 是否覆盖全部规则类型                    事实源：--list-review-classes 的键集合

用法：
  python scripts/check-doc-consistency.py            # 人读输出
  python scripts/check-doc-consistency.py --json     # 机读
退出码：有不一致项即 1。
"""

import json
import os
import re
import subprocess
import sys

CWD = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(CWD)


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_lint_classes():
    p = subprocess.run(["node", os.path.join(CWD, "check-ai-patterns.js"), "--list-review-classes"],
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise SystemExit("跑不动 node scripts/check-ai-patterns.js --list-review-classes：%s" % (p.stderr or ""))
    return json.loads(p.stdout)


def load_lint_categories():
    p = subprocess.run(["node", os.path.join(CWD, "check-ai-patterns.js"), "--list-categories"],
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise SystemExit("跑不动 node scripts/check-ai-patterns.js --list-categories：%s" % (p.stderr or ""))
    return json.loads(p.stdout)


def load_spec_module():
    """把 spec_check.py 当模块导入，直接读 THRESHOLDS / PLATFORM_PRESETS（不复制数字）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("spec_check_mod", os.path.join(CWD, "spec_check.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# blocking 规则集合：与 check-ai-patterns.js 里的 severity: 'blocking' 一一对应。
# 加一条 blocking 规则必须同时改这里，否则一致性检查会把数字算错——这是刻意的摩擦。
BLOCKING_TYPES = {
    "hard-banned-word", "not-is-comparison", "voice-contrast", "negation-parade",
    "reverse-not-is", "trailer-ending", "trailer-summary", "em-dash",
}


def main():
    as_json = "--json" in sys.argv
    skills = read(os.path.join(ROOT, "SKILL.md"))
    deslop = read(os.path.join(ROOT, "references", "deslop.md"))
    changelog = read(os.path.join(ROOT, "CHANGELOG.md"))
    platform_doc = read(os.path.join(ROOT, "references", "platform-adaptation.md"))

    classes = load_lint_classes()
    total = len(classes)
    spec = load_spec_module()
    T = spec.THRESHOLDS
    presets = spec.PLATFORM_PRESETS

    problems = []

    def need(cond, msg):
        if not cond:
            problems.append(msg)

    # 1) 规则总数：抓具体句式，不用「N 类」这种到处都能出现的子串
    #    （负向测试过：只查子串时，把 90 改成 95 也能「全部一致」，因为别处还有 90）
    need(re.search(r"AI 味检测（\*\*(\d+) 类规则\*\*", skills) is not None,
         "SKILL.md 脚本表里找不到「AI 味检测（**N 类规则**」")
    m = re.search(r"AI 味检测（\*\*(\d+) 类规则\*\*", skills)
    if m:
        need(int(m.group(1)) == total,
             "SKILL.md 脚本表的规则数写的是 %s，实测 %d" % (m.group(1), total))
    m = re.search(r"(\d+) 类 lint 规则全表", skills)
    if m:
        need(int(m.group(1)) == total,
             "SKILL.md 资源地图的规则数写的是 %s，实测 %d" % (m.group(1), total))
    else:
        problems.append("SKILL.md 资源地图找不到「N 类 lint 规则全表」")
    m = re.search(r"有 \*\*(\d+) 类规则\*\*", deslop)
    if m:
        need(int(m.group(1)) == total,
             "deslop.md 首段的规则数写的是 %s，实测 %d" % (m.group(1), total))
    else:
        problems.append("deslop.md 首段找不到「有 **N 类规则**」")

    # 2) deslop.md 必须覆盖每一个规则类型（旧版号称「全表」，实测缺 6 条）
    missing = [k for k in classes if "`%s`" % k not in deslop]
    need(not missing, "deslop.md 漏了这些规则类型：%s" % "、".join(sorted(missing)))

    # 3) blocking 条数：文档里「28 类中 8 条 blocking」必须等于实测
    block = sum(1 for k in classes if k in BLOCKING_TYPES)
    need(block > 0, "blocking 规则集合为空，检查 BLOCKING_TYPES 是否过期")
    m = re.search(r"(\d+) 类中 (\d+) 条 blocking", skills)
    if m:
        need(int(m.group(1)) == total and int(m.group(2)) == block,
             "SKILL.md 写「%s 类中 %s 条 blocking」，实测 %d 类中 %d 条"
             % (m.group(1), m.group(2), total, block))
    else:
        problems.append("SKILL.md 找不到「N 类中 M 条 blocking」这句")

    # 4) 段长三个数字：抓表格行，不抓全文子串
    m = re.search(r"\| 段落长度 \| ≤ (\d+) 字；\*\*每章允许 (\d+) 段放到 (\d+) 字", skills)
    if m:
        need(int(m.group(1)) == T["paragraph_max"],
             "SKILL.md 段长常态上限 %s，代码 %d" % (m.group(1), T["paragraph_max"]))
        need(int(m.group(2)) == T["paragraph_exempt_quota"],
             "SKILL.md 豁免段数 %s，代码 %d" % (m.group(2), T["paragraph_exempt_quota"]))
        need(int(m.group(3)) == T["paragraph_exempt_max"],
             "SKILL.md 豁免上限 %s，代码 %d" % (m.group(3), T["paragraph_exempt_max"]))
    else:
        problems.append("SKILL.md 第六节 blocking 表的段落长度行格式变了，无法核对")

    # 5) 字数带 / 首章带宽
    m = re.search(r"\| 单章字数 \| (\d+)–(\d+)", skills)
    if m:
        need((int(m.group(1)), int(m.group(2))) == (T["words_min"], T["words_max"]),
             "SKILL.md 字数带 %s–%s，代码 %d–%d"
             % (m.group(1), m.group(2), T["words_min"], T["words_max"]))
    else:
        problems.append("SKILL.md 找不到「| 单章字数 | N–M」")
    m = re.search(r"\*\*首章字数\*\* \| \*\*(\d+)–(\d+)\*\*", skills)
    if m:
        need((int(m.group(1)), int(m.group(2))) == (T["first_chapter_words_min"], T["first_chapter_words_max"]),
             "SKILL.md 首章带宽 %s–%s，代码 %d–%d"
             % (m.group(1), m.group(2), T["first_chapter_words_min"], T["first_chapter_words_max"]))
    else:
        problems.append("SKILL.md 找不到首章带宽行——首章口径必须进第六节 blocking 表")

    # 6) 平台预设：键要列全，数字要一致（按行核对，不比全文）
    for key, cfg in presets.items():
        need("`%s`" % key in skills, "SKILL.md 的平台取值没列 `%s`" % key)
        m = re.search(r"\| `%s`(?:（默认）)? \| (\d+)–(\d+) \| (\d+)–(\d+) \|" % key, platform_doc)
        if m:
            got = (int(m.group(1)), int(m.group(2)))
            need(got == (cfg["words_min"], cfg["words_max"]),
                 "platform-adaptation.md 的 %s 字数带 %s–%s，代码 %d–%d"
                 % (key, got[0], got[1], cfg["words_min"], cfg["words_max"]))
            gotf = (int(m.group(3)), int(m.group(4)))
            need(gotf == (cfg["first_chapter_words_min"], cfg["first_chapter_words_max"]),
                 "platform-adaptation.md 的 %s 首章带宽 %s–%s，代码 %d–%d"
                 % (key, gotf[0], gotf[1], cfg["first_chapter_words_min"], cfg["first_chapter_words_max"]))
        else:
            problems.append("platform-adaptation.md 表格里找不到 %s 那一行" % key)

    # 8) category 条数：deslop.md 的四类分区条数必须等于代码（v3.0）
    cats = load_lint_categories()
    need(len(cats) == total, "CATEGORIES 有 %d 条，规则表 %d 条（键集必须一一对应）" % (len(cats), total))
    for cname in ("ai-tone", "cliche", "literary", "readability"):
        actual = sum(1 for v in cats.values() if v == cname)
        m = re.search(r"\| `%s` \|[^\n]*\| (\d+) \|" % cname, deslop)
        if m:
            need(int(m.group(1)) == actual,
                 "deslop.md 说 %s 类是 %s 条，实测 %d 条" % (cname, m.group(1), actual))
        else:
            problems.append("deslop.md 的分类表找不到 `%s` 行的条数" % cname)

    # 9) X 级已否决清单：文档存在、条数够、SKILL.md 有路由（v3.0）
    rejected_path = os.path.join(ROOT, "references", "rejected-signals.md")
    need(os.path.exists(rejected_path), "references/rejected-signals.md 不存在")
    if os.path.exists(rejected_path):
        rejected = read(rejected_path)
        xids = set(re.findall(r"\*\*(X-\d+)\*\*", rejected))
        need(len(xids) >= 8, "rejected-signals.md 只有 %d 条 X 级结论（至少要 8 条）" % len(xids))
        need("rejected-signals.md" in skills, "SKILL.md 没有路由到 rejected-signals.md（加规则前要能查到）")
        need("test-rejected-signals.py" in skills, "SKILL.md 脚本表没登记 test-rejected-signals.py")
        need("test-rejected-signals.py" in deslop, "deslop.md 没说明 X 级由谁守卫")

    # 7) 版本三处一致
    m = re.search(r"^version:\s*([0-9.]+)\s*$", skills, re.M)
    ver = m.group(1) if m else None
    need(ver is not None, "SKILL.md frontmatter 没有 version")
    first_change = re.search(r"^## v([0-9.]+)", changelog, re.M)
    need(first_change is not None, "CHANGELOG.md 没有 ## vX.Y 标题")
    if ver and first_change:
        need(ver == first_change.group(1),
             "版本号不同步：SKILL.md=%s，CHANGELOG 首条=v%s" % (ver, first_change.group(1)))
    need("（当前）" not in changelog, "CHANGELOG 里还有「（当前）」标记，会与首条版本打架")

    # 输出
    if as_json:
        print(json.dumps({"ok": not problems, "problems": problems,
                          "lint_total": total, "lint_blocking": block,
                          "version": ver}, ensure_ascii=False, indent=2))
    else:
        print("=" * 60)
        print("文档 / 代码口径一致性")
        print("=" * 60)
        print("  lint 规则        : %d 类（blocking %d）" % (total, block))
        print("  段长             : 常态 ≤%d，豁免 ≤%d × %d 段"
              % (T["paragraph_max"], T["paragraph_exempt_max"], T["paragraph_exempt_quota"]))
        print("  字数             : %d–%d（首章 %d–%d）"
              % (T["words_min"], T["words_max"],
                 T["first_chapter_words_min"], T["first_chapter_words_max"]))
        print("  平台预设         : %s" % " / ".join(presets))
        print("  版本             : %s" % ver)
        print("-" * 60)
        if problems:
            for p in problems:
                print("  [不一致] %s" % p)
            print("共 %d 项不一致：改完代码顺手改文档，或反过来。" % len(problems))
        else:
            print("全部一致。")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
