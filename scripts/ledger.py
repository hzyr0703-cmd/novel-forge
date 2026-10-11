#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ledger.py — advisory 挂账台账（纯本地，零依赖，确定性）

回答一个问题：**故意违背 advisory 时许下的「以后补」承诺，还算数吗？**

背景：novel-forge 的门禁哲学是 blocking 只守四条底线，其余 advisory。
advisory 可以违背——但违背往往伴随一句「下一章补回来」。此前这句承诺
没有任何记录，说过就丢。本脚本把它变成台账：

    违背 advisory → 登记（章 + 规则 + 理由 + 承诺偿还的章）
    → 之后每次 gate 显示未还的账
    → 超过承诺章数 → 「债务逾期」强提醒（仍然 advisory，不阻断）

这不是新状态机，只是一份独立的 json 台账（追踪/advisory-ledger.json），
不与 _tracking-state.json 混写——台账是作者的手动承诺，追踪是事实记录，
两回事。借鉴 webnovel-writer v5.3 override_contracts 的思路，
按 novel-forge 的红线裁剪：纯标准库、零网络、不参与门禁退出码。

子命令：
    list     全部账目（默认），open 在前、按逾期排序
    add      登记一笔：--chapter 违背章 --rule 规则 --payback 承诺章 [--note 理由]
    fulfill  销账：--id L001 [--note 备注]
    overdue  只看逾期（当前章 > payback_chapter 的 open 账）
    check    给 gate 调：--at 当前章，输出挂账/逾期提示行（只读，永不阻断）

用法：
    python ledger.py --project <书稿根> add --chapter 13 --rule dialogue-ratio \
        --payback 15 --note 双人局刻意压对话，15 章后补群像
    python ledger.py --project <书稿根> list
    python ledger.py --project <书稿根> check --at 16
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

SCHEMA = "advisory-ledger/v1"
FILENAME = "advisory-ledger.json"

STATUS_OPEN = "open"
STATUS_FULFILLED = "fulfilled"
STATUS_CANCELLED = "cancelled"
TERMINAL = {STATUS_FULFILLED, STATUS_CANCELLED}


def ledger_path(project: Path) -> Path:
    return project / "追踪" / FILENAME


def load(project: Path) -> dict:
    p = ledger_path(project)
    if not p.exists():
        return {"schema": SCHEMA, "entries": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        sys.stderr.write("[挂账] 台账损坏（%s），拒绝读写——请手工修复后再用\n" % e)
        raise SystemExit(2)
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        sys.stderr.write("[挂账] 台账结构不对（缺 entries 数组），拒绝读写\n")
        raise SystemExit(2)
    return data


def save(project: Path, data: dict) -> None:
    p = ledger_path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def next_id(entries: list) -> str:
    n = 1
    used = set()
    for e in entries:
        m = re.fullmatch(r"L(\d+)", str(e.get("id", "")))
        if m:
            used.add(int(m.group(1)))
    while n in used:
        n += 1
    return "L%03d" % n


def _as_int(v, default=None):
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return default


def cmd_add(args) -> int:
    project = Path(args.project)
    data = load(project)
    chapter = _as_int(args.chapter)
    payback = _as_int(args.payback)
    if chapter is None or payback is None:
        sys.stderr.write("[挂账] --chapter 与 --payback 都必须是章号数字\n")
        return 2
    if payback <= chapter:
        sys.stderr.write("[挂账] 承诺章（%d）应晚于违背章（%d）——当章就还的账不用挂\n"
                         % (payback, chapter))
        return 2
    entry = {
        "id": next_id(data["entries"]),
        "chapter": chapter,
        "rule": (args.rule or "").strip() or "unspecified",
        "payback_chapter": payback,
        "note": (args.note or "").strip(),
        "status": STATUS_OPEN,
        "created_at": date.today().isoformat(),
    }
    data["entries"].append(entry)
    save(project, data)
    print("[挂账] 已登记 %s：第 %d 章违背 %s → 承诺第 %d 章前偿还%s"
          % (entry["id"], chapter, entry["rule"], payback,
             ("（%s）" % entry["note"]) if entry["note"] else ""))
    return 0


def cmd_fulfill(args) -> int:
    project = Path(args.project)
    data = load(project)
    for e in data["entries"]:
        if e.get("id") == args.id and e.get("status") == STATUS_OPEN:
            e["status"] = STATUS_FULFILLED
            e["fulfilled_at"] = date.today().isoformat()
            if args.note:
                e["note"] = (e.get("note", "") + "｜已还：" + args.note).strip("｜")
            save(project, data)
            print("[挂账] %s 已销账" % args.id)
            return 0
    # 没找到可销的
    hit = [e for e in data["entries"] if e.get("id") == args.id]
    if hit:
        print("[挂账] %s 状态是 %s，无需销账" % (args.id, hit[0].get("status")))
        return 0
    sys.stderr.write("[挂账] 找不到 %s\n" % args.id)
    return 2


def annotate(entries: list, at_chapter: int) -> list:
    """给账目标注逾期信息。返回 [(entry, overdue_by)]，open 的在前。"""
    out = []
    for e in entries:
        if e.get("status") in TERMINAL:
            out.append((e, -1))
            continue
        pay = _as_int(e.get("payback_chapter"))
        over = (at_chapter - pay) if (pay is not None and at_chapter > pay) else 0
        out.append((e, over))
    open_first = [x for x in out if x[0].get("status") == STATUS_OPEN]
    done = [x for x in out if x[0].get("status") != STATUS_OPEN]
    open_first.sort(key=lambda x: -x[1])
    return open_first + done


def cmd_list(args) -> int:
    data = load(Path(args.project))
    at = _as_int(args.at) or _as_int(data.get("last_seen_chapter")) or 0
    rows = annotate(data["entries"], at)
    if not rows:
        print("[挂账] 台账为空——违背 advisory 时用 add 登记，承诺就不丢了")
        return 0
    print("账目（%d 笔，at 第 %s 章）：" % (len(rows), at or "?"))
    for e, over in rows:
        mark = ""
        if e.get("status") == STATUS_OPEN and over > 0:
            mark = "  ⚠ 逾期 %d 章" % over
        elif e.get("status") == STATUS_OPEN:
            mark = "  （欠到第 %s 章）" % e.get("payback_chapter")
        else:
            mark = "  [%s]" % e.get("status")
        print("  %s  第%s章 %-18s -> 第%s章%s%s"
              % (e.get("id"), e.get("chapter"), e.get("rule"),
                 e.get("payback_chapter"), mark,
                 ("｜%s" % e["note"]) if e.get("note") else ""))
    return 0


def cmd_overdue(args) -> int:
    data = load(Path(args.project))
    at = _as_int(args.at) or 0
    if not at:
        sys.stderr.write("[挂账] overdue 需要 --at 当前章\n")
        return 2
    rows = [(e, o) for e, o in annotate(data["entries"], at)
            if e.get("status") == STATUS_OPEN and o > 0]
    if not rows:
        print("[挂账] 无逾期")
        return 0
    for e, over in rows:
        print("  ⚠ %s 逾期 %d 章：第%s章 %s -> 承诺第%s章前还%s"
              % (e.get("id"), over, e.get("chapter"), e.get("rule"),
                 e.get("payback_chapter"),
                 ("｜%s" % e["note"]) if e.get("note") else ""))
    return 1 if rows else 0  # 逾期非零退出，方便脚本化；gate 只读不使用该码


def cmd_check(args) -> int:
    """gate 的只读视图：打印未销账与逾期，永不改变退出码之外的行为。"""
    data = load(Path(args.project))
    at = _as_int(args.at)
    if at is None:
        return 0
    rows = [(e, o) for e, o in annotate(data["entries"], at)
            if e.get("status") == STATUS_OPEN]
    if not rows:
        return 0
    over = [x for x in rows if x[1] > 0]
    print("  挂账          : open %d 笔%s" % (len(rows),
          ("｜⚠ 逾期 %d 笔" % len(over)) if over else ""))
    for e, o in rows:
        mark = "⚠ 逾期 %d 章" % o if o > 0 else "欠到第 %s 章" % e.get("payback_chapter")
        print("      %s  第%s章 %s -> %s｜%s"
              % (e.get("id"), e.get("chapter"), e.get("rule"), mark,
                 e.get("note") or "无备注"))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="advisory 挂账台账（故意违背时许的承诺，这里记账）")
    ap.add_argument("--project", required=True, help="书稿根目录（含 追踪/）")
    sub = ap.add_subparsers(dest="cmd")
    p_list = sub.add_parser("list", help="全部账目")
    p_list.add_argument("--at", help="当前章（算逾期用），缺省读台账内 last_seen_chapter")
    p_add = sub.add_parser("add", help="登记一笔挂账")
    p_add.add_argument("--chapter", required=True, help="违背发生的章")
    p_add.add_argument("--rule", required=True, help="规则/类型（如 dialogue-ratio）")
    p_add.add_argument("--payback", required=True, help="承诺偿还的章（必须晚于违背章）")
    p_add.add_argument("--note", help="理由/计划，一句话")
    p_ful = sub.add_parser("fulfill", help="销账")
    p_ful.add_argument("--id", required=True, help="账目 ID，如 L001")
    p_ful.add_argument("--note", help="销账备注")
    p_over = sub.add_parser("overdue", help="只看逾期（逾期时退出码 1）")
    p_over.add_argument("--at", required=True, help="当前章")
    p_chk = sub.add_parser("check", help="gate 只读视图")
    p_chk.add_argument("--at", help="当前章")
    args = ap.parse_args(argv)
    if not args.cmd:
        args.cmd = "list"
        args.at = None
    return {"list": cmd_list, "add": cmd_add, "fulfill": cmd_fulfill,
            "overdue": cmd_overdue, "check": cmd_check}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
