#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
人类网文分布基线采集器 —— references/human-baseline.json 的唯一生成者。

为什么需要它（方案【七】）：
  human-baseline.json 是 check-distribution.py 的对比基准。基线数字必须可溯源：
  每一个 mean/stdev/skewness 都要能指向「哪几本书、多少章、用什么口径算的」。
  本脚本把这条链路固化下来——想改基线，就跑它重新生成，不要手改 JSON。

口径（与 check-distribution.py 保持一致，改一侧必须同步改另一侧）：
  句子切分     ：。！？…（含其后引号收尾）
  字数单位     ：字符数（含标点，不含空白）
  对话占比     ：引号内文字 / 全文字符（不含引号；不跨行；嵌套只计一次）
  段落         ：非空行 = 段（网文 txt 一段一行；markdown 空行分段时每段一行，等价）
  章长         ：单章正文字符数

用法：
  # 从语料目录采集（目录结构：<corpus>/<书名>/*.txt|*.md，每文件一章或用 --split-chapters）
  python collect-baseline.py --corpus <语料目录> --out ../references/human-baseline.json

  # 只统计、不写文件（先看数字合不合理）
  python collect-baseline.py --corpus <语料目录> --dry-run

  # 校准现有基线：只看当前 JSON 的摘要
  python collect-baseline.py --show ../references/human-baseline.json

语料目录约定（--corpus 下的直接子目录 = 一本「书」）：
  <corpus>/
  ├── 十日终焉/          ← 一本书
  │   ├── 第001章.txt
  │   ├── 第002章.txt
  │   └── ...
  ├── 宿命之环/
  │   └── ...
  └── ...

  也支持「一本书一个 txt（整本合并）」——此时加 --split-chapters
  会按「第N章」标题行切分。

版权与合规：
  本脚本只读取你本地的语料文件，不上传、不联网。请自行确保语料来源合法、
  仅用于分布统计（不复制原文进仓库）。生成的 JSON 只含聚合统计量，
  不含任何原文句子。

依赖：仅标准库（纯 CPU、零第三方依赖）。
"""

import argparse
import glob
import json
import math
import os
import re
import sys

# ---------------------------------------------------------------- 口径常量
# 与 check-distribution.py 保持同步；改这里必须同步改那边。

SENTENCE_END = "。！？…"
# 与 check-distribution.py 的 QUOTE_PAIRS 保持一致（含【】）；改一侧必须同步改另一侧
QUOTE_PAIRS = [("「", "」"), ("『", "』"), ("【", "】"), ("“", "”"), ("‘", "’"), ('"', '"'), ("'", "'")]
CHAPTER_HEAD = re.compile(r"^第[零一二三四五六七八九十百千万\d]+章")
HEAD_STRUCT = re.compile(r"^(#{1,6}\s|>\s?|[-*+]\s|\d+[.)]\s|\|)")

PERCENTILES = [10, 25, 50, 75, 90, 99]


def visible_len(s):
    """可见字符数：去掉所有空白。"""
    return len(re.sub(r"\s", "", s))


def read_text(path):
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def strip_non_prose(text):
    """去掉 markdown 结构行（标题/引用/列表/表格）。"""
    out = []
    for line in text.splitlines():
        t = line.strip()
        if not t:
            out.append("")
            continue
        if HEAD_STRUCT.match(t):
            continue
        out.append(line)
    return "\n".join(out)


def split_sentences(text):
    """按中文句末标点切分，保留标点。"""
    sents = []
    buf = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        buf.append(ch)
        if ch in SENTENCE_END:
            # 吸收紧随其后的收尾引号
            j = i + 1
            while j < n and text[j] in "」』”’\"'":
                buf.append(text[j])
                j += 1
            i = j
            sents.append("".join(buf))
            buf = []
            continue
        i += 1
    if buf:
        tail = "".join(buf).strip()
        if tail:
            sents.append(tail)
    return sents


def split_paragraphs(text):
    """非空行 = 段。

    口径依据：网文 txt 一段一行（段间无空行），markdown 空行分段时每段恰好一行——
    两种格式下「非空行」都等价于段落。禁止按空行分块：对无空行的 txt 会把整章
    当成一个段落，段长分布被章长污染（实测段均 2331 字的假分布就是这么来的）。
    """
    paras = []
    for line in text.splitlines():
        b = line.strip()
        if not b:
            continue
        paras.append(b)
    return paras


QUOTE_ANY = re.compile(r'[「」『』【】“”‘’"\']')
QUOTE_SET = frozenset('「」『』【】“”‘’"\'')


def dialogue_inside_chars(text):
    """对话字符数：已配对引号区间内、非空白、非引号字符。同一字符只计一次。

    口径（collect-baseline.py 与 check-distribution.py 双侧同款函数，改一侧必须同步改另一侧）：
    1. 逐行统计——引号不跨行。网文对话一段一行；跨行配对会把相隔数行的
       两个孤立引号配成巨型假对话段（collect-baseline 旧实现就允许跨行）。
    2. 开闭符不同的对（「」『』【】“”）按深度配对；直引号 " ' 开闭同形，
       逐字符 toggle 配对。行内未闭合的开引号按行尾截断计入（不跨行是唯一边界）。
    3. 嵌套/多型引号重叠只计一次（布尔掩码取并集）。旧实现按匹配对累加，
       重复计数可把占比推到 >1（实测 zhanshen/ch1153.txt：半角 " 与 ' 区间
       重叠 817 字，ratio=1.18，物理上不可能）。
    4. 引号字符本身一律不计（无论作定界符还是嵌套在区间内）——「对话文字量」
       不含括住它的标点，规则对任意嵌套深度统一，无歧义边界。
    """
    inside = 0
    for line in text.splitlines():
        if not QUOTE_ANY.search(line):
            continue
        n = len(line)
        mask = bytearray(n)
        for op, cl in QUOTE_PAIRS:
            if op == cl:
                inq = False
                for i, ch in enumerate(line):
                    if ch == op:
                        inq = not inq
                    elif inq:
                        mask[i] = 1
            else:
                depth = 0
                for i, ch in enumerate(line):
                    if ch == op:
                        depth += 1
                    elif depth > 0:
                        if ch == cl:
                            depth -= 1
                        else:
                            mask[i] = 1
        inside += sum(1 for i in range(n) if mask[i] and not line[i].isspace() and line[i] not in QUOTE_SET)
    return inside


def split_chapters_if_needed(text):
    """整本合并文本 → 按「第N章」行切成章列表。"""
    lines = text.splitlines()
    chapters, cur = [], []
    for line in lines:
        if CHAPTER_HEAD.match(line.strip()) and cur:
            chapters.append("\n".join(cur))
            cur = [line]
        else:
            cur.append(line)
    if cur:
        chapters.append("\n".join(cur))
    return [c for c in chapters if c.strip()]


# ---------------------------------------------------------------- 统计工具

def pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return float(sorted_vals[lo])
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def describe(vals, digits=3):
    """对一列数值给出 n/mean/stdev/cv/skew/kurt/分位数/max（统一 schema）。

    digits 控制 mean/stdev 小数位（比例型数值如对话占比传 4 保留精度）；
    分位数与 max 统一保留 4 位——比 2 位更精确，对长度型整数统计无实际影响。
    所有送入 merge_stats 的统计必须出自本函数，保证字段集一致：
    任何字段缺失都会在合并层被跳过并告警，不会被静默当 0 参与加权。
    """
    n = len(vals)
    if n == 0:
        return {"n": 0}
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    sd = math.sqrt(var)
    if sd == 0:
        skew = kurt = 0.0
    else:
        skew = sum(((v - mean) / sd) ** 3 for v in vals) / n
        kurt = sum(((v - mean) / sd) ** 4 for v in vals) / n - 3.0  # excess kurtosis
    sv = sorted(vals)
    out = {
        "n": n,
        "mean": round(mean, digits),
        "stdev": round(sd, digits),
        "cv": round(sd / mean, 4) if mean else 0.0,
        "skewness": round(skew, 4),
        "kurtosis": round(kurt, 4),
        "max": round(max(vals), 4),
    }
    for p in PERCENTILES:
        out["p%d" % p] = round(pct(sv, p), 4)
    return out


def merge_stats(group_descs, key):
    """按权重把多本书的同一字段合并成总体描述（近似：用各书 n 加权均值）。

    防呆（假阴性教训）：某书缺某字段 → 跳过该书对该字段的贡献并告警，
    绝不把「缺」当 0 参与加权——dialogue_ratio 曾因单书层字段不全，
    合并层 .get(field, 0) 静默补零，cv/skewness/kurtosis/p25/p75/p99/max 全 0。
    """
    contributors = [g for g in group_descs if key in g and g[key].get("n")]
    total_n = sum(g[key]["n"] for g in contributors)
    if not total_n:
        return {"n": 0}

    def weighted(field, nd=4):
        vals = [(g[key][field], g[key]["n"]) for g in contributors if field in g[key]]
        if not vals:
            return None
        if len(vals) < len(contributors):
            sys.stderr.write(
                "[collect-baseline] 警告：%s 的 %s 有 %d/%d 本书缺失——已跳过缺失书再加权（不按 0 参与），请检查采集口径\n"
                % (key, field, len(contributors) - len(vals), len(contributors))
            )
        return round(sum(v * w for v, w in vals) / sum(w for _, w in vals), nd)

    merged = {"n": total_n}
    for field in ("mean", "stdev", "cv", "skewness", "kurtosis"):
        v = weighted(field)
        if v is not None:
            merged[field] = v
    for p in PERCENTILES:
        v = weighted("p%d" % p)
        if v is not None:
            merged["p%d" % p] = v
    maxes = [g[key]["max"] for g in contributors if "max" in g[key]]
    if maxes:
        merged["max"] = max(maxes)
    return merged


# ---------------------------------------------------------------- 采集主流程

def collect_book(book_dir, split_chapters=False, label=None, meta=None):
    """采集一本书的分布。返回 (book_stats, chapter_count, char_count)。"""
    files = []
    for ext in ("*.txt", "*.md", "*.text"):
        files += glob.glob(os.path.join(book_dir, ext))
    files.sort()
    if not files:
        return None, 0, 0

    sent_lens, para_lens, chap_lens, dlg_ratios = [], [], [], []

    for fp in files:
        raw = read_text(fp)
        # 一本书一个 txt：按章切分
        if split_chapters:
            chapters = split_chapters_if_needed(raw)
        else:
            chapters = [raw]
        for ch in chapters:
            body = strip_non_prose(ch)
            if visible_len(body) < 200:  # 太短的碎片不算一章（版权页/目录）
                continue
            chap_lens.append(visible_len(body))
            for s in split_sentences(body):
                L = visible_len(s)
                if L:
                    sent_lens.append(L)
            for p in split_paragraphs(body):
                L = visible_len(p)
                if L:
                    para_lens.append(L)
            total = visible_len(body)
            dlg_ratios.append(dialogue_inside_chars(body) / total if total else 0.0)

    if not chap_lens:
        return None, 0, 0

    stats = {
        "chapters_used": len(chap_lens),
        "sentence": describe(sent_lens),
        "paragraph": describe(para_lens),
        "chapter_len": describe(chap_lens),
        # 与 sentence/paragraph 同一 schema（describe 全字段）；手搓子集字段
        # 会在合并层被 .get 静默补 0（cv/skew/分位数曾因此全 0），禁止回退
        "dialogue_ratio": describe(dlg_ratios, digits=4),
    }
    if meta:
        stats.update(meta)
    if label:
        stats["label"] = label
    return stats, len(chap_lens), sum(chap_lens)


def run_collect(corpus, out_path, split_chapters=False, dry_run=False, meta_map=None):
    if not os.path.isdir(corpus):
        sys.stderr.write("[collect-baseline] 语料目录不存在：%s\n" % corpus)
        return 2
    book_dirs = sorted(
        d for d in glob.glob(os.path.join(corpus, "*")) if os.path.isdir(d)
    )
    if not book_dirs:
        sys.stderr.write("[collect-baseline] %s 下没有子目录（每本书一个子目录）\n" % corpus)
        return 2

    sources, books, group_descs = [], {}, []
    total_chapters = total_chars = 0
    for bd in book_dirs:
        name = os.path.basename(bd)
        meta = (meta_map or {}).get(name, {})
        stats, nch, nchars = collect_book(bd, split_chapters=split_chapters, label=name, meta=meta)
        if not stats:
            print("  [跳过] %s（无有效章节）" % name)
            continue
        books[name] = stats
        group_descs.append(stats)
        total_chapters += nch
        total_chars += nchars
        sources.append({
            "label": name,
            "book": meta.get("book", name),
            "author": meta.get("author", ""),
            "genre": meta.get("genre", ""),
            "note": meta.get("note", ""),
            "chapters": nch,
            "chars": nchars,
        })
        print("  [采集] %-24s 章 %-5d 字 %-9d 句均 %.2f 段均 %.2f"
              % (name, nch, nchars, stats["sentence"]["mean"], stats["paragraph"]["mean"]))

    if not books:
        sys.stderr.write("[collect-baseline] 没有采集到任何有效数据。\n")
        return 2

    merged = {
        "sentence": merge_stats(group_descs, "sentence"),
        "paragraph": merge_stats(group_descs, "paragraph"),
        "chapter_len": merge_stats(group_descs, "chapter_len"),
        "dialogue_ratio": merge_stats(group_descs, "dialogue_ratio"),
    }

    payload = {
        "_meta": {
            "version": 2,
            "generated": _now(),
            "purpose": "human writing distribution baseline for novel-forge check-distribution.py",
            "generator": "scripts/collect-baseline.py",
            "caveat": ("人类写作存在作者差异，本基线给出的是「专业人类网文作者的真实分布区间」，"
                       "用于识别 AI 初稿的分布异常（过度均匀/过度整齐），非硬性标准。"),
            "sampling": {
                "sentence_split": "。！？…",
                "length_unit": "字符数（含标点，不含空白）",
                "dialogue_ratio": "引号内文字 / 全文字符（不含引号；不跨行；嵌套只计一次）",
                "paragraph": "非空行 = 段（网文一段一行；markdown 空行分段时等价）",
            },
            "totals": {
                "books": len(books),
                "chapters": total_chapters,
                "chars": total_chars,
                "sentences": merged["sentence"]["n"],
                "paragraphs": merged["paragraph"]["n"],
            },
        },
        "sources": sources,
        "books": books,
        "merged": merged,
    }

    print("-" * 64)
    print("合计：%d 本书 / %d 章 / %d 句 / %d 段 / %d 字"
          % (len(books), total_chapters, merged["sentence"]["n"],
             merged["paragraph"]["n"], total_chars))
    print("句长：mean %.3f sd %.3f skew %.4f kurt %.4f"
          % (merged["sentence"]["mean"], merged["sentence"]["stdev"],
             merged["sentence"]["skewness"], merged["sentence"]["kurtosis"]))
    print("段长：mean %.3f sd %.3f" % (merged["paragraph"]["mean"], merged["paragraph"]["stdev"]))
    print("章长：mean %.1f sd %.1f" % (merged["chapter_len"]["mean"], merged["chapter_len"]["stdev"]))
    print("对话占比：mean %.4f sd %.4f" % (merged["dialogue_ratio"]["mean"], merged["dialogue_ratio"]["stdev"]))

    if dry_run:
        print("-" * 64)
        print("[dry-run] 未写文件。确认数字合理后去掉 --dry-run 重新生成。")
        return 0

    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("-" * 64)
    print("[写入] %s" % out_path)
    print("       记得同步跑 python forge.py selftest 确认 check-distribution 仍全绿。")
    return 0


def _now():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def show(path):
    if not os.path.exists(path):
        sys.stderr.write("[collect-baseline] 文件不存在：%s\n" % path)
        return 2
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    t = data.get("_meta", {}).get("totals", {})
    print("=" * 64)
    print("novel-forge 人类基线摘要 ｜ %s" % path)
    print("=" * 64)
    print("  生成时间 : %s" % data.get("_meta", {}).get("generated", "?"))
    print("  生成脚本 : %s" % data.get("_meta", {}).get("generator", "（早于本脚本，来源待核）"))
    print("  书 / 章  : %s / %s" % (t.get("books", "?"), t.get("chapters", "?")))
    print("  句 / 段  : %s / %s" % (t.get("sentences", "?"), t.get("paragraphs", "?")))
    for name in ("sentence", "paragraph", "chapter_len", "dialogue_ratio"):
        m = data.get("merged", {}).get(name, {})
        print("  %-14s mean %.3f sd %.3f" % (name, m.get("mean", 0), m.get("stdev", 0)))
    for s in data.get("sources", []):
        print("    - %-22s 章 %-5s 字 %-9s %s" % (s.get("label"), s.get("chapters"),
                                                  s.get("chars"), s.get("book")))
    return 0


def main():
    ap = argparse.ArgumentParser(description="人类网文分布基线采集器（human-baseline.json 的唯一生成者）")
    ap.add_argument("--corpus", help="语料目录（每本书一个子目录）")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "..", "references", "human-baseline.json"),
                    help="输出 JSON（默认 references/human-baseline.json）")
    ap.add_argument("--split-chapters", action="store_true",
                    help="整本一个 txt 时，按「第N章」标题行切分")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    ap.add_argument("--meta", help="每本书的元信息 JSON（book/author/genre/note），键为子目录名")
    ap.add_argument("--show", help="只打印现有基线摘要（用于核查来源）")
    args = ap.parse_args()

    if args.show:
        return show(args.show)
    if not args.corpus:
        ap.error("需要 --corpus 或 --show")
    meta_map = None
    if args.meta and os.path.exists(args.meta):
        with open(args.meta, "r", encoding="utf-8") as f:
            meta_map = json.load(f)
    return run_collect(args.corpus, args.out, split_chapters=args.split_chapters,
                       dry_run=args.dry_run, meta_map=meta_map)


if __name__ == "__main__":
    sys.exit(main())
