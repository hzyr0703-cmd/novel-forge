#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
novel-forge 统一命令入口。

补齐 oh-story 在 WorkBuddy 中失效的两块能力：
  1. 细纲门禁（原本是 Claude Code 的阻断式 hook，WorkBuddy 无 hook 系统 → 用子命令实现）
  2. 作者真相/读者已知双时间线一致性校验（原本靠 agent 协作保证 → 用机器校验）

设计原则：report-only，不改用户的正文。所有子命令退出码非 0 即代表存在硬错误，
必须修到通过才允许交付。

用法：
  python forge.py gate --book <书目录> --chapter N          细纲门禁 + 连续性门禁
  python forge.py gate --book <书目录> --chapter N --stage all  含正文全检（调用 node lint）
  python forge.py hitrate --file <方案.md>                    15 维爆款命中率复核
  python forge.py truth --book <书目录>                       双时间线一致性校验
  python forge.py wordcount --file <正文.md> --target 2300    字数口径检查
  python forge.py export --book <书目录>                      导出层：final/ -> 导出/
  python forge.py selftest                                   脚本自检
"""

import argparse
import json
import os
import re
import subprocess
import sys

CWD = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 15 维模型

HIT_DIMENSIONS = {
    "D1 赛道热度与生命周期": 7,
    "D2 差异化切口": 7,
    "D3 金手指新颖度与可延展性": 7,
    "D4 开局冲突强度": 9,
    "D5 书名点击力": 7,
    "D6 简介转化力": 6,
    "D7 标签精准度": 4,
    "D8 主角人设记忆点": 7,
    "D9 爽点供给节奏": 9,
    "D10 情绪曲线设计": 7,
    "D11 长线可持续性": 8,
    "D12 AI 味风险": 6,
    "D13 读者心理契合度": 6,
    "D14 改编与传播潜力": 5,
    "D15 竞争壁垒与记忆点": 5,
}
HIT_PASS = 75
HIT_VETO = ["D4 开局冲突强度", "D12 AI 味风险"]
HIT_CEILING = ["D1 赛道热度与生命周期", "D11 长线可持续性", "D13 读者心理契合度"]
MAX_FULL_MARKS = 3   # 打分纪律：满分维度不得超过 3 项

DIM_KEYWORDS = {
    "D1 赛道热度与生命周期": ["赛道", "热度", "生命周期", "蓝海", "过热", "均衡"],
    "D2 差异化切口": ["差异化", "切口", "独特", "排他"],
    "D3 金手指新颖度与可延展性": ["金手指", "延展", "三阶段", "进化"],
    "D4 开局冲突强度": ["开局冲突", "开篇冲突", "冲突强度", "300 字", "前300字", "代价"],
    "D5 书名点击力": ["书名", "点击力", "命名公式"],
    "D6 简介转化力": ["简介", "转化"],
    "D7 标签精准度": ["标签"],
    "D8 主角人设记忆点": ["人设", "记忆点", "专属动作", "反差"],
    "D9 爽点供给节奏": ["爽点", "甜头", "500", "800"],
    "D10 情绪曲线设计": ["情绪曲线", "温度", "憋屈", "张弛"],
    "D11 长线可持续性": ["长线", "可持续", "势能", "跃升", "三卷"],
    "D12 AI 味风险": ["AI 味", "AI味", "AI 腔", "人工改写"],
    "D13 读者心理契合度": ["读者心理", "情绪刚需", "代入感", "心理契合"],
    "D14 改编与传播潜力": ["改编", "传播", "短剧", "金句", "对话占比"],
    "D15 竞争壁垒与记忆点": ["竞争壁垒", "排他", "一句话说清", "记忆点"],
}


# ---------------------------------------------------------------- 工具

def die(msg):
    sys.stderr.write("[novel-forge] %s\n" % msg)
    sys.exit(2)


def read(path):
    if not os.path.exists(path):
        die("文件不存在：%s" % path)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def find_chapter_file(book, chapter, layer=None):
    """在书目录下找第 N 章细纲/正文，兼容 021 / 21 / 第021章 多种命名。

    v2.1.0：正文分 正文/draft/ 与 正文/final/ 两层，原实现只 os.listdir("正文/")
    非递归，两层布局下返回 []（【九】审计确认的 bug）。现改为递归搜索。

    layer 参数（可选）：
      - "draft"：只搜 正文/draft/
      - "final"：只搜 正文/final/
      - None  ：搜整个 正文/（含 draft/final 子目录），沿用旧行为
    """
    cands = []
    for sub in ("大纲/细纲", "细纲", "大纲"):
        d = os.path.join(book, sub)
        if os.path.isdir(d):
            for name in os.listdir(d):
                nums = re.findall(r"(\d+)", name)
                if nums and int(nums[0]) == chapter:
                    cands.append(os.path.join(d, name))

    body_root = os.path.join(book, "正文")
    if os.path.isdir(body_root):
        if layer in ("draft", "final"):
            roots = [os.path.join(body_root, layer)]
        else:
            roots = [body_root]
        for root in roots:
            if not os.path.isdir(root):
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                for name in filenames:
                    nums = re.findall(r"(\d+)", name)
                    if nums and int(nums[0]) == chapter:
                        cands.append(os.path.join(dirpath, name))
    return cands


# ---------------------------------------------------------------- gate

def run_spec(path, chapter=None):
    """调用 spec_check.py 执行单章规格机检（SKILL.md 第六节，blocking/advisory 分层）。

    独立子进程调用而非 import：spec_check 会随规则演进入增改，进程隔离
    避免一次运行内的模块状态污染，也保证 spec_check 的 sys.exit 不带崩 gate。
    """
    cmd = [sys.executable, os.path.join(CWD, "spec_check.py"),
           "--file", path, "--json"]
    if chapter:
        cmd += ["--chapter", str(chapter)]
    if chapter == 1:
        cmd.append("--first-chapter")
    # v2.1.0：声明块在 meta/第N章.json，显式传路径（spec_check 也会自动回溯定位）。
    mp = find_meta_file(os.path.dirname(os.path.dirname(path)), chapter)
    if mp:
        cmd += ["--meta", mp]
    prev = spec_state_path(os.path.dirname(os.path.dirname(path)), chapter)
    if prev and os.path.exists(prev):
        cmd += ["--prev", prev]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        die("未找到 python，无法执行硬规格门禁")
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"errors": [], "warns": [], "stats": {},
                "raw": (p.stdout or "") + (p.stderr or "")}


def spec_state_path(book, chapter):
    """硬规格状态文件位置：追踪/_spec-state.json（供下一章比对钩子轮换与温度连档）。"""
    if not chapter or chapter <= 1:
        return None
    return os.path.join(book, "追踪", "_spec-state.json")


def find_meta_file(book, chapter):
    """定位 meta/第N章.json（v2.1.0 声明块 sidecar）。

    book 参数此处传入的是「书目录」——注意 run_spec 里用 dirname(dirname(path)) 得到，
    当 path=书目录/正文/draft/第N章.md 时，两层 dirname 正好回到书目录。
    找不到返回 None（spec_check 会自行把相关 advisory 项跳过）。
    """
    if not chapter:
        return None
    meta_dir = os.path.join(book, "meta")
    if not os.path.isdir(meta_dir):
        return None
    for name in os.listdir(meta_dir):
        nums = re.findall(r"(\d+)", name)
        if nums and int(nums[0]) == chapter and name.lower().endswith(".json"):
            return os.path.join(meta_dir, name)
    return None


def book_has_prose(book):
    """书目录是否有正文/ 目录。

    2026-10-07 bug 修复：gate --stage outline 的核心场景是「刚写完细纲、
    正文还没动笔」，此时四个全书级检测（跨章复述/事实冲突/中文腔/平台合规）
    原本会在子进程里因找不到正文目录而 exit 2 → gate 直接 die——新书第一次
    跑门禁就崩，outline 门禁形同虚设。修复方式：集成层预检，无正文时跳过
    全书级检测（单跑子脚本时的报错行为保留，帮作者发现路径错误）。
    """
    return os.path.isdir(os.path.join(book, "正文"))


def run_crossrepeat(book, threshold=None):
    """调用 check-crossrepeat.py 做跨章自我复述检测。

    与 run_lint/run_spec 同构：子进程调用 + JSON 解析，隔离模块状态。
    段级 MinHash 跨章比对与现有单章 lint 互补——现有脚本全是单文件视角，
    AI 复述前文章节时每章内部都不重复，只能靠跨章比对才拦得住。
    """
    if not book_has_prose(book):
        return {"findings": [], "skipped": "no-prose-dir"}
    cmd = [sys.executable, os.path.join(CWD, "check-crossrepeat.py"),
           "--book", book, "--json"]
    if threshold is not None:
        cmd += ["--threshold", str(threshold)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        die("找不到 python，无法执行跨章复述检测")
    if p.returncode == 2:
        # 环境/用法错误不可静默跳过——那等于放行盲区
        errors = (p.stderr or p.stdout or "").strip()
        die("跨章复述检测无法运行：%s" % errors)
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"findings": [], "error": "输出解析失败"}


def run_factconflicts(book):
    """调用 check-factconflicts.py 做事实冲突检测（story-skills 判定移植）。

    查的是作者真相层的确定性矛盾（死人复活、回收早于埋设），与 debt.py
    的伏笔压力计算互补：debt 算「已登记的债该还了没」，这个查「台账字段
    本身自相矛盾」。
    """
    if not book_has_prose(book):
        return {"findings": [], "skipped": "no-prose-dir"}
    cmd = [sys.executable, os.path.join(CWD, "check-factconflicts.py"),
           "--book", book, "--json"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        die("找不到 python，无法执行事实冲突检测")
    if p.returncode == 2:
        errors = (p.stderr or p.stdout or "").strip()
        die("事实冲突检测无法运行：%s" % errors)
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"findings": [], "error": "输出解析失败"}


def run_chinese_tone(book):
    """调用 check-chinese-tone.py 做中文 AI 腔密度检测（lieflat 11 项特征）。

    advisory 层：密度超标只提示不阻断——与 lint 的 blocking 不同，
    这些特征（翻案腔、冒号提示语等）在网文里有 legitimate 用法，
    密度超标说明「该回头读一读」，不是「禁止交付」。
    """
    if not book_has_prose(book):
        return {"findings": [], "skipped": "no-prose-dir"}
    cmd = [sys.executable, os.path.join(CWD, "check-chinese-tone.py"),
           "--book", book, "--json"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        die("找不到 python，无法执行中文 AI 腔检测")
    if p.returncode == 2:
        errors = (p.stderr or p.stdout or "").strip()
        die("中文 AI 腔检测无法运行：%s" % errors)
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"findings": [], "error": "输出解析失败"}


def run_platform(book):
    """调用 check-platform-compliance.py 做平台合规检测（meta 混入 + 导流）。

    gate 的最后一道闸：管的是「发出去会不会被平台拒/限流」，与
    lint（像不像 AI）、crossrepeat/fact（是否一致）、spec（规格达标）
    完全不同轴。blocking 级（meta 泄漏、链接、导流组合）计入 errors
    阻断交付；warn 级（都市文合法词、虚构手机号）只提示人工确认——
    平台机审不区分引号内外，但人对情节合法性的判断机器替代不了。
    """
    if not book_has_prose(book):
        return {"findings": [], "blocking": 0, "warns": 0, "skipped": "no-prose-dir"}
    cmd = [sys.executable, os.path.join(CWD, "check-platform-compliance.py"),
           "--book", book, "--json"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        die("找不到 python，无法执行平台合规检测")
    if p.returncode == 2:
        errors = (p.stderr or p.stdout or "").strip()
        die("平台合规检测无法运行：%s" % errors)
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"findings": [], "blocking": 0, "warns": 0, "error": "输出解析失败"}


def cmd_gate(args):
    """细纲门禁：复刻 oh-story 那个阻断式 hook 的语义。

    原本由 guard-outline-before-prose.sh 拦截；WorkBuddy 无 hook 系统，
    此子命令把同一规则做成显式调用。
    """
    book = args.book
    if not os.path.isdir(book):
        die("书目录不存在：%s" % book)

    errors, warns = [], []

    # 追踪状态必须存在
    state = os.path.join(book, "追踪", "_tracking-state.json")
    if not os.path.exists(state):
        errors.append("追踪状态缺失：追踪/_tracking-state.json 不存在。长篇不允许凭记忆续写，先建追踪。")
        tracking_ok = False
    else:
        tracking_ok = True

    # 细纲必须存在（正文的前置门禁）
    outline = find_chapter_file(book, args.chapter)
    outline = [p for p in outline if "细纲" in p]
    if not outline:
        errors.append(
            "第 %d 章细纲缺失。这是硬门禁：没有细纲不许写正文（oh-story 原阻断式 hook 的语义）。"
            "先按 references/opening-surgery.md 与 SKILL.md 第二节「流水线」第 6 步写出本章细纲，"
            "文件名建议 正文/第%03d章细纲.md，内容需含「字数范围」行与情节点。" % (args.chapter, args.chapter)
        )
        outline_ok = False
    else:
        outline_ok = True
        text = read(outline[0])
        # 细纲需含字数目标，否则字数检查无基准
        if not re.search(r"字数(范围|目标)", text):
            warns.append("细纲未写「字数范围」行，字数检查将回退到默认带（±15%）。")

    # 连续性风险：未解决的伏笔不得悬空到下一章开头
    ctx = os.path.join(book, "追踪", "上下文.md")
    if tracking_ok and os.path.exists(ctx):
        ctext = read(ctx)
        if "连贯性风险" in ctext:
            risk_block = ctext.split("连贯性风险", 1)[1]
            if "未" in risk_block or "不得" in risk_block or "先" in risk_block:
                warns.append("上下文卡存在未解除的连贯性风险，开写前先读「连贯性风险」段确认。")

    # 正文全检（可选）
    # v2.1.0 --stage 取值扩展为 outline|draft|final|all：
    #   outline —— 只查细纲（新书第一次门禁）
    #   draft   —— 查 正文/draft/ 层（写正文阶段自查）
    #   final   —— 查 正文/final/ 层（定稿层交付前）
    #   all     —— 同 draft（兼容旧脚本调用），正文层默认 draft
    # 若显式给 --layer draft|final，则覆盖上面推断出的层。
    lint_result = None
    spec_result = None
    need_body = args.stage in ("all", "draft", "final")
    if need_body:
        layer = getattr(args, "layer", None)
        if not layer:
            layer = "final" if args.stage == "final" else "draft"
        body = [p for p in find_chapter_file(book, args.chapter, layer=layer)]
        body = [p for p in body if "正文" in p] if body else []
        if not body:
            errors.append("第 %d 章正文不存在（层=%s），无法执行 %s 阶段门禁。" % (args.chapter, layer, args.stage))
        else:
            lint_result = run_lint([body[0]])
            if lint_result["blocking"] > 0:
                errors.append("正文含 %d 处 blocking AI 味命中，必须改到 0。" % lint_result["blocking"])
            spec_result = run_spec(body[0], args.chapter)
            for msg in spec_result["errors"]:
                errors.append("硬规格｜%s" % msg)

    # 跨章复述 + 事实冲突：全书级检查，与上面的单章检查性质不同。
    # 两项都可--skip 跳过（长篇调试期允许），但默认执行——
    # 它们是当前唯一能拦住「AI 复述前文」与「死人复活」的两道闸。
    cross_result = None
    fact_result = None
    if not getattr(args, "skip_cross", False):
        cross_result = run_crossrepeat(book)
        for f in cross_result.get("findings", []):
            errors.append(
                "跨章复述｜第 %d 章 复述了 第 %d 章（%s，复述 %d 段，占该章 %.0f%%）"
                % (f["chapter_b"], f["chapter_a"], f["kind"],
                   f["flagged_segments"], f["mass_ratio"] * 100)
            )
    if not getattr(args, "skip_fact", False):
        fact_result = run_factconflicts(book)
        for f in fact_result.get("findings", []):
            errors.append("事实冲突｜%s（%s）：%s" % (f["who"], f["rule"], f["detail"]))
    # 中文 AI 腔密度（advisory 层，不阻断——密度超标是提示不是禁止）
    tone_result = None
    if not getattr(args, "skip_tone", False):
        tone_result = run_chinese_tone(book)

    # 平台合规（gate 最后一道闸）：blocking 级阻断，warn 级人工确认
    platform_result = None
    if not getattr(args, "skip_platform", False):
        platform_result = run_platform(book)
        for f in platform_result.get("findings", []):
            if f.get("severity") == "blocking":
                where = ("第%s章 " % f["chapter"]) if "chapter" in f else ""
                errors.append(
                    "平台合规｜%sL%s %s：%s「%s」"
                    % (where, f.get("line", "?"), f["rule"], f["detail"], f.get("snippet", ""))
                )

    # ---- 输出
    print("=" * 64)
    print("novel-forge 门禁 ｜ 书=%s ｜ 第 %d 章 ｜ 阶段=%s" % (os.path.basename(book.rstrip("/\\")), args.chapter, args.stage))
    print("=" * 64)
    print("  追踪状态      : %s" % ("有" if tracking_ok else "缺失"))
    print("  细纲          : %s" % (os.path.basename(outline[0]) if outline_ok else "缺失"))
    if lint_result is not None:
        print("  正文 lint     : blocking %d / advisory %d" % (lint_result["blocking"], lint_result["advisory"]))
    if spec_result is not None:
        st = spec_result["stats"]
        print("  硬规格        : 字数 %s ｜ 对话 %s ｜ 爽点 %s ｜ 钩子 %s ｜ 感官 %s ｜ 回报 %s ｜ 温度 %s"
              % (st.get("字数", "-"), st.get("对话占比", "-"), st.get("爽点信号", "-"),
                 st.get("章末钩子型号", "-"), st.get("感官", "-"), st.get("三重回报", "-"),
                 st.get("情绪温度", "-")))
    if cross_result is not None:
        if cross_result.get("skipped"):
            print("  跨章复述      : 跳过（无正文目录，outline 阶段正常）")
        else:
            print("  跨章复述      : %s"
                  % ("检出 %d 组" % len(cross_result.get("findings", [])) if cross_result.get("findings")
                     else "未检出"))
    if fact_result is not None:
        if fact_result.get("skipped"):
            print("  事实冲突      : 跳过（无正文目录，outline 阶段正常）")
        else:
            print("  事实冲突      : %s"
                  % ("检出 %d 处" % len(fact_result.get("findings", [])) if fact_result.get("findings")
                     else "未检出"))
    if tone_result is not None:
        if tone_result.get("skipped"):
            print("  中文AI腔      : 跳过（无正文目录，outline 阶段正常）")
        else:
            tf = tone_result.get("findings", [])
            if tf:
                print("  中文AI腔      : %d 项密度超标（advisory）｜ %s"
                      % (len(tf), "、".join(f["rule"] for f in tf[:3])
                         + ("…" if len(tf) > 3 else "")))
            else:
                print("  中文AI腔      : 未超标")
    if platform_result is not None:
        if platform_result.get("skipped"):
            print("  平台合规      : 跳过（无正文目录，outline 阶段正常）")
        else:
            pb, pw = platform_result.get("blocking", 0), platform_result.get("warns", 0)
            print("  平台合规      : blocking %d ｜ warn %d" % (pb, pw))
    for w in warns:
        print("  [提示] %s" % w)
    if spec_result:
        for w in spec_result["warns"]:
            print("  [提示] 硬规格｜%s" % w)
    if tone_result and tone_result.get("findings"):
        for f in tone_result["findings"]:
            unit = f.get("unit", "每千字")
            print("  [AI腔提示] 第%s章 %s：%d 处（%s%s，阈值 %s%s，R=%.1f）——advisory，交付前读一读"
                  % (f.get("chapter", "?"), f["rule"], f["count"],
                     f["per_kchars"], unit, f["threshold"], unit, f["r_value"]))
    if platform_result and platform_result.get("findings"):
        wf = [f for f in platform_result["findings"] if f.get("severity") == "warn"]
        # html_comment 是章级汇总（每章一条），50 章的书会刷 50 行——
        # 聚合成一条；其余 warn 保留行号逐条列出。
        n_comment = sum(1 for f in wf if f["rule"] == "html_comment")
        if n_comment:
            print("  [平台确认] %d 章存在 HTML 注释（钩子型号等合法工作标记）——导出发布前统一剥离"
                  % n_comment)
        for f in wf:
            if f["rule"] == "html_comment":
                continue
            where = ("第%s章 " % f["chapter"]) if "chapter" in f else ""
            print("  [平台确认] %sL%s %s：%s"
                  % (where, f.get("line", "?"), f["rule"], f["detail"]))
    if errors:
        print()
        for e in errors:
            print("  [阻断] %s" % e)
        print("-" * 64)
        print("门禁未通过：存在 %d 项硬错误，禁止写正文/ 交付。" % len(errors))
        return 1
    print("-" * 64)
    print("门禁通过：可以进入正文写作。")
    # advisory 挂账视图（ledger.py 只读，不参与退出码）：故意违背 advisory 时
    # 许下的「以后补」承诺在这里显示，超期标 ⚠。台账缺失/损坏都不影响门禁。
    try:
        import ledger as _ledger
        _ledger.main(["--project", book, "check", "--at", str(args.chapter)])
    except SystemExit:
        pass
    except Exception:
        pass
    return 0


def run_lint(files):
    """调用 vendored node lint，统计 blocking / advisory。"""
    cmd = ["node", os.path.join(CWD, "check-ai-patterns.js"), "--json",
           "--fail-on=all"] + files
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        die("未找到 node，请先安装 Node.js（去AI味 lint 依赖 node 运行）")
    try:
        data = json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"blocking": -1, "advisory": -1, "raw": p.stdout + p.stderr}
    fs = data.get("findings", [])
    return {
        "blocking": sum(1 for f in fs if f.get("severity") == "blocking"),
        "advisory": sum(1 for f in fs if f.get("severity") == "advisory"),
        "findings": fs,
    }


def run_platform_file(path):
    """单文件调用平台合规检测（selftest 用）。"""
    cmd = [sys.executable, os.path.join(CWD, "check-platform-compliance.py"),
           "--file", path, "--json"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"findings": [], "blocking": -1, "warns": -1}


def run_dist_file(path):
    """单文件调用分布级检测（selftest 用）。"""
    cmd = [sys.executable, os.path.join(CWD, "check-distribution.py"),
           "--file", path, "--json"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"advisories": []}


def run_spec_file(path, prev=None, chapter=None):
    """单文件调用硬规格检测（selftest 用）。"""
    cmd = [sys.executable, os.path.join(CWD, "spec_check.py"),
           "--file", path, "--json"]
    if prev:
        cmd += ["--prev", prev]
    if chapter:
        cmd += ["--chapter", str(chapter)]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"errors": [], "warns": [], "stats": {}}


# ---------------------------------------------------------------- hitrate

SCORE_RE = re.compile(r"([D]\d{1,2})\s*[^0-9]{0,20}?(\d+)\s*(?:/|／)\s*(\d+)")

# 「理由」列的最低要求：必须出现可核查的具体物。纯主观形容词一律不接受。
# 依据：hit-formula.md 第四节打分纪律第1 条「理由指向具体文本或设定，禁止感觉不错」。
# 旧实现是一个 pass 空循环，等于这条纪律完全没生效——模型给自己打高分、
# 理由写「感觉不错」照样放行。现改为实证校验。
EVIDENCE_PATTERNS = [
    re.compile(r"[「」“”\"'『』]"),          # 引了具体设定/台词
    re.compile(r"第\s*\d+\s*[章节卷]"),          # 指向具体位置
    re.compile(r"\d+\s*[字页%％]"),               # 指向具体数量
    re.compile(r"[A-Za-z]{2,}"),                # 指向具体名词/术语
    re.compile(r"[一-鿿]{2,}(?:流|格|型|线|牌|设定|机制|桥段|结构|模板|律|法|规)"),
    re.compile(r"(?:genre-radar|hit-formula|references|SKILL)"),
    re.compile(r"[≥≤><]=?\s*\d"),# 指向可比较的阈值
    # 具体设定名：中文专名/复合词（如「合同猎手」「签到系统」「重生」）。
    # 理由里出现具体名词即算有据——主观套话已在上面整句剔除，
    # 剩下的都是「这个项目具体是什么」的陈述。
    re.compile(r"[一-鿿]{2,}"),                  # 任意 2 字以上中文实词
]

# 主观套话：出现即视为「没给证据」，无论前后有什么词。
VAGUE_ONLY = {
    "感觉不错", "感觉良好", "感觉还行", "还可以", "不错", "良好", "较好", "一般",
    "有潜力", "挺好的", "还行吧", "看情况", "不确定", "凭感觉", "直觉",
    "很有", "很棒", "很好", "强", "弱", "高", "低", "中",
}


def has_evidence(reason):
    """理由是否含可核查的具体物。返回 (通过?, 命中的证据类型描述)。"""
    text = reason.strip()
    if not text:
        return False, "理由为空"
    # 先剔除所有主观套话；剩余部分若为空，说明整句都是套话
    stripped = text
    for phrase in VAGUE_ONLY:
        stripped = stripped.replace(phrase, "")
    if not re.sub(r"[\s，。、；：,.;:\-—（）()]+", "", stripped):
        return False, "整句只有主观套话，无具体设定/文本/数据支撑"
    for pat in EVIDENCE_PATTERNS:
        if pat.search(text):
            return True, ""
    return False, "理由未指向任何具体设定、文本或数据（需含具体名词/章号/数值/公式名）"


def parse_reason(cells, dim_key):
    """从表格行里取理由列。

    表格形如`| D4 开局冲突强度 | 9 | 9 | 理由文本 | — |`（末列常是空占位）。
    规则：跳过纯数字格（权重/得分/满分）与占位符（—/-/空），取剩下的最后一格。
    """
    try:
        start = next(i for i, c in enumerate(cells) if re.match(r"^\**\s*%s\b" % dim_key, c))
    except StopIteration:
        return ""
    placeholders = {"", "-", "—", "——", "/", "·", "…"}
    candidates = []
    for c in cells[start + 1:]:
        c = c.strip().strip("*").strip()
        if re.fullmatch(r"\d+", c):        # 权重/得分/满分列
            continue
        if c in placeholders:             # 空占位列
            continue
        candidates.append(c)
    return candidates[-1] if candidates else ""


def parse_total(text):
    """解析「合计」行的总分。

    旧实现用固定列序正则抓 `| **合计** | **N** |`，命中的是**权重**列（100）
    而不是得分列——结果作者写了 68 分也会被读成 100 分放行，「总分<75 禁止写正文」
    这条铁律实际失效。现改为按表格实际列位置取第 N 列。
    """
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        if not re.match(r"^\**\s*合计\s*\**$", cells[0]):
            continue
        # 取最后一个纯数字格= 得分列（权重列在前、满分列在后）
        nums = []
        for c in cells[1:]:
            m = re.fullmatch(r"\*?\*?(\d+)\*?\*?", c.strip())
            if m:
                nums.append(int(m.group(1)))
        if not nums:
            continue
        # 两列（得分/满分）或三列（权重/得分/满分）：取倒数第二个
        return nums[-2] if len(nums) >= 2 else nums[0]
    return None


def cmd_hitrate(args):
    text = read(args.file)

    # 抓「D4 开局冲突强度 | 9 | 9 | 理由」这类表格行，取第 2、3 列为 得分/满分
    scores = {}
    reasons = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        m = re.match(r"^\**\s*(D\d{1,2})\b", cells[0])
        if not m:
            continue
        try:
            got, full = int(re.sub(r"\D", "", cells[1]) or -1), int(re.sub(r"\D", "", cells[2]) or -1)
        except ValueError:
            continue
        if got < 0 or full <= 0:
            continue
        scores[m.group(1)] = (got, full)
        reasons[m.group(1)] = parse_reason(cells, m.group(1))

    # 补齐维度名
    byname = {}
    for name, full in HIT_DIMENSIONS.items():
        key = name.split()[0]
        if key in scores:
            got, declared = scores[key]
            byname[name] = (min(got, declared if declared > 0 else got), full)

    errors, warns = [], []
    missing = [n for n in HIT_DIMENSIONS if n not in byname]
    if missing:
        errors.append("命中卡缺少维度：%s" % "、".join(missing))

    total = sum(g for g, _ in byname.values()) if byname else 0

    # 打分纪律第 1 条：每维必须给可核查的理由（机器实证，不再是空循环）
    weak_reasons = []
    for name in HIT_DIMENSIONS:
        key = name.split()[0]
        if key not in scores:
            continue
        ok, why = has_evidence(reasons.get(key, ""))
        if not ok:
            weak_reasons.append("%s（%s）" % (name, why))
    if weak_reasons:
        errors.append(
            "打分纪律第 1 条未满足：%d 维理由无具体依据——%s。"
            "每维理由必须指向具体设定/章号/数值/公式名，不许写「感觉不错」。"
            % (len(weak_reasons), "；".join(weak_reasons))
        )

    # 合计行：按实际列位解析得分列（不再用固定列序抓错权重列）
    declared_total = parse_total(text)
    full_marks = [n for n, (g, f) in byname.items() if g == f and f > 0]

    # 否决项
    for d in HIT_VETO:
        if d in byname and byname[d][0] == 0:
            errors.append("否决项 %s = 0，总分再高也不得开工。" % d)

    # 天花板项：任一 ≤2 锁总分上限 74
    capped = False
    for d in HIT_CEILING:
        if d in byname and byname[d][0] <= 2:
            capped = True
            warns.append("天花板项 %s ≤2，总分上限锁定 74。" % d)

    # 打分纪律：满分维度不得超过 3 项。未在合计行下调则拦截
    if len(full_marks) > MAX_FULL_MARKS:
        msg = ("打分虚高：满分维度共 %d 项（%s），超过 %d 项上限。"
               "须在「合计」行之前把超出项各下调一档，或在合计行写入修正后的总分。"
               % (len(full_marks), "、".join(full_marks), MAX_FULL_MARKS))
        if declared_total and declared_total < total:
            warns.append(msg + "已检测到合计行声明 %d < 逐维求和 %d，按声明分计。" % (declared_total, total))
        else:
            errors.append(msg + "当前无修正声明，判定不合规。")

    # 理由实证校验已在上方完成（原此处是一个 pass 空循环，等于纪律没生效）

    print("=" * 64)
    print("novel-forge 15 维爆款命中率复核")
    print("=" * 64)
    print("  识别维度 : %d / 15" % len(byname))
    print("  逐维求和 : %d / 100%s" % (total, "（表头声明 %d，以声明为准）" % declared_total if declared_total else ""))
    effective_total = declared_total if declared_total else total
    for n in byname:
        g, f = byname[n]
        flag = ""
        if n in HIT_VETO:
            flag = "  ← 否决项"
        if n in HIT_CEILING:
            flag += "  ← 天花板项"
        print("    %-28s %d/%d%s" % (n, g, f, flag))
    if capped:
        print("  锁定总分 : 74（天花板项触发）")
    print("  满分项数 : %d %s" % (len(full_marks), "（合规）" if len(full_marks) <= MAX_FULL_MARKS else "（超限，须下调）"))
    print("  达标线: %d" % HIT_PASS)
    print()
    for w in warns:
        print("  [提示] %s" % w)
    for e in errors:
        print("  [阻断] %s" % e)

    effective = 74 if capped else effective_total
    if errors:
        print("-" * 64)
        print("复核未通过：%d 项硬错误。" % len(errors))
        return 1
    if effective < HIT_PASS:
        print("-" * 64)
        print("复核未通过：总分 %d < %d，禁止进入写作环节。" % (effective, HIT_PASS))
        return 1
    print("-" * 64)
    print("复核通过：总分 %d ≥ %d，可以开写。" % (effective, HIT_PASS))
    return 0


# ---------------------------------------------------------------- truth

TRUTH_FILE = ("追踪/时间线/作者真相.md")
KNOWN_FILE = ("追踪/时间线/读者已知.md")


def cmd_truth(args):
    """双时间线一致性校验。

    这是 novel-forge 的原创机制：防止「角色提前知道尚未揭示的真相」
    与「未揭示真相被写成已揭示」两类吃书事故。
    """
    book = args.book
    truth_path = os.path.join(book, TRUTH_FILE)
    known_path = os.path.join(book, KNOWN_FILE)
    for p in (truth_path, known_path):
        if not os.path.exists(p):
            die("双时间线文件缺失：%s。先建追踪。" % os.path.relpath(p, book))

    errors, warns = [], []
    truth = read(truth_path)
    known = read(known_path)

    # 解析作者真相表：| E001 | 第20章 | 时间 | 事实 | 读者认知 | 已揭示/未揭示 | 揭示章 |
    eid_pattern = re.compile(r"\|\s*(E\d{3})\s*\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|")
    truth_rows = {}
    for m in eid_pattern.finditer(truth):
        eid, first, _, fact, reader_knows, status, revealed = [x.strip() for x in m.groups()]
        truth_rows[eid] = {"status": status, "revealed": revealed, "first": first, "fact": fact}

    if not truth_rows:
        errors.append("作者真相表未解析到任何 E 编号行，请检查表格格式（需 7 列：ID/首次登记章/故事时间/客观事实/读者当前认知/揭示状态/实际揭示章）。")

    # 规则 1：未揭示的真相，其「读者当前认知」不得为空、不得泄露事实片段
    for m in eid_pattern.finditer(truth):
        eid, first, story_time, fact, reader_knows, status, revealed = [x.strip() for x in m.groups()]
        if "未揭示" not in status:
            continue
        if not reader_knows or reader_knows in ("-", "—"):
            errors.append("%s 标为未揭示，但「读者当前认知」为空——揭示前必须写明读者知道多少。" % eid)
            continue
        fact_core = fact.replace("候选真相：", "")
        if len(fact_core) > 12:
            head = fact_core[:12]
            if head and head in reader_knows:
                warns.append("%s 未揭示，但读者认知里出现事实片段「%s」——检查是否提前泄底。" % (eid, head))
        if "未揭示" in status:
            # 未揭示行的「实际揭示章」列只能是空或占位符；填了章号/含「已揭示」字样都算自相矛盾
            if revealed and revealed not in ("-", "—", "—", "未揭示", "待定"):
                errors.append(
                    "%s 状态为未揭示，却填了实际揭示章「%s」，两者必须一致——未揭示的行应留空或写「—」。"
                    % (eid, revealed)
                )
        elif "已揭示" in status:
            if not revealed or revealed in ("-", "—", "待定"):
                warns.append("%s 标记已揭示但未填实际揭示章，续写时无法确认读者读到哪一章。" % eid)

    # 规则 2：读者已知表不得出现未揭示的 E 编号
    known_ids = set(re.findall(r"\|\s*(E\d{3})\s*\|", known))
    for eid in sorted(known_ids):
        row = truth_rows.get(eid)
        if row and "未揭示" in row["status"]:
            errors.append("%s 在作者真相里是未揭示状态，却已登记进读者已知表——提前泄底。" % eid)

    # 规则 3：已揭示项必须在读者已知表里有对应认知
    for eid, row in sorted(truth_rows.items()):
        if "已揭示" in row["status"] and eid not in known_ids:
            warns.append("%s 已揭示但读者已知表无对应条目，读者可能漏读了关键信息。" % eid)

    print("=" * 64)
    print("novel-forge 双时间线一致性校验 ｜ 书=%s" % os.path.basename(book.rstrip("/\\")))
    print("=" * 64)
    print("  作者真相条目 : %d" % len(truth_rows))
    unrevealed = [e for e, r in truth_rows.items() if "未揭示" in r["status"]]
    print("  未揭示条目   : %d%s" % (len(unrevealed), ("（%s）" % "、".join(unrevealed)) if unrevealed else ""))
    print("  读者已知条目 : %d" % len(known_ids))
    print()
    for w in warns:
        print("  [提示] %s" % w)
    for e in errors:
        print("  [阻断] %s" % e)
    if errors:
        print("-" * 64)
        print("一致性校验未通过：%d 项硬错误。" % len(errors))
        return 1
    print("-" * 64)
    print("一致性校验通过：作者真相与读者已知无冲突。")
    return 0


# ---------------------------------------------------------------- wordcount

def cmd_wordcount(args):
    text = read(args.file)
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    en = len(re.findall(r"[A-Za-z]+", text))
    actual = cjk + en
    target = args.target
    band = target * 0.15
    lo, hi = target - band, target + band
    print("=" * 64)
    print("novel-forge 字数口径检查")
    print("=" * 64)
    print("  文件     : %s" % os.path.basename(args.file))
    print("  目标字数 : %d (±15%% → %d–%d)" % (target, int(lo), int(hi)))
    print("  实测字数 : %d" % actual)
    if actual < lo:
        print("  [提示] 欠字 %d 字。按 oh-story 口径欠字不加戏，交作者决定是否补。" % int(lo - actual))
        return 0
    if actual > hi:
        print("  [阻断] 超字 %d 字。超字最多压缩一次，不得靠注水达标。" % int(actual - hi))
        return 1
    print("-" * 64)
    print("字数达标。")
    return 0


def cmd_export(args):
    """委派给 export.py（独立可跑，此处仅作统一入口转发）。"""
    import subprocess as _sp
    script = os.path.join(CWD, "export.py")
    if not os.path.isfile(script):
        print("[导出] 找不到 export.py：%s" % script, file=sys.stderr)
        return 2
    cmd = [sys.executable, script]
    if args.book:
        cmd += ["--book", args.book]
    if args.file:
        cmd += ["--file", args.file]
    if args.chapter is not None:
        cmd += ["--chapter", str(args.chapter)]
    if args.out:
        cmd += ["--out", args.out]
    if args.check_only:
        cmd += ["--check-only"]
    return _sp.run(cmd).returncode


def cmd_dist(args):
    """委派给 check-distribution.py。全部 advisory，退出码恒 0。"""
    import subprocess as _sp
    script = os.path.join(CWD, "check-distribution.py")
    if not os.path.isfile(script):
        print("[分布] 找不到 check-distribution.py", file=sys.stderr)
        return 2
    cmd = [sys.executable, script]
    if args.file:
        cmd += ["--file", args.file]
    if args.book:
        cmd += ["--book", args.book]
        # 基线自动解析（v2.2）：本书自带文风基线（import_manuscript / analyze_book
        # 产出）优先于通用人类基线——「像不像这一本」比「像不像人类」灵敏。
        if not args.baseline:
            own = os.path.join(args.book, "文风基线.json")
            if os.path.isfile(own):
                cmd += ["--baseline", own]
    if args.chapter is not None:
        cmd += ["--chapter", str(args.chapter)]
    if args.baseline:
        cmd += ["--baseline", args.baseline]
    if args.json:
        cmd += ["--json"]
    return _sp.run(cmd).returncode


def cmd_diversity(args):
    """委派给 check-cross-diversity.py。全部 advisory，退出码恒 0。"""
    import subprocess as _sp
    script = os.path.join(CWD, "check-cross-diversity.py")
    if not os.path.isfile(script):
        print("[多样性] 找不到 check-cross-diversity.py", file=sys.stderr)
        return 2
    cmd = [sys.executable, script, "--book", args.book]
    if args.chapters:
        cmd += ["--chapters", args.chapters]
    if args.json:
        cmd += ["--json"]
    return _sp.run(cmd).returncode


# ---------------------------------------------------------------- selftest

def cmd_selftest(args):
    import shutil
    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_selftest")
    if os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    cases = []
    try:
        _run_selftest_cases(tmp, cases)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 64)
    print("novel-forge 脚本自检")
    print("=" * 64)
    ok = True
    for name, passed, extra in cases:
        print("  [%s] %s%s" % ("通过" if passed else "失败", name, "" if passed else " (exit=%s)" % extra))
        ok = ok and passed
    print("-" * 64)
    print("自检%s" % ("全部通过" if ok else "存在失败用例"))
    return 0 if ok else 1


def _run_selftest_cases(tmp, cases):

    # 用例 1a：满分全给（虚高）→ 应被纪律拦截
    inflated = os.path.join(tmp, "hit_inflated.md")
    with open(inflated, "w", encoding="utf-8") as f:
        rows = ["# 命中卡", "", "| 维度 | 得分 | 满分 | 理由 |", "|---|---|---|---|"]
        for name, full in HIT_DIMENSIONS.items():
            rows.append("| %s | %d | %d | 理由 | — |" % (name, full, full))
        rows.append("| **合计** | **100** | **100** | |")
        f.write("\n".join(rows))
    e_infl = cmd_hitrate(argparse.Namespace(file=inflated))
    cases.append(("用例1a 满分虚高（15项满）被纪律拦截", e_infl == 1, e_infl))

    # 用例 1b：合规命中卡（满分≤3 项 + 合计行声明修正分）→ 应通过
    good = os.path.join(tmp, "hit_good.md")
    with open(good, "w", encoding="utf-8") as f:
        rows = ["# 命中卡", "", "| 维度 | 得分 | 满分 | 理由 |", "|---|---|---|---|"]
        # 刻意让前 3 项满分，其余给 ~0.85，合计行写修正后的分（保证 ≥75 且总分 ≥ 门槛）
        dims = list(HIT_DIMENSIONS.items())
        total = 0
        for i, (name, full) in enumerate(dims):
            got = full if i < 3 else max(1, int(full * 0.85))
            total += got
            rows.append("| %s | %d | %d | 依据 genre-radar 第%d 节与references/hit-formula.md | — |" % (name, got, full, i + 1))
        rows.append("| **合计** | **%d** | **100** | |" % total)
        f.write("\n".join(rows))
    e = cmd_hitrate(argparse.Namespace(file=good))
    cases.append(("用例1b 合规命中卡（满分≤3项且≥门槛）通过", e == 0, e))

    # 用例 1c：合计行声明低于逐维求和 → 以声明为准（防虚高）
    declared = os.path.join(tmp, "hit_declared.md")
    with open(declared, "w", encoding="utf-8") as f:
        rows = ["# 命中卡", "", "| 维度 | 得分 | 满分 | 理由 |", "|---|---|---|---|"]
        dims = list(HIT_DIMENSIONS.items())
        total = 0
        for i, (name, full) in enumerate(dims):
            got = full if i < 3 else max(1, int(full * 0.7))
            total += got
            rows.append("| %s | %d | %d | 依据 genre-radar 第%d 节与references/hit-formula.md | — |" % (name, got, full, i + 1))
        rows.append("| **合计** | **80** | **100** | |")  # 故意低于求和
        f.write("\n".join(rows))
    e_decl = cmd_hitrate(argparse.Namespace(file=declared))
    cases.append(("用例1c 合计行声明分低于求和时以声明为准", e_decl == 0, e_decl))

    # 用例 1d：套话理由（2026-10 回归）—— 曾经「理由必须指向具体设定」是pass 空循环，
    # 写「感觉不错」也能过；现由 has_evidence 实证拦截。
    vague = os.path.join(tmp, "hit_vague.md")
    with open(vague, "w", encoding="utf-8") as f:
        rows = ["# 命中卡", "", "| 维度 | 得分 | 满分 | 理由 |", "|---|---|---|---|"]
        dims = list(HIT_DIMENSIONS.items())
        for i, (name, full) in enumerate(dims):
            got = full if i < 3 else max(1, int(full * 0.85))
            rows.append("| %s | %d | %d | %s | — |" % (name, got, full,
                        ["感觉不错", "挺好的", "还可以", "很强", "还行"][i % 5]))
        rows.append("| **合计** | **85** | **100** | |")
        f.write("\n".join(rows))
    e_vague = cmd_hitrate(argparse.Namespace(file=vague))
    cases.append(("用例1d 套话理由被实证校验拦截", e_vague == 1, e_vague))

    # 用例 1e：合计行得分列取错列（2026-10 回归）—— 旧实现用固定列序抓合计行，
    # 命中的是权重列100 而非得分列，导致作者写 68 分也被当100 分放行。
    low = os.path.join(tmp, "hit_lowtotal.md")
    with open(low, "w", encoding="utf-8") as f:
        rows = ["# 命中卡", "", "| 维度 | 权重 | 得分 | 满分 | 理由 |", "|---|---|---|---|---|"]
        dims = list(HIT_DIMENSIONS.items())
        for i, (name, full) in enumerate(dims):
            got = max(1, int(full * 0.45))
            rows.append("| %s | %d | %d | %d | 依据 references/hit-formula.md |" % (name, full, got, full))
        rows.append("| **合计** | **100** | **%d** | **100** | |" % sum(
            max(1, int(full * 0.45)) for _, full in dims))
        f.write("\n".join(rows))
    e_low = cmd_hitrate(argparse.Namespace(file=low))
    cases.append(("用例1e 合计行得分列取对列（低分卡被拦截）", e_low == 1, e_low))

    # 用例 2：否决项 D4=0 被拦截
    bad = os.path.join(tmp, "hit_veto.md")
    with open(bad, "w", encoding="utf-8") as f:
        rows = ["# 命中卡", ""]
        for name, full in HIT_DIMENSIONS.items():
            got = 0 if name.startswith("D4") else full
            rows.append("| %s | %d | %d | 依据 genre-radar 第%d 节与references/hit-formula.md | — |" % (name, got, full, i + 1))
        f.write("\n".join(rows))
    e2 = cmd_hitrate(argparse.Namespace(file=bad))
    cases.append(("用例2 否决项 D4=0 被拦截", e2 == 1, e2))

    # 用例 3：字数欠字/超字
    wc = os.path.join(tmp, "wc.md")
    with open(wc, "w", encoding="utf-8") as f:
        f.write("测试文本。" * 10)
    e3 = cmd_wordcount(argparse.Namespace(file=wc, target=200))
    cases.append(("用例3 字数达标", e3 == 0, e3))

    # 用例 4：超字拦截
    with open(wc, "w", encoding="utf-8") as f:
        f.write("测试文本。" * 200)
    e4 = cmd_wordcount(argparse.Namespace(file=wc, target=100))
    cases.append(("用例4 超字被拦截", e4 == 1, e4))

    # 用例 5：lint 可运行且能检出 blocking
    sample = os.path.join(tmp, "ai.md")
    with open(sample, "w", encoding="utf-8") as f:
        f.write("不是愤怒，而是更深的东西。\n")
    lr = run_lint([sample])
    cases.append(("用例5 lint 运行且检出 AI 味", lr["blocking"] >= 1, lr["blocking"]))

    # 用例 5b：工程引用规则的真残留必报（半角数字+空格形态，旧全角规则会漏）
    p_res = os.path.join(tmp, "residual.md")
    with open(p_res, "w", encoding="utf-8") as f:
        f.write("他又想起第 5 章在食堂那回，胃里发紧。\n")
    lr_res = run_lint([p_res])
    cases.append(("用例5b 工程引用残留（第 5 章）被拦截", lr_res["blocking"] >= 1, lr_res["blocking"]))

    # 用例 5c：文内合法分章引用（书名号/载体词）必须豁免，不能误报 blocking
    p_ok = os.path.join(tmp, "inworld.md")
    with open(p_ok, "w", encoding="utf-8") as f:
        f.write("他翻开《指南》第九章，又合上了。\n")
    lr_ok = run_lint([p_ok])
    cases.append(("用例5c 文内《书》第九章不误报工程引用", lr_ok["blocking"] == 0, lr_ok["blocking"]))

    # 用例 6：双时间线泄底拦截
    book = os.path.join(tmp, "book")
    os.makedirs(os.path.join(book, "追踪", "时间线"), exist_ok=True)
    with open(os.path.join(book, "追踪", "_tracking-state.json"), "w", encoding="utf-8") as f:
        f.write('{"schema":"story-tracking-state/v1","state_revision":1}')
    with open(os.path.join(book, "追踪", "时间线", "作者真相.md"), "w", encoding="utf-8") as f:
        f.write("| ID | 首次登记章 | 故事时间 | 客观事实 | 读者当前认知 | 揭示状态 | 实际揭示章 |\n"
                "|---|---:|---|---|---|---:|---:|\n"
                "| E001 | 第1章 | 周一 | 真凶是高凯 | 读者知陆迟被冤枉 | 未揭示 | — |\n")
    with open(os.path.join(book, "追踪", "时间线", "读者已知.md"), "w", encoding="utf-8") as f:
        f.write("| ID | 读者当前认知 | 认知截至章 |\n|---|---|---|\n")
    e6 = cmd_truth(argparse.Namespace(book=book))
    cases.append(("用例6 双时间线无泄底时通过", e6 == 0, e6))
    with open(os.path.join(book, "追踪", "时间线", "读者已知.md"), "w", encoding="utf-8") as f:
        f.write("| ID | 读者当前认知 | 认知截至章 |\n|---|---|---:|\n"
                "| E001 | 读者已知真凶是高凯 | 第1章 |\n")
    e7 = cmd_truth(argparse.Namespace(book=book))
    cases.append(("用例7 未揭示真相泄底被拦截", e7 == 1, e7))

    # 用例 8：平台合规 —— 干净章零误报，dirty 章 blocking 全中（2026-10 新增层）
    plat_clean = os.path.join(tmp, "plat_clean.md")
    with open(plat_clean, "w", encoding="utf-8") as f:
        f.write("# 第001章 测\n\n他说道：\"这事没问题，明天再说。\"\n"
                "陈默看着窗外的雨，心里盘算着下一步。\n")
    pc = run_platform_file(plat_clean)
    cases.append(("用例8a 平台合规：干净章零命中", pc["blocking"] == 0 and pc["warns"] == 0,
                  (pc["blocking"], pc["warns"])))

    plat_dirty = os.path.join(tmp, "plat_dirty.md")
    with open(plat_dirty, "w", encoding="utf-8") as f:
        f.write("# 第002章 测\n\nTODO 这里补一段打斗。\n本章字数：2300\n"
                "作者的话：今天三更。\n爽点：打脸反转\n"
                "想咨询的加微信 abc_12345。\n完整版在网盘链接里自取。\n"
                "详见 https://example.com/book\n")
    pd = run_platform_file(plat_dirty)
    rules = {f["rule"] for f in pd["findings"] if f["severity"] == "blocking"}
    need = {"meta_todo", "meta_wordcount", "meta_authornote", "meta_keyval",
            "divert_contact", "off_platform", "url"}
    cases.append(("用例8b 平台合规：meta混入与导流全命中", need <= rules, need - rules))

    # 用例 8c：剧情合法词不阻断（都市文「微信/淘宝/手机号」只是 warn）
    plat_legal = os.path.join(tmp, "plat_legal.md")
    with open(plat_legal, "w", encoding="utf-8") as f:
        f.write("# 第003章 测\n\n\"加我微信，他把号码发过来了。\"\n"
                "她打开淘宝下了一单。\n他拨通了 13812345678。\n")
    pl = run_platform_file(plat_legal)
    cases.append(("用例8c 平台合规：都市文合法词只warn不阻断", pl["blocking"] == 0,
                  (pl["blocking"], pl["warns"])))

    # 用例 8d：手机号不被误判为导流（2026-10 回归：CONTACT_HANDLE 拼接
    # 未包组时 \| 会把 \d{6,} 拆成独立分支，任何 6 位数字都命中导流）
    has_divert = any(f["rule"] == "divert_contact" for f in pl["findings"])
    cases.append(("用例8d 手机号不误判为导流", not has_divert, has_divert))

    # 用例 8e（2026-10）：对话层导流显式化——引号内命中以 dialogue_divert 上报，
    # 证明「对话层不豁免」真的在跑（平台机审不区分引号内外）
    plat_dlg = os.path.join(tmp, "plat_dlg.md")
    with open(plat_dlg, "w", encoding="utf-8") as f:
        f.write("# 第010章 测\n\n「想看后续的加微信 shanxu8888」老王压低了声音。\n")
    pg = run_platform_file(plat_dlg)
    dlg_rules = {x["rule"] for x in pg["findings"] if x["severity"] == "blocking"}
    cases.append(("用例8e 平台合规：引号内导流命中上报 dialogue_divert",
                  "dialogue_divert" in dlg_rules and pg.get("dialogue_lines", 0) >= 1,
                  (dlg_rules, pg.get("dialogue_lines"))))

    # 用例 8f（2026-10）：分布检测长句/长段盲区——全短句全短段章必须触发
    # 「40 字以上长句」advisory（《燃识》四章实测：分布在短端，旧检测看不到）
    dist_f = os.path.join(tmp, "dist_short.md")
    with open(dist_f, "w", encoding="utf-8") as f:
        f.write("# 第011章 测\n\n" +
                "\n".join("他点点头没说话。窗外风很大，吹得人发冷。" for _ in range(40)) + "\n")
    dj = run_dist_file(dist_f)
    has_long_adv = any("长句" in a for a in dj.get("advisories", []))
    has_longpara_adv = any("长段" in a or "70 字以上段落" in a for a in dj.get("advisories", []))
    cases.append(("用例8f 分布检测：全短句全短段章触发长句/长段 advisory",
                  has_long_adv and has_longpara_adv, dj.get("advisories")))

    # 用例 9（v2.0 更新）：对话占比已降为 advisory —— 高对话章只提示、不阻断
    spec = os.path.join(tmp, "spec_hi_dlg.md")
    with open(spec, "w", encoding="utf-8") as f:
        # 写足 2500+ 字正文，确保不触发字数 blocking，只考察对话占比降级
        f.write("# 第009章 测\n\n" +
                "\n".join("\"%s，你说呢？\"" % ("话" * 40) for _ in range(40)) +
                "\n\n" + "他看着窗外，夜色一寸寸压下来，楼下的路灯次第亮起。\n" * 20 +
                "\n门外忽然传来脚步声。谁？\n<!-- 钩子型号：H1 -->\n")
    dlg = run_spec_file(spec)
    cases.append(("用例9a 对话占比超上限降为 advisory（不阻断、有提示）",
                  dlg.get("blocking_count") == 0 and any("对话占比" in w for w in dlg.get("warns", [])),
                  dlg["stats"].get("对话占比")))

    # 用例 9b：上行趋势提示（历史前半 25% 后半 35%）
    dlg2 = os.path.join(tmp, "spec_trend.md")
    with open(dlg2, "w", encoding="utf-8") as f:
        f.write("# 第013章 测\n\n" +
                "\n".join("\"%s，你说呢？\"" % ("话" * 10) for _ in range(30)) +
                "\n\n" + "他看着窗外。\n" * 12)
    state_path = os.path.join(tmp, "_spec-state.json")
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"dialogue_history": [0.25] * 6 + [0.35] * 6}, f)
    dlg2r = run_spec_file(dlg2, prev=state_path)
    cases.append(("用例9b 对话占比上行趋势触发提示",
                  any("上行趋势" in w for w in dlg2r["warns"]),
                  dlg2r["stats"].get("对话占比")))

    # 用例 9c（v2.0 新增）：blocking / advisory 分离汇报
    cases.append(("用例9c blocking_count / advisory_count 分离汇报",
                  isinstance(dlg.get("blocking_count"), int) and isinstance(dlg.get("advisory_count"), int),
                  "blocking=%s advisory=%s" % (dlg.get("blocking_count"), dlg.get("advisory_count"))))

    # 用例 10a（v2.0 新增）：export 清洗注释/标记/裸声明 -> 干净导出
    import export as _export
    book = os.path.join(tmp, "expbook")
    final_dir = os.path.join(book, "正文", "final")
    os.makedirs(final_dir, exist_ok=True)
    with open(os.path.join(final_dir, "第001章.md"), "w", encoding="utf-8") as f:
        f.write("# 第001章 试探\n[DRAFT]\n\n他推开门。\n\n<!-- 温度：冷\n钩子型号：H1 -->\n\n温度：冷\n爽点：试探\n\n门外停了雨。\n")
    e_exp = _export.run_book(book)
    out_file = os.path.join(book, "导出", "第001章.md")
    ok_exp = (e_exp == 0 and os.path.isfile(out_file))
    body = ""
    if ok_exp:
        with open(out_file, "r", encoding="utf-8") as f:
            body = f.read()
        ok_exp = ("<!--" not in body and "[DRAFT]" not in body
                  and "温度：" not in body and "钩子型号：" not in body
                  and "门外停了雨。" in body and "他推开门。" in body)
    cases.append(("用例10a export 剥离注释/标记/裸声明且保留正文", ok_exp, e_exp))

    # 用例 10b（v2.0 新增）：export 检出残留工程词 -> 非 0 退出
    with open(os.path.join(final_dir, "第002章.md"), "w", encoding="utf-8") as f:
        f.write("# 第002章\n\n正文。\n\n这里残留 agent_created 与 forge.py。\n")
    e_dirty = _export.run_book(book)
    cases.append(("用例10b export 检出工程词残留并退出非0", e_dirty == 1, e_dirty))

    # 用例 11a（v2.0 新增）：分布检测——均匀短句章应触发 advisory，且不阻断
    import importlib.util
    def _load(name, fname):
        spec = importlib.util.spec_from_file_location(name, os.path.join(CWD, fname))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    _dist = _load("check_distribution", "check-distribution.py")
    uniform = os.path.join(tmp, "uniform.md")
    with open(uniform, "w", encoding="utf-8") as f:
        f.write("\n\n".join(["他推开门。", "风很大。", "他很冷。", "他走进去。", "屋里没人。"] * 30))
    dr = _dist.check_distribution(uniform)
    ok_dist = (isinstance(dr, dict) and dr.get("advisory_count", 0) >= 1
               and dr["sentence"]["stdev"] < 5)
    cases.append(("用例11a 分布检测：均匀短句章触发 advisory（不阻断）", ok_dist,
                  dr.get("advisory_count") if isinstance(dr, dict) else "n/a"))

    # 用例 11b（v2.0 新增）：跨章多样性——同型书触发 advisory
    _div = _load("check_cross_diversity", "check-cross-diversity.py")
    samey = os.path.join(tmp, "samey")
    os.makedirs(os.path.join(samey, "正文"), exist_ok=True)
    for i in range(1, 5):
        with open(os.path.join(samey, "正文", "第%03d章.md" % i), "w", encoding="utf-8") as f:
            f.write("他推开门。\n\n他一动不动地站着。\n\n他握紧了拳头，没有动。\n")
    dv = _div.check_diversity(samey)
    ok_div = (isinstance(dv, dict) and dv.get("advisory_count", 0) >= 2)
    cases.append(("用例11b 跨章多样性：同型开头/结尾触发 advisory（不阻断）", ok_div,
                  dv.get("advisory_count") if isinstance(dv, dict) else "n/a"))

    # 用例 12a（v2.1.0 新增）：spec_check 从 meta/第N章.json 读声明
    # —— 方案【五】要求：声明块进 meta/，正文文件不读任何声明块。
    mbook = os.path.join(tmp, "metabook")
    os.makedirs(os.path.join(mbook, "正文", "draft"), exist_ok=True)
    os.makedirs(os.path.join(mbook, "meta"), exist_ok=True)
    ch7 = os.path.join(mbook, "正文", "draft", "第007章.md")
    with open(ch7, "w", encoding="utf-8") as f:
        # 正文里没有任何声明注释；声明全在 meta/
        f.write("# 第007章 落锤\n\n" + "他站在雨里，伸手把那枚硬币按回柜台。\n" * 30 +
                "\n门外忽然响起脚步声。谁？\n")
    with open(os.path.join(mbook, "meta", "第007章.json"), "w", encoding="utf-8") as f:
        json.dump({"chapter": 7, "title": "落锤", "temperature": "热",
                   "satisfaction": "全押落锤买回自己",
                   "returns": ["信息", "情绪", "进展"],
                   "mid_hook": {"position": 0.5, "desc": "当场开罐见门牌"},
                   "end_hook": {"type": "H10", "desc": "雨里伸手"}}, f, ensure_ascii=False)
    meta_res = run_spec_file(ch7, chapter=7)
    ok_meta = (meta_res["stats"].get("章末钩子型号") == "H10（meta）"
               and "meta 声明" in meta_res["stats"].get("情绪温度", ""))
    cases.append(("用例12a spec_check 读 meta/第N章.json 声明（章末钩型/温度）", ok_meta,
                  meta_res["stats"].get("章末钩子型号")))

    # 用例 12b（v2.1.0 新增）：meta/ 缺失时不因缺声明而阻断（advisory 项跳过）
    nombook = os.path.join(tmp, "nometabook")
    os.makedirs(os.path.join(nombook, "正文", "draft"), exist_ok=True)
    nofile = os.path.join(nombook, "正文", "draft", "第004章.md")
    with open(nofile, "w", encoding="utf-8") as f:
        f.write("# 第004章\n\n" + "他看着窗外的雨，慢慢把烟按灭，屋里只剩墙上钟摆的声响。\n" * 90
                + "\n门外忽然传来脚步声。谁？\n")
    no_res = run_spec_file(nofile, chapter=4)
    ok_nometa = (no_res.get("blocking_count") == 0
                 and "无 meta/" in no_res["stats"].get("声明来源", ""))
    cases.append(("用例12b meta/ 缺失时不阻断（声明项跳过、正文照检）", ok_nometa,
                  no_res["stats"].get("声明来源")))

    # 用例 12c（v2.1.0 新增）：find_chapter_file 递归解析 draft/final 两层
    lay = os.path.join(tmp, "layerbook")
    os.makedirs(os.path.join(lay, "正文", "draft"), exist_ok=True)
    os.makedirs(os.path.join(lay, "正文", "final"), exist_ok=True)
    with open(os.path.join(lay, "正文", "draft", "第003章.md"), "w", encoding="utf-8") as f:
        f.write("草稿\n")
    with open(os.path.join(lay, "正文", "final", "第003章.md"), "w", encoding="utf-8") as f:
        f.write("定稿\n")
    d_hit = find_chapter_file(lay, 3, layer="draft")
    f_hit = find_chapter_file(lay, 3, layer="final")
    a_hit = find_chapter_file(lay, 3)
    ok_layer = (len(d_hit) == 1 and d_hit[0].endswith(os.path.join("draft", "第003章.md"))
                and len(f_hit) == 1 and f_hit[0].endswith(os.path.join("final", "第003章.md"))
                and len(a_hit) == 2)
    cases.append(("用例12c find_chapter_file 递归解析 draft/final 两层", ok_layer,
                  "draft=%d final=%d all=%d" % (len(d_hit), len(f_hit), len(a_hit))))

    # 用例 13（2026-10）：可选 LLM 审稿层——无 key 优雅跳过（exit 0 不阻断）、
    # dry-run 只打印 prompt 不联网。红线核验：本层缺席时流水线零感知。
    env_nokey = {k: v for k, v in os.environ.items() if not k.startswith("NOVEL_FORGE_")}
    lr = os.path.join(tmp, "lr_chapter.md")
    with open(lr, "w", encoding="utf-8") as f:
        f.write("# 第012章 测\n\n" + "他推门进来，把伞收好，抖了抖水。\n" * 30)
    p13a = subprocess.run([sys.executable, os.path.join(CWD, "llm_review.py"), "--file", lr],
                          capture_output=True, text=True, encoding="utf-8", env=env_nokey)
    cases.append(("用例13a LLM审稿层：无 key 优雅跳过（exit 0 不阻断）",
                  p13a.returncode == 0 and "默认关闭" in (p13a.stdout or ""),
                  (p13a.returncode, (p13a.stdout or "").splitlines()[:1])))
    p13b = subprocess.run([sys.executable, os.path.join(CWD, "llm_review.py"),
                           "--file", lr, "--dry-run"],
                          capture_output=True, text=True, encoding="utf-8", env=env_nokey)
    cases.append(("用例13b LLM审稿层：dry-run 打印 prompt 不联网",
                  p13b.returncode == 0 and "非功能对话" in (p13b.stdout or ""),
                  (p13b.returncode, (p13b.stdout or "").splitlines()[:1])))


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description="novel-forge 统一命令入口")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gate", help="细纲门禁 + 连续性门禁")
    g.add_argument("--book", required=True)
    g.add_argument("--chapter", type=int, required=True)
    g.add_argument("--stage", default="outline", choices=["outline", "draft", "final", "all"])
    g.add_argument("--layer", default=None, choices=["draft", "final"],
                   help="显式指定检查的正文层（默认：stage=draft/all→draft，stage=final→final）")
    g.add_argument("--skip-cross", action="store_true",
                   help="跳过跨章自我复述检测（调试期可用；正式交付勿开）")
    g.add_argument("--skip-fact", action="store_true",
                   help="跳过事实冲突检测（调试期可用；正式交付勿开）")
    g.add_argument("--skip-tone", action="store_true",
                   help="跳过中文AI腔密度检测（advisory 层，一般无需跳过）")
    g.add_argument("--skip-platform", action="store_true",
                   help="跳过平台合规检测（meta混入+导流；正式交付勿开）")
    g.set_defaults(func=cmd_gate)

    h = sub.add_parser("hitrate", help="15 维爆款命中率复核")
    h.add_argument("--file", required=True)
    h.set_defaults(func=cmd_hitrate)

    t = sub.add_parser("truth", help="作者真相/读者已知双时间线一致性校验")
    t.add_argument("--book", required=True)
    t.set_defaults(func=cmd_truth)

    w = sub.add_parser("wordcount", help="字数口径检查")
    w.add_argument("--file", required=True)
    w.add_argument("--target", type=int, required=True)
    w.set_defaults(func=cmd_wordcount)

    e = sub.add_parser("export", help="导出层：final/ 纯正文 -> 导出/（零元数据、零注释、零工程词）")
    e.add_argument("--book", help="书目录（自动找 正文/final/）")
    e.add_argument("--file", help="单文件模式：源 md 路径")
    e.add_argument("--chapter", type=int, help="只导出指定章号")
    e.add_argument("--out", help="单文件模式的输出路径")
    e.add_argument("--check-only", action="store_true", help="只校验不写文件")
    e.set_defaults(func=cmd_export)

    d = sub.add_parser("dist", help="分布级检测（句长/段长/对话/密度，全部 advisory）")
    d.add_argument("--file", help="正文 md 路径")
    d.add_argument("--book", help="书目录（配合 --chapter）")
    d.add_argument("--chapter", type=int)
    d.add_argument("--baseline", help="人类基线 json（默认 references/human-baseline.json）")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_dist)

    dv = sub.add_parser("diversity", help="跨章多样性检测（开头/结尾/长度/爽点，全部 advisory）")
    dv.add_argument("--book", required=True)
    dv.add_argument("--chapters", help="限定章范围，如 1-30")
    dv.add_argument("--json", action="store_true")
    dv.set_defaults(func=cmd_diversity)

    s = sub.add_parser("selftest", help="脚本自检")
    s.set_defaults(func=cmd_selftest)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()