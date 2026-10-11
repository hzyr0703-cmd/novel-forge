# -*- coding: utf-8 -*-
"""BM25 召回基准 —— 回答"要不要向量层"用真实数据，不靠感觉。

背景
----
向量层（embedding + rerank）能补语义改写召回，但要让用户掏钱/配 key，
就得先证明 BM25 到底缺什么。本脚本量化这个缺口。

做法
----
造N 章语料，埋入 K 条答案，其中每条答案与它的查询**零实词重叠**
（自检不通过直接退出，防止自己把答案抄进查询造成假100%）。
跑两组对照：
  · 词面组：查询词直接抄自答案 → BM25 的上限
  · 改写组：查询是场景描述，答案用完全不同的措辞 → BM25 的真实短板
两组 recall 的差值，就是向量层要补的部分。

踩过的坑（别重犯）
----------------
1. 第一版把查询串原样抄进 payload → recall@6 假性 100%。
2. 第二版「删除埋点看结果是否变化」→ 删除后答案已不存在，
   结果当然不变，这个证伪设计无效。
3. 第三版「搬动埋点看top6 是否跟随」→ 合成语料本身无语义，
   top6 由长度和位置噪声决定，测不出语义能力。
结论：评估检索必须用「查询/答案配对 + recall@k」，不要用 top6 目视。

用法
----
    python bench_bm25.py            # 默认 600 章 / 5 组
    python bench_bm25.py --ch 900   # 更大规模
"""
from __future__ import annotations

import argparse
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import recall as R  # noqa: E402

# 高频虚词/代词：中文里无法回避，且 BM25 靠 IDF 加权后权重极低。
# 计算"实词重叠"时排除，否则永远达不到零重叠。
STOP = set(
    "他她的了的着过是有人这那一个们不也就都被把和与在为之上下来去"
    "说出到会时要没还只又能可但如则及其此所以然而并更还又很再从向给让"
)

# (查询, 答案, 埋点章)
# 答案里刻意塞唯一标记码，方便人工确认召回的是哪一条。
CASES = [
    ("他被人强行喂下毒物",
     "逼那具身躯吞下苦胆草的，正是昔日同门。ANS1", 337),
    ("有个双目失明的老人认出他",
     "那对早已浑浊的眼珠，仅凭足音便唤出了你的幼名。ANS2", 268),
    ("山门被雪压塌师父替他挡",
     "崩落的巨石当头砸下，那位白发男子一把将你推出屋舍。ANS3", 415),
    ("旧友其实是敌人派来的内应",
     "三年来的殷勤问候，皆是对手布下的棋子。ANS4", 191),
    ("他失去的记忆逐渐苏醒",
     "梦中反复出现的火光终于有了具体的名姓。ANS5", 522),
]

# 词面组：查询词抄自对应答案的实词，代表 BM25 能达到的上限
DIRECT = [
    ("苦胆草 同门", 337),
    ("浑浊 眼珠 幼名", 268),
    ("崩落 巨石 屋舍", 415),
    ("殷勤问候 棋子", 191),
    ("梦中 火光 名姓", 522),
]

SURNAME = ["顾", "沈", "陆", "苏", "裴", "姜", "萧", "temp", "楚", "洛"]
GIVEN = ["长风", "清河", "照野", "知微", "停舟", "观澜", "抱一", "重霄", "拂衣", "寒山"]
LOC = ["青玄门", "落霞谷", "听雪楼", "漠北", "东海墟", "南陵", "太虚幻境", "赤霄城", "幽冥海"]
ITEM = ["断霜剑", "照心镜", "九转丹", "缚龙索", "太虚符", "沉渊印", "归元珠", "斩尘刀"]
SECT = ["太清", "玄冥", "天枢", "曲阳", "寒山派", "流云谷", "紫霄宫", "万兽山庄"]
EVENT = ["血战", "闭关", "夺宝", "问剑", "祭祀", "逃亡", "顿悟", "反杀", "血洗", "交易"]
BODY = ["灵气", "经脉", "丹田", "真元", "识海", "气血", "剑意", "道韵", "神魂", "元婴"]


def core_chars(s: str) -> set[str]:
    return set(re.findall(r"[\u4e00-\u9fff]", s)) - STOP


def self_check() -> None:
    """自检：答案与查询必须零实词重叠，否则整个实验无效。"""
    bad = []
    for q, ans, _ in CASES:
        ov = core_chars(q) & core_chars(ans)
        if ov:
            bad.append((q, "".join(sorted(ov))))
    if bad:
        print("❌ 实验无效 —— 查询与答案存在共有实词：")
        for q, ov in bad:
            print(f"   {q!r}  共有：{ov}")
        print("   改写答案措辞后重跑，不要绕过这个检查。")
        sys.exit(1)
    print(f"✅ 自检通过：{len(CASES)} 条答案与查询零共有实词（虚词已排除）\n")


def make_sentence(rng: random.Random) -> str:
    a = rng.choice(SURNAME) + rng.choice(GIVEN)
    b = rng.choice(SURNAME) + rng.choice(GIVEN)
    return rng.choice([
        f"{a}握紧{rng.choice(ITEM)}，脚下{rng.choice(SECT)}的{rng.choice(BODY)}翻涌不休。",
        f"{b}冷声道：'{rng.choice(EVENT)}之事岂是儿戏，{a}你莫要自误。'",
        f"两人在{rng.choice(LOC)}对峙，{rng.choice(BODY)}碰撞处爆开{rng.choice(EVENT)}的余波。",
        f"{a}想起三年前在{rng.choice(LOC)}的那一战，{rng.choice(BODY)}至今未复。",
        f"{b}望向{rng.choice(LOC)}方向，那里是{rng.choice(SECT)}的禁地。",
        f"{rng.choice(BODY)}在{a}经脉中横冲直撞，痛得他额角青筋暴起。",
        f"{b}沉默良久，终是叹了口气：'{rng.choice(EVENT)}，我早说过不该碰。'",
    ])


def build(root: Path, n_ch: int, seed: int) -> None:
    rng = random.Random(seed)
    (root / "正文").mkdir(parents=True)
    (root / "追踪").mkdir(parents=True)
    used = {ch for _, _, ch in CASES}
    for ch in range(1, n_ch + 1):
        blocks = ["\n\n".join(make_sentence(rng) for _ in range(4))
                  for _ in range(9)]
        for _, ans, c in CASES:
            if ch == c:
                blocks[-1] += "\n\n" + ans
        (root / "正文" / f"{ch:04d}.md").write_text(
            f"# 第{ch}章\n\n" + "\n\n".join(blocks), encoding="utf-8")
    (root / "追踪" / "_tracking-state.json").write_text(
        '{"characters":[],"foreshadowing":[],"timeline_events":[]}',
        encoding="utf-8")


def recall_at(book: Path, idx: dict, q: str, want: int, k: int) -> tuple[bool, int]:
    chs = [h.get("meta", {}).get("chapter")
           for h in R._search(idx, q, k, ["chapter"], book)]
    return (want in chs, chs.index(want) + 1 if want in chs else 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ch", type=int, default=600)
    ap.add_argument("--seed", type=int, default=20261006)
    ap.add_argument("--top", type=int, default=6)
    args = ap.parse_args()

    self_check()

    base = Path(__file__).resolve().parent.parent / ".bench-tmp"
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    book = base / f"bm25bench_{args.ch}"
    build(book, args.ch, args.seed)
    subprocess.run([sys.executable, str(SCRIPTS / "recall.py"), "--project",
                    str(book), "index", "--no-vector", "--quiet"],
                   check=True, capture_output=True)
    idx = R._load_index(book)
    print(f"语料：{args.ch} 章 / {len(idx['chunks'])} 片段 / 模式={idx.get('mode')}")
    print(f"查询 top-N = {args.top}\n")

    k = args.top
    print("── 词面组（查询抄自答案）：BM25 的上限 ──")
    hit_d = 0
    for q, want in DIRECT:
        ok, rank = recall_at(book, idx, q, want, k)
        hit_d += ok
        print(f"  {q:<20} → {'✓ 第%d位' % rank if ok else '✗ 漏检'}")
    print(f"\n  recall@{k} = {hit_d}/{len(DIRECT)} ({hit_d/len(DIRECT):.0%})\n")

    print("── 改写组（零实词重叠）：BM25 的真实短板 ──")
    hit_r = 0
    for q, _, want in CASES:
        ok, rank = recall_at(book, idx, q, want, k)
        hit_r += ok
        print(f"  {q:<20} → {'✓ 第%d位' % rank if ok else '✗ 漏检'}")
    print(f"\n  recall@{k} = {hit_r}/{len(CASES)} ({hit_r/len(CASES):.0%})\n")

    gap = hit_d - hit_r
    print("=" * 60)
    print(f"词面 {hit_d/len(DIRECT):.0%} vs 改写 {hit_r/len(CASES):.0%}"
          f"  → 缺口 {gap}/{len(CASES)}")
    print("这个缺口就是向量层（embedding + rerank）要补的部分。")
    print("结论：缺口≈0 → 只用 BM25，别配 key；缺口显著 → 值得开向量层。")
    print(f"\n（临时语料在 {book}，可自行删除）")
    return 0


if __name__ == "__main__":
    sys.exit(main())