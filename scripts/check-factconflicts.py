#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""事实冲突检测：作者真相层的确定性校验（story-skills 判定逻辑移植）

补 novel-forge 的第二个盲区。现有 `debt.py` 算的是**读者侧感知到的伏笔
压力**——某笔债压了太久该还。它不判定「作者真相层的事实冲突」：

    第 3 章写死角色，第 40 章该角色正常出场 → 现有链路完全静默

移植自 story-skills（MIT，Copyright (c) 2025 danjdewhurst）的判定逻辑，
按 novel-forge 台账结构重写（上游是 story-bible 文件夹模型，不直接 vendor）。
见 VENDOR-NOTICE.md。

数据前提（2026-10-06 修复后确立）：
  死亡/复活走 `characters.<name>.died_in` / `revived_in` **结构化字段**。
  这两个字段已加入 tracking_commit.py 的 snapshot 白名单，写入时即受
  约束（revived >= died、有复活必有死亡）。本脚本在写入校验之上做
  **跨章一致性**判定——写入时拦得住「字段自相矛盾」，拦不住
  「字段与状态文本/已发文章节冲突」，那正是本脚本的职责。

移植的判定（对应 story-skills 的 err(...)）：
  revival-before-death*      复活章早于死亡章（写入时已拦，此处兜底）
  revived-without-death*     有复活无死亡（写入时已拦，此处兜底）
  death-status-mismatch      died_in 与 state 文本矛盾
  revival-status-mismatch    revived_in 与 state 文本矛盾
  dead-character-appears     死亡后仍有以该角色挂起的存活记录
  promise-payoff-before-plant 回收早于埋设（写入时已拦，此处兜底）
  promise-payoff-missing     标记已回收但无回收章
  plant-not-drafted          记录了埋设章但该章未成稿
  reveal-chapter-missing     标记已揭示但无揭示章（写入时已拦，此处兜底）
  reveal-in-future           揭示章晚于当前进度（提前登记未来揭示）

  * 标「写入时已拦」的规则是防御性兜底：tracking_commit 拦得住经它写入
    的数据，拦不住绕过它手改 `_tracking-state.json` 的情况——而手改
    台账恰恰是长篇维护中真实发生的事。

设计约束（沿用 novel-forge 铁律）：
- **纯派生，不落第二份状态**：只读 `_tracking-state.json`，不写任何文件
- 零外部依赖，纯 CPU
- 不自行"圆回来"：发现问题只报告，不改数据（见 SKILL.md 失败策略）

退出码：0 = 无冲突；1 = 检出冲突（blocking）；2 = 用法/环境错误
"""

import argparse
import json
import os
import re
import sys

# 台账合法状态值（与 tracking_commit.py 的 FORESHADOW_STATUSES 对齐）
FORESHADOW_STATUSES = {"已埋", "已回收", "已过期", "放弃"}
REVEAL_STATUSES = {"未揭示", "部分揭示", "已揭示"}

# state 文本里的死亡/存活信号。台账 state 是自由文本（max 300 字），
# 结构化字段才是权威，但 state 与结构化字段矛盾时必须报——
# 写章的人看的是派生视图里的 state 行，它错了后面全错。
#
# 判定取向（2026-10-06 二次修复确立）：**缺失即报**。
# died_in 是结构化死亡事实；state 是写章人每天看的那行字。
# 当角色已死且未复活时，state 里若看不到任何死亡语义词，
# 写章人就会把死人当活人写——「在城南养伤」这种中性偏活的描述
# 也要报。宁可多报一条让人核对，不可漏掉一个死人复活事故。
_DEATH_WORDS = re.compile(r"死亡|已死|阵亡|身亡|殒命|毙命|尸|死|亡故|殉")
# 身后事词汇：死亡角色的未结线若属此类，不算「需要本人活着」
_AFTERLIFE_WORDS = re.compile(r"复仇|报仇|查.{0,6}死因|凶手|身后|遗.{0,4}(愿|物|言|孤)|葬|祭|吊")


def load_state(book_dir):
    """读 _tracking-state.json。"""
    path = os.path.join(book_dir, "追踪", "_tracking-state.json")
    if not os.path.isfile(path):
        raise SystemExit(f"[x] 找不到台账：{path}")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[x] 台账 JSON 解析失败：{exc}")


def _chapter_number(value):
    """从各种写法解析章号，统一返回 int 或 None。

    支持 3 / "3" / "chapter-003" / "第003章"。
    注意：None 与空串返回 None（合法的「未定」），不算错误。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        match = re.search(r"(\d+)", value)
        if match:
            return int(match.group(1))
    return None


def _drafted_chapters(book_dir, state):
    """已成稿的章号集合。

    判定依据是**该章的正文文件是否存在**——「已导入追踪」不等于
    「已写完」。用文件存在性更接近作者真实进度。
    """
    drafted = set()
    chapter_dir = os.path.join(book_dir, "正文")
    if os.path.isdir(chapter_dir):
        # v2.1：递归扫，兼容 正文/draft|final 两层
        for dirpath, _dirs, files in os.walk(chapter_dir):
            for name in files:
                if not name.endswith(".md") or "细纲" in name:
                    continue
                num = _chapter_number(name)
                if num is not None:
                    drafted.add(num)
    through = state.get("imported_through_chapter")
    if isinstance(through, int):
        drafted.add(through)
    return drafted


def check_deaths(state, drafted, through):
    """死亡/复活一致性判定（结构化字段 + state 文本交叉验证）。"""
    errors = []
    characters = state.get("characters") or {}
    for name, char in characters.items():
        if not isinstance(char, dict):
            continue
        state_text = str(char.get("state") or "")
        died = char.get("died_in")
        revived = char.get("revived_in")
        died_n = _chapter_number(died)
        revived_n = _chapter_number(revived)

        # --- 写入校验的兜底（tracking_commit 拦正常路径，这里拦手改台账）---
        if revived_n is not None and died_n is None:
            errors.append({
                "rule": "revived-without-death",
                "who": name,
                "detail": f"记录了复活于第 {revived_n} 章，但没有 died_in",
                "fix": f"给「{name}」补 died_in，或删除 revived_in",
            })
            continue
        if revived_n is not None and died_n is not None and revived_n < died_n:
            errors.append({
                "rule": "revival-before-death",
                "who": name,
                "detail": f"复活于第 {revived_n} 章，早于死亡于第 {died_n} 章",
                "fix": f"核对「{name}」的 died_in / revived_in，通常是写反了",
            })
            continue

        # --- 结构化字段 × state 文本交叉验证 ---
        # state 是写章人每天看的那行字。取向：**缺失即报**——
        # died_in 存在且当前已死，state 却看不到死亡语义，
        # 写章人会把死人当活人写。「在城南养伤」也要报。
        if died_n is not None:
            now_dead = revived_n is None or through < revived_n
            if now_dead and not _DEATH_WORDS.search(state_text):
                errors.append({
                    "rule": "death-status-mismatch",
                    "who": name,
                    "detail": (f"台账记录「{name}」第 {died_n} 章死亡（未复活），"
                               f"但身心状态写着「{state_text[:40]}」——看不出已死"),
                    "fix": f"把 state 改为含死亡语义的描述（如「坠崖身亡」），否则写章会当活人写",
                })
            if not now_dead and _DEATH_WORDS.search(state_text):
                errors.append({
                    "rule": "revival-status-mismatch",
                    "who": name,
                    "detail": (f"「{name}」第 {died_n} 章死亡、第 {revived_n} 章已复活，"
                               f"但身心状态仍写着「{state_text[:40]}」"),
                    "fix": f"把 state 改为复活后的状态描述",
                })

            # --- 死后仍挂着的存活痕迹（dead-character-appears）---
            # 台账侧的存活痕迹：该角色仍出现在 active_character_names，
            # 或在死亡章之后仍有以其挂起的 open_threads。
            if revived_n is None or through >= died_n:
                active = state.get("context", {}).get("active_character_names", [])
                if name in active and (revived_n is None or revived_n > through):
                    errors.append({
                        "rule": "dead-character-appears",
                        "who": name,
                        "detail": (f"「{name}」第 {died_n} 章死亡（未复活），"
                                   f"但仍列在当前活跃角色名单里"),
                        "fix": (f"若剧情需要其「复活后出场」，先补 revived_in；"
                                f"否则从 active_character_names 移除"),
                    })
                # open_threads 是纯字符串列表（无章号），只查「死亡角色仍挂线」。
                # 取向同上：该角色已死未复活，挂着的线默认需要本人——
                # 除非线的描述本身就写着身后事（寻凶、复仇、查死因）。
                for thread in char.get("open_threads") or []:
                    if not isinstance(thread, str):
                        continue
                    if not _AFTERLIFE_WORDS.search(thread):
                        errors.append({
                            "rule": "dead-character-appears",
                            "who": name,
                            "detail": (f"「{name}」第 {died_n} 章死亡（未复活），"
                                       f"但未结事项仍挂着「{thread[:40]}」——需要本人活着才能推进"),
                            "fix": (f"该线要么转给活人，要么改写成身后事（复仇/查死因类）；"
                                    f"若为复活剧情，先补 revived_in"),
                        })
                        break   # 一个角色只报一次挂线，避免刷屏
    return errors


def check_foreshadow(state, drafted, through):
    """伏笔埋设/回收的顺序与状态一致性。"""
    errors = []
    foreshadow = state.get("foreshadow") or {}
    for fid, item in foreshadow.items():
        if not isinstance(item, dict):
            continue
        label = f"伏笔 {fid}（{item.get('summary', '未命名')}）"
        planted = _chapter_number(item.get("planted_chapter"))
        payoff = _chapter_number(item.get("planned_resolution_chapter"))
        status = str(item.get("status") or "")

        if planted is not None and payoff is not None and payoff < planted:
            errors.append({
                "rule": "promise-payoff-before-plant",
                "who": fid,
                "detail": f"{label} 计划第 {payoff} 章回收，但埋设章是第 {planted} 章——回收早于埋设",
                "fix": f"核对 {fid} 的 planted_chapter 与 planned_resolution_chapter",
            })
        if status == "已回收" and payoff is None:
            errors.append({
                "rule": "promise-payoff-missing",
                "who": fid,
                "detail": f"{label} 状态是「已回收」，但没有回收章",
                "fix": f"补 {fid} 的 planned_resolution_chapter，或把状态改回「已埋」",
            })
        # 实际回收早于埋设：已回收时 updated_chapter 记的是「最后一次动这条目」的章，
        # 若它早于 planted_chapter，说明埋设章号记错或回收时序记反——
        # planned（计划值）再合理也救不回实际记录的矛盾。同章埋收（==）合法。
        actual = _chapter_number(item.get("updated_chapter")) if status == "已回收" else None
        if actual is not None and planted is not None and actual < planted:
            errors.append({
                "rule": "promise-payoff-actual-before-plant",
                "who": fid,
                "detail": (f"{label} 状态「已回收」且最后更新于第 {actual} 章，"
                           f"但埋设章是第 {planted} 章——实际回收早于埋设，台账记反了"),
                "fix": (f"核对 {fid} 的 planted_chapter（埋设）与 updated_chapter（回收时动过）——"
                        f"要么埋设章号写大了，要么这条目在埋设前被误标已回收"),
            })
        # 埋设章必须有正文——status=已埋 但那章还是细纲，等于埋设没落地
        if planted is not None and status == "已埋" and planted not in drafted:
            errors.append({
                "rule": "plant-not-drafted",
                "who": fid,
                "detail": f"{label} 记录埋设于第 {planted} 章，但该章尚无正文",
                "fix": f"若第 {planted} 章还是细纲，埋设实际未发生",
            })
        # 回收章已过但状态还是已埋——债已到还款日（这是 debt.py 的领地，
        # 此处只查「登记回收章 < 当前进度但状态未更新」的账实不符）
        if payoff is not None and payoff <= through and status == "已埋":
            errors.append({
                "rule": "payoff-overdue",
                "who": fid,
                "detail": (f"{label} 登记回收章是第 {payoff} 章（当前已到第 {through} 章），"
                           f"但状态仍是「已埋」——要么回收了没记账，要么没按计划还债"),
                "fix": f"若第 {payoff} 章已回收，改 status=已回收；否则与 debt.py 的催债清单合并处理",
            })
    return errors


def check_timeline(state, through):
    """双时间线一致性。"""
    errors = []
    timeline = state.get("timeline") or {}
    for eid, item in timeline.items():
        if not isinstance(item, dict):
            continue
        reveal_status = str(item.get("reveal_status") or "")
        reveal_chapter = _chapter_number(item.get("reveal_chapter"))
        label = f"真相 {eid}（{str(item.get('objective_fact') or '')[:30]}）"

        if reveal_status in ("部分揭示", "已揭示") and reveal_chapter is None:
            errors.append({
                "rule": "reveal-chapter-missing",
                "who": eid,
                "detail": f"{label} 标记为「{reveal_status}」，但没有揭示章",
                "fix": f"补 {eid} 的 reveal_chapter",
            })
        # 揭示章晚于当前进度——把未来计划当成了既成事实登记
        if reveal_status in ("部分揭示", "已揭示") and reveal_chapter is not None and reveal_chapter > through:
            errors.append({
                "rule": "reveal-in-future",
                "who": eid,
                "detail": (f"{label} 标记为「{reveal_status}」于第 {reveal_chapter} 章，"
                           f"但当前只写到第 {through} 章——把未来计划登记成了既成事实"),
                "fix": (f"若第 {reveal_chapter} 章还没写，reveal_status 应为「未揭示」，"
                        f"未来计划留在大纲里（台账只收既成事实，见 templates/README.md）"),
            })
    return errors


def scan(book_dir):
    state = load_state(book_dir)
    drafted = _drafted_chapters(book_dir, state)
    through = state.get("imported_through_chapter") or state.get("last_committed_chapter") or 0
    if not isinstance(through, int):
        through = _chapter_number(through) or 0
    errors = check_deaths(state, drafted, through)
    errors += check_foreshadow(state, drafted, through)
    errors += check_timeline(state, through)
    return {
        "book": os.path.basename(os.path.abspath(book_dir)),
        "drafted": sorted(drafted),
        "through": through,
        "findings": errors,
    }


def render_text(result):
    lines = [
        f"事实冲突检测  书：{result['book']}",
        f"  已成稿章号：{result['drafted'] or '（无）'}  ｜ 当前进度：第 {result['through']} 章",
        "",
    ]
    if not result["findings"]:
        lines.append("  [OK] 未检出事实冲突。")
        return "\n".join(lines)

    lines.append(f"  [X] 检出 {len(result['findings'])} 处事实冲突（blocking）：")
    lines.append("")
    for idx, f in enumerate(result["findings"], 1):
        lines.append(f"  {idx}. [{f['rule']}] {f['who']}")
        lines.append(f"      {f['detail']}")
        lines.append(f"      处置：{f['fix']}")
        lines.append("")
    lines.append("  以上均需你裁定。本脚本只报告，不改台账——")
    lines.append("  改了就算「自行圆回来」，是本技能明令禁止的。")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="事实冲突检测（story-skills 逻辑移植）")
    parser.add_argument("--book", help="书目录（含 追踪/_tracking-state.json）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    if not args.book:
        parser.print_help()
        print("\n[x] 必须指定 --book", file=sys.stderr)
        return 2

    try:
        result = scan(args.book)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"[x] 文件系统错误：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_text(result))
    return 1 if result["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())