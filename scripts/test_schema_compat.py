# -*- coding: utf-8 -*-
"""recall.py / debt.py 的台账 schema 兼容回归测试。

背景：早期两个脚本把台账字段名写死了（foreshadow / last_chapter /
planted_chapter / constraints在 context 下），换成另一种写法就**静默漏检**——
不报错、不提示，只是永远检索不到或算不出债务，排查成本极高。

这类 bug 必须有测试守着。现在两种写法都要能跑通。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import debt as D  # noqa: E402
import recall as R  # noqa: E402

# 两种台账写法，内容等价
STD = {
    "schema_version": 1,
    "last_chapter": 40,
    "context": {
        "long_term_constraints": ["玉佩不能在前50章揭示来源"],
        "continuity_risks": ["顾长风与师父外貌相似易被误认"],
        "next_chapter_commitments": ["必须交代玉佩的来历"],
        "recent_chapters": [{"chapter": 39, "summary": "落霞谷旧账被翻出"}],
    },
    "character_snapshots": {"顾长风": {"identity": "落霞谷遗孤", "goal": "查清师门惨案"}},
    "foreshadow": [{"id": "fs_玉佩", "summary": "随身玉佩来路不明",
                "planted_chapter": 1, "planned_resolution_chapter": 80,
                "importance": "高", "status": "open"}],
    "timeline_events": [{"id": "e1", "story_time": "景元三年春",
                   "objective_fact": "落霞谷之战，师父殒命",
                   "reader_knowledge": "读者已知师父之死",
                   "reveal_status": "已揭示"}],
}
ALT = {
    "schema_version": 1,
    "last_chapter_written": 40,
    "characters": [{"id": "c1", "name": "顾长风", "identity": "落霞谷遗孤",
                "goal": "查清师门惨案"}],
    "constraints": [{"id": "r1", "text": "玉佩不能在前50章揭示来源"}],
    "risks": [{"id": "k1", "text": "顾长风与师父外貌相似易被误认"}],
    "commitments": ["必须交代玉佩的来历"],
    "recent": [{"chapter": 39, "summary": "落霞谷旧账被翻出"}],
    "foreshadowing": [{"id": "fs_玉佩", "desc": "随身玉佩来路不明",
                 "planted_ch": 1, "target_ch": 80,
                 "importance": "高", "status": "open"}],
    "timeline_events": [{"id": "e1", "story_time": "景元三年春",
                   "objective_fact": "落霞谷之战，师父殒命",
                   "reader_knowledge": "读者已知师父之死",
                   "reveal_status": "已揭示"}],
}

FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global FAIL
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f" —— {detail}" if detail and not ok else ""))
    if not ok:
        FAIL += 1


def kinds(chunks: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in chunks:
        out[c["kind"]] = out.get(c["kind"], 0) + 1
    return out


# 真实书稿《燃识》提交后的形态：foreshadow / timeline / characters 全是
# {id: row} 字典，且真相键名是 `timeline` 而非 `timeline_events`。
# 这套形态曾让 13 条伏笔全部静默消失、debt.py 直接抛AttributeError。
COMMITTED = {
    "schema_version": 1,
    "book_title": "燃识",
    "last_committed_chapter": 9,
    "state_revision": 11,
    "foreshadow": {
        "F001": {"id": "F001", "summary": "溢出记录被人为封存删除，本月第三回",
               "planted_chapter": 1, "planned_resolution_chapter": 105,
               "importance": "高", "status": "已埋", "updated_chapter": 5},
        "F010": {"id": "F010", "summary": "每周送净识剂的人只露一双手、戴白手套",
               "planted_chapter": 6, "planned_resolution_chapter": 40,
               "importance": "中", "status": "已埋", "updated_chapter": 8},
    },
    "timeline": {
        "E001": {"id": "E001", "story_time": "觉醒日当夜",
               "objective_fact": "第317号检测仪读数记录为溢出，被封存删除",
               "reader_knowledge": "读者在第1章末看到维修工删除记录",
               "reveal_status": "部分揭示", "reveal_chapter": 1,
               "first_recorded_chapter": 1, "updated_chapter": 3},
    },
    "characters": {
        "林灼": {"id": "c1", "name": "林灼", "location": "医院", "goal": "查清读数异常"},
    },
    "context": {"position": {"volume": "第一卷", "story_time": "第9章",
                           "scene": "医院"},
                "recent_chapters": [{"chapter": 8, "summary": "白手套来了"}]},
}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nf_schema_"))
    try:
        book = tmp / "book"
        (book / "正文").mkdir(parents=True)
        (book / "正文" / "0001.md").write_text(
            "# 第1章\n\n顾长风推门而入，落霞谷的雪尚未化。\n", encoding="utf-8")

        print("【召回层】两种 schema 应产出相同的 kind 分布")
        seen = {}
        for tag, state in (("标准", STD), ("别名", ALT)):
            (book / "追踪").mkdir(exist_ok=True)
            (book / "追踪" / "_tracking-state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8")
            cs = R._chunks_from_state(state)
            seen[tag] = kinds(cs)
            print(f"  {tag}：{seen[tag]}")

        for k in ("foreshadow", "timeline", "constraint", "risk", "commitment", "recent"):
            check(f"kind={k} 两种写法都在",
                  seen["标准"].get(k) == 1 and seen["别名"].get(k) == 1,
                  f"标准={seen['标准'].get(k)} 别名={seen['别名'].get(k)}")

        print("\n【召回层】角色与条目内容不为空")
        for tag, state in (("标准", STD), ("别名", ALT)):
            cs = R._chunks_from_state(state)
            chars = [c for c in cs if c["kind"] == "character"]
            check(f"{tag} 角色召回", len(chars) == 1 and bool(chars[0]["text"].strip()),
                  f"chars={len(chars)}")
            cons = [c for c in cs if c["kind"] == "constraint"]
            txt = cons[0]["text"] if cons else ""
            check(f"{tag} 约束不是 Python dict", "{" not in txt and "玉佩" in txt,
                  repr(txt[:60]))

        print("\n【召回层】伏笔摘要取到（非「缺摘要」）")
        for tag, state in (("标准", STD), ("别名", ALT)):
            cs = [c for c in R._chunks_from_state(state) if c["kind"] == "foreshadow"]
            body = cs[0]["text"] if cs else ""
            check(f"{tag} 伏笔正文含摘要", "玉佩" in body and "缺摘要" not in body,
                  repr(body[:60]))

        print("\n【债务层】两种 schema 应算出同一条债、同等紧迫度")
        urg = {}
        for tag, state in (("标准", STD), ("别名", ALT)):
            rows = D.compute_debts(state)
            urg[tag] = [(r["id"], round(r["urgency"], 3), r["planted_chapter"],
                         r["planned_resolution_chapter"]) for r in rows]
            print(f"  {tag}：{urg[tag]}")
        check("债务条数一致", len(urg["标准"]) == len(urg["别名"]) == 1,
              f"{len(urg['标准'])} vs {len(urg['别名'])}")
        check("紧迫度/章号完全一致", urg["标准"] == urg["别名"],
              f"{urg['标准']} != {urg['别名']}")

        print("\n【债务层】当前章号读取（别名 last_chapter_written）")
        check("标准 last_chapter=40", D._current_ch(STD) == 40, str(D._current_ch(STD)))
        check("别名 last_chapter_written=40", D._current_ch(ALT) == 40, str(D._current_ch(ALT)))
        check("都缺失时回落 0 而非报错", D._current_ch({}) == 0, str(D._current_ch({})))

        print("\n【债务层】摘要别名")
        rows = D.compute_debts(ALT)
        check("别名 schema 能取到摘要", bool(rows) and "玉佩" in rows[0]["summary"],
              repr(rows[0]["summary"] if rows else ""))

        print("\n【回归】真实书稿形态（foreshadow/timeline 为 dict，键名 timeline）")
        cs = R._chunks_from_state(COMMITTED)
        kd = kinds(cs)
        check("伏笔 2 条都召回", kd.get("foreshadow") == 2, str(kd))
        check("真相 1 条召回（键名 timeline）", kd.get("timeline") == 1, str(kd))
        check("角色 1 条召回（characters dict）", kd.get("character") == 1, str(kd))

        print("\n【回归】伏笔内容真的取到（不是空壳）")
        fs = [c for c in cs if c["kind"] == "foreshadow"]
        check("伏笔摘要非空", all("白手套" in c["text"] or "溢出" in c["text"] for c in fs),
              str([c["text"][:40] for c in fs]))
        tl = [c for c in cs if c["kind"] == "timeline"]
        check("真相客观事实非空", bool(tl) and "317" in tl[0]["text"],
              repr(tl[0]["text"][:60]) if tl else "无")

        print("\n【回归】债务层不崩且读出last_committed_chapter")
        rows = D.compute_debts(COMMITTED)
        check("算出 2 条债", len(rows) == 2, str(len(rows)))
        check("当前章读成 9", D._current_ch(COMMITTED) == 9, str(D._current_ch(COMMITTED)))
        check("摘要不显示「缺摘要」",
              all("缺摘要" not in r["summary"] for r in rows),
              str([r["summary"] for r in rows]))

        print("\n【回归】真相揭示体检不崩")
        rev = D.compute_reveals(COMMITTED)
        check("真相体检有输出", len(rev) == 1, str(len(rev)))

        print("\n" + "-" * 60)
        if FAIL:
            print(f"{FAIL} 项失败")
            return 1
        print("schema 兼容测试全部通过")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())