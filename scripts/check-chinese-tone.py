#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中文 AI 腔检测（lieflat-less-ai-tone 11 项特征的网文适配层）

与 check-ai-patterns.js 的分工：
  check-ai-patterns.js（vendored 自 oh-story）查的是**英文 AI 味的中文
  移植层**——「不是A而是B」句式、章尾预告、情绪直述词等 22 类。
  本脚本查的是 lieflat 用 283 万字对照语料**实证成立**的中文特征，
  其中 4 项是现有检测器没有的中文专项：
    段首零回指评论（R=4.4）、冒号滥用（R=9.4）、拟人化喻体（R=7.3）、
    相邻句结构同款（R=2.0）

数据来源：lieflat-less-ai-tone（MIT，larashero3-dotcom）RESEARCH.md。
629 篇对照文章（AI 300 篇 / 人类 329 篇，共 2,826,972 汉字），
26 项候选特征逐项检验：11 项成立，15 项被证伪。

**被证伪的三条流行认知（不要据此改文字）**：
  - 比喻：人类用得多 2.4 倍（盲目删比喻会更像 AI）
  - 正文设问：人类用得多 17 倍（盲目删设问会更像 AI）
  - 句长均匀度：R=0.87 无差异（「AI 句长更均匀」是切句缺陷造成的假象）

**网文适配（与上游论述文语境的差异）**：
  1. 对话层豁免——台词是人物口语，「不是A而是B」式对白是正常写法
  2. 密度阈值放宽——网文的修辞密度天然高于论述文，advisory 阈值
     取上游实测 AI 侧均值的 2 倍（论述文取 1.5 倍）
  3. 文学性表达不报——比喻、排比、独立成段的短句是网文正装
  4. 只报叙述层——引号内文字一律跳过

设计约束（沿用 novel-forge 铁律）：零依赖、纯 CPU、只报告不修改。
退出码：0 = 干净；1 = 有 advisory 命中（不阻断交付，见 forge.py 集成）；
2 = 用法/环境错误。
"""

import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# R 值：生成侧频率 ÷ 人类侧频率（lieflat RESEARCH.md 实测）。
# adv_th = advisory 阈值（每千字），取上游 AI 侧均值 × 2（网文修辞余量）。
# 来源频率列保留，供复核校准。
# ---------------------------------------------------------------------------
MARKERS = {
    # --- 第 1 条 翻案腔（R=3.4，AI 0.73 vs 人类 0.22）---
    # 注意：check-ai-patterns.js 已硬禁「不是A，而是B」句式；这里只查
    # 上游清单里它没有的变体，避免双报。
    "翻案腔变体": {
        "pattern": (r"(?:与其说[^。]{1,20}?不如说"
                    r"|表面[^，。]{1,12}实际[^，。]{1,20}"
                    r"|看似[^，。]{1,12}实则[^，。]{1,20}"
                    r"|你以为[^。]{1,20}?其实"
                    r"|说到底，?[^，。]{2,20}(?!的话))"),
        "r": 3.4, "adv_th": 1.5,
    },
    # --- 第 2 条 顿号罗列过密（R=1.8，AI 3.21 vs 人类 1.78）---
    "顿号罗列": {
        "pattern": r"[一-鿿]{1,6}、[一-鿿]{1,6}、[一-鿿]{1,6}、[一-鿿]{1,6}",
        "r": 1.8, "adv_th": 6.4,   # 上游 AI 均值 3.21 × 2
    },
    # --- 第 5 条 冒号滥用（提示语 R=3.8 / 空转句 R=9.4）---
    # 网文正文冒号主要出现在「某某说：」引语前——那是对话标记，豁免。
    # 只查提示语引出与空转列表两类。
    "冒号提示语": {
        "pattern": (r"(?:一句话(?:总结|说|概括)|简单说|说白了|说到底|总结|结论|核心(?:是|在于)?"
                    r"|关键(?:是|在于)?|重点(?:是)?|原因(?:如下|有|在于)?|问题(?:是|在于)?"
                    r"|答案(?:是)?|本质(?:是|上)?|换句话说|也就是说|建议(?:是)?)[：:]"),
        "r": 3.8, "adv_th": 0.6,
    },
    # --- 第 9 条 起手式（R=3.2）---
    "起手式": {
        "pattern": r"(?:^|[。！？\n])(说白了|说穿了|先说结论|先说重点)",
        "r": 3.2, "adv_th": 0.6,
    },
    # --- 第 10 条 翻译腔 5 种（R=2.6–5.3）---
    "长前置定语": {
        "pattern": r"(?:一个|一种|一套|这种|这个)[一-鿿][^，。、；：！？\n]{14,}的[一-鿿]{2,5}",
        "r": 4.9, "adv_th": 0.9,
    },
    "前置话题壳": {
        "pattern": r"(对于[一-鿿]{2,15}来说|对[一-鿿]{2,15}而言|就[一-鿿]{2,15}而言|在[一-鿿]{2,12}方面)",
        "r": 3.2, "adv_th": 1.2,
    },
    "轻动词结构": {
        "pattern": r"(进行|作出|给予|予以)了?[一-鿿]{0,6}(分析|调整|优化|支持|评估|检查|讨论)",
        "r": 5.3, "adv_th": 0.6,
    },
    "形式主语": {
        "pattern": r"(值得注意的是|有必要指出的是|可以说的是|需要指出的是|不难看出)",
        "r": 4.2, "adv_th": 0.4,
    },
    "这意味着": {
        "pattern": r"(这意味着|这表明|这说明)(?!的是)",
        "r": 4.4, "adv_th": 0.8,
    },
    # --- 第 7 条 拟人化喻体（R=7.3，带修饰时 12.6）---
    # 网文人物可以有「像老师傅」这类具体人喻体（豁免）；
    # 只查「理想化职业人格 + 褒义修饰」形态。修饰词在角色词前或后都算
    #（上游例句「像一位智慧的导师」是前修饰——初版只匹配后修饰，漏检）。
    "拟人化喻体": {
        "pattern": (r"像(?:一个|一位)[一-鿿]{0,4}?"
                    r"(?:智慧的|全能的|永不疲倦的|永不停歇的|知心的|贴心的)?"
                    r"[一-鿿]{0,2}?(?:导师|秘书|助手|顾问|管家|审查员|法官|守护者)"
                    r"|像(?:一个|一位)(?:永不|不知)疲倦的[一-鿿]{0,6}"),
        "r": 7.3, "adv_th": 0.3,
    },
}

# 段首零回指评论（R=4.4，占非首段 0.61% vs 0.14%）。
# 网文短段落极多，只查叙述层段落。
_COMMENT = re.compile(
    r"^(?:听起来|看起来|看上去|听上去|说白了|说到底|换句话说|意味着|值得注意|"
    r"不难看出|细看|再看|回过头看|问题在于|原因在于|结果是|有意思的是|"
    r"更重要的是|关键在于|真正的)")
_ANAPHOR = re.compile(
    r"^(?:这|那|其|此|上面|前面|刚才|以上|该|它|他|她|它们|他们|同样|类似|相比|反过来|但|不过|所以|因此|于是|而|另|除此|与此)")

# 相邻句结构同款（R=2.0，每百段 AI 9.41 vs 人类 4.81）
def _signature(sent):
    """句子结构指纹：逗号数、有无冒号、有无括号、长度档。"""
    return (sent.count("，"), "：" in sent, ("（" in sent or "(" in sent), len(sent) // 15)


# 引号内容（对话/引语）——台词是人物口语，全部豁免
OPEN_QUOTES = "「『\"“"
CLOSE_QUOTES = "」』\"”"
# ``` 声明块
_META_RE = re.compile(r"```.*?```", re.DOTALL)


def strip_dialogue(text):
    """把引号内文字替换为等长占位（保持位置对齐），只留叙述层。"""
    out = []
    in_quote = False
    for ch in text:
        if not in_quote and ch in OPEN_QUOTES:
            in_quote = True
            out.append("□")
        elif in_quote and ch in CLOSE_QUOTES:
            in_quote = False
            out.append("□")
        else:
            out.append("□" if in_quote else ch)
    return "".join(out)


def strip_meta(text):
    return _META_RE.sub("", text)


def kchars(text):
    return len(re.findall(r"[一-鿿]", text)) / 1000


def paragraphs(text):
    out = []
    for p in text.split("\n"):
        p = p.strip()
        if len(p) < 8 or p.startswith(("#", "|", "```", ">", "- ", "* ", "!", "[")):
            continue
        out.append(p)
    return out


def scan_text(text, thresholds=None):
    """对单章正文做中文 AI 腔密度检测。返回 findings 列表。"""
    text = strip_meta(text)
    narrative = strip_dialogue(text)
    k = kchars(narrative)
    if k < 0.2:
        return []      # 短于 200 汉字，密度统计无意义（真实章节 2000+ 字远超）

    findings = []
    for name, spec in MARKERS.items():
        rx = re.compile(spec["pattern"])
        hits = rx.findall(narrative)
        n = len(hits)
        per_k = n / k
        th = spec["adv_th"]
        if per_k >= th and n >= 2:        # 密度超标且绝对数≥2 才报
            findings.append({
                "rule": name,
                "count": n,
                "per_kchars": round(per_k, 2),
                "threshold": th,
                "r_value": spec["r"],
                "severity": "advisory",
                "examples": [h if isinstance(h, str) else h[0] for h in hits[:3]],
            })

    # --- 段首零回指评论（结构项，分母是段数）---
    ps = paragraphs(narrative)
    if len(ps) >= 8:
        nonfirst = len(ps) - 1
        zero = 0
        zero_examples = []
        for i, p in enumerate(ps):
            if i == 0:
                continue
            if _COMMENT.match(p) and not _ANAPHOR.match(p):
                zero += 1
                if len(zero_examples) < 3:
                    zero_examples.append(p[:24])
        ratio = zero / nonfirst
        if ratio > 0.05 and zero >= 2:    # 人类基线 0.14%，AI 0.61%，取 5% 界
            findings.append({
                "rule": "段首零回指评论",
                "count": zero,
                "per_kchars": round(ratio * 100, 1),
                "threshold": 5.0,
                "unit": "%非首段",
                "r_value": 4.4,
                "severity": "advisory",
                "examples": zero_examples,
            })

    # --- 相邻句结构同款（分母是段数）---
    # 阈值依据：《燃识》9 章实战校准（2026-10-06）。人类基线 4.81、
    # AI 均值 9.41（每百段）的区间内存在大量「镜头并列」——网文用
    # 同构句做电影式蒙太奇（「橘子瓣摆成整齐的一排，是烧。检测仪上
    # 没来由的抖动，也是烧。」），这是**有意的修辞**，不是填表。
    # 真假判据看内容：AI 填表是同一主语干同一类事（「他抬头看天。
    # 他低头看路。」），镜头并列是不同主语不同事，仅句法巧合同构。
    # 纯句法指纹区分不了这两者，因此把上报线提到 AI 均值之上：
    # 只有明显高于正常叙事水平的密度才值得人回头读。
    if len(ps) >= 5:
        iso_hits = 0
        iso_examples = []
        for p in ps:
            # 网文适配：句长阈值从上游的 10 字降到 4 字。
            # 网文短句是常态（「山风从谷底涌上来」8 字、「他抬头看天」
            # 5 字——10 字阈值全部滤掉，结构同款检测对网文失灵；
            # 4 字以下多是语气碎片，纳入只会引入噪声）。
            sents = [s.strip() for s in re.split(r"[。！？]", p) if len(s.strip()) > 3]
            for i in range(len(sents) - 1):
                sig_a = _signature(sents[i])
                # 上游要求逗号数≥1——那防的是「超长复合句堆叠」。
                # 网文同款填表多是无逗号短句（「他抬头看天。他低头看路。」），
                # 句长档相同 + 无逗号本身就是同款证据，去掉逗号门槛。
                if sig_a == _signature(sents[i + 1]) and len(sents[i]) >= 6:
                    iso_hits += 1
                    if len(iso_examples) < 2:
                        iso_examples.append(sents[i][:18] + " / " + sents[i + 1][:18])
        # 上报线：每百段 ≥15%（AI 侧均值 9.41 的 1.6 倍）且绝对数 ≥6。
        # 初版 12%/4 在真实网文上把正常蒙太奇段落全部误报（9 章里
        # 6 章命中，其中 5 章在 4.8–10.4% 的人类区间内）。
        if iso_hits >= 6 and iso_hits / len(ps) * 100 >= 15.0:
            findings.append({
                "rule": "相邻句结构同款",
                "count": iso_hits,
                "per_kchars": round(iso_hits / len(ps) * 100, 1),
                "threshold": 15.0,
                "unit": "%每百段",
                "r_value": 2.0,
                "severity": "advisory",
                "examples": iso_examples,
            })
    return findings


def scan_chapter_file(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = strip_meta(fh.read())
    findings = scan_text(text)
    k = kchars(strip_dialogue(text))
    return findings, k


def collect_chapters(book_dir, chapter_dir=None):
    """递归收集正文章节（v2.1：支持 正文/draft|final 两层）。"""
    if chapter_dir is None:
        chapter_dir = os.path.join(book_dir, "正文")
    if not os.path.isdir(chapter_dir):
        raise SystemExit(f"[x] 找不到正文章节目录：{chapter_dir}")
    items = []
    for dirpath, _dirs, files in os.walk(chapter_dir):
        for name in files:
            if not name.endswith(".md") or "细纲" in name:
                continue
            path = os.path.join(dirpath, name)
            digits = re.findall(r"\d+", name)
            num = int(digits[0]) if digits else 0
            items.append((num, os.path.relpath(path, chapter_dir), path))
    items.sort()
    if not items:
        raise SystemExit(f"[x] {chapter_dir} 下没有 .md 正文文件")
    return items


def render(result, book):
    lines = [f"中文 AI 腔检测  书：{os.path.basename(os.path.abspath(book))}",
             f"  章节数 {result['chapters']}  汉字 {result['kchars']:.1f} 千字",
             ""]
    if not result["findings"]:
        lines.append("  [OK] 未检出密度超标的中文 AI 腔特征。")
        lines.append("  （机检零命中不代表没有 AI 味——主观质量见 references/quality-rubric.md）")
        return "\n".join(lines)
    lines.append(f"  [!] {len(result['findings'])} 项特征密度超标（advisory，不阻断交付）：")
    lines.append("")
    for f in result["findings"]:
        unit = f.get("unit", "每千字")
        lines.append(f"  · {f['rule']}  {f['count']} 处（{f['per_kchars']}{unit}，"
                     f"阈值 {f['threshold']}{unit}，R={f['r_value']}）")
        for ex in f.get("examples", [])[:2]:
            lines.append(f"      · 「{ex}」")
    lines.append("")
    lines.append("  处置建议：advisory 只提示不阻断。对照 283 万字语料实测，")
    lines.append("  这些特征密度达到 AI 侧均值的 2 倍。逐条读上下文判断——")
    lines.append("  比喻、正文设问、句长节奏不要动（人类用得更多，删了更假）。")
    return "\n".join(lines)


def scan(book_dir, chapter_dir=None):
    chapters = collect_chapters(book_dir, chapter_dir)
    findings = []
    total_k = 0.0
    for num, name, path in chapters:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        text = strip_meta(text)
        total_k += kchars(strip_dialogue(text))
        for f in scan_text(text):
            f["chapter"] = num
            f["file"] = name
            findings.append(f)
    return {
        "book": os.path.basename(os.path.abspath(book_dir)),
        "chapters": len(chapters),
        "kchars": round(total_k, 1),
        "findings": findings,
    }


def main():
    parser = argparse.ArgumentParser(description="中文 AI 腔密度检测（lieflat 11 项特征网文适配）")
    parser.add_argument("--book", help="书目录（含 正文/）")
    parser.add_argument("--dir", help="直接指定章节目录")
    parser.add_argument("--file", help="单章文件检测")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not any([args.book, args.dir, args.file]):
        parser.print_help()
        return 2

    try:
        if args.file:
            findings, k = scan_chapter_file(args.file)
            result = {"book": os.path.basename(args.file), "chapters": 1,
                      "kchars": round(k, 1), "findings": findings}
        else:
            result = scan(args.book or os.path.dirname(os.path.abspath(args.dir or "")),
                          chapter_dir=args.dir)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"[x] 文件系统错误：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render(result, args.book or args.dir or args.file))
    return 1 if result["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())