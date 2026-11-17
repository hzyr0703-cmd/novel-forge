#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
投稿前必查（编辑层体检）：把番茄作家学院两节公开课里「可机检」的三条做成脚本。

来源：
- 《投稿前必查：影响作品质量的 8 个文字硬伤》（2026-11-09）
- 《投稿前必查：告别"文风呆板"和"错误排版"》（2026-11-11）

本脚本只做三件课程原文明确可数的事（其余靠人读）：

  E1 章首模板化：统计每章开头类型（对话 / 时间·环境 / 其他），报滚动 10 章里
     对话开场偏少（课程原话：每章都从环境描写开始，读者的耐心会被磨灭）。
  E2 极短段：三五个字独立成段破坏句子完整性（课程原话），面板/系统播报除外。
  E3 专名变体：同一角色名在全书出现两种写法（课程原话：主角打着打着改名），
     做法是把台账角色卡里的名字当权威，扫「同长度、差一个字」的候选。

设计纪律（沿用 pitfalls P18/P30）：
- 全部 advisory，永不阻断；退出码 0 = 正常完成（有建议也是 0），2 = 文件/参数错误。
- 不做「阈值达标」提示语，只报现状数字 + 一句怎么判断，避免变成填格子指标。
- 课程明确的豁免必须写进消息里：论坛留言/短信弹幕/围观喊话/念信读纸不算短对话堆砌；
  系统面板不算清单罗列，也不算极短段。

用法：
  python check-editorial.py --book <书目录>                         # 全书
  python check-editorial.py --book <书目录> --chapter 105           # 单章
  python check-editorial.py --book <书目录> --range 100-105         # 区段
  python check-editorial.py --book <书目录> --names 林灼 魏守仓      # 手工补专名
  python check-editorial.py --book <书目录> --json                  # 机器可读
"""
import argparse
import json
import os
import re
import sys

CHAPTER_RE = re.compile(r"第(\d+)章")
TITLE_LINE_RE = re.compile(r"^#?\s*第\s*\d+\s*章")
PANEL_RE = re.compile(r"^【[^】]+】$")
HAN_RE = re.compile(r"[\u4e00-\u9fff]+")

# 时间/环境起手词（课程点名的「清晨、阳光、微风」一类）
TIME_ENV_LEADS = (
    "清晨", "清早", "早晨", "早上", "天亮", "天没", "天刚", "上午", "晌午", "中午",
    "午后", "下午", "傍晚", "入夜", "夜里", "半夜", "子时", "丑时", "寅时", "卯时",
    "夜里", "当晚", "当夜", "第二天", "次日", "三日后", "开板第",
    "风", "雨", "雪", "雾", "阳光", "日头", "月亮", "街", "巷", "墙", "屋里",
    "窗外", "土路", "院子里", "灯",
)
# 时间/环境起手还允许「第N日」这类写法
TIME_ENV_NUM_RE = re.compile(r"^(?:开板)?第[一二三四五六七八九十百零百\d]+日")
QUOTE_LEADS = ("「", "“", "『", '"', "”")

SHORT_PARA_MAX = 6
SHORT_PARA_MAX_PER_CHAPTER = 5
OPENING_WINDOW = 10
OPENING_DIALOGUE_MIN = 2
OPENING_TIME_ENV_MAX = 5


def chapter_no(path):
    m = CHAPTER_RE.search(os.path.basename(path))
    return int(m.group(1)) if m else None


def read_paras(path):
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    out = []
    for idx, line in enumerate(lines, 1):
        text = line.strip()
        if not text or TITLE_LINE_RE.match(text):
            continue
        out.append((idx, text))
    return out


def is_short_narration(text):
    """极短段只数叙述段：系统面板不算，纯台词短段也不算（一句一段的对话是正常节奏）。"""
    if PANEL_RE.match(text):
        return False
    if text.startswith(QUOTE_LEADS) and text[-1:] in ("」", "”", "』", '"'):
        inner = text[1:-1]
        if len(re.sub(r"\s", "", inner)) <= SHORT_PARA_MAX:
            return False
    return len(re.sub(r"\s", "", text)) <= SHORT_PARA_MAX


def classify_opening(para):
    if para.startswith(QUOTE_LEADS):
        return "dialogue"
    if TIME_ENV_NUM_RE.match(para):
        return "time_env"
    for word in TIME_ENV_LEADS:
        if para.startswith(word):
            return "time_env"
    return "other"


def collect_names(book, extra):
    """默认从台账角色卡文件名收集（追踪/角色状态/*.md），角色卡缺失时退回 --names。"""
    names = set(n for n in (extra or []) if n)
    cards = os.path.join(book, "追踪", "角色状态")
    if os.path.isdir(cards):
        for fn in os.listdir(cards):
            if not fn.endswith(".md"):
                continue
            stem = fn[:-3].strip()
            if 2 <= len(stem) <= 4 and HAN_RE.fullmatch(stem):
                names.add(stem)
    return sorted(names)


def scan_name_variants(paras, names):
    """只查 3 字及以上人名：同长度、只差一个字、且至少两个字相同。

    2 字名（林灼/严律/老周）不参与：差一个字就已经是同形不同人，候选量爆炸（方向/方才/老师
    这类普通词全会被抓），误报远大于收益，这种只能靠人读。所以本检查的定位是「长名字写串了」
    （例如 陆九章 → 陆久章），不是通用错字表。
    """
    by_len = {}
    for name in names:
        if len(name) >= 3:
            by_len.setdefault(len(name), []).append(name)
    hits = []
    if not by_len:
        return hits
    for line_no, text in paras:
        for length, canon in by_len.items():
            for i in range(0, max(0, len(text) - length + 1)):
                token = text[i:i + length]
                if not HAN_RE.fullmatch(token) or token in canon:
                    continue
                for real in canon:
                    # 首字（姓）必须一致：否则「林师傅」会被当成「唐师傅」的变体，
                    # 而它本来就是另一个人。变体只可能发生在名或第二个字上。
                    if token[0] != real[0]:
                        continue
                    shared = sum(1 for a, b in zip(token, real) if a == b)
                    if shared == length - 1:
                        hits.append({
                            "line": line_no,
                            "token": token,
                            "canon": real,
                            "context": text[max(0, i - 6):i + length + 6],
                        })
                        break
    return hits


def check_opening(book, chapters):
    per_chapter = []
    for ch, paras in chapters:
        if not paras:
            continue
        per_chapter.append((ch, classify_opening(paras[0][1])))
    notes = []
    dialogue = sum(1 for _, kind in per_chapter if kind == "dialogue")
    notes.append(f"对话开场 {dialogue} / {len(per_chapter)} 章")
    worst = None
    for i in range(max(0, len(per_chapter) - OPENING_WINDOW + 1)):
        window = per_chapter[i:i + OPENING_WINDOW]
        if len(window) < OPENING_WINDOW:
            continue
        dlg = sum(1 for _, kind in window if kind == "dialogue")
        env = sum(1 for _, kind in window if kind == "time_env")
        if dlg < OPENING_DIALOGUE_MIN or env > OPENING_TIME_ENV_MAX:
            if worst is None or (dlg, -env) < (worst[1], -worst[2]):
                worst = (window[0][0], dlg, env)
    if worst is not None:
        start, dlg, env = worst
        notes.append(
            f"[建议] 第 {start}–{start + OPENING_WINDOW - 1} 章：对话开场 {dlg} 章、时间/环境起手 {env} 章；"
            "课程判据是「每章都从环境描写开始会把耐心磨灭」——把其中几章改成直接切入动作/声音/冲突点，"
            "注意不是每章都必须开口说话，动作起手同样算入戏（详见公告/论坛/喊话等场景除外）"
        )
    return per_chapter, notes


def check_short_paragraphs(chapters):
    notes = []
    worst = []
    for ch, paras in chapters:
        short = [(no, t) for no, t in paras if is_short_narration(t)]
        if len(short) > SHORT_PARA_MAX_PER_CHAPTER:
            worst.append((ch, len(short), short[0][1]))
    total = sum(len([1 for _, t in paras if is_short_narration(t)]) for _, paras in chapters)
    notes.append(f"极短段（≤{SHORT_PARA_MAX} 字）全书 {total} 段")
    for ch, count, sample in worst:
        notes.append(
            f"[建议] 第{ch}章 极短段 {count} 段（课程判据：三五个字独立成段会破坏句子完整性，"
            f"系统面板/属性列表除外）；样例「{sample}」——先看是不是有意做的节奏，"
            "不是就把短段并回上句，别为降低计数去注水"
        )
    return notes


def main():
    ap = argparse.ArgumentParser(description="投稿前必查（章首轮换/极短段/专名变体），全部 advisory")
    ap.add_argument("--book", required=True, help="书目录（含 正文/final）")
    ap.add_argument("--chapter", type=int, help="只查这一章")
    ap.add_argument("--range", dest="range_", help="区段，如 100-105")
    ap.add_argument("--layer", default="final", choices=["final", "draft"], help="读哪层正文")
    ap.add_argument("--names", nargs="*", default=None, help="手工补充专名（默认读 追踪/角色状态 角色卡）")
    ap.add_argument("--json", action="store_true", help="输出机读 JSON")
    args = ap.parse_args()

    body_dir = os.path.join(args.book, "正文", args.layer)
    if not os.path.isdir(body_dir):
        print(f"[错误] 找不到正文目录：{body_dir}", file=sys.stderr)
        return 2

    low, high = None, None
    if args.chapter is not None:
        low = high = args.chapter
    if args.range_:
        m = re.match(r"^(\d+)-(\d+)$", args.range_)
        if not m:
            print(f"[错误] --range 要写成 100-105，收到 {args.range_!r}", file=sys.stderr)
            return 2
        low, high = int(m.group(1)), int(m.group(2))

    chapters = []
    for fn in sorted(os.listdir(body_dir)):
        if not fn.endswith(".md"):
            continue
        ch = chapter_no(fn)
        if ch is None:
            continue
        if low is not None and not (low <= ch <= high):
            continue
        chapters.append((ch, read_paras(os.path.join(body_dir, fn))))
    if not chapters:
        print("[错误] 区间内没有正文文件", file=sys.stderr)
        return 2
    chapters.sort(key=lambda item: item[0])

    openings, opening_notes = check_opening(args.book, chapters)
    short_notes = check_short_paragraphs(chapters)
    names = collect_names(args.book, args.names)
    variant_hits = []
    for ch, paras in chapters:
        for hit in scan_name_variants(paras, names):
            hit["chapter"] = ch
            variant_hits.append(hit)

    if args.json:
        print(json.dumps({
            "chapters": len(chapters),
            "opening": [{"chapter": ch, "kind": kind} for ch, kind in openings],
            "notes": opening_notes + short_notes,
            "name_variants": variant_hits,
            "names_checked": len(names),
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"区间 {chapters[0][0]}-{chapters[-1][0]}：共 {len(chapters)} 章（{args.layer} 层）")
    for note in opening_notes + short_notes:
        print(f"  {note}")
    print(f"  专名表 {len(names)} 个：{'、'.join(names[:12])}{' …' if len(names) > 12 else ''}")
    if variant_hits:
        for hit in variant_hits:
            print(f"  [建议] 第{hit['chapter']}章 L{hit['line']} 疑似「{hit['token']}」"
                  f"（权威名「{hit['canon']}」）上下文：…{hit['context']}…")
    else:
        print("  专名变体：未检出（同长度差一字的候选为 0）")
    print("  说明：以上全部 advisory。课程豁免——论坛留言/短信/弹幕/围观喊话/念信读纸"
          "不算短对话堆砌；系统面板不算罗列清单，也不计入极短段。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
