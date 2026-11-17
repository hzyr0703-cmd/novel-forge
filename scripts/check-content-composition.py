#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内容构成门禁：管「这一章有没有发生事」，不管「文字像不像人」。

为什么单独一支：句子层（AI 词/句式）和形状层（分布/多样性）都有脚本，
唯独「内容构成」长期只靠人记。实测《开局无异能》102 章：句子层零命中、
分布层全绿，读者仍因「纸面章/谈话章/私下爽点」流失。

判据与规则全文见 references/content-composition.md。本脚本只做可机检的部分：

  outline 阶段（写正文前，默认）
    C3 连续谈话/纸面章（相邻两章同为 谈话|纸面 → 阻断；历史章未标注则降级 advisory）
    B1 event_count == 0 → 阻断（什么都没发生）
    B2 event_count / scenario 未填 → advisory（老项目未迁移，不打断）
    C4 滚动 5 章无实战 → advisory
    C5 滚动 10 章独处实验 > 1 → advisory

  draft/final 阶段（写完正文后自查）
    C1 纸面段占比 > 25% → advisory（按段统计纸面词族）
    C2 数字问答 ≥ 2 处 → advisory
    D  细纲 scenario 与正文实际形态对不上 → advisory

  --volume（卷纲提交前）
    读 卷纲/*.md 里的 volume_check 块：public_validation ≥2 / antagonist_defeat ≥1 /
    growth_steps ≥ 章数/15 / longest_no_event_run ≤3；缺字段报「未填」。

退出码：仅阻断项 > 0 时非 0；advisory 只打印。
"""

import argparse
import glob
import os
import re
import sys

# ---- 词族与常量（唯一事实源放这里，改阈值只改这一处）
SCENARIOS = ("战斗", "副本", "委托处置", "谈话", "纸面", "独处实验", "日常过渡")
PRACTICAL = ("战斗", "副本", "委托处置")
TALK_OR_PAPER = ("谈话", "纸面")
PAPER_WORDS = ("账面", "台账", "记录", "登记", "清单", "报表", "对账", "核算",
               "条目", "备注", "文件", "档案", "条款", "目录", "价目", "单据",
               "凭证", "表格", "条目表", "花名册")
PAPER_RATIO_MAX = 0.25
NUM_QA_MAX = 1
ROLLING_PRACTICAL = 5
SOLO_WINDOW = 10
SOLO_MAX = 1


def die(msg):
    sys.stderr.write("[错误] %s\n" % msg)
    sys.exit(2)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def find_outline(book, chapter):
    cand = []
    for pat in ("第%03d章细纲.md", "第%d章细纲.md", "第%03d章.md", "第%d章.md"):
        cand += glob.glob(os.path.join(book, "细纲", pat % chapter))
    return cand[0] if cand else None


def find_body(book, chapter, layer):
    for pat in ("第%03d章.md", "第%d章.md"):
        p = os.path.join(book, "正文", layer, pat % chapter)
        if os.path.exists(p):
            return p
    hits = glob.glob(os.path.join(book, "正文", layer, "第*%d章*.md" % chapter))
    return hits[0] if hits else None


def parse_header(text):
    """读细纲头：`> key：value` 行 + ```yaml 块。缺字段返回 None。"""
    data = {}
    for line in text.splitlines():
        m = re.match(r"^>\s*([^：:]+)[：:]\s*(.+)$", line.strip())
        if m:
            k, v = m.group(1).strip(), m.group(2).strip()
            if k in ("字数范围", "温度", "爽点类型", "章末钩子型号", "出场", "所属段", "文风基线", "章节目标"):
                data.setdefault(k, v)
    block = re.search(r"```ya?ml\n(.*?)```", text, re.S)
    if block:
        for line in block.group(1).splitlines():
            m = re.match(r"^\s*([a-z_]+)\s*[:：]\s*(.*)$", line.strip())
            if m:
                key, val = m.group(1), m.group(2).split("#")[0].strip().strip('"')
                data[key] = val
    # 兼容 `- scenario: 战斗` 这类列表写法
    for line in text.splitlines():
        m = re.match(r"^\s*[-*]\s*([a-z_]+)\s*[:：]\s*(.*)$", line.strip())
        if m:
            data.setdefault(m.group(1), m.group(2).split("#")[0].strip())
    return data


def get_scenario(book, chapter):
    p = find_outline(book, chapter)
    if not p:
        return None, None
    h = parse_header(read(p))
    sc = h.get("scenario") or ""
    sc = sc.strip()
    return (sc if sc in SCENARIOS else None), h


def paragraphs(text):
    """按空行切段，去掉标题/引用行。"""
    out = []
    for chunk in re.split(r"\n\s*\n", text):
        lines = [l for l in chunk.splitlines()
                 if l.strip() and not l.lstrip().startswith(("#", ">", "|", "-", "*", "[DRAFT]"))]
        if lines:
            out.append("\n".join(lines))
    return out


def paper_ratio(text):
    paras = paragraphs(text)
    if not paras:
        return 0.0, 0, 0
    n = 0
    for p in paras:
        hits = sum(p.count(w) for w in PAPER_WORDS)
        if hits >= 2 or (hits >= 1 and len(p) < 120):
            n += 1
    return n / len(paras), n, len(paras)


NUM_PAT = re.compile(
    r"(多少|几万|几千|几个|几位|多少钱)[^。！？\n]{0,25}[？?][^。\n]{0,80}?"
    r"(([0-9０-９]{1,4})|([一二三四五六七八九十百]{1,3}))[万块元]"
)


def num_qa_count(text):
    return len(NUM_PAT.findall(text))


def volume_check(book):
    rows = []
    for p in sorted(glob.glob(os.path.join(book, "卷纲", "*.md"))):
        text = read(p)
        block = re.search(r"```ya?ml\s*\n(.*?)```", text, re.S)
        fields = {}
        if block:
            for line in block.group(1).splitlines():
                m = re.match(r"^\s*([a-z_]+)\s*[:：]\s*(.*)$", line.strip())
                if m:
                    fields[m.group(1)] = m.group(2).split("#")[0].strip()
        rows.append((os.path.basename(p), fields, text))
    return rows


def outline_chapters(book):
    """列出 细纲/ 下所有章（按章号升序）：[(章号, 路径)]。"""
    rows = []
    for p in glob.glob(os.path.join(book, "细纲", "*.md")):
        m = re.search(r"第0*([0-9]+)章", os.path.basename(p))
        if m:
            rows.append((int(m.group(1)), p))
    return sorted(rows)


def _ranges(nums):
    """把章号列表压成区间串：1-3, 7, 9-12。"""
    if not nums:
        return "无"
    out, start, prev = [], nums[0], nums[0]
    for x in nums[1:]:
        if x == prev + 1:
            prev = x
            continue
        out.append("%d" % start if start == prev else "%d-%d" % (start, prev))
        start = prev = x
    out.append("%d" % start if start == prev else "%d-%d" % (start, prev))
    return ", ".join(out)


def audit_all(book):
    """存量项目迁移体检：哪些细纲还没填 scenario / event_count，哪些填了 event_count=0。
    只报告不阻断（退出码 0）——迁移是用户的事，门禁在填完之后才生效。"""
    rows = outline_chapters(book)
    if not rows:
        die("%s/细纲/ 下没有可识别的「第N章」细纲文件" % book)
    has_sc, has_ec, missing_sc, missing_ec, zero, bad_ec = [], [], [], [], [], []
    for ch, p in rows:
        h = parse_header(read(p))
        sc = h.get("scenario")
        ec = h.get("event_count")
        if sc in SCENARIOS:
            has_sc.append(ch)
        else:
            missing_sc.append(ch)
        if ec is None:
            missing_ec.append(ch)
        else:
            has_ec.append(ch)
            try:
                if int(ec) == 0:
                    zero.append(ch)
            except (TypeError, ValueError):
                bad_ec.append(ch)
    print("内容构成迁移体检｜书=%s｜细纲 %d 章" % (book, len(rows)))
    print("  已填 scenario  %3d / %d ｜ 缺：%s" % (len(has_sc), len(rows), _ranges(missing_sc)))
    print("  已填 event_count %3d / %d ｜ 缺：%s" % (len(has_ec), len(rows), _ranges(missing_ec)))
    if bad_ec:
        print("  event_count 非整数：%s（改成数字）" % _ranges(bad_ec))
    if zero:
        print("  event_count=0（写正文前必须改细纲）：%s" % _ranges(zero))
    if not missing_sc and not missing_ec and not zero:
        print("  已全部迁移，构成门禁可按 blocking 跑。")
    else:
        print("  未迁移期间内容构成只出 advisory；按 references/content-composition.md 第三节补字段。")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="内容构成门禁（细纲/正文/卷纲）")
    ap.add_argument("--book", required=True, help="书目录")
    ap.add_argument("--chapter", type=int, help="章号（--volume 时可省）")
    ap.add_argument("--layer", default=None, choices=["draft", "final"],
                    help="查正文层；省略则只查细纲头（outline 语义）")
    ap.add_argument("--volume", action="store_true", help="卷级检查（读 卷纲/*.md 的 volume_check 块）")
    ap.add_argument("--audit", action="store_true",
                    help="存量迁移体检：统计全部细纲缺哪些字段（只报告，退出码 0）")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.book):
        die("书目录不存在：%s" % args.book)

    blocks, warns = [], []

    if args.volume:
        rows = volume_check(args.book)
        if not rows:
            die("卷纲/ 下没有 .md 文件，无法做卷级检查")
        for name, f, text in rows:
            chs = [int(x) for x in re.findall(r"第\s*(\d+)\s*章", text)]
            span = (max(chs) - min(chs) + 1) if chs else 0
            need_growth = max(1, span // 15) if span else 1
            if not f:
                warns.append("%s 未填 volume_check 块（模板见 references/content-composition.md 第五节）" % name)
                continue
            pv = int(f.get("public_validation", -1) or -1)
            ad = int(f.get("antagonist_defeat", -1) or -1)
            gs = int(f.get("growth_steps", -1) or -1)
            ln = int(f.get("longest_no_event_run", -1) or -1)
            if 0 <= pv < 2:
                blocks.append("%s 公开验证次数 %d < 2（R1：爽点必须被人看见、被报价）" % (name, pv))
            if 0 <= ad < 1:
                blocks.append("%s 有名反派真败次数 %d < 1（R2：读者攒的憋屈没有出口）" % (name, ad))
            if 0 <= gs < need_growth:
                blocks.append("%s 可数成长 %d < %d（R3：成长必须落在读者能数的地方）" % (name, gs, need_growth))
            if ln > 3:
                warns.append("%s 连续无事件章最长 %d > 3（C4/水章连片）" % (name, ln))
            for k in ("public_validation", "antagonist_defeat", "growth_steps", "longest_no_event_run"):
                if k not in f:
                    warns.append("%s 未填 %s" % (name, k))

    else:
        if args.audit:
            return audit_all(args.book)
        if not args.chapter:
            die("--layer/--volume/--audit 之外必须给 --chapter")
        if args.layer:
            body = find_body(args.book, args.chapter, args.layer)
            if not body:
                die("第 %d 章正文不存在（层=%s）" % (args.chapter, args.layer))
            text = read(body)
            ratio, n, total = paper_ratio(text)
            if ratio > PAPER_RATIO_MAX:
                warns.append("C1 纸面段 %d/%d（%.0f%%）> 25%%：这一段在「看纸/算账/念数字」，读者看不到事"
                             % (n, total, ratio * 100))
            qa = num_qa_count(text)
            if qa > NUM_QA_MAX:
                warns.append("C2 数字问答 %d 处 > %d：一问一答报数字是记账流水，改成让数字自己出现"
                             % (qa, NUM_QA_MAX))
            sc, h = get_scenario(args.book, args.chapter)
            if sc and total:
                actual_talk = len(re.findall(r"[“「][^”」]{5,}[”」]", text)) / max(1, len(paragraphs(text)))
                if sc in TALK_OR_PAPER and actual_talk < 0.3 and ratio < 0.3:
                    warns.append("D 细纲 scenario=%s，但正文纸面段 %.0f%%、引号段 %d：细纲与正文形态不一致"
                                 % (sc, ratio * 100, actual_talk))
            print("内容构成自查｜章=%d 层=%s｜纸面段 %d/%d（%.0f%%）｜数字问答 %d 处｜细纲 scenario=%s"
                  % (args.chapter, args.layer, n, total, ratio * 100, qa, sc or "未填"))
            for b in blocks:
                print("  [阻断] %s" % b)
            for w in warns:
                print("  [建议] %s" % w)
            if not blocks and not warns:
                print("  构成自查通过：纸面与问答都在带内。")
            return 1 if blocks else 0

        # ---- outline 语义
        p = find_outline(args.book, args.chapter)
        if not p:
            die("第 %d 章细纲不存在：%s/细纲/ 下没有匹配文件" % (args.chapter, args.book))
        h = parse_header(read(p))
        sc = h.get("scenario") if h.get("scenario") in SCENARIOS else None
        ec = h.get("event_count")
        if sc is None:
            warns.append("细纲未填 scenario（老项目未迁移，构成门禁按 advisory 跑）。可选值：%s" % "/".join(SCENARIOS))
        if ec is None:
            warns.append("细纲未填 event_count（同上）。填了才会启用「什么都没发生」阻断。")
        else:
            try:
                if int(ec) == 0:
                    blocks.append("B1 event_count=0：这一章什么都没发生，先改细纲再写正文")
            except ValueError:
                warns.append("event_count 不是整数：%r" % ec)
        if sc:
            prev, _ = get_scenario(args.book, args.chapter - 1)
            if prev in TALK_OR_PAPER and sc in TALK_OR_PAPER:
                blocks.append("C3 第 %d 章=%s、第 %d 章=%s 连续两章以谈话/纸面为主：读者要的是事，不是记录"
                              % (args.chapter - 1, prev, args.chapter, sc))
        # C4 滚动实战（第 1–2 章窗口太短，不报——开篇本来就要先立人）
        win = [get_scenario(args.book, c)[0] for c in range(max(1, args.chapter - ROLLING_PRACTICAL + 1),
                                                           args.chapter + 1)]
        if args.chapter > 2 and all(w is not None for w in win) and not any(w in PRACTICAL for w in win):
            warns.append("C4 最近 %d 章无实战（战斗/副本/委托处置）：读者等的是事，不是信息" % len(win))
        # C5 独处限频
        solo_win = [get_scenario(args.book, c)[0] for c in range(max(1, args.chapter - SOLO_WINDOW + 1),
                                                                args.chapter + 1)]
        if all(w is not None for w in solo_win):
            n_solo = sum(1 for w in solo_win if w == "独处实验")
            if n_solo > SOLO_MAX:
                warns.append("C5 最近 %d 章有 %d 章独处实验 > %d" % (len(solo_win), n_solo, SOLO_MAX))
        print("内容构成门禁｜章=%d 细纲=%s｜scenario=%s｜event_count=%s"
              % (args.chapter, os.path.basename(p), sc or "未填", ec if ec is not None else "未填"))
        for b in blocks:
            print("  [阻断] %s" % b)
        for w in warns:
            print("  [建议] %s" % w)
        if blocks:
            print("构成门禁未通过：%d 项阻断。" % len(blocks))
            return 1
        print("构成门禁通过。")
        return 0

    print("内容构成卷级检查｜卷纲 %d 份" % len(volume_check(args.book)))
    for b in blocks:
        print("  [阻断] %s" % b)
    for w in warns:
        print("  [建议] %s" % w)
    if blocks:
        print("卷级检查未通过：%d 项阻断。" % len(blocks))
        return 1
    print("卷级检查通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
