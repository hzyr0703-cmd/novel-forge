#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨章自我复述检测（段级 MinHash / Jaccard 估计）

解决 novel-forge 现有门禁的系统性盲区：所有现有脚本（check-degeneration.js
的 --check 模式等）都是**单文件视角**——只查一章之内的复读、截断、占位符。
AI 复述自己前面章节时，每章内部都不重复，每章单独看都「干净」，但全书
300 章里同一段话出现 5 次。现有 9 个脚本对此 100% 漏检。

本脚本做**跨章**比对，零第三方依赖（仅 hashlib + re），纯 CPU。

关键工程约束（实测得出，改动前务必先读 references/crossrepeat.md）：
必须做段级，不能做章级。复述段只占全章约 8%，章级指纹会被正常内容稀释
到噪声之下——实测真实语料上，真实复述对的汉明距离是 25 和 19，而全库最
「像」的一对*无关*章节只有 10，信号完全不可分辨。

阈值经真实文本校准（《红楼梦》15.8 万汉字，切1427 段，注入 40 处复述）：
  Jaccard 估计 >= 0.5、num_perm=64  →  召回 97.5%，误报 0

与 check-degeneration.js 的分工：那个查章内复读，这个查章间复述，两者
互补不重叠，不要动那个脚本。

退出码：
  0  = 未检出跨章复述
  1  = 检出（blocking）
  2  = 用法错误 / 环境不可用
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict

# ---------------------------------------------------------------------------
# 阈值。每个数字都对应references/crossrepeat.md 里的一条校准记录。
# 不要静默调整——擅改会把误报率推高。
# ---------------------------------------------------------------------------
THRESHOLDS = {
    # Jaccard 相似度估计阈值（MinHash），低于此值不算复述。
    # 0.5 =「一半字重叠」，语义直观，便于日后调参解释。
    "jaccard": 0.5,
    # MinHash 置换次数。64 在召回/速度/内存之间取平衡：
    # 实测 1427 段耗时 5.4s。提高可降方差但线性变慢。
    "num_perm": 64,
    # 段切分的目标长度（字）。超过此长度的单段会被二次切分，
    # 避免单段过大导致指纹掩盖局部复述。真实网文段落多在 200-400 字，
    # 因此该值主要约束超长段（如无空行的长段或对话密集段）。
    "segment_target": 120,
    # 段切分时允许的最小长度。低于此长度的段落与相邻段合并——
    # 它们不足以构成有意义的「语块」，单独建指纹会引入噪声。
    # 定 20 的依据：真实章节段落远长于此，本值只在短段场景生效；
    # 实测从 40 降到 20 可完整保留自然段（9 段不再被压成 4 段），
    # 同时避免降到 16 以下时短段指纹噪声上升导致的假阳性。
    "segment_min": 20,
    # 一章之间达到多少个复述段才报。1 段可能是有意呼应，
    # 2 段以上基本可判定为 AI 复述。
    "min_flagged_segments": 2,
    # 但单段命中达到此相似度也报——0.8 意味着「几乎逐字」，
    # 一整段逐字复制绝不是有意呼应，是事故。此值之下 1 段命中
    # 视为呼应忽略，之上视为复述上报（2026-10-06 修复：整段
    # 0.89 相似度逐字复制曾因段数门槛漏报）。
    "single_segment_jaccard": 0.8,
    # 命中段落数占比达到此值，直接判定该章为「整章复述」，
    # 错误信息更明确，置信度更高。
    "mass_ratio_high": 0.5,
}

# 声明块、注释等机器标记不参与比对（与 spec_check.py 的处理保持一致）。
_META_RE = re.compile(r"```.*?```", re.DOTALL)
# 中文字符 + 常用标点，作为 shingle 的基本单元。
_SHINGLE_RE = re.compile(r"[一-鿿　-〿A-Za-z0-9]+")

MASK64 = (1 << 64) - 1


def _read_text(path):
    """读文件，失败返回空串而不是抛异常——单个坏文件不该中断全书检查。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except (OSError, UnicodeError):
        return ""


def strip_meta(text):
    """剥离机器标记块（``` 声明块），它们是给检查器看的，不是正文。"""
    return _META_RE.sub("", text)


def segments(text, target=None, minimum=None):
    """把正文切成段——指纹单位。

    **以段落边界为首选切点**，这是本函数的核心约束。曾经的实现按句子
    切分再合并到目标长度，结果把「原创段+ 复述段」混进同一个指纹，
    Jaccard 被稀释到阈值以下，复述完全检不出来（实测 6 章 9 个自然段被
    压成 2 段，注入的复述段漏检）。段级检测的前提是「段是作者写下的
    一个完整语块」，跨段合并会直接摧毁这个前提。

    合并只在两种情况下发生，且都发生在段落内部或尾部，绝不跨段：
      1. 单个自然段本身太短（< minimum）——与相邻段合并
      2. 章节末尾的残段——并入前一段
    """
    target = target or THRESHOLDS["segment_target"]
    minimum = minimum or THRESHOLDS["segment_min"]

    text = strip_meta(text)

    # 以空行（Markdown 段落边界）为第一优先；若整章没有空行，退回按句末标点切。
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if not blocks:
        blocks = [b for b in re.split(r"[。！？]", text) if b.strip()]
    if not blocks:
        return []

    out = []
    pending = ""          # 等待合并的过短段（可能连续多个微段）
    for block in blocks:
        # 段内换行压成空格（跨行书写但属同一段）
        flat = re.sub(r"\s*\n\s*", "", block)
        if len(flat) < minimum:
            # 太短，挂起等相邻段；连续微段累积，凑够 minimum 即出段。
            # （曾经写反：多个微段累积后仍 < minimum 时在尾部被整个
            # 丢弃，全微段章节产出 0 段——零指纹比坏指纹更危险。）
            pending += flat
            if len(pending) >= minimum:
                out.append(pending)
                pending = ""
            continue
        if pending:
            flat = pending + flat
            pending = ""
        # 过长的大段按句末标点二次切分，避免单段过大导致指纹失真
        if len(flat) > target * 2:
            for piece in re.split(r"(?<=[。！？])", flat):
                piece = piece.strip()
                if not piece:
                    continue
                if len(piece) < minimum:
                    if out:
                        out[-1] += piece
                    continue
                out.append(piece)
        else:
            out.append(flat)

    # 尾部残段并入前一段；无前段（整章全微段）则合并为一段兜底
    if pending:
        if out:
            out[-1] += pending
        elif pending.strip():
            out.append(pending)
    return out


def shingles(seg, size=3):
    """字符级 shingle。

    size=3（3-gram）而非 1-gram：1-gram 在中文短段上 Jaccard 虚高到几乎
    所有段都相似；3-gram 保留了「短语」这个语义单位，能区分
    「复述同一句话」和「都用了很多常见字」。
    """
    text = "".join(_SHINGLE_RE.findall(seg))
    if len(text) < size:
        return {text} if text else set()
    return {text[i:i + size] for i in range(len(text) - size + 1)}


def minhash(shingle_set, num_perm=None):
    """MinHash 签名。返回 num_perm 长度的元组。

    构造：Carter-Wegman 2-universal 哈希族（datasketch 的标准实现）。
    每 shingle 哈希一次得 (h1, h2)，第 i 个 permutation 取
    (h1 + i*h2) mod 2^64——这是被广泛接受的 MinHash 工程构造。

    两个被否决的方案（勿回退）：
    1. 每 shingle 每 permutation 独立 md5——正确但 O(shingles × 64)
       次 md5，300 章长篇要 30 秒。
    2. md5(s) XOR salt_i——快但**数学错误**：两值 XOR 常数后的大小
       关系只由最高不同位和 salt 该位的取值决定，各 permutation
       之间强相关，估计有系统性偏差。XOR 双射不等于 min-wise
       independent，看着像哈希族实际不是。
    """
    num_perm = num_perm or THRESHOLDS["num_perm"]
    if not shingle_set:
        return tuple([0] * num_perm)
    # 每shingle 一次 md5，16 字节拆成两个 64 位流
    pairs = []
    for s in shingle_set:
        digest = hashlib.md5(s.encode("utf-8")).digest()
        h1 = int.from_bytes(digest[:8], "big")
        h2 = int.from_bytes(digest[8:16], "big") | 1   # |1 保证 h2 为奇数，i*h2 遍历满周期
        pairs.append((h1, h2))
    signature = [
        min((h1 + i * h2) & MASK64 for h1, h2 in pairs)
        for i in range(num_perm)
    ]
    return tuple(signature)


def jaccard_est(sig_a, sig_b):
    """MinHash 签名估算 Jaccard 相似度：相同位置比例。"""
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 0.0
    return sum(1 for x, y in zip(sig_a, sig_b) if x == y) / len(sig_a)


def _iter_prose_files(chapter_dir):
    """递归收集正文章节（支持 v2.1 的 正文/draft|final/ 两层布局）。

    同一章号若 draft 与 final 都有，优先取 final（定稿层是导出源）。
    返回 [(章号, 显示名, 路径)]，已按章号排序。
    """
    by_num = {}
    for dirpath, _dirs, files in os.walk(chapter_dir):
        for name in files:
            if not name.endswith(".md") or "细纲" in name:
                continue
            path = os.path.join(dirpath, name)
            if not os.path.isfile(path):
                continue
            digits = re.findall(r"\d+", name)
            num = int(digits[0]) if digits else 0
            is_final = os.path.basename(dirpath) == "final"
            prev = by_num.get(num)
            if prev is None or (is_final and not prev[3]):
                rel = os.path.relpath(path, chapter_dir)
                by_num[num] = (num, rel, path, is_final)
    items = [(num, rel, path) for num, rel, path, _ in by_num.values()]
    items.sort(key=lambda x: (x[0], x[1]))
    return items


def collect_chapters(book_dir, chapter_dir=None):
    """扫描书目录下的正文章节，返回 [(章号, 路径, 文本)]。

    v2.1：支持 正文/draft|final 两层（递归）；同章两层取 final。
    """
    if chapter_dir is None:
        chapter_dir = os.path.join(book_dir, "正文")
    if not os.path.isdir(chapter_dir):
        raise SystemExit(f"[x] 找不到正文章节目录：{chapter_dir}")
    items = _iter_prose_files(chapter_dir)
    if not items:
        raise SystemExit(f"[x] {chapter_dir} 下没有 .md 正文文件")
    return items


def build_index(chapters, num_perm=None):
    """为每章的每个段建指纹，返回 (records, signatures)。"""
    records = []
    signatures = []
    for num, name, path in chapters:
        text = _read_text(path)
        segs = segments(text)
        sigs = []
        for seg in segs:
            sh = shingles(seg)
            sig = minhash(sh, num_perm)
            sigs.append(sig)
            records.append({
                "chapter": num,
                "file": name,
                "seg_index": len(sigs) - 1,
                "chars": len(seg),
                "text": seg,
                "sig": sig,
            })
        signatures.append(sigs)
    return records, signatures


def optimal_param(threshold, num_perm, wfp=0.4):
    """datasketch 式最优 (b, r) 选参——数值积分，不引第三方依赖。

    把 [0,1] 上的 Jaccard 相似度 s 当均匀分布，最小化
    wfp×(0..t 的假阳性面积) + (1-wfp)×(t..1 的假阴性面积)。
    默认 wfp=0.4 偏召回：门禁漏检的代价（复述章节流出）远高于
    多验一些候选对的成本。t=0.5/num_perm=64 时给出 (16,4)，
    P(0.8)=0.9998——与 2026-10-11 五百章埋点实测的选参一致。
    """
    n = 300
    t = threshold
    best = None
    for b in range(1, num_perm + 1):
        for r in range(1, num_perm // b + 1):
            fp_area = sum(1 - (1 - (t / n * i) ** r) ** b for i in range(n)) * threshold / n
            fn_area = sum((1 - (threshold + (1 - threshold) * i / n) ** r) ** b for i in range(n)) * (1 - threshold) / n
            err = fp_area * wfp + fn_area * (1 - wfp)
            if best is None or err < best[0]:
                best = (err, b, r)
    return best[1], best[2]


def lsh_candidate_pairs(records, bands, rows):
    """banding 生成候选章对（只剪候选，判定逻辑不动）。"""
    buckets = defaultdict(list)
    for rec in records:
        sig = rec["sig"]
        for bi in range(bands):
            buckets[(bi, tuple(sig[bi * rows:(bi + 1) * rows]))].append(rec["chapter"])
    cand = set()
    for members in buckets.values():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                ca, cb = members[i], members[j]
                if ca != cb:
                    cand.add((min(ca, cb), max(ca, cb)))
    return cand


def scan(book_dir, threshold=None, num_perm=None, chapter_dir=None, verbose=False,
         since=None, banding=True):
    """主检测逻辑。返回 findings 列表。"""
    threshold = THRESHOLDS["jaccard"] if threshold is None else threshold
    num_perm = THRESHOLDS["num_perm"] if num_perm is None else num_perm
    min_flagged = THRESHOLDS["min_flagged_segments"]
    mass_ratio_high = THRESHOLDS["mass_ratio_high"]

    chapters = collect_chapters(book_dir, chapter_dir)
    records, _ = build_index(chapters, num_perm)

    # 按章分组
    by_chapter = defaultdict(list)
    for rec in records:
        by_chapter[rec["chapter"]].append(rec)

    findings = []
    chapter_numbers = sorted(by_chapter)

    # 候选剪枝（2026-10-11）：banding 只负责生成候选章对，判定逻辑零改动。
    # 增量模式（--since）天然只有少量新章对全历史，无需剪枝。
    bands = rows = None
    candidates = None
    if banding and since is None and len(chapter_numbers) > 40:
        bands, rows = optimal_param(threshold, num_perm)
        candidates = lsh_candidate_pairs(records, bands, rows)

    # 逐章两两比对。**双向**比对：单向前向匹配会让较早的章节把所有段
    # 都「匹配上」，把占比算成 100%，从而把「后面章节复述了前文两段」
    # 误报成「整章复述」。取两侧占比的较小值，才是对复述严重程度的
    # 如实刻画——它回答的是「有多少内容是被抄的」。
    for i, chap_a in enumerate(chapter_numbers):
        for chap_b in chapter_numbers[i + 1:]:
            if since is not None and chap_b <= since:
                continue  # 增量模式：只查晚于 since 的章（对全历史）
            if candidates is not None and (chap_a, chap_b) not in candidates:
                continue  # banding 剪枝：非候选对直接跳过
            segs_a = by_chapter[chap_a]
            segs_b = by_chapter[chap_b]

            def match_best(source, target):
                """source 中每段在 target 里能匹配上的最佳项。"""
                hits = []
                for rec in source:
                    best, best_sim = None, threshold
                    for other in target:
                        sim = jaccard_est(rec["sig"], other["sig"])
                        if sim >= best_sim:
                            best_sim, best = sim, other
                    if best is not None:
                        hits.append((rec, best, best_sim))
                return hits

            # 双向匹配，各自独立计算
            fwd = match_best(segs_a, segs_b)   # a 的内容出现在 b（b 抄 a）
            rev = match_best(segs_b, segs_a)   # b 的内容出现在 a（a 抄 b）

            # 逐段去重：同一段可能匹配到对方多段，取相似度最高的
            def dedup(hits):
                seen = {}
                for rec, other, sim in hits:
                    key = rec["seg_index"]
                    if key not in seen or sim > seen[key][2]:
                        seen[key] = (rec, other, sim)
                return sorted(seen.values(), key=lambda x: x[0]["seg_index"])

            fwd, rev = dedup(fwd), dedup(rev)
            if not fwd and not rev:
                continue

            # 方向由**故事时间**决定：复述永远是较晚的章节重复较早章节的内容
            # （chap_a < chap_b 已由循环顺序保证）。段数与占比都只按
            # 较早章（chap_a）计量——它回答「有多少原文被后来抄走」，
            # 这是复述严重程度的唯一定义。两侧段数受段落长度影响，
            # 不可比，不能用来选基准。
            src_chap, dst_chap = chap_a, chap_b
            repeats = fwd
            total_a = sum(r["chars"] for r in segs_a) or 1
            mass = sum(r[0]["chars"] for r in fwd) / total_a

            if len(fwd) < min_flagged and len(rev) >= min_flagged:
                # fwd 侧段数不足但反向够——说明较晚章整段搬了较早章的内容，
                # 按反向计量（分母取较晚章）
                repeats = rev
                total_b = sum(r["chars"] for r in segs_b) or 1
                mass = sum(r[0]["chars"] for r in rev) / total_b

            # 单段豁免的高相似度出口：段数不足 2，但那一 hit 几乎逐字
            # （>= single_segment_jaccard），不算「有意呼应」，照样报。
            # （2026-10-06 修复：整段 0.89 相似度的逐字复制曾因段数
            # 门槛漏报——门槛防的是轻度呼应，不是防逐字搬运。）
            strong_single = any(sim >= THRESHOLDS["single_segment_jaccard"]
                                for _, _, sim in fwd + rev)
            if len(repeats) < min_flagged and not strong_single:
                continue

            kind = "整章复述" if mass >= mass_ratio_high else "局部复述"
            findings.append({
                "type": "cross-chapter-repeat",
                "chapter_a": src_chap,
                "chapter_b": dst_chap,
                "flagged_segments": len(repeats),
                "mass_ratio": round(mass, 3),
                "kind": kind,
                "examples": [
                    {
                        "chars": ra["chars"],
                        "sim": round(sim, 3),
                        "seg_a": ra["seg_index"],
                        "seg_b": rb["seg_index"],
                        "text": ra["text"][:60] + ("..." if len(ra["text"]) > 60 else ""),
                    }
                    for ra, rb, sim in repeats[:3]
                ],
            })

    return {
        "chapters": len(chapters),
        "segments": len(records),
        "findings": findings,
        "threshold": threshold,
        "num_perm": num_perm,
        "mode": "incremental" if since is not None else ("banding" if candidates is not None else "full"),
        "since": since,
        "bands": bands,
        "rows": rows,
        "candidate_pairs": len(candidates) if candidates is not None else None,
    }


def render_text(result, book_dir):
    findings = result["findings"]
    lines = [
        f"跨章自我复述检测  书：{os.path.basename(os.path.abspath(book_dir))}",
        f"  章节数 {result['chapters']}  段数 {result['segments']}"
        f"  阈值 Jaccard>={result['threshold']}"
        f"  模式 {result['mode']}"
        + (f"（since={result['since']}）" if result["mode"] == "incremental" else "")
        + (f"（banding b={result['bands']} r={result['rows']}，候选对 {result['candidate_pairs']}）" if result["mode"] == "banding" else ""),
        "",
    ]
    if not findings:
        lines.append("  [OK] 未检出跨章复述。")
        return "\n".join(lines)

    lines.append(f"  [X] 检出 {len(findings)} 组跨章复述（blocking）：")
    lines.append("")
    for idx, f in enumerate(findings, 1):
        lines.append(
            f"  {idx}. 第{f['chapter_b']}章 复述了 第{f['chapter_a']}章"
            f"  [{f['kind']}] 复述段 {f['flagged_segments']} 个"
            f"  占该章 {f['mass_ratio']:.0%}"
        )
        for ex in f["examples"]:
            lines.append(f"      · 相似度 {ex['sim']:.2f}  「{ex['text']}」")
        lines.append("")
    lines.append("  改法：这是AI 在复述自己前文的典型特征。删掉复述段，用新情节推进；")
    lines.append("       不要同义词轮换——那只会把一字不差变成意思重复。")
    return "\n".join(lines)


def render_json(result):
    return json.dumps(result, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="跨章自我复述检测（段级 MinHash）")
    parser.add_argument("--book", help="书目录（含 正文/ 子目录）")
    parser.add_argument("--dir", help="直接指定正文章节目录（覆盖 --book 推导）")
    parser.add_argument("--threshold", type=float, help=f"覆盖 Jaccard 阈值（默认 {THRESHOLDS['jaccard']}）")
    parser.add_argument("--num-perm", type=int, help=f"覆盖 MinHash 置换次数（默认 {THRESHOLDS['num_perm']}）")
    parser.add_argument("--since", type=int, default=None, metavar="N",
                        help="增量模式：只查章节号 > N 的章对全历史（每章提交后日常用）")
    parser.add_argument("--no-banding", action="store_true",
                        help="禁用 banding 候选剪枝，强制全量逐对比对（默认大书自动启用 banding）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    args = parser.parse_args()

    if not args.book and not args.dir:
        parser.print_help()
        print("\n[x] 必须指定 --book 或 --dir", file=sys.stderr)
        return 2

    try:
        result = scan(
            args.book or os.path.dirname(os.path.abspath(args.dir)),
            threshold=args.threshold,
            num_perm=args.num_perm,
            chapter_dir=args.dir,
            verbose=args.verbose,
            since=args.since,
            banding=not args.no_banding,
        )
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"[x] 文件系统错误：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(render_json(result))
    else:
        print(render_text(result, args.book or os.path.dirname(os.path.abspath(args.dir))))

    return 1 if result["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())