#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
novel-forge 存量书导入引擎（v2.2）—— 把已完结/连载中的旧稿接进追踪体系。

为什么只回填近 20 章：
  六百章的存量书全量重建台账等于重写一遍书，没有人会做完。
  可行的接法是 Kinema 式冷启动接管：**只精读近 10–20 章**，
  从里面恢复「当前状态」——活跃角色、开放伏笔、双时间线悬置点、
  卷位置。更早的事实一律不回填，它们的价值由 recall.py 的
  正文检索层承接（写前捞前文时按需捞回），不占台账字节。

八步协议（与 .story/import/init-填写说明.md 一一对应）：
  1. 盘点        脚本：扫原稿、章号/断号/字数 → 盘点报告.md
  2. 文风基线    脚本：近 N 章分布 → 文风基线.json（forge.py dist 自动消费）
  3. 回填阅读    模型：读近 N 章（清单见 回填章节清单.md）
  4. 注册伏笔    模型：把开放伏笔写进预填单 foreshadow[]
  5. 角色快照    模型：核心角色当前状态（离场/死亡角色按规则登记）
  6. 时间线      模型：悬置事件登记（objective_fact vs reader_knowledge）
  7. 卷位置      模型：context.position + 最近三章一句话摘要
  8. 提交校验    双方：tracking_commit.py init → import_manuscript.py verify
                 → forge.py truth → recall.py index

用法：
  # 一条龙（盘点+基线+清单+预填单）。--source 缺省时按 final→draft→导出 探测
  python import_manuscript.py prepare --project <书目录> [--source <原稿目录|单文件>]
      [--title 书名] [--recent 20] [--split-chapters]

  # 提交 init 之后跑：验 TODO 残留、章号对齐、给下一步命令
  python import_manuscript.py verify --project <书目录>

依赖：仅标准库（纯 CPU、零第三方依赖、零网络）。原稿只读，本脚本不改原稿。
"""

import argparse
import datetime
import importlib.util
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, path):
    if not os.path.isfile(path):  # pragma: no cover
        sys.stderr.write("[import] 找不到依赖脚本：%s\n" % path)
        sys.exit(2)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cb = _load("nf_collect_baseline", os.path.join(_HERE, "collect-baseline.py"))
wc = _load("nf_wordcount_core", os.path.join(_HERE, "wordcount_core.py"))

CHAPTER_NUM_IN_NAME = re.compile(r"第\s*(\d+)\s*章|(\d{1,5})\s*[章回]?")
CHAPTER_HEAD = re.compile(r"^\s*第\s*([0-9０-９零一二三四五六七八九十百千万]+)\s*[章回]")
DEFAULT_RECENT = 20
SOURCE_PROBE_DIRS = (
    (os.path.join("正文", "final"), False),
    (os.path.join("正文", "draft"), False),
    (os.path.join("导出",), False),
)
CN_NUM = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10, "百": 100, "千": 1000, "万": 10000}


def cn_to_int(s):
    """中文数字 → int（支持 三千二百零五 这类；纯阿拉伯数字直接转）。"""
    s = s.strip()
    if s.isdigit():
        return int(s)
    total, section, digit = 0, 0, 0
    for ch in s:
        if ch in "零一二三四五六七八九":
            digit = CN_NUM[ch]
            section += digit
        elif ch in "十百千万":
            unit = CN_NUM[ch]
            section = (section or 1) * unit
            if unit >= 10000:
                total += section
                section = 0
            elif unit >= 10:
                total += section
                section = 0
    return total + section


def detect_chapters(source, split=False):
    """返回 [(章号, 章文本)]。章号来源：文件名 > 文件首行标题 > 顺序号。"""
    items = []
    if os.path.isdir(source):
        files = []
        for ext in ("*.txt", "*.md", "*.text"):
            files += [f for f in __import__("glob").glob(os.path.join(source, ext))]
        files.sort(key=lambda p: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", os.path.basename(p))])
        if not files:
            return []
        for fp in files:
            text = cb.read_text(fp)
            num = None
            m = re.search(r"第\s*(\d+)\s*章", os.path.basename(fp))
            if m:
                num = int(m.group(1))
            else:
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    m2 = CHAPTER_HEAD.match(line)
                    if m2:
                        num = cn_to_int(m2.group(1))
                    break
            items.append((num, fp, text))
    else:
        text = cb.read_text(source)
        if split:
            for i, ch in enumerate(cb.split_chapters_if_needed(text), 1):
                items.append((None, None, ch))
        else:
            items.append((None, source, text))

    chapters, seq = [], 0
    fallback_last = 0
    for num, fp, text in items:
        seq += 1
        if num is None:
            head = None
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                m2 = CHAPTER_HEAD.match(line)
                if m2:
                    head = cn_to_int(m2.group(1))
                break
            num = head or seq
        chapters.append((num, fp, text))
    chapters.sort(key=lambda x: x[0])
    # 同号去重：同一章号出现多次时保留靠后的文件（修订稿在后）
    dedup = {}
    for num, fp, text in chapters:
        dedup[num] = (fp, text)
    return [(num, fp, text) for num, (fp, text) in sorted(dedup.items())]


def find_source(project, source):
    if source:
        if not os.path.exists(source):
            sys.stderr.write("[import] --source 不存在：%s\n" % source)
            sys.exit(2)
        return source, None
    for rel, split in ((os.path.join("正文", "final"), False),
                       (os.path.join("正文", "draft"), False),
                       (os.path.join("导出",), False)):
        cand = os.path.join(project, rel)
        if os.path.isdir(cand) and os.listdir(cand):
            return cand, False
    for name in os.listdir(project):
        if name.lower().endswith((".txt", ".md")):
            return os.path.join(project, name), True
    return None, None


def fmt_wordcount(text):
    """与门禁同口径的可见字数。"""
    try:
        return wc.count_visible_chars(text)
    except Exception:
        return cb.visible_len(cb.strip_non_prose(text))


def cmd_prepare(args):
    project = os.path.abspath(args.project)
    if os.path.isdir(os.path.join(project, "追踪")):
        sys.stderr.write("[import] %s 已有 追踪/（init 过了）。存量修订走 "
                         "tracking_commit.py 的 revision 流程，不要重复导入。\n" % project)
        return 2
    source, split = find_source(project, args.source)
    if args.split_chapters:
        split = True
    if not source:
        sys.stderr.write("[import] 找不到原稿：--source 或 <书目录> 下的 正文/final|draft、导出、*.txt 均为空\n")
        return 2
    chapters = detect_chapters(source, split=split)
    if not chapters:
        sys.stderr.write("[import] %s 里没有读到任何章节\n" % source)
        return 2

    nums = [num for num, _, _ in chapters]
    max_ch = max(nums)
    gaps = sorted(set(range(1, max_ch + 1)) - set(nums)) if max_ch <= 5000 else []
    wordcounts = {num: fmt_wordcount(text) for num, _, text in chapters}
    total_chars = sum(wordcounts.values())

    # 近 N 章（按章号取，不按文件顺序）
    recent_nums = sorted(nums)[-args.recent:]
    recent_map = {num: (fp, text) for num, fp, text in chapters}

    imp_dir = os.path.join(project, ".story", "import")
    os.makedirs(imp_dir, exist_ok=True)

    # ---- 1. 盘点报告 ----
    lines = [
        "# 盘点报告",
        "",
        "- 生成：%s（scripts/import_manuscript.py prepare）" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "- 原稿来源：`%s`%s" % (source, "（整本单文件已切分）" if split else ""),
        "- 章节总数：**%d**（最高章号 %d）｜ 总字数：%s（门禁同口径）" % (len(chapters), max_ch, format(total_chars, ",")),
        "- 章均字数：%d" % (total_chars // max(len(chapters), 1)),
    ]
    if gaps:
        lines += ["- **断号 %d 处**：%s%s" % (len(gaps), "、".join(map(str, gaps[:30])), " …" if len(gaps) > 30 else "")]
    else:
        lines += ["- 章号连续无断号"]
    dup_warn = ""
    lines += ["", "| 章号 | 字数 | 文件 |", "|---:|---:|---|"]
    for num in sorted(nums)[-min(len(nums), 30):]:
        fp = recent_map[num][0]
        lines.append("| %d | %d | %s |" % (num, wordcounts[num], os.path.basename(fp) if fp else "（单文件切分）"))
    if len(nums) > 30:
        lines.append("| … | … | 仅列最近 30 章，全量见本次运行输出 |")
    with open(os.path.join(imp_dir, "盘点报告.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("[写入] %s" % os.path.join(imp_dir, "盘点报告.md"))

    # ---- 2. 文风基线（近 N 章，human-baseline schema，forge.py dist 自动消费）----
    sent_lens, para_lens, chap_lens, dlg = [], [], [], []
    for num in recent_nums:
        text = cb.strip_non_prose(recent_map[num][1])
        n = cb.visible_len(text)
        if n < 200:
            continue
        chap_lens.append(n)
        for s in cb.split_sentences(text):
            L = cb.visible_len(s)
            if L:
                sent_lens.append(L)
        for p in cb.split_paragraphs(text):
            L = cb.visible_len(p)
            if L:
                para_lens.append(L)
        dlg.append(cb.dialogue_inside_chars(text) / n if n else 0.0)
    baseline = {
        "_meta": {
            "version": 2,
            "generated": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "purpose": "own-book style baseline for novel-forge check-distribution.py (--baseline)",
            "generator": "scripts/import_manuscript.py prepare",
            "label": os.path.basename(project),
            "scope": "import 时近 %d 章（第%s章起）" % (len(recent_nums), min(recent_nums) if recent_nums else "-"),
            "caveat": ("本书自己的文风基线：后续新写的章节应与旧稿文风连续，"
                       "分布偏离本书基线 = 文风漂移。章数不足 10 时统计不稳。"),
            "sampling": {"sentence_split": "。！？…", "length_unit": "字符数（含标点，不含空白）",
                          "dialogue_ratio": "引号内文字 / 全文字符（不含引号；不跨行；嵌套只计一次）",
                          "paragraph": "非空行 = 段"},
        },
        "merged": {
            "sentence": cb.describe(sent_lens),
            "paragraph": cb.describe(para_lens),
            "chapter_len": cb.describe(chap_lens),
            "dialogue_ratio": cb.describe(dlg, digits=4),
        },
    }
    baseline_path = os.path.join(project, "文风基线.json")
    with open(baseline_path, "w", encoding="utf-8") as f:
        json.dump(baseline, f, ensure_ascii=False, indent=1)
    print("[写入] %s（forge.py dist 会自动用它当对比基准）" % baseline_path)

    # ---- 3. 回填章节清单 ----
    m = baseline["merged"]
    lines = [
        "# 回填章节清单（近 %d 章）" % len(recent_nums),
        "",
        "> 精读以下章节后填写 `init-skeleton.json`。更早的章不读——",
        "> 它们由 recall.py 的正文检索层承接，写前捞前文时按需捞。",
        "",
        "| # | 章号 | 字数 |",
        "|---:|---:|---:|",
    ]
    for i, num in enumerate(recent_nums, 1):
        lines.append("| %d | %d | %d |" % (i, num, wordcounts[num]))
    lines += [
        "",
        "读的时候逐章记四类事实（填进 init-skeleton.json 用）：",
        "",
        "1. **出场角色**：谁在场、状态变化（位置/目标/能力/关系）→ character_snapshots",
        "2. **开放伏笔**：埋了没回收的线（含本章新埋）→ foreshadow[]",
        "3. **悬置事件**：读者知道什么、真相是什么、揭到哪一步 → timeline_events[]",
        "4. **近三章摘要**：每章一句话（谁+干了什么+结果）→ context.recent_chapters",
        "",
        "文风基线摘要（近 %d 章）：句长 mean %.1f sd %.1f ｜ 段长 mean %.1f ｜ "
        "对话 %.1f%%（p10 %.1f%% / p90 %.1f%%）" % (
            len(recent_nums), m["sentence"]["mean"], m["sentence"]["stdev"],
            m["paragraph"]["mean"], m["dialogue_ratio"]["mean"] * 100,
            m["dialogue_ratio"].get("p10", 0) * 100, m["dialogue_ratio"].get("p90", 0) * 100),
    ]
    with open(os.path.join(imp_dir, "回填章节清单.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("[写入] %s" % os.path.join(imp_dir, "回填章节清单.md"))

    # ---- 4. init 预填单 ----
    last3 = sorted(nums)[-3:]
    title = args.title or os.path.basename(os.path.normpath(project))
    skeleton = {
        "schema_version": 1,
        "book_title": title,
        "last_chapter": max_ch,
        "context": {
            "position": {
                "volume": "TODO：当前卷名",
                "volume_start_chapter": 1,
                "story_time": "TODO：第%d章结束时的故事时间" % max_ch,
                "scene": "TODO：第%d章结束时的场景" % max_ch,
            },
            "long_term_constraints": [],
            "active_character_names": ["TODO：主角名"],
            "continuity_risks": [],
            "recent_chapters": [
                {"chapter": n, "summary": "TODO：一句话摘要（谁+干了什么+结果）"} for n in last3
            ],
            "next_chapter_commitments": [],
        },
        "character_snapshots": {
            "TODO：主角名": {
                "identity": "TODO：身份与处境",
                "location": "TODO：当前所在",
                "goal": "TODO：当前目标",
                "state": "TODO：身心状态一句话",
                "abilities_resources": ["TODO：已获得的金手指/资源"],
                "relationships": ["TODO：与某角色：关系一句话"],
                "knowledge": ["TODO：知道：某事"],
                "open_threads": ["TODO：挂着的事"],
            }
        },
        "foreshadow": [],
        "timeline_events": [],
    }
    skeleton_path = os.path.join(imp_dir, "init-skeleton.json")
    with open(skeleton_path, "w", encoding="utf-8") as f:
        json.dump(skeleton, f, ensure_ascii=False, indent=1)
    print("[写入] %s" % skeleton_path)

    # ---- 5. 填写说明（八步协议）----
    guide = "\n".join([
        "# init 填写说明（八步协议）",
        "",
        "脚本已完成 1–2 步，你（模型/作者）完成 3–7 步，第 8 步双方各半：",
        "",
        "| 步 | 谁 | 动作 |",
        "|---|---|---|",
        "| 1 盘点 | 脚本 ✓ | 看 `盘点报告.md`（断号 %s） |" % ("有" if gaps else "无"),
        "| 2 文风基线 | 脚本 ✓ | `文风基线.json` 已生成，后续 dist 自动消费 |",
        "| 3 回填阅读 | 模型 | 精读 `回填章节清单.md` 所列近 %d 章 |" % len(recent_nums),
        "| 4 注册伏笔 | 模型 | 开放伏笔填进 skeleton 的 foreshadow[]（F001 起，"
        "planted_chapter 按正文实际章号，status=已埋，importance=高/中/低） |",
        "| 5 角色快照 | 模型 | 替换占位快照；核心角色每人都一份（identity/location/goal/"
        "state + 四个列表）。离场角色不进 active_character_names；已死角色写 died_in |",
        "| 6 时间线 | 模型 | 悬置事件填 timeline_events[]（E001 起；objective_fact=真相，"
        "reader_knowledge=读者所见，reveal_status=未揭示/部分揭示/已揭示，"
        "部分揭示必带 reveal_chapter） |",
        "| 7 卷位置 | 模型 | context.position 四项 + recent_chapters 三条摘要 + "
        "long_term_constraints / continuity_risks（从近 20 章里提炼） |",
        "| 8 提交 | 双方 | `python scripts/tracking_commit.py init --project <书目录> "
        "--input .story/import/init-skeleton.json`，然后 `python scripts/import_manuscript.py "
        "verify --project <书目录>` → `python scripts/forge.py truth --book <书目录>` "
        "→ `python scripts/recall.py index --project <书目录>` |",
        "",
        "字段格式详见 `templates/README.md`；填完的 skeleton 里不允许残留任何 TODO 字样"
        "（verify 会拦）。",
        "",
    ]) + "\n"
    with open(os.path.join(imp_dir, "init-填写说明.md"), "w", encoding="utf-8") as f:
        f.write(guide)
    print("[写入] %s" % os.path.join(imp_dir, "init-填写说明.md"))

    print("-" * 64)
    print("盘点：%d 章 / 最高章号 %d / 总字数 %s%s" % (
        len(chapters), max_ch, format(total_chars, ","),
        " ｜ 断号 %d 处" % len(gaps) if gaps else ""))
    print("下一步：模型按 %s 精读并填 %s，然后 tracking_commit.py init。"
          % (os.path.join(".story", "import", "回填章节清单.md"),
             os.path.join(".story", "import", "init-skeleton.json")))
    return 0


def cmd_verify(args):
    project = os.path.abspath(args.project)
    state_path = os.path.join(project, "追踪", "_tracking-state.json")
    if not os.path.exists(state_path):
        sys.stderr.write("[import] 还没 init：找不到 %s\n" % state_path)
        return 2
    with open(state_path, "r", encoding="utf-8") as f:
        state = json.load(f)
    problems = []
    raw = json.dumps(state, ensure_ascii=False)
    todo_hits = re.findall(r"[^\"]*TODO[^\"]*", raw)
    if todo_hits:
        problems.append("状态里残留 %d 处 TODO（示例：%s…）——占位符没替换完"
                        % (len(todo_hits), todo_hits[0][:40]))
    last = state.get("last_committed_chapter")
    report = os.path.join(project, ".story", "import", "盘点报告.md")
    max_ch = None
    if os.path.exists(report):
        with open(report, "r", encoding="utf-8") as f:
            m = re.search(r"最高章号 (\d+)", f.read())
            if m:
                max_ch = int(m.group(1))
    if max_ch is not None and last != max_ch:
        problems.append("台账 last_committed_chapter=%s ≠ 盘点最高章号 %s——导入截断了？"
                        % (last, max_ch))
    snaps = state.get("characters", {})
    empty_snaps = [name for name, s in snaps.items()
                   if isinstance(s, dict) and not any(str(v).strip() for k, v in s.items()
                                                      if k not in ("died_in", "revived_in"))]
    if empty_snaps:
        problems.append("空快照：%s" % "、".join(empty_snaps))
    fs = state.get("foreshadow", {})
    tl = state.get("timeline", {})
    print("=" * 64)
    print("novel-forge 导入校验 ｜ %s" % os.path.basename(project))
    print("=" * 64)
    print("  台账章号 : %s%s" % (last, "（盘点 %s ✓）" % max_ch if max_ch == last else ""))
    print("  角色快照 : %d 个%s" % (len(snaps), "（空快照 %d）" % len(empty_snaps) if empty_snaps else ""))
    print("  开放伏笔 : %d 条 ｜ 悬置事件 : %d 条" % (len(fs), len(tl)))
    if len(fs) == 0 or len(tl) == 0:
        problems.append("伏笔 %d 条 / 事件 %d 条——660 章的存量书不可能没有悬置线，"
                        "回填阅读没做透" % (len(fs), len(tl)))
    if problems:
        print("-" * 64)
        for i, p in enumerate(problems, 1):
            print("  [问题%d] %s" % (i, p))
        print("修完重跑 verify。")
        return 1
    print("-" * 64)
    print("校验通过。接续写作前的最后两步：")
    print("  python scripts/forge.py truth --book <书目录>        # 双时间线一致性")
    print("  python scripts/recall.py index --project <书目录>    # 重建检索索引")
    return 0


def main():
    ap = argparse.ArgumentParser(description="novel-forge 存量书导入引擎（冷启动接管，只回填近 N 章）")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare", help="盘点 + 文风基线 + 回填清单 + init 预填单")
    p.add_argument("--project", required=True, help="书目录")
    p.add_argument("--source", help="原稿目录或单文件（缺省按 正文/final→draft→导出→*.txt 探测）")
    p.add_argument("--title", help="书名（默认取书目录名）")
    p.add_argument("--recent", type=int, default=DEFAULT_RECENT, help="回填精读的近 N 章（默认 20）")
    p.add_argument("--split-chapters", action="store_true", help="单文件整本时按「第N章」行切分")
    p.set_defaults(func=cmd_prepare)

    v = sub.add_parser("verify", help="init 提交后校验：TODO 残留 / 章号对齐 / 空快照 / 悬置线")
    v.add_argument("--project", required=True, help="书目录")
    v.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
