#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
novel-forge 跨章多样性检测（v2.0）。

单章合规 ≠ 全书好看。AI 写长篇的另一个指纹是「章与章之间太像」：开头都是动作、
结尾都是悬念、每章长度几乎相同、爽点总是同一类。本脚本做跨章视角的多样性检测。

全部输出 advisory（不阻断）。与 check-distribution.py 一样，机器给线索，人做判断。

检测项（连续 N 章同型即提示）：
  8.1 开头方式   动作 / 对话 / 环境 / 心理 / 时间 —— 连续 3 章同类 → advisory
  8.2 结尾方式   动作定格 / 台词 / 画面 / 悬念 / 心理 —— 连续 3 章同类 → advisory
  8.3 章节长度   长(2800+) / 中(2200–2800) / 短(1800–2200) —— 连续 5 章同档 → advisory
  8.4 爽点类型   打脸 / 反转 / 揭秘 / 升级 / 情感 / 智斗 —— 连续 3 章同类 → advisory
  8.5 对话风格   短/中/长句 × 有无口癖（吧/啊/呢/嘛等）—— 连续 3 章同型 → advisory
                （《燃识》四章实测反馈：对话全是短句、直接、功能化，没有口癖/
                  重复/答非所问；本项粗分风格档位，精判靠人）

判定来源：
  - 开头/结尾方式：优先读 meta/第N章.json 的 opening_type / ending_type；
    缺失则按首句/末句启发式分类。
  - 章节长度：直接数字数。
  - 爽点类型：优先读 meta sidecar 的 payoffs[].type；缺失则跳过该项（不猜）。

用法：
  python check-cross-diversity.py --book <书目录>
  python check-cross-diversity.py --book <书目录> --chapters 1-30
  python check-cross-diversity.py --book <书目录> --json

设计原则：纯 CPU、零外部依赖、只读。全部 advisory，退出码恒为 0（目录不存在=2）。
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

CH_IN_RE = re.compile(r"第\s*0*(\d+)\s*章")
VISIBLE = re.compile(r"[^\s]")
# 结尾悬念/预告词（用于启发式分类）
SUSPENSE_WORDS = ["究竟", "到底", "是谁", "为什么", "难道", "身后", "门外", "忽然", "突然"]
PSYCH_WORDS = ["想", "觉得", "心里", "心口", "念头", "意识", "记得", "明白"]
TIME_WORDS = ["天", "夜", "清晨", "傍晚", "凌晨", "第二天", "第二天早上", "风", "雨", "雪", "光"]
ENV_WORDS = ["天空", "街道", "房间", "屋里", "远处", "山", "海", "楼", "灯", "阳光", "空气"]
ACTION_WORDS = ["推", "拉", "跑", "走", "站", "抓", "扔", "转身", "抬手", "低头", "抬头", "冲", "踢", "坐"]

# 8.5 对话风格：引号对集合与 check-distribution.py 保持一致（含【】）；改一侧必须同步改另一侧
QUOTE_PAIRS = [("「", "」"), ("『", "』"), ("【", "】"), ("“", "”"), ("‘", "’"), ('"', '"'), ("'", "'")]
TONE_CHARS = "吧啊呢嘛呗哟哈嗯哦噢喔啦"
SENT_SPLIT_RE = re.compile(r"[。！？…!?]+")


def quoted_parts(text):
    """抽取引号内文本片段（粗风格分档用，非计量口径）。"""
    parts = []
    for op, cl in QUOTE_PAIRS:
        for m in re.finditer(re.escape(op) + "([^" + re.escape(cl) + r"\n]*)" + re.escape(cl), text):
            parts.append(m.group(1))
    return parts


def dialogue_style(text):
    """对话风格粗分型：{短|中|长}句 × {有|无}口癖；无对话返回「无对话」。

    粗判口径：对话句均长 ≤8 短 / 9–14 中 / >14 长；口癖 = 语气词在对话文本中
    出现即「有」。机器只分档供跨章对比，句级精判（非功能句/答非所问）靠人。
    """
    parts = quoted_parts(strip_comments(text))
    if not parts:
        return "无对话"
    blob = "".join(parts)
    sents = [s for s in SENT_SPLIT_RE.split(blob) if vlen(s)]
    if not sents:
        return "无对话"
    avg = vlen(blob) / len(sents)
    bucket = "长" if avg > 14 else ("中" if avg >= 9 else "短")
    tone = "有" if any(ch in TONE_CHARS for ch in blob) else "无"
    return "%s句/%s口癖" % (bucket, tone)


def vlen(s):
    return len(VISIBLE.findall(s))


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def strip_comments(text):
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def prose_paragraphs(text):
    """返回正文段列表（去标题/空行/结构行/[DRAFT] 等状态标记行）。"""
    text = strip_comments(text)
    # 状态标记行（如 [DRAFT]、[FINAL]）不是正文；注意有的文件 [DRAFT] 行尾带两个
    # 空格（markdown 硬换行）会和首段粘在同一段里，必须按行剔除而不是按段剔除。
    text = re.sub(r"^\s*\[(?:DRAFT|FINAL|草稿|定稿)\][^\n]*$", "", text, flags=re.M)
    out = []
    for para in re.split(r"\n\s*\n", text):
        p = para.strip()
        if not p:
            continue
        if re.match(r"^(#{1,6}\s|>\s?|[-*+]\s|\d+[.)]\s|\||[-*_]{3,}$)", p):
            continue
        if CH_IN_RE.match(p) and len(p) < 40:
            continue
        out.append(p)
    return out


# 人物到来开场：首段以「X来/X又来/X敲门/X进门」句式开局，X 为人名。
ARRIVE_OPEN_RE = re.compile(
    r"^([\u4e00-\u9fa5]{2,3}?)(?:又来|来敲门|来的时候|敲门的时候|是.{0,6}来的|推门进来|进门|送到|来了"
    r"|把门[推掩合带]上?|闪进来|挤进来|探头)"
)


def arrival_subject(first_para):
    """若首段为「人物到来」句式，返回人名；否则返回 None。"""
    if not first_para:
        return None
    m = ARRIVE_OPEN_RE.match(first_para[:20])
    if m:
        return m.group(1)
    return None


def classify_opening(first_para):
    """首段归纳为 动作/对话/环境/心理/时间。"""
    if not first_para:
        return "未知"
    if first_para[0] in "「『\"“" or "：" in first_para[:8]:
        return "对话"
    head = first_para[:24]
    if any(w in head for w in ACTION_WORDS):
        return "动作"
    if any(w in head for w in PSYCH_WORDS):
        return "心理"
    if any(w in head for w in TIME_WORDS):
        return "时间"
    if any(w in head for w in ENV_WORDS):
        return "环境"
    return "其他"


def classify_ending(last_para):
    """末段归纳为 动作定格/台词/画面/悬念/心理/节拍。"""
    if not last_para:
        return "未知"
    tail = last_para.strip()
    if tail and tail[-1] in "」』\"”":
        return "台词"
    if tail and tail[-1] in "。！？…":
        head = tail[-30:]
        if any(w in head for w in SUSPENSE_WORDS):
            return "悬念"
        if any(w in head for w in PSYCH_WORDS):
            return "心理"
        if any(w in head for w in ACTION_WORDS):
            return "动作定格"
        if any(w in head for w in ENV_WORDS + TIME_WORDS):
            return "画面"
        # 节拍收束：超短末段的否定式/陈述式收尾（「老祝没回来。」「蹭不掉。」）
        # 没有悬念词但确实有收束感，单列一类避免落「其他」造成结尾同型半误报。
        if len(re.sub(r"\s", "", tail)) <= 12:
            return "节拍"
    return "其他"


def len_bucket(n):
    if n >= 2800:
        return "长"
    if n >= 2200:
        return "中"
    return "短"


def load_meta(book_dir, ch):
    for sub in ("meta",):
        p = os.path.join(book_dir, sub, "第%03d章.json" % ch)
        if os.path.exists(p):
            try:
                return json.loads(read(p))
            except Exception:
                return {}
    return {}


def collect_chapters(book_dir):
    """返回 [{no, path, text}]，优先 final/，回退 draft/正文。"""
    order = []
    for sub in (("正文", "final"), ("正文", "draft"), ("正文",)):
        d = os.path.join(book_dir, *sub)
        if os.path.isdir(d):
            order.append(d)
    seen = {}
    for d in order:
        for name in sorted(os.listdir(d)):
            if not name.lower().endswith(".md") or name.startswith("_"):
                continue
            m = CH_IN_RE.search(name)
            if not m:
                continue
            ch = int(m.group(1))
            if ch not in seen:              # 先出现的目录优先（final 先）
                seen[ch] = os.path.join(d, name)
    out = []
    for ch in sorted(seen):
        p = seen[ch]
        out.append({"no": ch, "path": p, "text": read(p)})
    return out


def parse_range(s, chapters):
    """--chapters 1-30 / 5,6,7 => set。"""
    if not s:
        return None
    sel = set()
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                sel.update(range(int(a), int(b) + 1))
            except ValueError:
                pass
        elif part.isdigit():
            sel.add(int(part))
    return sel or None


def longest_run(seq):
    """返回 (最长连续同值长度, 值, 起始索引)。seq 为字符串列表。"""
    best_n = 0
    best_v = None
    best_i = 0
    run_n = 0
    run_v = None
    for i, v in enumerate(seq):
        if v == run_v:
            run_n += 1
        else:
            run_v = v
            run_n = 1
        if run_n > best_n:
            best_n = run_n
            best_v = v
            best_i = i - run_n + 1
    return best_n, best_v, best_i


def check_diversity(book_dir, chapters=None):
    chs = collect_chapters(book_dir)
    if chapters:
        chs = [c for c in chs if c["no"] in chapters]
    if not chs:
        return None

    opens, ends, lens, payoff_types, dlg_styles, nos, arrivals = [], [], [], [], [], [], []
    for c in chs:
        paras = prose_paragraphs(c["text"])
        meta = load_meta(book_dir, c["no"])
        op = meta.get("opening_type") or classify_opening(paras[0] if paras else "")
        arrivals.append(arrival_subject(paras[0]) if paras else None)
        en = meta.get("ending_type") or classify_ending(paras[-1] if paras else "")
        n = vlen(re.sub(r"\s", "", strip_comments(c["text"])))
        opens.append(op)
        ends.append(en)
        lens.append(len_bucket(n))
        dlg_styles.append(dialogue_style(c["text"]))
        ps = meta.get("payoffs") or []
        pt = None
        for item in ps:
            if isinstance(item, dict) and item.get("type"):
                pt = item["type"]
                break
            if isinstance(item, str):
                pt = item
                break
        payoff_types.append(pt)
        nos.append(c["no"])

    adv = []

    # 8.1 开头方式
    n1, v1, i1 = longest_run(opens)
    if n1 >= 3:
        adv.append("第 %d–%d 章连续 %d 章以「%s」开头 → 开头方式同型。"
                   "换一换：动作/对话/环境/心理/时间交替。"
                   % (nos[i1], nos[i1 + n1 - 1], n1, v1))

    # 8.2 结尾方式
    n2, v2, i2 = longest_run(ends)
    if n2 >= 3:
        adv.append("第 %d–%d 章连续 %d 章以「%s」结尾 → 结尾方式同型。"
                   "换一换：动作定格/台词/画面/悬念/心理/节拍交替。"
                   % (nos[i2], nos[i2 + n2 - 1], n2, v2))

    # 8.3 章节长度
    n3, v3, i3 = longest_run(lens)
    if n3 >= 5:
        adv.append("第 %d–%d 章连续 %d 章长度同为「%s」档 → 章长过于工整。"
                   "偶尔来一章明显偏长或偏短的，制造节奏起伏。"
                   % (nos[i3], nos[i3 + n3 - 1], n3, v3))

    # 8.4 爽点类型（有 meta 数据才判）
    if any(payoff_types):
        seq = [t for t in payoff_types if t]
        if len(seq) >= 3:
            n4, v4, i4 = longest_run(payoff_types)
            if n4 >= 3 and v4:
                adv.append("第 %d–%d 章连续 %d 章爽点同为「%s」→ 爽点类型重复。"
                           "打脸/反转/揭秘/升级/情感/智斗轮着来。"
                           % (nos[i4], nos[i4 + n4 - 1], n4, v4))

    # 8.5 对话风格（有对话的章才判；全无对话由分布侧低对话提示兜底）
    if any(s != "无对话" for s in dlg_styles):
        n5, v5, i5 = longest_run(dlg_styles)
        if n5 >= 3 and v5 != "无对话":
            adv.append("第 %d–%d 章连续 %d 章对话风格同为「%s」→ 对话风格单一。"
                       "换一换：口癖、重复、答非所问、寒暄；每章至少一句"
                       "「删了不影响剧情」的非功能对话，长短句交替。"
                       % (nos[i5], nos[i5 + n5 - 1], n5, v5))

    # 8.6 人物到来开场：同一人物连续多章用「到来」动作开局（比「其他」同型更细的指纹）
    # 允许中间夹个别非到来章，用滑动窗口：任意窗口内同人物到来 ≥3 次即提示。
    win = 5
    for i in range(len(arrivals) - win + 1):
        window = arrivals[i:i + win]
        names = {}
        for a in window:
            if a:
                names[a] = names.get(a, 0) + 1
        name, cnt = (max(names.items(), key=lambda kv: kv[1]) if names else (None, 0))
        if cnt >= 3 and name:
            adv.append("第 %d–%d 章中 %d 次以「%s 到来」开场 → 情报/事件入口单一，"
                       "读者会看出「又是他来敲门」。让消息换个入口（发现物、传闻、"
                       "主角主动出门），或换开场型（环境/对白/动作）。"
                       % (nos[i], nos[i + win - 1], cnt, name))
            break

    return {
        "book": book_dir,
        "chapters": nos,
        "opening_types": opens,
        "ending_types": ends,
        "length_buckets": lens,
        "payoff_types": payoff_types,
        "dialogue_styles": dlg_styles,
        "advisories": adv,
        "advisory_count": len(adv),
    }


def print_report(r):
    print("=" * 72)
    print("novel-forge 跨章多样性检测（advisory · 不阻断）")
    print("=" * 72)
    print("  书目录   : %s" % r["book"])
    print("  章数     : %d（第 %s–%s 章）"
          % (len(r["chapters"]), r["chapters"][0], r["chapters"][-1]))
    print("-" * 72)
    print("  开头:", " ".join("%s" % t for t in r["opening_types"]))
    print("  结尾:", " ".join("%s" % t for t in r["ending_types"]))
    print("  长度:", " ".join("%s" % t for t in r["length_buckets"]))
    pt = [t or "－" for t in r["payoff_types"]]
    print("  爽点:", " ".join(pt))
    print("  对话:", " ".join(r["dialogue_styles"]))
    print("-" * 72)
    if r["advisories"]:
        for i, a in enumerate(r["advisories"], 1):
            print("  [建议%d] %s" % (i, a))
        print("-" * 72)
        print("advisory %d 条。逐条判断——多样性的目的不是「每章都不同」，而是避免"
              "读者一眼看出「这几章是一个模子刻的」。" % r["advisory_count"])
    else:
        print("  跨章多样性未见明显异常。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="novel-forge 跨章多样性检测（全部 advisory）")
    ap.add_argument("--book", required=True, help="书目录")
    ap.add_argument("--chapters", help="限定章范围，如 1-30 或 5,6,7")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(args.book):
        print("[多样性] 目录不存在：%s" % args.book, file=sys.stderr)
        return 2

    # 先收集再解析范围
    all_chs = collect_chapters(args.book)
    sel = parse_range(args.chapters, all_chs) if args.chapters else None
    r = check_diversity(args.book, chapters=sel)
    if r is None:
        print("[多样性] 书目录下没有找到章节 md（找过 正文/final、正文/draft、正文）",
              file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    return print_report(r)


if __name__ == "__main__":
    sys.exit(main())
