#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
novel-forge 导出层（v2.0）。

职责：把 `正文/final/第N章.md` 清洗成「零元数据、零注释、零工程词」的纯正文，
写入 `导出/第N章.md`。只交付导出层——平台看到的就是这一份。

清洗规则（顺序敏感）：
  1. 剥离 HTML 注释（含声明块 <!-- 温度：… --> 等所有形态，含跨行）
  2. 剥离 [DRAFT] / [META] / [TODO] 等方括号标记行
  3. 剥离 skill/脚本/路径泄漏词（agent_created / novel-forge / forge.py / tracking /
     追踪路径 / 脚本名 / 细纲 / 卷纲 等）
  4. 剥离裸声明行（形如「温度：X」「爽点：Y」的键值行，无注释包裹时）
  5. 归一多余空行

校验（失败即退出非 0）：
  导出文件不得含 `<!--`、`-->`、`[DRAFT]`、`[META]`、`agent_created`、`forge`、
  `tracking`、`skill`、`novel-forge`、`spec_check`、`check-ai-patterns` 等词。
  任何一处残留都说明清洗不彻底——宁可不导出，不可带脏交付。

用法：
  python export.py --book <书目录>                 导出 final/ 全部章节
  python export.py --book <书目录> --chapter 12    只导出第 12 章
  python export.py --book <书目录> --check-only    只校验不写文件（CI/交付前用）
  python export.py --file 正文/final/第012章.md --out 导出/第012章.md   单文件模式

设计原则：纯 CPU、零外部依赖、只读 final/ 不改 final/。退出码非 0 = 有残留，必须修。
"""

import argparse
import os
import re
import sys

# ---------------------------------------------------------------- 清洗规则

# 1. HTML 注释（跨行，非贪婪）
RE_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

# 2. 方括号工程标记（整行或行内）
RE_BRACKET_TAG = re.compile(
    r"\[(DRAFT|META|TODO|WIP|NOTE|AGENT|FORGE|TRACKING|SKILL)\]",
    re.IGNORECASE,
)

# 3. 工程词 / 路径 / 脚本名（正文里出现即为泄漏）
LEAK_WORDS = [
    "agent_created",
    "novel-forge",
    "novel_forge",
    "forge.py",
    "spec_check",
    "check-ai-patterns",
    "check-degeneration",
    "check-crossrepeat",
    "check-factconflicts",
    "check-chinese-tone",
    "check-platform-compliance",
    "normalize-punctuation",
    "style-whitelist",
    "tracking_commit",
    "wordcount_core",
    "recall.py",
    "debt.py",
    "bench_bm25",
    "bench_context_cost",
    "test_schema_compat",
    "SKILL.md",
    "templates/",
    "references/",
    "examples/",
    "scripts/",
    "tracking",
    ".deslop-whitelist",
]

# 4. 裸声明键（无注释包裹时的键值行，如「温度：冷」「爽点：打脸」）
#    只剥离「短行 + 键 + 全/半角冒号 + 短值」形态，避免误伤正文里的正常冒号句。
RE_BARE_DECL = re.compile(
    r"^\s*(温度|情绪温度|爽点|爽点节点|回报|三重回报|章中钩子|章末钩子|钩子|钩子型号|"
    r"微钩子|声明|本章字数|字数)\s*[：:]\s*\S{1,40}\s*$"
)

# 校验用的残留黑名单（清洗后仍出现即失败）
CHECK_TOKENS = [
    "<!--",
    "-->",
    "[DRAFT]",
    "[META]",
    "[TODO]",
    "agent_created",
    "forge",
    "tracking",
    "novel-forge",
    "spec_check",
    "check-ai-patterns",
    "SKILL.md",
]


def strip_comments(text: str) -> str:
    """剥离 HTML 注释（含跨行）。"""
    return RE_HTML_COMMENT.sub("", text)


def strip_bracket_tags(text: str) -> str:
    """剥离 [DRAFT]/[META] 等方括号标记（保留同行其余内容）。"""
    return RE_BRACKET_TAG.sub("", text)


def strip_bare_decls(text: str) -> str:
    """剥离整行的裸声明键值行。"""
    out = []
    for line in text.split("\n"):
        if RE_BARE_DECL.match(line):
            continue
        out.append(line)
    return "\n".join(out)


def collapse_blank_lines(text: str) -> str:
    """连续 3+ 空行压成 1 个空行（段落分隔），并去掉首尾空白。"""
    text = re.sub(r"[ \t]+\n", "\n", text)          # 行尾空白
    text = re.sub(r"\n{3,}", "\n\n", text)          # 多空行归一
    return text.strip() + "\n"


def find_leaks(text: str) -> list:
    """返回导出文本里命中的残留黑名单。"""
    hits = []
    low = text.lower()
    for tok in CHECK_TOKENS:
        if tok.lower() in low:
            hits.append(tok)
    for w in LEAK_WORDS:
        if w.lower() in low and w not in hits:
            hits.append(w)
    return hits


def clean_text(raw: str) -> str:
    """完整清洗管线。"""
    t = raw
    t = strip_comments(t)
    t = strip_bracket_tags(t)
    t = strip_bare_decls(t)
    t = collapse_blank_lines(t)
    return t


# ---------------------------------------------------------------- 文件处理

CH_IN_RE = re.compile(r"第\s*0*(\d+)\s*章")


def chapter_no(path: str):
    """从文件名提取章号；抽不到返回 None。"""
    m = CH_IN_RE.search(os.path.basename(path))
    return int(m.group(1)) if m else None


def collect_final_files(book_dir: str):
    """收集 正文/final/ 下的 .md 文件，按章号排序。"""
    final_dir = os.path.join(book_dir, "正文", "final")
    if not os.path.isdir(final_dir):
        return None, final_dir
    files = []
    for name in os.listdir(final_dir):
        if name.lower().endswith(".md") and not name.startswith("_"):
            files.append(os.path.join(final_dir, name))
    files.sort(key=lambda p: (chapter_no(p) if chapter_no(p) is not None else 10 ** 9, p))
    return files, final_dir


def export_one(src: str, out_dir: str, check_only: bool = False):
    """
    处理单章。返回 (章号, 源路径, 导出路径或 None, 残留列表, 是否成功)。
    """
    with open(src, "r", encoding="utf-8") as f:
        raw = f.read()
    cleaned = clean_text(raw)
    leaks = find_leaks(cleaned)
    ch = chapter_no(src)

    if leaks:
        return ch, src, None, leaks, False

    if check_only:
        return ch, src, None, [], True

    os.makedirs(out_dir, exist_ok=True)
    if ch is not None:
        out_name = "第%03d章.md" % ch
    else:
        out_name = os.path.basename(src)
    out_path = os.path.join(out_dir, out_name)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(cleaned)
    # 二次校验：读回确认无残留
    with open(out_path, "r", encoding="utf-8") as f:
        reread = f.read()
    leaks2 = find_leaks(reread)
    if leaks2:
        return ch, src, out_path, leaks2, False
    return ch, src, out_path, [], True


# ---------------------------------------------------------------- 主流程

def run_book(book_dir: str, chapter=None, check_only: bool = False) -> int:
    files, final_dir = collect_final_files(book_dir)
    if files is None:
        print("[导出] 找不到目录：%s" % final_dir, file=sys.stderr)
        return 2
    if not files:
        print("[导出] %s 下没有 .md 文件" % final_dir, file=sys.stderr)
        return 2

    if chapter is not None:
        files = [p for p in files if chapter_no(p) == chapter]
        if not files:
            print("[导出] final/ 下没有第 %s 章" % chapter, file=sys.stderr)
            return 2

    out_dir = os.path.join(book_dir, "导出")
    failed = []
    ok = 0
    for src in files:
        ch, sp, op, leaks, success = export_one(src, out_dir, check_only=check_only)
        label = "第%s章" % (ch if ch is not None else "?")
        if success:
            ok += 1
            if not check_only:
                print("  [OK]   %s -> %s" % (label, os.path.basename(op)))
            else:
                print("  [OK]   %s 干净" % label)
        else:
            failed.append((label, sp, leaks))
            print("  [脏]   %s 残留：%s" % (label, "、".join(leaks)), file=sys.stderr)

    print("-" * 72)
    if failed:
        print("[导出失败] %d 章清洗后仍残留工程词，已中止导出（宁可不导出，不可带脏交付）" % len(failed))
        for label, sp, leaks in failed:
            print("  - %s（%s）：%s" % (label, sp, "、".join(leaks)))
        return 1
    if check_only:
        print("[校验通过] %d 章全部干净（未写文件）" % ok)
    else:
        print("[导出完成] %d 章 -> %s" % (ok, out_dir))
    return 0


def run_single(src: str, out_path: str, check_only: bool = False) -> int:
    with open(src, "r", encoding="utf-8") as f:
        raw = f.read()
    cleaned = clean_text(raw)
    leaks = find_leaks(cleaned)
    if leaks:
        print("[脏] %s 残留：%s" % (src, "、".join(leaks)), file=sys.stderr)
        return 1
    if check_only:
        print("[校验通过] %s 干净（未写文件）" % src)
        return 0
    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(cleaned)
    print("[导出完成] %s -> %s" % (src, out_path))
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="export.py",
        description="novel-forge 导出层：final/ 纯正文 -> 导出/（零元数据、零注释、零工程词）",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--book", help="书目录（自动找 正文/final/）")
    g.add_argument("--file", help="单文件模式：源 md 路径")
    p.add_argument("--chapter", type=int, help="只导出指定章号")
    p.add_argument("--out", help="单文件模式的输出路径")
    p.add_argument("--check-only", action="store_true", help="只校验不写文件")
    return p


def main():
    args = build_parser().parse_args()
    if args.file:
        if not args.check_only and not args.out:
            print("[导出] 单文件模式需指定 --out（或加 --check-only）", file=sys.stderr)
            return 2
        if not os.path.isfile(args.file):
            print("[导出] 找不到文件：%s" % args.file, file=sys.stderr)
            return 2
        return run_single(args.file, args.out, check_only=args.check_only)
    return run_book(args.book, chapter=args.chapter, check_only=args.check_only)


if __name__ == "__main__":
    sys.exit(main())
