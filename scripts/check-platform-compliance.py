#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台合规检测 —— 番茄发布前最后一道闸（meta 混入 + 站外导流 + 审核敏感形态）

与现有各层的分工：
  check-ai-patterns.js / check-chinese-tone.py —— 管「像不像 AI 写的」
  check-crossrepeat.py / check-factconflicts.py —— 管「前后是否一致」
  spec_check.py —— 管「规格达标不达标」
  本脚本 —— 管「发出去会不会被平台拒/限流/被判导流」

设计来源：读 guohei/fanqie-plus 的 gate_check.py 与 304769384-png/
fanqie-novel-skill 的 commercial-review.md 提炼**思路**后全部重写
（两个仓库均无 LICENSE，不复制其代码/正则，仅借鉴「meta 混入」与
「平台违规」两个检查方向；本文件规则集、分级、组合判定均为原创）。

与 tone 检查的关键区别 —— 对话层不豁免：
  tone 检查豁免引号内台词，因为那是「像不像人」的文体判断；
  平台机审不区分引号内外——台词里出现「加我微信 138xxxx」照样触发
  人工复审。所以本脚本对全文扫描，包括对话层。

分级约定：
  blocking —— 发布即风险（meta 泄漏、链接/导流组合、站外引流话术），
              必须改到 0 才允许导出交付。
  warn     —— 高频误报区（都市文里「微信」「打赏」是正当情节词、
              虚构手机号是正当道具），提示人工确认，不阻断。

组合判定原则（误报控制的核心）：
  「微信」「QQ」「扫码」这类词单独出现不报（warn 也只对最高危的少数词），
  必须 + 联系方式形态（连续数字/字母串）或站外话术组合才升级 blocking——
  因为人写都市文合法使用这些词，只有 agent 流水线批量生成的正文才会
  无意识把它们和联系方式写在一起。

用法：
  python check-platform-compliance.py --book <书目录>
  python check-platform-compliance.py --file 正文/第001章.md
  python check-platform-compliance.py --book <书目录> --json
退出码：0 = 干净或仅 warn；1 = 有 blocking；2 = 用法/环境错误。
"""

import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------- 规则集

# --- blocking：meta 混入（AI 代写工作痕迹泄漏进正文）---
# 语义：这些词只该出现在 细纲/追踪/注释 里，出现在正文叙述行 = 工作痕迹泄漏。
# 键值形态（行首「爽点：」等）单独列，避免误伤叙述里偶然的「爽点」一词。
META_BLOCK_PATTERNS = [
    (r"\bTODO\b|\bFIXME\b|\bWIP\b", "meta_todo", "TODO/FIXME 工作标记"),
    (r"本章字数|字数(范围|目标)\s*[：:]|字数\s*[：:]\s*\d+", "meta_wordcount", "字数备注"),
    (r"门禁|Beat Sheet|Quality Gate", "meta_gate", "门禁/工作流标记"),
    (r"情感锚点|结尾钩子|章中钩子|章末钩子", "meta_hooknote", "钩子/锚点工作词"),
    (r"作者的话|作者有话说", "meta_authornote", "「作者的话」应填番茄后台单独字段，不得混入正文"),
    (r"\[说明[^\]]*\]|\[注[^\]]*\]|【说明[^】]*】|【注[^】]*】|（注[：:）]|\(注[:)]", "meta_bracket", "括号注释"),
    # 注：曾尝试用 F\d{3} 检测伏笔编号泄漏，已删除——中文紧贴时 \b 不成立
    # （「见F012」漏检），独立书写时又撞车名（「福特 F150」误报），两头不讨好。
    # 伏笔编号泄漏由 meta_bracket / meta_todo 等工作痕迹规则兜大部分。
    (r"\bV\d+\.\d+\b", "meta_version", "版本号"),
]

# 键值形态：行首就是「键：值」——spec_check 约定这些只能写在 HTML 注释里。
META_KEYVAL_RE = re.compile(
    r"^\s*(?:爽点|回报|温度|钩子型号|微钩子|三重回报|情绪温度|字数)\s*[：:]")

# --- blocking：链接与联系方式（审核红线，任何位置）---
LINK_PATTERNS = [
    (r"https?://\S+", "url", "URL 链接"),
    (r"\bwww\.[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "url", "www 链接"),
    # (?<!@)：排除邮箱 @ 后面的域名部分，避免 email/domain 双报同一处
    (r"(?<!@)\b[A-Za-z0-9_-]{2,}\.(?:com|cn|net|org|top|xyz|vip|cc|io|me|tv)\b", "domain", "裸域名"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "email", "邮箱地址"),
]

# --- blocking：导流组合（联系方式形态 + 导流词邻近共现）---
# 单个词不报；词 + 可复现的联系方式形态（≥6 位数字 / ≥5 位字母数字串）才报。
# 注意：HANDLE 必须整体包在非捕获组里——它内部含 |，不包组的话外层
# 正则拼接时 \| 会把它拆成独立分支，退化成「任何 6 位数字都报」。
CONTACT_WORD = r"(?:微信|微[\s.。]?信|QQ|Q群|QQ群|群号|企鹅号|VX|vx|V信|v信)"
CONTACT_HANDLE = r"(?:[A-Za-z0-9_]{5,}|\d{6,})"
DIVERT_COMBO_PATTERNS = [
    (CONTACT_WORD + r"[^\n]{0,8}(?:号|[:：])?\s*" + CONTACT_HANDLE,
     "divert_contact", "联系方式导流（平台词+号码/账号）"),
    (r"(?:加|进|入)[^\n]{0,4}(?:群|圈)[^\n]{0,10}" + CONTACT_HANDLE,
     "divert_group", "拉群导流"),
    # 注意：结尾词列表里不要加「看」这类单字动词——「完整版明天就能看」
    # 是合法叙述，2026-10-07 校准时发现会误报。真实导流话术中的
    #「…加微信看」已被 divert_contact（平台词+联系方式形态）覆盖。
    (r"(?:完整版|未删减|无删减|番外全|后续全部)[^\n]{0,12}(?:群|网盘|云盘|链接|私信|主页|公众号|外站)",
     "off_platform", "站外引流话术（付费章节分流）"),
    (r"(?:网盘|云盘)[^\n]{0,10}(?:链接|提取码|资源)",
     "off_platform", "网盘引流"),
    (r"二维码|扫码进群|扫码领", "divert_qr", "二维码导流"),
]

# --- blocking：交易/代写引导（正文中几乎不可能合法出现）---
TRADE_BLOCK_PATTERNS = [
    (r"商务合作|广告合作|广告投放|代写|代充|代更", "trade", "交易/代写引导"),
]

# --- warn：高误报区，人工确认（都市/职场文合法词）---
WARN_PATTERNS = [
    (r"1[3-9]\d{9}", "phone_number", "11 位手机号——剧情道具建议写成 138****8888 形式，降低机审敏感"),
    (r"微信|QQ|私信|私聊|公众号|二维码|扫码|加群|进群|打赏|众筹", "platform_word",
     "平台敏感词孤立出现——若为正当情节可保留，确认未与联系方式连用"),
    (r"淘宝|拼多多|京东|支付宝|优惠券|返利", "brand_word",
     "品牌/交易词孤立出现——若为正当情节可保留"),
]

# --- warn：格式残留（导出前应清理，不影响审核）---
FORMAT_WARN_PATTERNS = [
    (r"\*\*[^*\n]+\*\*|__[^_\n]+__", "markdown_bold", "Markdown 加粗残留（番茄不支持，会原样显示）"),
    (r"\[[^\]\n]{1,30}\]\([^)\n]+\)", "markdown_link", "Markdown 链接残留"),
]

HTML_COMMENT_RE = re.compile(r"<!--.*?-->", flags=re.S)

# 引号对集合：与 check-distribution.py / collect-baseline.py 保持一致（含【】）；
# 改一侧必须同步改另一侧。用于对话层独立扫描——把「对话层不豁免」做成
# 显式执行：引号内命中的导流组合以 dialogue_divert 规则名上报（可见性）。
QUOTE_PAIRS = [("「", "」"), ("『", "』"), ("【", "】"), ("“", "”"), ("‘", "’"), ('"', '"'), ("'", "'")]

# HTML 注释里的合法工作标记（spec_check 约定）：注释含这些词视为工作标记，
# 只提示导出前剥离；其他内容的注释同样提示剥离（级别相同，不区分）。

CHAPTER_HEAD_RE = re.compile(r"^第[零一二三四五六七八九十百千万\d]+章(?:\s|_|$)")
STRUCTURAL_RE = re.compile(r"^(#{1,6}\s|>\s?|[-*+]\s|\d+[.)]\s|\|)")
DIVIDER_RE = re.compile(r"^[-*_]{3,}$")


# ---------------------------------------------------------------- 工具

def read(path):
    if not os.path.exists(path):
        sys.stderr.write("[novel-forge] 文件不存在：%s\n" % path)
        sys.exit(2)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def is_prose_line(trimmed):
    """正文叙述行：排除标题/列表/引用/表格/分隔线/章节头。"""
    if not trimmed:
        return False
    if DIVIDER_RE.match(trimmed) or STRUCTURAL_RE.match(trimmed) or CHAPTER_HEAD_RE.match(trimmed):
        return False
    return True


def strip_comments(text):
    return HTML_COMMENT_RE.sub("", text)


def scan_text(text):
    """扫单文件文本，返回 (findings, dialogue_lines)。

    dialogue_lines = 含引号内容的行数——「对话层不豁免」的执行证明：
    引号内命中的导流组合以 dialogue_divert 规则名上报（与整行 divert_*
    规则同线互斥，不做双报——2026-10-07 教训：同线双报是纯噪音）。
    """
    findings = []
    dialogue_lines = 0
    no_comment = strip_comments(text)
    for ln, raw in enumerate(no_comment.splitlines(), 1):
        line = raw.rstrip()
        trimmed = line.strip()
        # 对话层独立扫描的输入：本行引号内片段（引号对集合见 QUOTE_PAIRS 注释）
        quote_spans = []
        for op, cl in QUOTE_PAIRS:
            for m in re.finditer(re.escape(op) + "[^" + re.escape(cl) + r"\n]*" + re.escape(cl), line):
                quote_spans.append(m.span())
        if quote_spans:
            dialogue_lines += 1
        # 键值形态的 meta 行（任何行都查，包括表格行——键值行不该出现在任何地方）
        if META_KEYVAL_RE.match(trimmed):
            findings.append({
                "line": ln, "rule": "meta_keyval", "severity": "blocking",
                "detail": "工作键值行（%s）混入正文" % trimmed[:12],
                "snippet": trimmed[:40],
            })
            continue
        if not is_prose_line(trimmed):
            continue

        def _hit(pats, severity, tag):
            hits = []
            for pat, rule, detail in pats:
                m = re.search(pat, line, flags=re.I if tag == "meta" else 0)
                if m:
                    hits.append({
                        "line": ln, "rule": rule, "severity": severity,
                        "detail": detail,
                        "snippet": line[max(0, m.start() - 8):m.end() + 8].strip()[:48],
                    })
            return hits

        # 导流组合：逐处匹配并判断是否整体落在引号内——引号内命中改判
        # dialogue_divert（对话层不豁免的可见性证明），规则语义不变
        divert_hits = []
        for pat, rule, detail in DIVERT_COMBO_PATTERNS:
            for m in re.finditer(pat, line):
                in_quote = any(qs <= m.start() and m.end() <= qe for qs, qe in quote_spans)
                divert_hits.append({
                    "line": ln,
                    "rule": "dialogue_divert" if in_quote else rule,
                    "severity": "blocking",
                    "detail": ("对话层导流（引号内命中，平台机审不区分引号内外）：%s" % detail)
                              if in_quote else detail,
                    "snippet": line[max(0, m.start() - 8):m.end() + 8].strip()[:48],
                })
        blocking_hits = (_hit(META_BLOCK_PATTERNS, "blocking", "meta")
                         + _hit(LINK_PATTERNS, "blocking", "link")
                         + divert_hits
                         + _hit(TRADE_BLOCK_PATTERNS, "blocking", "trade"))
        findings.extend(blocking_hits)
        # 同一行已有 blocking 命中时跳过 warn ——「微信 abc_12345」会同时命中
        # divert_contact（blocking）和 platform_word（warn），双报是纯噪音；
        # blocking 的处置已覆盖 warn 的处置。
        if not blocking_hits:
            findings.extend(_hit(WARN_PATTERNS, "warn", "warn"))
            findings.extend(_hit(FORMAT_WARN_PATTERNS, "warn", "format"))
    # 注释残留整体提示一次（钩子型号等是合法工作标记，只需导出前剥离）
    comments = HTML_COMMENT_RE.findall(text)
    if comments:
        findings.append({
            "line": 0, "rule": "html_comment", "severity": "warn",
            "detail": "存在 %d 处 HTML 注释（工作标记）——导出发布前剥离" % len(comments),
            "snippet": "",
        })
    return findings, dialogue_lines


def collect_chapters(book_dir):
    """递归收集正文章节（v2.1：支持 正文/draft|final 两层）。

    同章号 draft 与 final 都有时，两个都扫——平台合规要保证最终可能被导出的
    final 层干净，同时 draft 层的问题也值得提示。
    """
    chapter_dir = os.path.join(book_dir, "正文")
    if not os.path.isdir(chapter_dir):
        sys.stderr.write("[x] 找不到正文章节目录：%s\n" % chapter_dir)
        sys.exit(2)
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
        sys.stderr.write("[x] %s 下没有 .md 正文文件\n" % chapter_dir)
        sys.exit(2)
    return items


def scan_book(book_dir):
    chapters = collect_chapters(book_dir)
    findings = []
    dialogue_lines = 0
    for num, name, path in chapters:
        text = read(path)
        file_findings, dlg = scan_text(text)
        dialogue_lines += dlg
        for f in file_findings:
            f["chapter"] = num
            f["file"] = name
            findings.append(f)
    return {
        "book": os.path.basename(os.path.abspath(book_dir)),
        "chapters": len(chapters),
        "findings": findings,
        "blocking": sum(1 for f in findings if f["severity"] == "blocking"),
        "warns": sum(1 for f in findings if f["severity"] == "warn"),
        "dialogue_lines": dialogue_lines,
    }


def scan_file(path):
    text = read(path)
    findings, dlg = scan_text(text)
    return {
        "book": os.path.basename(path),
        "chapters": 1,
        "findings": findings,
        "blocking": sum(1 for f in findings if f["severity"] == "blocking"),
        "warns": sum(1 for f in findings if f["severity"] == "warn"),
        "dialogue_lines": dlg,
    }


def render(result):
    lines = ["平台合规检测  书：%s ｜ %d 章" % (result["book"], result["chapters"]),
             "  blocking %d ｜ warn %d" % (result["blocking"], result["warns"]),
             "  对话层：含引号内容 %d 行，已随全文扫描（引号内导流命中以 dialogue_divert 上报）"
             % result.get("dialogue_lines", 0),
             ""]
    blocks = [f for f in result["findings"] if f["severity"] == "blocking"]
    warns = [f for f in result["findings"] if f["severity"] == "warn"]
    if not blocks and not warns:
        lines.append("  [OK] 未检出 meta 混入与平台违规形态。")
        return "\n".join(lines)
    if blocks:
        lines.append("  [阻断] %d 处发布风险：" % len(blocks))
        for f in blocks[:30]:
            where = "第%s章 " % f.get("chapter", "?") if result["chapters"] > 1 else ""
            lines.append("    · %sL%s %s：%s「%s」"
                         % (where, f["line"], f["rule"], f["detail"], f["snippet"]))
        if len(blocks) > 30:
            lines.append("    …（其余 %d 处略）" % (len(blocks) - 30))
    if warns:
        lines.append("  [确认] %d 处需人工确认：" % len(warns))
        for f in warns[:15]:
            where = "第%s章 " % f.get("chapter", "?") if result["chapters"] > 1 else ""
            lines.append("    · %sL%s %s：%s「%s」"
                         % (where, f["line"], f["rule"], f["detail"], f["snippet"]))
        if len(warns) > 15:
            lines.append("    …（其余 %d 处略）" % (len(warns) - 15))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="平台合规检测（meta 混入 + 导流 + 审核敏感形态）")
    ap.add_argument("--book", help="书目录（含 正文/）")
    ap.add_argument("--file", help="单章文件检测")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not any([args.book, args.file]):
        ap.print_help()
        return 2
    try:
        result = scan_file(args.file) if args.file else scan_book(args.book)
    except OSError as exc:
        sys.stderr.write("[x] 文件系统错误：%s\n" % exc)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render(result))
    return 1 if result["blocking"] else 0


if __name__ == "__main__":
    sys.exit(main())
