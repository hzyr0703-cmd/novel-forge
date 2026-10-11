#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""debt.py — 伏笔债务压力计算（纯派生，不落第二份状态）

从追踪台账的 `foreshadow` 与 `timeline_events` 派生"读者欠账压力"，
用来回答两个问题：

1. 哪条伏笔已经拖太久、这章再不接就要出事？
2. 有没有真相长期不揭示，读者已经忘了自己在等什么？

**这不是新状态**。所有数字都由台账现有字段实时算出，
台账一改，这里的结论立刻跟着变。不写任何文件（除显式 --json 输出）。

核心模型：
    紧迫度 = 重要度权重 × 时间压力 × 停滞惩罚
    时间压力随"距计划回收章的剩余章数"非线性上升；一旦逾期，进入逾期区间按章累罚。
    停滞惩罚随"已埋但从未推进"递增——这是最容易被作者忘掉的债。

子命令：
    status   债务总览（默认），按紧迫度排序
    due      只列出该处理的（逾期 + 临期 + 高重要度停滞）
    reveal   真相揭示节奏体检

用法：
    python debt.py --project <书稿根> status
    python debt.py --project <书稿根> due --within 3
    python debt.py --project <书稿根> status --json
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"

# 重要度权重：读者对不同量级的伏笔期待不同
_IMPORTANCE_W = {"高": 1.6, "极高": 1.9, "中高": 1.35, "中": 1.0, "中低": 0.75, "低": 0.5}

# 视为"已回收"的 status 关键词
_RESOLVED_HINTS = ("已回收", "已兑现", "已完成", "已揭示", "已解决", "已闭合", "已埋完")
# 视为"已废弃/取消"的关键词——不该继续计债
_DROPPED_HINTS = ("已废弃", "已取消", "已放弃", "已删除", "废弃", "取消")

# 连续剧节奏经验值：距计划回收章的剩余章数 → 时间压力系数
def _time_pressure(remaining: float) -> float:
    """remaining = planned_resolution - current_chapter（可为负）。"""
    if remaining > 30:
        return 0.05           # 还早，读者不急
    if remaining > 12:
        return 0.20
    if remaining > 6:
        return 0.45
    if remaining > 3:
        return 0.75
    if remaining > 0:
        return 1.10           # 临期，必须这章附近接
    # 已逾期：每多欠一章加罚。
    # 早期版本这里硬封顶 2.40，600 章实测导致几十笔债全部并列满分、排序彻底失效——
    # 长篇里"欠得最久的那笔"必须永远排在最前面。改用对数增长，不设硬上限。
    overdue = -remaining
    return 1.40 + 1.60 * math.log10(1 + overdue)


def _stall_penalty(planted: int, current: int) -> float:
    """埋了多久都没动过。

    600 章实测：原来 40 章以上一律返回 1.20，会让几十条长龄债并列同分。
    改用对数增长，保证"埋得最久且没推进的"始终排在最前。
    """
    age = max(0, current - planted)
    if age < 5:
        return 0.0
    if age < 12:
        return 0.20
    if age < 25:
        return 0.50
    if age < 40:
        return 0.85
    return 0.85 + 0.90 * math.log10(1 + (age - 40) / 40)


def _as_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _pick(state: dict, *keys, default=None):
    """按优先级取第一个存在且非空的键。

    台账字段名出现过多种写法（foreshadow/foreshadowing、
    last_chapter/last_chapter_written、planted_chapter/planted_ch）。
    早先只认一种写法，另一种会静默漏检——不报错，只是永远算不出来。
    """
    for k in keys:
        v = state.get(k)
        if v not in (None, "", [], {}):
            return v
    return default if default is not None else []


def _rows(v: Any) -> list[dict[str, Any]]:
    """把台账里的集合字段统一成 list。

    真实台账两种形态都存在：
      · list  —— 手写 JSON 常见
      · dict  —— tracking_commit.py 提交后是 {id: row}（燃识 就是这种，
                 13 条伏笔）
    早先只当 list 处理，for...in 会遍历到 **key 字符串**，
    于是 f.get("status") 直接抛 AttributeError（崩溃）或整类条目消失。
    """
    if isinstance(v, dict):
        out = []
        for k, row in v.items():
            if isinstance(row, dict):
                out.append({**row, "id": row.get("id", k)})
        return out
    if isinstance(v, (list, tuple)):
        return [r for r in v if isinstance(r, dict)]
    return []


def _current_ch(state: dict) -> int:
    """当前章号。兼容多种字段名；都没有时退回正文目录的最大章号。"""
    v = _pick(state, "last_chapter", "last_committed_chapter",
              "last_chapter_written", "current_chapter", "last_ch", default=None)
    return _as_int(v, 0)


def _as_id(v: Any, fallback: str = "未编号") -> str:
    """把 None / 空值 / 非字符串统一成可读标签，不输出 Python repr。"""
    if v is None:
        return fallback
    s = str(v).strip()
    return s if s and s.lower() != "none" else fallback


def _load(project: Path) -> dict[str, Any]:
    for name in ("_tracking-state.json", "tracking.json"):
        for cand in (project / name, project / "追踪" / name):
            if cand.is_file():
                try:
                    return json.loads(cand.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise SystemExit(f"[debt] 台账解析失败: {cand} ({exc})")
    raise SystemExit(
        f"[debt] 找不到追踪台账。请先跑 tracking_commit.py init --project {project}"
    )


def _classify(status: str) -> str:
    s = (status or "").strip()
    if any(h in s for h in _DROPPED_HINTS):
        return "dropped"
    if any(h in s for h in _RESOLVED_HINTS) and "已埋" not in s:
        return "resolved"
    return "active"


def compute_debts(state: dict[str, Any]) -> list[dict[str, Any]]:
    """从台账派生全部活跃债务的紧迫度。纯函数。"""
    current = _current_ch(state)
    rows: list[dict[str, Any]] = []

    for f in _rows(_pick(state, "foreshadow", "foreshadowing")):
        status = str(f.get("status", ""))
        cls = _classify(status)
        if cls != "active":
            continue

        fid = _as_id(f.get("id"))
        importance = _as_id(f.get("importance"), "中")
        w = _IMPORTANCE_W.get(importance, 1.0)
        planted = _as_int(f.get("planted_chapter", f.get("planted_ch")), current)
        # 计划回收章字段三种写法都见过
        planned = f.get("planned_resolution_chapter",
                         f.get("target_ch", f.get("due_chapter")))
        planned_n = _as_int(planned, 0) if planned is not None else None

        if planned_n is None:
            # 没有计划回收章：不是债，是无主悬念，必须单独提示
            age = max(0, current - planted)
            urgency = min(1.0 + 0.10 * age, 2.2) * w
            rows.append(
                {
                    "id": fid,
                    "summary": f.get("summary") or f.get("desc") or f.get("content") or "",
                    "importance": importance,
                    "planted_chapter": planted,
                    "planned_resolution_chapter": None,
                    "remaining": None,
                    "urgency": round(urgency, 3),
                    "level": "无主悬念",
                    "age": age,
                    "advice": "未设计划回收章。补一个章号，或确认废弃——长期无主悬念会稀释所有伏笔的分量。",
                }
            )
            continue

        remaining = planned_n - current
        tp = _time_pressure(remaining)
        sp = _stall_penalty(planted, current)
        urgency = w * (tp + sp)

        if remaining < 0:
            level, advice = "逾期", f"已超计划 {abs(remaining)} 章未回收。要么这章接上，要么改计划——但别装看不见。"
        elif remaining == 0:
            level, advice = "到期", "计划回收章就是当前章。这章不接就是违约。"
        elif remaining <= 3:
            level, advice = "临期", f"还有 {remaining} 章窗口。可以留钩子，但要开始铺垫。"
        elif remaining <= 6:
            level, advice = "接近", f"还有 {remaining} 章。安排一次正面推进。"
        else:
            level, advice = "宽松", "时间充裕，但别忘了它还挂着。"

        if sp >= 0.50:
            advice += f" 已埋 {max(0, current - planted)} 章无推进，建议至少露一次头。"
        if urgency >= 2.0:
            level = "高危"

        rows.append(
            {
                "id": fid,
                "summary": _as_id(f.get("summary") or f.get("desc")
                                  or f.get("content"), "（缺摘要）"),
                "importance": importance,
                "planted_chapter": planted,
                "planned_resolution_chapter": planned_n,
                "remaining": remaining,
                "urgency": round(urgency, 3),
                "level": level,
                "age": max(0, current - planted),
                "advice": advice,
            }
        )

    rows.sort(key=lambda r: r["urgency"], reverse=True)
    return rows


def compute_reveals(state: dict[str, Any]) -> list[dict[str, Any]]:
    """真相揭示节奏体检。"""
    current = _current_ch(state)
    rows = []
    for e in _rows(_pick(state, "timeline_events", "timeline")):
        status = str(e.get("reveal_status", "")).strip()
        reveal_ch = e.get("reveal_chapter")
        revealed = "已揭示" in status or (reveal_ch is not None and _as_int(reveal_ch, 0) > 0)
        rows.append(
            {
                "id": _as_id(e.get("id")),
                "objective_fact": _as_id(e.get("objective_fact"), "（缺客观事实）"),
                "story_time": _as_id(e.get("story_time"), "?"),
                "reader_knowledge": _as_id(e.get("reader_knowledge"), "—"),
                "reveal_status": status or "未标注",
                "reveal_chapter": reveal_ch,
                "revealed": revealed,
                "characters": e.get("characters", []) or [],
            }
        )
    rows.sort(key=lambda r: (r["revealed"], r["id"]))
    return rows


# ---------------------------------------------------------------- 渲染

_LEVEL_TAG = {
    "高危": "!!",
    "逾期": "!",
    "到期": "!",
    "临期": "*",
    "无主悬念": "?",
    "接近": "-",
    "宽松": ".",
}


def _bar(v: float, width: int = 10) -> str:
    # 量程按600 章实测的实际上界（≈11）取，低于 2 的旧债也能看出差别
    n = int(min(max(v, 0.0), 11.0) / 11.0 * width)
    return "#" * n + "." * (width - n)


def _render_status(rows: list[dict], current: int, show_resolved: bool = False) -> str:
    out = [f"当前进度：第 {current} 章 · 活跃债务 {len(rows)} 条", ""]
    if not rows:
        out.append("没有活跃伏笔。台账干净。")
        return "\n".join(out)
    out.append(f"{'':2}{'ID':<8}{'级别':<8}{'紧迫度':<14}{'余章':>5}{'龄':>5}  摘要")
    out.append("-" * 78)
    for r in rows:
        tag = _LEVEL_TAG.get(r["level"], "-")
        remain = "—" if r["remaining"] is None else f"{r['remaining']:+d}"
        out.append(
            f"{tag:<2}{r['id']:<8}{r['level']:<8}"
            f"{_bar(r['urgency'])} {r['urgency']:<5.2f}{remain:>5}{r['age']:>5}  {r['summary'][:28]}"
        )
    return "\n".join(out)


def _render_due(rows: list[dict], within: int, current: int) -> str:
    hits = [
        r
        for r in rows
        if r["level"] in ("高危", "逾期", "到期", "无主悬念")
        or (r["remaining"] is not None and 0 <= r["remaining"] <= within)
        or (r["importance"] in ("高", "极高") and r["age"] >= 25)
    ]
    hits.sort(key=lambda r: r["urgency"], reverse=True)
    if not hits:
        return f"第 {current} 章：无需偿债窗口。可安心推进主线或埋新线。"
    out = [
        f"第 {current} 章：{len(hits)} 条债进入处理窗口。",
        "",
    ]
    for r in hits:
        out.append(f"[{r['level']}] {r['id']}（紧迫度 {r['urgency']:.2f}）")
        out.append(f"  {r['summary']}")
        out.append(f"  → {r['advice']}")
        out.append("")
    return "\n".join(out)


def _render_reveal(rows: list[dict], current: int) -> str:
    pending = [r for r in rows if not r["revealed"]]
    done = [r for r in rows if r["revealed"]]
    out = [
        f"真相揭示：共 {len(rows)} 条 · 已揭示 {len(done)} · 待揭示 {len(pending)}",
        "",
    ]
    if pending:
        out.append("待揭示（读者在等）：")
        for r in pending:
            out.append(f"  {r['id']}  {r['objective_fact'][:44]}")
            out.append(f"       读者已知：{r['reader_knowledge'][:50]}")
        out.append("")
    if done:
        out.append("已揭示：")
        for r in done:
            out.append(f"  {r['id']}  (第{r['reveal_chapter']}章) {r['objective_fact'][:40]}")
    if len(pending) >= 5:
        out.append("")
        out.append(
            f"⚠ 待揭示真相 {len(pending)} 条。长期挂着不揭示，读者会放弃等待——"
            "考虑在 1~2 章内至少揭示一条。"
        )
    return "\n".join(out)


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="novel-forge 伏笔债务压力计算（纯派生）")
    ap.add_argument("--project", required=True, help="书稿根目录（含 追踪/）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    sub = ap.add_subparsers(dest="cmd")

    p_st = sub.add_parser("status", help="债务总览")
    p_st.add_argument("--show-resolved", action="store_true", help="含已回收条目")

    p_du = sub.add_parser("due", help="只看该处理的")
    p_du.add_argument("--within", type=int, default=3, help="提前多少章预警，默认 3")

    sub.add_parser("reveal", help="真相揭示节奏体检")

    args = ap.parse_args(argv)
    cmd = args.cmd or "status"
    project = Path(args.project).expanduser().resolve()
    state = _load(project)
    current = _current_ch(state)

    if cmd == "reveal":
        rows = compute_reveals(state)
        if args.json:
            print(json.dumps({"current_chapter": current, "reveals": rows}, ensure_ascii=False, indent=2))
        else:
            print(_render_reveal(rows, current))
        return 0

    debts = compute_debts(state)

    if cmd == "due":
        if args.json:
            due = [r for r in debts if r["level"] in ("高危", "逾期", "到期", "无主悬念")
                   or (r["remaining"] is not None and 0 <= r["remaining"] <= args.within)]
            print(json.dumps({"current_chapter": current, "due": due}, ensure_ascii=False, indent=2))
        else:
            print(_render_due(debts, args.within, current))
        return 0

    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "current_chapter": current,
                    "total": len(debts),
                    "debts": debts,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(_render_status(debts, current, args.show_resolved))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())