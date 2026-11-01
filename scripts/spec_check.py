#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
单章硬规格机检 —— SKILL.md 第六节硬规格的可执行实现。

设计前提：check-ai-patterns.js 只管「AI 味」（文本形态像不像机器写的），
本脚本管「规格达标不达标」（该有的东西有没有）。两者互补，都不替代语感。

分级约定（v2.0 分层，与 forge.py gate 一致）：
  blocking —— 违反即不合格。只保留底线项：字数 / 段落长度 / AI 味 / 章末钩子存在。
              blocking_count > 0 才允许退出非 0。
  advisory —— 逐条判断，不阻断。对话占比 / 爽点 / 感官 / 三重回报 / 温度 /
              钩子型号 / 章中钩子等。advisory_count 单独汇报，仅供人工审稿参考。

为什么要分层（方案【二】/【三】）：
  blocking 越多，模型越倾向「填格子」而不是写故事。把非底线项降为 advisory，
  给正文留出参差、毛边、不完美——这些才是人味。

阈值全部单列在 THRESHOLDS 里，每条对应 SKILL.md 第六节的一个数字，
调整前先读该数字的出处注释——不要静默改阈值。

用法：
  python spec_check.py --file 正文/draft/第001章.md          # 默认检查 draft 层
  python spec_check.py --file 正文/final/第001章.md --meta meta/第001章.json
  python spec_check.py --file 正文/draft/第001章.md --prev 追踪/_spec-state.json
  python spec_check.py --file 正文/draft/第001章.md --json
退出码：存在 blocking 即1，否则 0。

v2.1.0 变更（方案【五】/【三】）：
  - 声明块（温度/爽点/回报/章中/钩子型号）改从 meta/第N章.json 读取，
    正文文件不再读任何声明块。meta/ 缺失时这些 advisory 项跳过不报。
  - 正文路径默认按 draft 层（正文/draft/第N章.md）组织，final 层传 --file 指定即可。
"""

import argparse
import json
import os
import re
import sys

CWD = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 阈值

# 每个键对应 SKILL.md 第六节表格的一行；注释写清数字出处。
# v2.0 分层：每项标注 [blocking] 或 [advisory]。
# 出处：deepseek 优化方案 v2.0（2026-10）【三】硬规格分层
#   —— blocking 只留「字数 / 段落长度 / AI 味 / 章末钩子存在」四条底线，
#      其余全部降为 advisory，避免模型填格子。
THRESHOLDS = {
    # | 单章字数 | 1800–3200 [blocking] |
    # v2.0 放宽上下限（旧值 2000–3000），允许章间波动。
    "words_min": 1800,
    "words_max": 3200,
    # 首章口径保持更严（开篇节奏要求更高）
    "first_chapter_words_min": 1500,
    "first_chapter_words_max": 2000,

    # | 段落长度 | ≤ 90 字 [blocking]；每章允许 1 段放到 120 字 [豁免] |
    # v2.8 统一口径：此前文档写「每章允许一到两段 70–100 字」、lint 写「>200 字 advisory」、
    # 本脚本写 90 blocking——三套数字互相打架（91–100 字＝文档说可以、机器判死）。
    # 现在只有一个事实源：常态上限 90；91–120 只给 1 段豁免（长段是节奏工具，不是默认形态）；
    # >120 一律 blocking。第七节正向约束第 7 条的说法同步改成这个口径。
    "paragraph_max": 90,
    "paragraph_exempt_max": 120,
    "paragraph_exempt_quota": 1,

    # | 对话占比 | 20%–55% [advisory] |（v2.0 双向放宽；下限防叙述堆砌，上限防对话注水）
    "dialogue_ratio_min": 0.20,
    "dialogue_ratio_soft_max": 0.55,
    "dialogue_ratio_hard_max": 0.65,

    # | 对话占比上行趋势 | 近半均值比前半高 ≥ 8 个百分点 → advisory |
    # 出处：fanqie-novel-skill v2.0.1 实战审阅经验——「前期 15–30% 健康，
    # 后期飙到 40–60%」是渐进退化，单章不超标时趋势先行。历史不足 8 章不判。
    "dialogue_trend_pp": 0.08,
    "dialogue_trend_min_history": 8,

    # | 爽点节点 | 每章 ≥ 2 个 [advisory] |
    # v2.0 改为「每章 ≥ 2」，不再要求每 500–800 字一个，位置不固定。
    "beats_min_per_chapter": 2,
    # 保留间隙信号用于统计（不再单独作 blocking）
    "beat_hard_gap": 800,
    "beat_soft_gap": 500,

    # | 微钩子 | 每 500 字 ≥ 1 [advisory] |
    "microhook_gap": 500,

    # | 章末钩子 | 必须存在 [blocking]；型号建议标注、不强制不同型 [advisory] |
    # | 章中钩子 | 建议第 40%–60% 位置 1 个 [advisory] |
    "midhook_low": 0.40,
    "midhook_high": 0.60,

    # | 感官/微动作 | 每章 ≥ 2 处 [advisory] |（v2.0 从「≥3 处且 ≥3 种」放宽）
    "sensory_min_hits": 2,
    "sensory_min_kinds": 2,

    # | 三重回报 | 每章 ≥ 1/3 [advisory] |（v2.0 从 2/3 放宽）
    "payoff_min": 1,

    # | 情绪温度 | 不可连续 3 章同档 [advisory] |（v2.0 从 5 章收紧但降为 advisory）
    "temperature_same_run_max": 3,
    "frustration_run_max": 2,
}

# ---------------------------------------------------------------- 平台预设
# v2.8：把 references/platform-adaptation.md 的表格变成可执行开关。
# 此前文档写「起点 2500–3500/章」，本脚本上限硬编码 3200 且没有任何平台入口，
# 同一份文档结尾又写「不要为迁就平台改阈值」——三条互相矛盾，照着做必然撞墙。
# 现在只换「字数带 + 爽点/微钩子间隔」这几项平台性参数；
# AI 味、跨章复述、事实冲突、段长都与平台无关，一律不动。
# 首章口径按各平台「黄金三章字数」一栏同步。
PLATFORM_PRESETS = {
    # 番茄（默认）：免费+广告分成，完读率驱动，节奏最快
    "fanqie": {
        "words_min": 1800, "words_max": 3200,
        "first_chapter_words_min": 1500, "first_chapter_words_max": 2000,
        "beat_hard_gap": 800, "microhook_gap": 500,
    },
    # 起点：订阅制，信息量更重，读者容忍铺垫
    "qidian": {
        "words_min": 2500, "words_max": 3500,
        "first_chapter_words_min": 2500, "first_chapter_words_max": 3500,
        "beat_hard_gap": 1000, "microhook_gap": 800,
    },
    # 七猫：免费+分成，节奏比番茄还快
    "qimao": {
        "words_min": 1800, "words_max": 2500,
        "first_chapter_words_min": 1500, "first_chapter_words_max": 2000,
        "beat_hard_gap": 600, "microhook_gap": 400,
    },
    # 知乎盐言：盐选付费，短章高密度
    "yanyan": {
        "words_min": 1200, "words_max": 2000,
        "first_chapter_words_min": 1000, "first_chapter_words_max": 1500,
        "beat_hard_gap": 600, "microhook_gap": 300,
    },
}

# 中文名与常见写法都能认，省得记 key
PLATFORM_ALIASES = {
    "番茄": "fanqie", "番茄小说": "fanqie", "fanqie": "fanqie",
    "起点": "qidian", "起点中文网": "qidian", "qidian": "qidian",
    "七猫": "qimao", "七猫中文网": "qimao", "qimao": "qimao",
    "盐言": "yanyan", "知乎盐言": "yanyan", "知乎": "yanyan", "yanyan": "yanyan",
}


def apply_platform(name):
    """按平台名改写 THRESHOLDS，返回 (规范 key, 实际改动的键值)。

    只在进程内改，不写回文件——平台是「这一本书的设定」，不是新基线。
    """
    if not name:
        return None, {}
    key = PLATFORM_ALIASES.get(str(name).strip().lower(), str(name).strip().lower())
    preset = PLATFORM_PRESETS.get(key)
    if not preset:
        raise SystemExit(
            "未知平台「%s」。可选：%s（也认中文名：番茄/起点/七猫/盐言）"
            % (name, " / ".join(PLATFORM_PRESETS))
        )
    THRESHOLDS.update(preset)
    return key, preset


# 情绪温度五档（references/genre-radar.md 第八节）。章内出现这些标记即认定该章主档位。
TEMPERATURE_MARKS = {
    "冷": ["冷", "谷底", "失去", "回忆", "落寞", "沉默"],
    "温": ["温", "过渡", "日常", "信息", "平静", "铺垫"],
    "热": ["热", "打脸", "对峙", "揭秘", "冲突", "交锋"],
    "爆": ["爆", "爽感", "反转", "爆发", "碾压", "翻盘"],
    "极爆": ["极爆", "高潮", "决战", "生死", "终局"],
}

# 爽点/回报的机器可识别信号。这些是「有就加分」的证据，不是唯一判据——
# 写得好的爽点不一定含这些词，所以真缺了才报 blocking，含了就认为作者已处理。
BEAT_SIGNALS = [
    "冷哼", "嗤笑", "愣住", "呆住", "震惊", "哗然", "炸了", "沸腾",
    "倒吸", "瞪大", "傻眼", "跪", "瘫", "悔青", "脸色一变", "脸色煞白",
    "打脸", "反转", "翻盘", "碾压", "完胜", "秒杀", "碾压式",
    "服软", "求饶", "道歉", "低头", "让步", "撤回", "赔礼", "签约",
    "涨停", "到账", "到手", "拿到", "赢了", "胜了", "过关", "通过",
    "松口", "服输", "改口", "夸赞", "喝彩", "掌声", "排队",
]

PAYOFF_SIGNALS = {
    "信息": ["发现", "得知", "明白", "真相", "线索", "证据", "原来", "竟然", "秘密"],
    "情绪": ["爽", "怒", "愤", "心疼", "暖", "甜", "激动", "痛快", "解气", "想哭"],
    "进展": ["变强", "升级", "突破", "得到", "获得", "加入", "收服", "扩张", "搬到", "拿下"],
}

# 感官词表：按五感分组，用于「≥3 种不同感官」判定。
SENSORY_GROUPS = {
    "视觉": ["看", "望", "盯", "瞥", "颜色", "光", "亮", "暗", "红", "黑", "白", "闪", "映", "瞳", "视线", "目光"],
    "听觉": ["听", "声", "响", "吵", "安静", "寂静", "嗡", "喊", "叫", "笑", "哭", "音乐", "话音", "语气", "嗓音"],
    "嗅觉": ["闻", "气味", "味道", "香", "臭", "腥", "霉味", "香水", "刺鼻", "清香"],
    "味觉": ["尝", "甜", "苦", "辣", "咸", "酸", "涩", "口味", "舌"],
    "触觉": ["摸", "触", "碰", "冷", "热", "烫", "冰", "粗糙", "光滑", "湿", "干", "风", "雨", "温度", "疼", "痛", "麻"],
    "体感": ["累", "饿", "困", "晕", "重心", "呼吸", "心跳", "体温", "僵硬", "发软", "无力"],
}

# 章末钩子 12 型（references/opening-surgery.md 第四节）。
HOOK_TYPES = ["H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8", "H9", "H10", "H11", "H12"]
HOOK_COMMENT = re.compile(r"<!--\s*钩子型号\s*[：:]\s*(H\d{1,2})\s*-->")

QUOTE_PAIRS = [("「", "」"), ("『", "』"), ("【", "】"), ("“", "”"), ("‘", "’"), ('"', '"'), ("'", "'")]

# ---------------------------------------------------------------- 文本工具

CJK = re.compile(r"[\u4e00-\u9fff]")
VISIBLE = re.compile(r"[^\s]")
STRUCTURAL = re.compile(r"^(#{1,6}\s|>\s?|[-*+]\s|\d+[.)]\s|\|)")
DIVIDER = re.compile(r"^[-*_]{3,}$")
CHAPTER_HEAD = re.compile(r"^第[零一二三四五六七八九十百千万\d]+章(?:\s|_|$)")
FRONTMATTER = re.compile(r"^---\s*$")


def read(path):
    if not os.path.exists(path):
        sys.stderr.write("[novel-forge] 文件不存在：%s\n" % path)
        sys.exit(2)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def strip_comments(text):
    """抹掉 HTML 注释（钩子型号等），并返回注释内容列表。"""
    comments = re.findall(r"<!--(.*?)-->", text, flags=re.S)
    return re.sub(r"<!--.*?-->", "", text, flags=re.S), comments


# ---------------------------------------------------------------- meta sidecar
#
# v2.1.0（方案【五】）：爽点/回报/温度/章中/钩子型号这几项机器无法从正文推断，
# 声明从「正文文末 HTML 注释」迁移到「meta/第N章.json」sidecar。
# 本脚本不再从正文读任何声明块；改从 meta/ 读取。
# 若 meta/ 缺失，这些 advisory 项跳过不报（不阻断，也不误报）。
#
# meta 文件路径约定：<书目录>/meta/第N章.json（也兼容 第007章.json / 7.json 等命名）。
# 正文路径通常为 <书目录>/正文/draft/第N章.md 或 .../final/第N章.md，
# 因此从正文路径向上回溯到「书目录」，再拼 meta/。

META_KEYS = ("temperature", "satisfaction", "returns", "mid_hook", "end_hook")


def find_meta_sidecar(prose_path, chapter=None):
    """从正文路径回溯书目录，定位 meta/第N章.json。

    回溯规则：正文一般在 <书目录>/正文/draft|final/第N章.md。
    逐级向上找，遇到名为 正文 / draft / final 的目录就继续上跳，
    直到找到含 meta/ 的目录，或到达根。
    """
    p = os.path.abspath(prose_path)
    # 用文件名推断章号（若未显式给 chapter）
    if chapter is None:
        nums = re.findall(r"(\d+)", os.path.basename(p))
        chapter = int(nums[0]) if nums else None

    cur = os.path.dirname(p)
    for _ in range(6):
        meta_dir = os.path.join(cur, "meta")
        if os.path.isdir(meta_dir):
            cands = []
            if chapter is not None:
                for name in os.listdir(meta_dir):
                    nums = re.findall(r"(\d+)", name)
                    if nums and int(nums[0]) == chapter and name.lower().endswith(".json"):
                        cands.append(os.path.join(meta_dir, name))
            if cands:
                return cands[0]
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def load_meta(prose_path, chapter=None):
    """读取 meta/第N章.json；缺失或损坏返回 None（调用方跳过相关 advisory）。"""
    mp = find_meta_sidecar(prose_path, chapter)
    if not mp:
        return None
    try:
        with open(mp, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    data["_path"] = mp
    return data


def strip_code_fence(text):
    out, fence = [], None
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^(`{3,}|~{3,})", s)
        if m:
            mark = m.group(1)
            if fence and mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
                continue
            if not fence:
                fence = mark
                continue
        if not fence:
            out.append(line)
    return "\n".join(out)


def is_prose_line(trimmed):
    """正文叙述行：排除标题、列表、引用、表格、分隔线、章节头、空行。"""
    if not trimmed:
        return False
    if DIVIDER.match(trimmed) or STRUCTURAL.match(trimmed) or CHAPTER_HEAD.match(trimmed):
        return False
    return True


def quote_spans(text):
    """返回引号内区间 [start, end)，含引号本身。"""
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


def in_spans(pos, spans):
    return any(s <= pos < e for s, e in spans)


def strip_quoted_spans(text):
    """抹掉引号内容，只留叙述层——用于「这个信号是不是在叙述里说的」判断。"""
    spans = quote_spans(text)
    if not spans:
        return text
    chars = list(text)
    for s, e in spans:
        for i in range(s, min(e, len(chars))):
            chars[i] = " " if chars[i] != "\n" else "\n"
    return "".join(chars)


def visible_len(text):
    return len(VISIBLE.findall(text))


def prose_lines(body):
    """产出 (行号, 文本) —— 只含叙述行；行号相对清理后的正文，用于报错定位。"""
    out = []
    for i, raw in enumerate(body.splitlines(), 1):
        t = raw.strip()
        if is_prose_line(t):
            out.append((i, raw))
    return out


def mask_comments_and_quotes(body):
    """把注释与引号内容替换成等长占位，避免注释/台词污染叙述层统计。"""
    masked = re.sub(r"<!--.*?-->", lambda m: " " * len(m.group(0)), body, flags=re.S)
    spans = quote_spans(masked)
    if not spans:
        return masked, spans
    chars = list(masked)
    for s, e in spans:
        for i in range(s, min(e, len(chars))):
            chars[i] = " " if chars[i] != "\n" else "\n"
    return "".join(chars), spans


# ----------------------------------------------------------------各项检查

def check_wordcount(body, first_chapter, errors, warns, stats):
    total = visible_len(body)
    stats["字数"] = total
    lo, hi = THRESHOLDS["words_min"], THRESHOLDS["words_max"]
    if first_chapter:
        lo, hi = THRESHOLDS["first_chapter_words_min"], THRESHOLDS["first_chapter_words_max"]
    if total < lo:
        errors.append("字数 %d 低于下限 %d（欠 %d 字）。按纪律不加戏，请用户决定是否补。" % (total, lo, lo - total))
    elif total > hi:
        errors.append("字数 %d 超上限 %d（超 %d 字）。删冗余或拆章，不要靠注水达标。" % (total, hi, total - hi))


def check_paragraph_length(lines, errors, warns, stats):
    """| 段落长度 | ≤ 90 字 [blocking]；每章允许 1 段放到 120 字 [豁免] |

    分两档判，不再有第三套口径：
      > 120 字           —— 一律 blocking（手机屏 5 行以上，必须断）
      91–120 字           —— 每章只豁免 1 段；第 2 段起 blocking
      90 字以内            —— 不动
    豁免不是「允许」，是「长段是节奏工具，一章用一次就够」——全章都是长段照样判死。
    """
    hard, soft = [], []
    for ln, raw in lines:
        n = visible_len(raw)
        if n > THRESHOLDS["paragraph_exempt_max"]:
            hard.append((ln, n, raw.strip()[:28]))
        elif n > THRESHOLDS["paragraph_max"]:
            soft.append((ln, n, raw.strip()[:28]))
    quota = THRESHOLDS["paragraph_exempt_quota"]
    over = hard + soft[quota:]
    stats["超长段落数"] = len(over)
    stats["长段豁免"] = min(len(soft), quota)
    if soft and len(soft) <= quota:
        warns.append(
            "长段用掉豁免：%d 段在 %d–%d 字之间（每章上限 %d 段，属正常节奏波动，无需改）。"
            % (len(soft), THRESHOLDS["paragraph_max"] + 1,
               THRESHOLDS["paragraph_exempt_max"], quota)
        )
    if over:
        errors.append(
            "段落长度超限：%d 段越界（常态上限 %d 字，每章只有 %d 段可放到 %d 字）。"
            "最长第 %d 行 %d 字。按动作/视线断段。"
            % (len(over), THRESHOLDS["paragraph_max"], quota,
               THRESHOLDS["paragraph_exempt_max"], over[0][0],
               max(n for _, n, _ in over))
        )


def check_dialogue_ratio(body, spans, prev, errors, warns, stats):
    """| 对话占比 | 20%–55% [advisory] | + 上限 65% + 上行趋势。

    v2.0 降级为 advisory：双向放宽（下限 20%、上限 55%），仅超硬上限 65% 才提示。
    全部走 warns（不计入 blocking_count）。
    """
    total = visible_len(body)
    quoted = 0
    chars = body
    for s, e in spans:
        quoted += visible_len(chars[s:e])
    ratio = (quoted / total) if total else 0.0
    stats["对话占比"] = "%.1f%%" % (ratio * 100)

    # 非功能对话句粗判（v2.1.1，只报数不判好坏，供人工审稿参考）：
    # 逐条引号区间独立切句——跨区间拼接会把两句并成一句假长句（实测句数虚降一半）；
    # 剥引号符与空白再量长度（「」占位曾把碎片算成句子）。
    # 疑似非功能 = ≤10 字、无数字、无动作动词、非问句（短问句多为剧情功能）。
    # 另计重复句（同句出现 ≥2 次 = 口癖/重复信号，属非功能类）。
    # 「删了不影响剧情」的语义机器判不准——这里只给数量线索，人工核。
    _ACTION_HINTS = ("走", "跑", "打", "杀", "拿", "递", "塞", "抓", "推", "拉",
                     "开", "关", "进", "出", "吃", "喝", "买", "卖", "抢", "躲",
                     "看", "听", "问", "答", "找", "等")
    _QSTRIP = re.compile("[" + re.escape("「」『』【】“”‘’\"'") + r"\s]+")
    nf = total_dlg_sents = 0
    sent_hist = {}
    for s, e in spans:
        txt = _QSTRIP.sub("", chars[s:e])
        segs = re.split(r"([。！？…!?]+)", txt)
        for j in range(0, len(segs), 2):
            sent = segs[j]
            delim = segs[j + 1] if j + 1 < len(segs) else ""
            sv = visible_len(sent)
            if not sv:
                continue
            total_dlg_sents += 1
            sent_hist[sent] = sent_hist.get(sent, 0) + 1
            is_question = "？" in delim or sent.endswith(("吗", "呢", "么"))
            if (sv <= 10 and not re.search(r"\d", sent)
                    and not any(w in sent for w in _ACTION_HINTS)
                    and not is_question):
                nf += 1
    if total_dlg_sents:
        rep = sum(1 for sent, c in sent_hist.items() if c >= 2 and visible_len(sent) >= 2)
        stats["非功能对话句（粗判）"] = (
            "%d/%d 句疑似（短且无数字/动作/问句；另重复句 %d 个）；"
            "写完自查：抽一句「删了不影响剧情」的对话，抽不出就回补一句"
            % (nf, total_dlg_sents, rep))
    if ratio < THRESHOLDS["dialogue_ratio_min"]:
        warns.append(
            "对话占比 %.1f%% 低于建议下限 %d%%（引号内 %d 字 / 全文 %d 字）。"
            "叙述层过厚是AI 味的主要来源，可补对话或把描写改成交互。"
            % (ratio * 100, THRESHOLDS["dialogue_ratio_min"] * 100, quoted, total)
        )
    elif ratio > THRESHOLDS["dialogue_ratio_hard_max"]:
        warns.append(
            "对话占比 %.1f%% 超过硬上限 %d%%。全章几乎变成聊天记录——"
            "氛围与动作描写消失，是后期注水的典型形态。压缩对话，把信息"
            "改由动作/细节承载（fanqie 实战：对话占比失控是长篇崩坏主因之一）。"
            % (ratio * 100, THRESHOLDS["dialogue_ratio_hard_max"] * 100)
        )
    elif ratio > THRESHOLDS["dialogue_ratio_soft_max"]:
        warns.append(
            "对话占比 %.1f%% 超过提示线 %d%%。本章可接受，下章注意控制；"
            "连续多章接近上限说明在用对话凑字数。" % (ratio * 100, THRESHOLDS["dialogue_ratio_soft_max"] * 100)
        )

    # 上行趋势（advisory）：单章都合格但连续缓慢上行，是渐进退化的前兆。
    hist = (prev or {}).get("dialogue_history") or []
    if len(hist) >= THRESHOLDS["dialogue_trend_min_history"]:
        half = len(hist) // 2
        old = sum(hist[:half]) / half
        recent = sum(hist[half:]) / (len(hist) - half)
        drift = recent - old
        if drift >= THRESHOLDS["dialogue_trend_pp"]:
            warns.append(
                "对话占比上行趋势：近 %d 章均值 %.0f%% 比更早 %d 章均值 %.0f%% "
                "高出 %d 个百分点。渐进退化前兆——检查近期章节是否在偷懒用对话推剧情。"
                % (len(hist) - half, recent * 100, half, old * 100, int(drift * 100))
            )


def check_beats(body, lines, errors, warns, stats, meta=None):
    """爽点节点 | 每章 ≥ 2 个 [advisory]（v2.0）。

    v2.1.0：爽点声明从正文注释迁移到 meta/（字段 satisfaction / returns）。
    判定：数爽点信号词 + meta 声明，总数 < 2 → advisory。
    meta 缺失时仅用信号词（不因缺 meta 而报错，只是判据更弱）。
    """
    # v2.8：声明数与信号数分开数，不再混算。
    # 旧版把 meta.returns（三重回报条数，3 条起）算进「爽点声明」，于是
    # 一张声明了回报的 meta 能凭空凑够 ≥2 的爽点门槛；输出「信号 2 处（声明 4）」
    # 也把两种单位并列在一个括号里，读的人无从判断到底够不够。
    marked = len(re.findall(r"<!--\s*爽点", body))
    if meta and meta.get("satisfaction"):
        marked += 1
    sig_hits = sum(1 for _, raw in lines if any(sig in strip_quoted_spans(raw) for sig in BEAT_SIGNALS))
    stats["爽点信号"] = "%d 处" % sig_hits
    stats["爽点声明"] = ("%d 处" % marked) if marked else "无"
    need = THRESHOLDS["beats_min_per_chapter"]
    if sig_hits + marked < need:
        warns.append(
            "爽点供给偏薄：信号 %d 处 + 声明 %d 处，建议每章 ≥ %d。"
            "爽点靠阅读感，机器只能数信号——若写得更含蓄，可在 meta/第N章.json 的 "
            "satisfaction 字段声明本章主爽点。"
            % (sig_hits, marked, need)
        )
    elif marked and sig_hits < need:
        # 声明把数量补够了，但正文信号不足：这正是「在 meta 里写爽点」代替「把爽点写出来」的形态。
        warns.append(
            "声明与实测不符：meta 声明 %d 处，正文只数到 %d 处爽点信号（建议 ≥%d）。"
            "以正文为准——回读一遍，确认声明的爽点真的演成了场景，不是只在 sidecar 里写了一句。"
            % (marked, sig_hits, need)
        )


def _declared_beat_windows(body, hard_gap):
    """显式爽点声明覆盖的窗口序号集合。

    语义：`<!-- 爽点：… -->` 写在某段正文之后，视为「声明这一段有爽点」，
    因此它覆盖的是**它前面那一段所属的窗口**，不是声明自身所在的位置。
    这样即使所有声明都集中在文末（会被退化检测判truncated 的缘故），
    也能如实反映「作者声明了哪几处」，而不会全部堆到最后一窗。
    """
    covered = set()
    cursor = 0
    for raw in body.splitlines():
        t = raw.strip()
        is_comment = t.startswith("<!--")
        if not is_comment and is_prose_line(t):
            n = visible_len(raw)
            # 这一段所属的窗口序号（按段落中点归属，避免跨界段落算错窗）
            mid = cursor + n // 2
            if mid > 0:
                covered.add(mid // hard_gap)
            cursor += n
        if is_comment and "爽点" in t and cursor > 0:
            covered.add((cursor - 1) // hard_gap)
    return covered


def check_microhooks(body, errors, warns, stats):
    """微钩子 | 每 500 字 ≥ 1 | —— 用钩子信号词密度做代理指标。"""
    signals = [
        "？", "?", "竟然", "居然", "没想到", "不对", "等等", "别动", "谁", "为什么",
        "还剩", "只剩", "来不及", "已经", "却", "但", "然而", "突然", "猛地", "忽然",
    ]
    total = visible_len(body)
    hits = sum(body.count(s) for s in signals)
    need = max(1, total // THRESHOLDS["microhook_gap"])
    stats["微钩子信号"] = "%d 处（应约 %d）" % (hits, need)
    if hits < need:
        warns.append(
            "微钩子密度偏低：%d 处信号，应约 %d 处（每 %d 字 1 个）。"
            "这是 advisory——微钩子形式极多样，机器数不准，请人工读一遍确认段落间是否有悬置。"
            % (hits, need, THRESHOLDS["microhook_gap"])
        )


def check_hook_models(raw, comments, prev, errors, warns, stats, meta=None):
    """| 章末钩子 | 必须存在 [blocking]；型号建议标注、不强制不同型 [advisory] |（v2.0）。

    v2.1.0：钩子型号声明从正文注释迁移到 meta/（字段 end_hook.type）。
    blocking 只保留「钩子存在」这一条底线；型号缺失/非法/同型全部降为 advisory。
    型号来源优先级：meta.end_hook.type → 正文注释 `<!-- 钩子型号：H3 -->` → 正文 `【H3】`。
    """
    model = None
    src = "无"
    if meta and isinstance(meta.get("end_hook"), dict):
        mt = meta["end_hook"].get("type")
        if mt:
            model, src = str(mt).upper(), "meta"
    if not model:
        found = HOOK_COMMENT.findall(raw)
        if not found:
            found = re.findall(r"【\s*(H\d{1,2})\s*】", raw)
        if found:
            model, src = found[-1].upper(), "正文"
    stats["章末钩子型号"] = (model or "缺失") + ("" if src == "无" else "（%s）" % src)

    # blocking：钩子必须存在（型号可缺，但正文末尾要有钩子信号）
    hook_tail = raw[-400:]
    tail_signals = ["？", "?", "竟然", "居然", "没想", "不对", "等等", "还剩", "只剩",
                    "来不及", "谁", "为什么", "猛地", "突然", "忽然", "←", "却"]
    has_tail_hook = any(s in hook_tail for s in tail_signals)
    if model is None and not has_tail_hook:
        errors.append(
            "章末无钩子：结尾既未在 meta 声明型号，末尾 400 字内也没有悬念/断裂信号。"
            "请在 meta/第N章.json 的 end_hook.type 填一个型号。可用型号：%s。" % "、".join(HOOK_TYPES)
        )
        return

    # 以下全部 advisory
    if model is None:
        warns.append(
            "章末钩子未标型号（末尾有钩子信号，判定通过）。建议在 meta/第N章.json 的 "
            "end_hook.type 标注型号便于跨章轮换核查。可用型号：%s。" % "、".join(HOOK_TYPES)
        )
    elif model not in HOOK_TYPES:
        warns.append("章末钩子型号 %s 不在 12 型内，可用：%s。" % (model, "、".join(HOOK_TYPES)))
    elif prev:
        prev_model = prev.get("hook_model")
        if prev_model == model:
            warns.append(
                "章末钩子与上章同型（均为 %s）。建议换型——连续两章同型会让读者预判下一步。"
                % model
            )
        seen = prev.get("hook_history") or []
        same_vol = seen.count(model)
        if same_vol >= 4:
            warns.append("型号 %s 在本卷已用 %d 次，建议控制同卷同型 ≤4 次。" % (model, same_vol + 1))
    # 章中钩子：advisory（声明来源 meta.mid_hook，回退正文「章中」字样）
    mid_hit = bool(meta and isinstance(meta.get("mid_hook"), dict) and meta["mid_hook"].get("position") is not None)
    if not mid_hit:
        mid_hit = "章中" in raw
    stats["章中钩子"] = "已声明" if mid_hit else "未声明（建议第 40%–60% 处 1 个）"
    if not mid_hit:
        warns.append(
            "未声明章中钩子。建议在第 %d%%–%d%% 位置放 1 个「进行中的断裂感」"
            "（对话/动作被打断），并在 meta/第N章.json 的 mid_hook.position 记录位置。"
            % (int(THRESHOLDS["midhook_low"] * 100), int(THRESHOLDS["midhook_high"] * 100))
        )


def check_sensory(body, spans, errors, warns, stats):
    """| 感官/微动作 | 每章 ≥ 2 处 [advisory] |（v2.0 从「≥3 处且 ≥3 种」放宽）。"""
    chars = list(body)
    for s, e in spans:
        for i in range(s, min(e, len(chars))):
            chars[i] = " " if chars[i] != "\n" else "\n"
    narr = "".join(chars)

    kinds, hits = set(), 0
    for kind, words in SENSORY_GROUPS.items():
        n = sum(narr.count(w) for w in words)
        if n:
            kinds.add(kind)
            hits += min(n, 3)
    stats["感官"] = "%d 处 / %d 种（%s）" % (hits, len(kinds), "、".join(sorted(kinds)) or "无")
    if len(kinds) < THRESHOLDS["sensory_min_kinds"] or hits < THRESHOLDS["sensory_min_hits"]:
        warns.append(
            "感官描写偏少：%d 处 / %d 种（建议 ≥%d 处、≥%d 种）。"
            "补 1–2 种当前场景最合理的感官（不必全上），别硬塞五感清单。"
            % (hits, len(kinds), THRESHOLDS["sensory_min_hits"], THRESHOLDS["sensory_min_kinds"])
        )


def check_payoff(raw, errors, warns, stats, meta=None):
    """| 三重回报 | 每章 ≥ 1/3（信息/情绪/进展）[advisory] |（v2.0 从 2/3 放宽）。

    v2.1.0：回报声明从正文注释迁移到 meta/（字段 returns: ["信息","情绪","进展"]）。
    用信号词 + meta.returns 兜底。
    """
    got = []
    for kind, sigs in PAYOFF_SIGNALS.items():
        if any(s in raw for s in sigs):
            got.append(kind)
    declared = []
    if meta and meta.get("returns"):
        declared = [str(x) for x in (meta.get("returns") or [])]
    for d in declared:
        for kind in PAYOFF_SIGNALS:
            if kind in d and kind not in got:
                got.append(kind)
    stats["三重回报"] = "%d/3（%s）" % (len(got), "、".join(got) or "无")
    if len(got) < THRESHOLDS["payoff_min"]:
        warns.append(
            "三重回报只满足 %d/3（建议 ≥%d/3：信息/情绪/进展）。"
            "答不出具体一项回报就该考虑重写；写得含蓄时用 meta/第N章.json 的 "
            "returns 字段显式声明。"
            % (len(got), THRESHOLDS["payoff_min"])
        )


def check_temperature(raw, prev, errors, warns, stats, meta=None):
    """| 情绪温度 | 不可连续 3 章同档 [advisory]；憋屈连续 ≤ 2 章 |。

    v2.0：从 5 章收紧到 3 章，但降为 advisory（提醒换档，不阻断）。
    v2.1.0：温度声明从正文注释迁移到 meta/（字段 temperature）。
    """
    level = None
    source = None
    if meta and meta.get("temperature"):
        lv = str(meta["temperature"]).strip()
        if lv:
            level, source = lv, "meta 声明"
    if level is None:
        declared = re.findall(r"<!--\s*温度\s*[：:]\s*([冷温热爆极爆]+)\s*-->", raw)
        if declared:
            level, source = declared[-1], "正文显式标记"
    if level is None:
        counts = {}
        for lv, kws in TEMPERATURE_MARKS.items():
            n = sum(raw.count(k) for k in kws)
            if n:
                counts[lv] = n
        level = max(counts, key=counts.get) if counts else "温"
        source = "关键词推断（建议在 meta/第N章.json 的 temperature 字段声明）"
    stats["情绪温度"] = "%s（%s）" % (level, source)

    if not prev:
        return
    run = prev.get("temperature_run") or []
    run = run + [level] if run and run[-1] == level else [level]
    if len(run) >= THRESHOLDS["temperature_same_run_max"]:
        warns.append(
            "情绪温度连续 %d 章同档（%s），达到提醒线 %d。"
            "读者可能判「套路重复」，建议安排一章换档；冷章后尽量有热/爆跟进。"
            % (len(run), level, THRESHOLDS["temperature_same_run_max"])
        )
    if level == "冷":
        fr = (prev.get("frustration_run") or 0) + 1
        if fr > THRESHOLDS["frustration_run_max"]:
            warns.append(
                "憋屈（冷档）连续 %d 章，超过建议上限 %d 章。憋久了容易掉追更。"
                % (fr, THRESHOLDS["frustration_run_max"])
            )


def collect_hook_state(raw, level, out_path, dialogue_ratio=None, meta=None):
    """把本章钩子型号、温度档、对话占比写进状态文件，供下一章比对。

    v2.1.0：型号/温度优先取 meta 声明，回退正文注释。
    """
    model = None
    if meta and isinstance(meta.get("end_hook"), dict):
        mt = meta["end_hook"].get("type")
        if mt:
            model = str(mt).upper()
    if not model:
        found = HOOK_COMMENT.findall(raw)
        if not found:
            found = re.findall(r"【\s*(H\d{1,2})\s*】", raw)
        model = found[-1].upper() if found else None
    state = {}
    if os.path.exists(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (ValueError, OSError):
            state = {}
    history = state.get("hook_history") or []
    if model:
        history = history + [model]
    if meta and meta.get("temperature"):
        lv = str(meta["temperature"]).strip()
        if lv:
            level = lv
    else:
        declared = re.findall(r"<!--\s*温度\s*[：:]\s*([冷温热爆极爆]+)\s*-->", raw)
        if declared:
            level = declared[-1]
    run = state.get("temperature_run") or []
    run = run + [level] if run and run[-1] == level else [level]
    state["hook_model"] = model
    state["hook_history"] = history[-30:]
    state["temperature_run"] = run[-10:]
    state["frustration_run"] = (state.get("frustration_run", 0) + 1) if level == "冷" else 0
    if dialogue_ratio is not None:
        dh = state.get("dialogue_history") or []
        dh = dh + [round(dialogue_ratio, 4)]
        state["dialogue_history"] = dh[-20:]
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    except OSError:
        pass


# ---------------------------------------------------------------- 主流程

def run(path, first_chapter=False, prev_path=None, chapter=None, meta_path=None):
    raw = read(path)
    body_nc, comments = strip_comments(raw)
    body = strip_code_fence(body_nc)

    # v2.1.0：读取 meta/第N章.json sidecar（声明块不再写在正文里）。
    # meta_path 可由调用方显式指定；否则从正文路径回溯书目录自动定位。
    if meta_path:
        meta = None
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                if isinstance(meta, dict):
                    meta["_path"] = meta_path
                else:
                    meta = None
            except (ValueError, OSError):
                meta = None
    else:
        meta = load_meta(path, chapter)

    errors, warns, stats = [], [], {}
    masked, spans = mask_comments_and_quotes(body)
    lines = prose_lines(body)
    if not lines:
        errors.append("正文无有效叙述行：确认文件里有实际段落，且章节正文没写在代码块或HTML 注释里。")

    prev = None
    if prev_path and os.path.exists(prev_path):
        try:
            with open(prev_path, "r", encoding="utf-8") as f:
                prev = json.load(f)
        except (ValueError, OSError):
            warns.append("上一章状态文件 %s 读不了，钩子轮换/温度连档检查跳过。" % prev_path)

    check_wordcount(body, first_chapter, errors, warns, stats)
    check_paragraph_length(lines, errors, warns, stats)
    check_dialogue_ratio(body, spans, prev, errors, warns, stats)
    check_beats(body, lines, errors, warns, stats, meta=meta)
    check_microhooks(body, errors, warns, stats)

    check_hook_models(raw, comments, prev, errors, warns, stats, meta=meta)
    check_sensory(body, spans, errors, warns, stats)
    check_payoff(raw, errors, warns, stats, meta=meta)
    check_temperature(raw, prev, errors, warns, stats, meta=meta)

    if meta:
        stats["声明来源"] = os.path.relpath(meta.get("_path", ""), os.path.dirname(os.path.abspath(path)))
    else:
        stats["声明来源"] = "无 meta/ 声明（爽点/回报/温度/钩型项按正文信号推断）"

    level = stats.get("情绪温度", "温").split("（")[0]
    if prev_path:
        total = visible_len(body)
        quoted = sum(visible_len(body[s:e]) for s, e in spans)
        ratio = (quoted / total) if total else 0.0
        collect_hook_state(raw, level, prev_path, dialogue_ratio=ratio, meta=meta)
        stats["状态已更新"] = prev_path

    return {
        "file": path,
        "chapter": chapter,
        "errors": errors,
        "warns": warns,
        "blocking_count": len(errors),
        "advisory_count": len(warns),
        "stats": stats,
        "meta": meta or {},
    }


def print_report(result):
    print("=" * 64)
    print("novel-forge 单章规格 ｜ %s" % os.path.basename(result["file"]))
    print("=" * 64)
    for k, v in result["stats"].items():
        print("  %-14s : %s" % (k, v))
    print()
    for w in result["warns"]:
        print("  [建议] %s" % w)
    for e in result["errors"]:
        print("  [阻断] %s" % e)
    bc = result.get("blocking_count", len(result["errors"]))
    ac = result.get("advisory_count", len(result["warns"]))
    print("-" * 64)
    if bc:
        print("未通过：blocking %d 项必须修到 0（另有 advisory %d 项人工参考）。" % (bc, ac))
    else:
        print("blocking 通过（0 项）。advisory %d 项需人工判断——机器只数数，好句子靠人读。" % ac)
        print("下一步：按 SKILL.md 第七节「正向写作约束」与第十一节人工审稿清单过一遍「人味」，")
        print("     再跑 check-distribution.py / check-cross-diversity.py 看分布与跨章多样性。")


def main():
    ap = argparse.ArgumentParser(description="单章规格机检（SKILL.md 第六节，blocking/advisory 分层）")
    ap.add_argument("--file", required=True)
    ap.add_argument("--first-chapter", action="store_true", help="按首章 1500–2000 字口径")
    ap.add_argument("--prev", help="上一章状态 JSON（钩子轮换/温度连档比对）")
    ap.add_argument("--chapter", type=int)
    ap.add_argument("--meta", help="显式指定 meta/第N章.json（默认从正文路径自动定位）")
    ap.add_argument("--platform", help="平台预设：fanqie（默认）/ qidian / qimao / yanyan，也认中文名")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    plat_key, plat_preset = apply_platform(args.platform)

    result = run(args.file, args.first_chapter, args.prev, args.chapter, meta_path=args.meta)
    if plat_key:
        result["stats"]["平台口径"] = "%s（字数 %d–%d，首章 %d–%d）" % (
            plat_key, plat_preset["words_min"], plat_preset["words_max"],
            plat_preset["first_chapter_words_min"], plat_preset["first_chapter_words_max"],
        )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)
    _ = CWD
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())