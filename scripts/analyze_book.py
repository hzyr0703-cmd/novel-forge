#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
novel-forge 对标书拆文引擎（v2.2）—— 把 SKILL 里承诺的「拆解对标书」落成脚本。

职责边界（确定性层）：
  本脚本只做**机器能算**的部分：文风分布、标点指纹、高频用词、节奏观察，
  产出 ①文风基线.json（可直接喂 check-distribution.py --baseline）
       ②文风.md（人读的文风报告，带锚点数字）
       ③拆文报告.md（统计摘要 + 语义层拆解待办清单）。
  角色卡/剧情线/金手指/世界观这类**语义层**拆解属于模型工作，
  流程见 references/book-teardown.md——脚本不猜语义，模型不背统计。

为什么对标书要有自己的基线：
  通用 human-baseline.json 回答「像不像人类」，对标书基线回答
  「像不像这一本」。贴着对标书写时，句长/对话占比的对比基准换成
  对标书本身，检测的灵敏度立刻上一个量级——通用基线放行、
  对标基线报警的差异就是文风偏差。

取样口径：与 collect-baseline.py / check-distribution.py 逐字同款
（句切分 。！？…；字符数含标点不含空白；对话引号内/全文不跨行；
非空行=段）。改口径必须三侧同步。

用法：
  # 对标书目录（每章一个 txt/md，或整本一个 txt 加 --split-chapters）
  python analyze_book.py --book <对标书目录> --project <你的书目录> [--name 别名]
  python analyze_book.py --file <整本.txt> --split-chapters --project <你的书目录>

  # 只看数字不落盘
  python analyze_book.py --book <对标书目录> --out <输出目录> --dry-run

输出（默认 <你的书目录>/对标/<书名>/）：
  文风基线.json   check-distribution.py --baseline 直接消费
  文风.md         人读文风报告
  拆文报告.md     统计摘要 + 语义层待办

依赖：仅标准库（纯 CPU、零第三方依赖、零网络）。
"""

import argparse
import datetime
import glob
import importlib.util
import json
import math
import os
import re
import sys
from collections import Counter

# ---------------------------------------------------------------- 复用 collect-baseline 的口径

_HERE = os.path.dirname(os.path.abspath(__file__))
_CB_PATH = os.path.join(_HERE, "collect-baseline.py")
if not os.path.isfile(_CB_PATH):  # pragma: no cover
    sys.stderr.write("[analyze-book] 找不到 collect-baseline.py（口径依赖）：%s\n" % _CB_PATH)
    sys.exit(2)
_spec = importlib.util.spec_from_file_location("nf_collect_baseline", _CB_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover
    sys.stderr.write("[analyze-book] 无法加载 collect-baseline.py\n")
    sys.exit(2)
cb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cb)

MIN_CHAPTERS_FOR_STABLE = 10   # 低于此数在报告里标「统计不稳」
TOP_BIGRAMS = 30
TOP_TRIGRAMS = 15
TOP_PARA_HEADS = 10
CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")

PUNCT_MARKERS = [  # (显示名, 正则)
    ("省略号…", re.compile(r"…")),
    ("破折号——", re.compile(r"——")),
    ("叹号！", re.compile(r"！")),
    ("问号？", re.compile(r"？")),
    ("分号；", re.compile(r"；")),
    ("冒号：", re.compile(r"：")),
    ("顿号、", re.compile(r"、")),
]
QUOTE_STYLES = [("「」", "「"), ("“”", "“"), ('""', '"'), ("『』", "『")]


def load_human_baseline():
    """通用人类基线（对比用，找不到就跳过）。"""
    path = os.path.join(_HERE, "..", "references", "human-baseline.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("merged")
    except (OSError, ValueError):
        return None


def load_chapters(book=None, file=None, split=False):
    """返回 [(序号, 章文本, 可见字数)]，碎片（<200 可见字）已剔除。"""
    texts = []
    if book:
        files = []
        for ext in ("*.txt", "*.md", "*.text"):
            files += glob.glob(os.path.join(book, ext))
        files.sort()
        if not files:
            return []
        for fp in files:
            texts.append(cb.read_text(fp))
    elif file:
        raw = cb.read_text(file)
        if split:
            texts = cb.split_chapters_if_needed(raw)
        else:
            texts = [raw]
    chapters = []
    for i, t in enumerate(texts, 1):
        body = cb.strip_non_prose(t)
        n = cb.visible_len(body)
        if n < 200:
            continue
        chapters.append((i, body, n))
    return chapters


def punct_fingerprint(chapters):
    """每千可见字的标点频次 + 引号制式占比。"""
    full = "\n".join(t for _, t, _ in chapters)
    total = cb.visible_len(full) or 1
    punct = {}
    for name, rx in PUNCT_MARKERS:
        punct[name] = round(len(rx.findall(full)) * 1000.0 / total, 2)
    quotes = {}
    for name, ch in QUOTE_STYLES:
        quotes[name] = full.count(ch)
    qtotal = sum(quotes.values()) or 1
    quote_style = {name: round(n * 100.0 / qtotal, 1) for name, n in quotes.items() if n}
    return punct, quote_style


def word_freq(chapters):
    """CJK 二元组/三元组频次（跨标点不连）。返回 per-万字 频次表。"""
    bigrams, trigrams = Counter(), Counter()
    total_cjk = 0
    for _, t, _ in chapters:
        for run in CJK_RUN.findall(t):
            total_cjk += len(run)
            for i in range(len(run) - 1):
                bigrams[run[i:i + 2]] += 1
            for i in range(len(run) - 2):
                trigrams[run[i:i + 3]] += 1
    scale = total_cjk / 10000.0 if total_cjk else 1.0
    bg = [(w, n, round(n / scale, 1)) for w, n in bigrams.most_common(TOP_BIGRAMS)]
    tg = [(w, n, round(n / scale, 1)) for w, n in trigrams.most_common(TOP_TRIGRAMS)]
    return bg, tg, total_cjk


def para_heads(chapters):
    """段首二字 top N（叙述开口习惯：他/她/我/那/这…）。"""
    heads = Counter()
    for _, t, _ in chapters:
        for line in t.splitlines():
            s = line.strip()
            if len(s) >= 2:
                heads[s[:2]] += 1
    return heads.most_common(TOP_PARA_HEADS)


def chapter_end_types(chapters):
    """章末行类型粗判（钩子形态线索，advisory）。"""
    types = Counter()
    for _, t, _ in chapters:
        lines = [l.strip() for l in t.splitlines() if l.strip()]
        if not lines:
            continue
        last = lines[-1]
        if re.search(r'[」』”’"\']\s*$', last):
            types["对话收尾"] += 1
        elif last.endswith(("！", "？", "…")):
            types["感叹/悬问收尾"] += 1
        else:
            types["叙述收尾"] += 1
    total = sum(types.values()) or 1
    return types, {k: round(v * 100.0 / total, 1) for k, v in types.items()}


def per_chapter_dialogue(chapters):
    vals = []
    for _, t, n in chapters:
        vals.append(cb.dialogue_inside_chars(t) / n if n else 0.0)
    return vals


def build_baseline_payload(chapters, label):
    """与 human-baseline.json 同 schema 的单书基线（check-distribution 直接消费）。"""
    sent_lens, para_lens, chap_lens, dlg = [], [], [], []
    for _, t, n in chapters:
        chap_lens.append(n)
        for s in cb.split_sentences(t):
            L = cb.visible_len(s)
            if L:
                sent_lens.append(L)
        for p in cb.split_paragraphs(t):
            L = cb.visible_len(p)
            if L:
                para_lens.append(L)
        dlg.append(cb.dialogue_inside_chars(t) / n if n else 0.0)
    merged = {
        "sentence": cb.describe(sent_lens),
        "paragraph": cb.describe(para_lens),
        "chapter_len": cb.describe(chap_lens),
        "dialogue_ratio": cb.describe(dlg, digits=4),
    }
    return {
        "_meta": {
            "version": 2,
            "generated": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "purpose": "single-book style baseline for novel-forge check-distribution.py (--baseline)",
            "generator": "scripts/analyze_book.py",
            "label": label,
            "chapters": len(chapters),
            "caveat": ("单书基线：分布只代表这一本书的文风，用于「贴着对标书写」，"
                       "不代表人类总体。章数不足 %d 时统计不稳，仅供参考。" % MIN_CHAPTERS_FOR_STABLE),
            "sampling": {
                "sentence_split": "。！？…",
                "length_unit": "字符数（含标点，不含空白）",
                "dialogue_ratio": "引号内文字 / 全文字符（不含引号；不跨行；嵌套只计一次）",
                "paragraph": "非空行 = 段",
            },
        },
        "merged": merged,
    }


def fmt_desc(d, keys):
    return " / ".join("%.1f" % d[k] for k in keys if k in d)


def render_style_md(label, chapters, payload, punct, quote_style, bg, tg, heads,
                    end_types, end_ratio, dlg_per_ch, stable):
    m = payload["merged"]
    s, p, c, d = m["sentence"], m["paragraph"], m["chapter_len"], m["dialogue_ratio"]
    hb = load_human_baseline()
    chap_cv = c.get("cv", 0.0)
    lines = [
        "# 文风报告 · %s" % label,
        "",
        "> 由 `scripts/analyze_book.py` 生成，口径与 human-baseline.json 一致。",
        "样本：%d 章 / %s 字。%s" % (
            len(chapters),
            format(sum(n for _, _, n in chapters), ","),
            "" if stable else "**章数不足 %d，以下统计不稳，只看量级不看小数。**" % MIN_CHAPTERS_FOR_STABLE),
        "",
        "## 1. 章长与节奏",
        "",
        "- 章长 mean %.0f / p50 %.0f / p90 %.0f / max %.0f 字" % (c["mean"], c.get("p50", 0), c.get("p90", 0), c.get("max", 0)),
        "- 章长 CV %.3f（%s）" % (chap_cv, "章长相当稳定" if chap_cv < 0.15 else "章长波动正常" if chap_cv < 0.35 else "章长波动大——可能有番外/加更混入，或作者节奏本就跳跃"),
        "",
        "## 2. 句长",
        "",
        "- mean %.1f / sd %.1f / skew %.2f" % (s["mean"], s["stdev"], s.get("skewness", 0)),
        "- 分位 p10 %.0f / p25 %.0f / p50 %.0f / p75 %.0f / p90 %.0f / p99 %.0f"
        % tuple(s.get(k, 0) for k in ("p10", "p25", "p50", "p75", "p90", "p99")),
        "",
        "## 3. 段长",
        "",
        "- mean %.1f / sd %.1f" % (p["mean"], p["stdev"]),
        "- 分位 p50 %.0f / p75 %.0f / p90 %.0f / p99 %.0f"
        % tuple(p.get(k, 0) for k in ("p50", "p75", "p90", "p99")),
        "",
        "## 4. 对话",
        "",
        "- 全书对话占比 mean %.1f%% / p10 %.1f%% / p90 %.1f%%"
        % (d["mean"] * 100, d.get("p10", 0) * 100, d.get("p90", 0) * 100),
        "- 每章对话占比 min %.1f%% / max %.1f%%（两极差大 = 对话节奏敢留白）"
        % (min(dlg_per_ch) * 100 if dlg_per_ch else 0, max(dlg_per_ch) * 100 if dlg_per_ch else 0),
        "- 章末收尾类型：%s" % "；".join("%s %.0f%%" % (k, v) for k, v in sorted(end_ratio.items(), key=lambda kv: -kv[1])),
        "",
        "## 5. 标点指纹（每千字）",
        "",
        "| 标点 | 每千字 |",
        "|---|---:|",
    ]
    lines += ["| %s | %.2f |" % (k, v) for k, v in punct.items()]
    lines += [
        "",
        "引号制式：%s" % ("；".join("%s %.0f%%" % (k, v) for k, v in sorted(quote_style.items(), key=lambda kv: -kv[1])) or "未检出成对引号"),
        "",
        "## 6. 段首习惯（top %d）" % TOP_PARA_HEADS,
        "",
        "、".join("%s×%d" % (w, n) for w, n in heads) or "无",
        "",
        "## 7. 高频用词",
        "",
        "### 二元组（每万字频次）",
        "",
        "、".join("%s(%d)" % (w, f) for w, _, f in bg) or "无",
        "",
        "### 三元组（每万字频次）",
        "",
        "、".join("%s(%d)" % (w, f) for w, _, f in tg) or "无",
    ]
    if hb:
        lines += [
            "",
            "## 8. 对比通用人类基线",
            "",
            "| 指标 | 本书 | 通用人类基线 | 差异 |",
            "|---|---:|---:|---|",
        ]
        for key, name in (("sentence", "句长 mean"), ("paragraph", "段长 mean"), ("dialogue_ratio", "对话占比 mean")):
            a = m[key]["mean"]
            b = hb.get(key, {}).get("mean", 0)
            if b:
                diff = (a - b) / b * 100
                lines.append("| %s | %.2f | %.2f | %+.0f%% |" % (name, a, b, diff))
        lines += [
            "",
            "> 差异显著（±30% 以上）的项就是这本书的文风个性——写同类文时",
            "check-distribution 的对比基准应换成 `文风基线.json` 而不是通用基线。",
        ]
    lines += [
        "",
        "## 9. 怎么用",
        "",
        "```bash",
        "# 写本书正文时，把分布对比基准换成这本对标书：",
        "python scripts/check-distribution.py --book <书目录> --chapter N \\",
        "    --baseline <书目录>/对标/%s/文风基线.json" % label,
        "```",
        "",
        "语义层（角色卡/剧情线/金手指/世界观/爽点密度）拆解流程见",
        "`references/book-teardown.md`——脚本管统计，模型管语义。",
        "",
        "## 附：章末收尾明细",
        "",
        "；".join("%s×%d" % (k, v) for k, v in sorted(end_types.items(), key=lambda kv: -kv[1])) or "无",
        "",
    ]
    return "\n".join(lines)


def render_teardown_md(label, chapters, payload, stable):
    m = payload["merged"]
    c, d = m["chapter_len"], m["dialogue_ratio"]
    lines = [
        "# 拆文报告 · %s" % label,
        "",
        "> 确定性统计由 `scripts/analyze_book.py` 生成（%d 章 / %s 字）。%s" % (
            len(chapters), format(sum(n for _, _, n in chapters), ","),
            "" if stable else "（章数不足 %d，统计不稳）" % MIN_CHAPTERS_FOR_STABLE),
        "",
        "## 机器已算完的部分",
        "",
        "- 章长：mean %.0f / p90 %.0f（详见表 `文风.md` 第 1 节）" % (c["mean"], c.get("p90", 0)),
        "- 对话占比：mean %.1f%% / p10 %.1f%% / p90 %.1f%%" % (d["mean"] * 100, d.get("p10", 0) * 100, d.get("p90", 0) * 100),
        "- 文风分布/标点指纹/高频用词：见 `文风.md`",
        "",
        "## 语义层待办（模型执行，逐项完成后在方括号打 ×）",
        "",
        "流程与产出格式见 `references/book-teardown.md`。以下按顺序做：",
        "",
        "- [ ] 1. 概要.md：全书一句话梗概 + 当前进度断点",
        "- [ ] 2. 角色/：每核心角色一文件（身份/语言指纹/动机链/当前状态）",
        "- [ ] 3. 剧情/故事线.md：主线 1 + 支线 N，各线当前进度与悬置点",
        "- [ ] 4. 设定/世界观.md：力量体系/地理/势力，按主题拆文件",
        "- [ ] 5. 金手指.md：能力边界/成长成本/升级路径（对标书爽点发动机）",
        "- [ ] 6. 爽点密度抽样：抽最近 10 章，每章爽点类型与间隔记录",
        "- [ ] 7. 节奏样本：抽连续 5 章，记录每章「阻碍→代价→回报」三拍的实际字数占比",
        "",
        "全部完成后，本目录即是本书的**剧情模块库**，供对家轮审稿与",
        "本书写作时对标消费（`references/anti-pattern-checklist.md` 第三轮）。",
        "",
        "## 本目录文件",
        "",
        "| 文件 | 用途 |",
        "|---|---|",
        "| 文风基线.json | check-distribution.py --baseline 的对比基准 |",
        "| 文风.md | 人读文风报告（分布/标点/用词） |",
        "| 拆文报告.md | 本文件：统计摘要 + 语义层待办 |",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="novel-forge 对标书拆文引擎（文风基线 + 文风报告 + 拆文待办）")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--book", help="对标书目录（每章一个 txt/md）")
    src.add_argument("--file", help="整本单文件（txt/md）")
    ap.add_argument("--split-chapters", action="store_true", help="整本单文件按「第N章」行切分")
    ap.add_argument("--project", help="你的书目录；给了它，输出默认 <project>/对标/<书名>/")
    ap.add_argument("--out", help="输出目录（默认 <project>/对标/<书名> 或 ./对标分析_<书名>）")
    ap.add_argument("--name", help="书名（默认取目录名/文件名主干）")
    ap.add_argument("--dry-run", action="store_true", help="只打印统计摘要，不写文件")
    args = ap.parse_args()

    if args.book:
        label = args.name or os.path.basename(os.path.normpath(args.book))
        chapters = load_chapters(book=args.book)
    else:
        label = args.name or re.sub(r"\.(txt|md|text)$", "", os.path.basename(args.file))
        chapters = load_chapters(file=args.file, split=args.split_chapters)
    if not chapters:
        sys.stderr.write("[analyze-book] 没有读到有效章节（每章至少 200 可见字）\n")
        return 2
    stable = len(chapters) >= MIN_CHAPTERS_FOR_STABLE

    payload = build_baseline_payload(chapters, label)
    punct, quote_style = punct_fingerprint(chapters)
    bg, tg, _ = word_freq(chapters)
    heads = para_heads(chapters)
    end_types, end_ratio = chapter_end_types(chapters)
    dlg_per_ch = per_chapter_dialogue(chapters)

    if args.dry_run:
        m = payload["merged"]
        print("书名：%s ｜ %d 章 ｜ 稳定性：%s" % (label, len(chapters), "稳" if stable else "不稳（<%d 章）" % MIN_CHAPTERS_FOR_STABLE))
        print("句长 mean %.1f sd %.1f ｜ 段长 mean %.1f sd %.1f ｜ 章长 mean %.0f ｜ 对话 mean %.1f%%"
              % (m["sentence"]["mean"], m["sentence"]["stdev"], m["paragraph"]["mean"],
                 m["paragraph"]["stdev"], m["chapter_len"]["mean"], m["dialogue_ratio"]["mean"] * 100))
        print("标点/千字：%s" % "；".join("%s %.2f" % (k, v) for k, v in punct.items()))
        return 0

    out = args.out or (os.path.join(args.project, "对标", label) if args.project
                       else os.path.join("对标分析_" + label))
    os.makedirs(out, exist_ok=True)

    style_md = render_style_md(label, chapters, payload, punct, quote_style, bg, tg,
                               heads, end_types, end_ratio, dlg_per_ch, stable)
    teardown_md = render_teardown_md(label, chapters, payload, stable)
    for fname, content in (("文风基线.json", json.dumps(payload, ensure_ascii=False, indent=1) + "\n"),
                           ("文风.md", style_md + "\n"),
                           ("拆文报告.md", teardown_md + "\n")):
        path = os.path.join(out, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print("[写入] %s" % path)

    print("-" * 64)
    print("%d 章 ｜ 句长 mean %.1f ｜ 段长 mean %.1f ｜ 对话 %.1f%% ｜ 稳定性：%s"
          % (len(chapters), payload["merged"]["sentence"]["mean"], payload["merged"]["paragraph"]["mean"],
             payload["merged"]["dialogue_ratio"]["mean"] * 100, "稳" if stable else "不稳"))
    print("写正文时把对比基准换成它：")
    print("  python scripts/check-distribution.py --book <书目录> --chapter N --baseline %s"
          % os.path.join(out, "文风基线.json"))
    print("语义层拆解待办见：%s" % os.path.join(out, "拆文报告.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
