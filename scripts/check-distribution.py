#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
novel-forge 分布级检测（v2.0）。

「查词」能清零机械感，但查不出「分布像 AI」。本脚本做的是分布级检测：
把这一章的句长/段长/对话占比等统计量，跟 references/human-baseline.json
里的真实人类网文分布对比，识别「过度均匀 / 过度整齐」的 AI 指纹。

全部输出 advisory（不阻断）。用于人工审稿前先看一遍——机器给分布，人做判断。

检测项：
  1. 句长分布   标准差/偏度/峰度 vs 人类基线
  1b. 长句存在性 40 字以上句子占比 <10% → advisory（「分布在短端」盲区：
                均匀短句的 sd/skew 可能侥幸过线，但人类基线 40 字以上
                句约占 15%（p75=34.6/p90=53），一章没有必然异常）
  2. 段长分布   90% 段落是否挤在同一区间（过度均匀）
  2b. 长段存在性 70 字以上段落为 0 → advisory（人类基线 p90=66.5/p99=107.8，
                铺陈/闪回/过渡处本该有长段；永远短段 = AI 腔）
  3. 对话占比   按 500 字窗口的方差（节奏是否太匀）
  4. 信息密度   连续 N 段全部「推进」（无闲笔）
  5. 伏笔间隔   埋设→回收间隔是否全挤在 1–3 章（太工整）——需追踪数据，可选

取样口径（与 human-baseline.json 一致）：
  句子切分   。！？…（半角 .!? 仅在句末且后接空白/行尾时计入）
  长度单位   字符数（含标点，不含空白）
  对话占比   引号内文字 / 全文字符（不含引号；不跨行；嵌套只计一次）
  段落       非空行 = 段（网文 txt 一段一行；markdown 空行分段时每段一行，等价）

用法：
  python check-distribution.py --file 正文/第N章.md
  python check-distribution.py --book <书目录> --chapter N
  python check-distribution.py --file 第N章.md --baseline references/human-baseline.json
  python check-distribution.py --file 第N章.md --json     机器可读输出

设计原则：纯 CPU、零外部依赖、只读。全部 advisory，退出码恒为 0（除非文件不存在=2）。
"""

import argparse
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BASELINE = os.path.join(os.path.dirname(HERE), "references", "human-baseline.json")

# ---------------------------------------------------------------- 文本处理

QUOTE_PAIRS = [("「", "」"), ("『", "』"), ("【", "】"), ("“", "”"), ("‘", "’"), ('"', '"'), ("'", "'")]
VISIBLE = re.compile(r"[^\s]")
CJK = re.compile(r"[\u4e00-\u9fff]")

# 句末标点（与基线取样一致）
SENT_END = "。！？…"
STRUCTURAL = re.compile(r"^(#{1,6}\s|>\s?|[-*+]\s|\d+[.)]\s|\|)")
DIVIDER = re.compile(r"^[-*_]{3,}$")
CHAPTER_HEAD = re.compile(r"^第[零一二三四五六七八九十百千万\d]+章(?:\s|_|$)")
FRONTMATTER = re.compile(r"^---\s*$")

# 段落是否「推进主线/设定/冲突」的粗信号（无闲笔检测用）
ADVANCE_SIGNALS = [
    "推开", "冲", "跑", "抓", "打", "砍", "杀", "摔", "砸", "踢", "拽", "扯", "拔",
    "喊", "吼", "叫", "问", "答", "说", "道", "开口",
    "发现", "察觉", "意识", "明白", "知道", "想起", "回忆",
    "决定", "必须", "得先", "下一步", "计划", "盯上", "锁定",
    "突破", "晋级", "升级", "获得", "拿到", "得到", "解锁",
    "死", "伤", "血", "危险", "警告", "威胁", "陷阱", "真相", "秘密",
]


def read(path):
    if not os.path.exists(path):
        sys.stderr.write("[novel-forge] 文件不存在：%s\n" % path)
        sys.exit(2)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def strip_comments(text):
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def strip_markdown_scaffold(text):
    """去标题、分隔线、frontmatter，只留散文正文。"""
    out = []
    in_fm = False
    for i, raw in enumerate(text.splitlines()):
        s = raw.strip()
        if i == 0 and FRONTMATTER.match(s):
            in_fm = True
            continue
        if in_fm:
            if FRONTMATTER.match(s):
                in_fm = False
            continue
        if not s:
            out.append("")
            continue
        if DIVIDER.match(s) or STRUCTURAL.match(s) or CHAPTER_HEAD.match(s) or FRONTMATTER.match(s):
            continue
        out.append(raw)
    return "\n".join(out)


def vlen(s):
    """可见长度：字符数，不含空白。"""
    return len(VISIBLE.findall(s))


def split_sentences(text):
    """按 。！？… 切句（保留标点），含半角 .!? 句末形态。返回长度列表。"""
    parts = re.split(r"(?<=[。！？…])|(?<=[.!?])(?=\s|$)", text)
    lens = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        n = vlen(p)
        if n > 0:
            lens.append(n)
    return lens


def split_paragraphs(text):
    """非空行 = 段，返回段长列表。

    口径依据：网文 txt 一段一行（段间无空行），markdown 空行分段时每段恰好一行——
    两种格式下「非空行」都等价于段落。禁止按空行分块：对无空行的 txt 会把整章
    当成一个段落，段长分布被章长污染。
    """
    lens = []
    for line in text.splitlines():
        p = line.strip()
        if not p:
            continue
        n = vlen(p)
        if n > 0:
            lens.append(n)
    return lens


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


def dialogue_ratio(text):
    """引号内文字（不含引号；不跨行；嵌套只计一次）/ 全文字符。"""
    total = vlen(text)
    if total == 0:
        return 0.0
    return dialogue_inside_chars(text) / total


def quoted_spans(text):
    spans = []
    for op, cl in QUOTE_PAIRS:
        for m in re.finditer(re.escape(op) + "[^" + re.escape(cl) + r"\n]*" + re.escape(cl), text):
            spans.append((m.start(), m.end()))
    spans.sort()
    merged = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def strip_quotes(text):
    spans = quoted_spans(text)
    if not spans:
        return text
    chars = list(text)
    for s, e in spans:
        for i in range(s, min(e, len(chars))):
            chars[i] = " " if chars[i] != "\n" else "\n"
    return "".join(chars)


# ---------------------------------------------------------------- 统计

def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def skewness(xs):
    n = len(xs)
    if n < 3:
        return 0.0
    m = mean(xs)
    s = stdev(xs)
    if s == 0:
        return 0.0
    return sum(((x - m) / s) ** 3 for x in xs) / n


def kurtosis(xs):
    """超额峰度（正态=0；python 口径与 numpy fisher 一致）。"""
    n = len(xs)
    if n < 4:
        return 0.0
    m = mean(xs)
    s = stdev(xs)
    if s == 0:
        return 0.0
    return sum(((x - m) / s) ** 4 for x in xs) / n - 3.0


def percentile(xs, q):
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return float(s[lo])
    return s[lo] * (hi - k) + s[hi] * (k - lo)


def window_dialogue_variance(text, win=500):
    """按 win 字窗口统计对话占比，返回 (方差, 窗口数)。"""
    clean = text
    # 按可见字符滚动窗口
    idx = [i for i, c in enumerate(clean) if VISIBLE.match(c)]
    if len(idx) < win * 2:
        # 太短：整篇一个窗口，方差 0 无法判断
        return 0.0, 1
    ratios = []
    step = win
    for start in range(0, len(idx) - win, step):
        chunk_idx = idx[start:start + win]
        seg = "".join(clean[i] for i in chunk_idx)
        ratios.append(dialogue_ratio(seg))
    if len(ratios) < 2:
        return 0.0, len(ratios)
    return stdev(ratios) ** 2, len(ratios)


def advance_ratio_of_para(para):
    """粗判这一段是否「推进」：含推进信号动词/名词。"""
    t = strip_quotes(para)
    hits = sum(1 for w in ADVANCE_SIGNALS if w in t)
    return hits > 0


def longest_advance_run(text):
    """返回最长「连续全部推进」的段数（段 = 非空行，与 split_paragraphs 同口径）。"""
    run = best = 0
    for line in text.splitlines():
        p = line.strip()
        if not p:
            continue  # 空行不是段落，不重置连续计数
        if advance_ratio_of_para(p):
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


# ---------------------------------------------------------------- 检测

def load_baseline(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    return d.get("merged") or d


def check_distribution(path, baseline_path=DEFAULT_BASELINE):
    raw = read(path)
    text = strip_markdown_scaffold(strip_comments(raw))

    sents = split_sentences(text)
    paras = split_paragraphs(text)
    dr = dialogue_ratio(text)

    adv = []

    # ---- 1. 句长分布 ----
    sent_stats = {
        "n": len(sents),
        "mean": round(mean(sents), 3),
        "stdev": round(stdev(sents), 3),
        "skewness": round(skewness(sents), 4),
        "kurtosis": round(kurtosis(sents), 4),
        "p50": percentile(sents, 0.5),
        "p90": percentile(sents, 0.9),
    }
    base = load_baseline(baseline_path)
    if base:
        bs = base.get("sentence", {})
        b_std = bs.get("stdev")
        b_skew = bs.get("skewness")
        # 标准差显著偏低 → 句长太匀（AI 指纹）
        if b_std and sent_stats["stdev"] < b_std * 0.6:
            adv.append("句长标准差 %.1f 明显低于人类基线 %.1f（<60%%）→ 句长过于均匀，"
                       "缺少长句黏连。建议故意写几个长句（把两个短句用逗号/连词接起来）。"
                       % (sent_stats["stdev"], b_std))
        if b_skew and sent_stats["skewness"] < b_skew * 0.5:
            adv.append("句长偏度 %.2f 低于人类基线 %.2f（<50%%）→ 分布太对称，"
                       "人类写作会有更多异常长句。建议来一两个「本来该断但没断」的长句。"
                       % (sent_stats["skewness"], b_skew))
        # 长度上限集中：p90 过窄
        if sent_stats["n"] >= 30 and sent_stats["p90"] < 30:
            adv.append("句长 p90=%.0f 偏窄 → 90%% 的句子都在 30 字以内，全篇短句节奏。"
                       % sent_stats["p90"])

    # ---- 1b. 长句存在性（分布在短端盲区）----
    # 人类基线 sentence p75=34.6 / p90=53 → ≥40 字句约占 15–18%。
    # 阈值 10% 卡在人类常态下沿之下：人类章不触发，AI 均匀短句章触发。
    long_sents = sum(1 for L in sents if L >= 40)
    sent_stats["long_ge40"] = long_sents
    sent_stats["long_ge40_ratio"] = round(long_sents / len(sents), 3) if sents else 0.0
    if len(sents) >= 30 and long_sents / len(sents) < 0.10:
        adv.append("40 字以上长句仅 %d/%d（%.0f%%，<10%%）→ 全章几乎无长句。"
                   "人类基线 40 字以上句约占 15%%（p75=34.6 / p90=53）。"
                   "别全篇同一个呼吸节奏：把该断的短句用逗号/连词接起来，"
                   "每章至少 3 个长句。"
                   % (long_sents, len(sents), long_sents / len(sents) * 100))

    # ---- 2. 段长分布 ----
    para_stats = {
        "n": len(paras),
        "mean": round(mean(paras), 3),
        "stdev": round(stdev(paras), 3),
        "p50": percentile(paras, 0.5),
        "p90": percentile(paras, 0.9),
    }
    if paras:
        in_band = sum(1 for p in paras if 30 <= p <= 60)
        ratio_band = in_band / len(paras)
        if ratio_band >= 0.90 and len(paras) >= 10:
            adv.append("90%% 段落落在 30–60 字（实际 %.0f%%）→ 段长过度均匀。"
                       "人类写作会有明显更长和更短的段。建议拆几个单句段、留一两个长段。"
                       % (ratio_band * 100))
    para_stats["band_30_60_ratio"] = round(
        (sum(1 for p in paras if 30 <= p <= 60) / len(paras)) if paras else 0.0, 3)

    # ---- 2b. 长段存在性（段长全部落在短端的盲区）----
    # 人类基线 paragraph p90=66.5 / p99=107.8 → ≥70 字段约占 8–9%。
    # 一章一个 70 字以上的段都没有 → 段长全挤在短端（AI 均匀短段指纹）。
    long_paras = sum(1 for L in paras if L >= 70)
    para_stats["long_ge70"] = long_paras
    if len(paras) >= 10 and long_paras == 0:
        adv.append("70 字以上段落为 0（共 %d 段，最长 %d 字）→ 段长全挤在短端。"
                   "人类基线 p90=66.5：铺陈、闪回、场景过渡处本该有长段。"
                   "允许一到两段「本来可以拆成三段但选择不拆」的长段。"
                   % (len(paras), max(paras) if paras else 0))

    # ---- 3. 对话占比波动 ----
    dvar, nwin = window_dialogue_variance(text, win=500)
    # 只有在窗口足够多（≥5）时才判方差，否则小样本噪声大，容易误报人类作品
    if nwin >= 5 and dvar < 0.002:
        adv.append("500 字窗口的对话占比方差 %.4f 过小（%d 个窗口）→ 节奏太匀，"
                   "每个位置的对话密度都差不多。建议安排一段几乎没对话的纯叙述 + "
                   "一段密集对话。" % (dvar, nwin))

    if base:
        br = base.get("dialogue_ratio", {})
        if br:
            lo = br.get("p10"); hi = br.get("p90")
            if lo is not None and dr < lo * 0.6:
                adv.append("全章对话占比 %.1f%% 远低于人类基线 p10（%.1f%%）→ 通篇叙述，"
                           "读起来会闷。适度加对话。" % (dr * 100, lo * 100))
            if hi is not None and dr > hi * 1.25:
                adv.append("全章对话占比 %.1f%% 远高于人类基线 p90（%.1f%%）→ 对话注水，"
                           "叙述承载力不足。" % (dr * 100, hi * 100))

    # ---- 4. 信息密度波动（闲笔检测）----
    run = longest_advance_run(text)
    if run >= 10:
        adv.append("连续 %d 段全部在推剧情/设定/冲突 → 无闲笔。人类写作会有纯气氛、"
                   "纯环境、人物小动作的段落。建议插入一段不推进主线的闲笔。" % run)

    return {
        "file": path,
        "chapter": _chapter_no(path),
        "sentence": sent_stats,
        "paragraph": para_stats,
        "dialogue_ratio": round(dr, 4),
        "dialogue_window_variance": round(dvar, 6),
        "dialogue_windows": nwin,
        "longest_advance_run": run,
        "advisories": adv,
        "advisory_count": len(adv),
        "baseline": os.path.basename(baseline_path) if base else None,
    }


def _chapter_no(path):
    m = re.search(r"第\s*0*(\d+)\s*章", os.path.basename(path))
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- 输出

def print_report(r):
    print("=" * 72)
    print("novel-forge 分布级检测（advisory · 不阻断）")
    print("=" * 72)
    print("  文件     : %s" % os.path.basename(r["file"]))
    print("  基线     : %s" % (r.get("baseline") or "（未找到，仅做绝对判断）"))
    print("-" * 72)
    s = r["sentence"]
    print("  [句长] n=%d mean=%.1f sd=%.1f skew=%.2f kurt=%.2f p50=%.0f p90=%.0f 长句≥40=%d(%.0f%%)"
          % (s["n"], s["mean"], s["stdev"], s["skewness"], s["kurtosis"], s["p50"], s["p90"],
             s.get("long_ge40", 0), s.get("long_ge40_ratio", 0.0) * 100))
    p = r["paragraph"]
    print("  [段长] n=%d mean=%.1f sd=%.1f p50=%.0f p90=%.0f 30–60字占比=%.0f%% 长段≥70=%d"
          % (p["n"], p["mean"], p["stdev"], p["p50"], p["p90"], p["band_30_60_ratio"] * 100,
             p.get("long_ge70", 0)))
    print("  [对话] 全章占比=%.1f%% 窗口方差=%.4f(%d窗)"
          % (r["dialogue_ratio"] * 100, r["dialogue_window_variance"], r["dialogue_windows"]))
    print("  [密度] 最长连续推进段=%d" % r["longest_advance_run"])
    print("-" * 72)
    if r["advisories"]:
        for i, a in enumerate(r["advisories"], 1):
            print("  [建议%d] %s" % (i, a))
        print("-" * 72)
        print("advisory %d 条。逐条判断，不必全改——这些是「读起来像不像人写的」的线索，"
              "不是违规项。" % r["advisory_count"])
    else:
        print("  分布未见明显异常（相对人类基线）。")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="novel-forge 分布级检测（句长/段长/对话/密度，全部 advisory）")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="正文 md 路径")
    src.add_argument("--book", help="书目录（配合 --chapter）")
    ap.add_argument("--chapter", type=int, help="章号（配合 --book）")
    ap.add_argument("--baseline", default=DEFAULT_BASELINE, help="人类基线 json")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    path = args.file
    if args.book:
        if args.chapter is None:
            print("[分布] --book 需配合 --chapter", file=sys.stderr)
            return 2
        cand = [
            os.path.join(args.book, "正文", "final", "第%03d章.md" % args.chapter),
            os.path.join(args.book, "正文", "draft", "第%03d章.md" % args.chapter),
            os.path.join(args.book, "正文", "第%03d章.md" % args.chapter),
            os.path.join(args.book, "正文", "第%d章.md" % args.chapter),
        ]
        path = next((c for c in cand if os.path.exists(c)), None)
        if not path:
            print("[分布] 找不到第 %d 章（试过 final/draft/正文）" % args.chapter, file=sys.stderr)
            return 2

    r = check_distribution(path, baseline_path=args.baseline)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    return print_report(r)


if __name__ == "__main__":
    sys.exit(main())
