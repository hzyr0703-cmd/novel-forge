# -*- coding: utf-8 -*-
"""长篇写作的上下文方案成本测算。

回答一个具体问题：**有了 1M 上下文之后，还要不要检索层？**

结论预告：1M 让"全书塞进去"从不可能变成可能，
但让它变得毫无经济性——因为每写一章都要重传一遍全书。

用法
----
    python bench_context_cost.py                # 默认 150 万字 / 600 章
    python bench_context_cost.py --chars 3000000 --chapters 1200
    python bench_context_cost.py --rate 0.8     # 调token 换算率

注意
----
token 换算率各家 tokenizer 有差异，默认取 0.8（1 汉字≈0.8 token，
含标点/空行/章节标题）。想更保守用 1.0，更乐观用 0.6。
这个数不确定，但它只影响量级，不影响结论 —— 即使最乐观的 0.6，
全书仍占 1M 窗口的 90%，价格仍是两位数到四位数级别。
"""
from __future__ import annotations

import argparse

# 智谱官方价（元 / 百万 token），2026-11-15 复核快照，仅作量级估算；2027-02 后使用前先核对官网现价
# 复核结论：GLM-5.3 旗舰价未变（入 8 / 出 28 / 缓存 2，官网口径与三家价格汇总站一致）；
# GLM-5.3-Flash 的 5 折促销已于 2026-09-09 结束，下表已改回刊例价（入 0.8 / 出 2.8 / 缓存 0.23）
PRICES = {
    "GLM-5.3 旗舰":        {"in": 8.0, "out": 28.0, "cache": 2.0},
    "GLM-5.3 旗舰（缓存命中）": {"in": 8.0, "out": 28.0, "cache": 2.0, "use_cache": True},
    "GLM-5.3-Flash":       {"in": 0.8, "out": 2.8, "cache": 0.23},
}

# 单章写作实际需要的输入：细纲 + 摘要 + 角色卡 + 债务提示 + 召回top6
RECALL_IN = 5600
# 单章输出（正文 2500 字 ≈ 3000 token）
GEN_OUT = 3000
CONTEXT_LIMIT = 1_000_000


def hr(title: str = "") -> None:
    print("\n" + "=" * 84)
    if title:
        print(title)
        print("=" * 84)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chars", type=int, default=1_500_000)
    ap.add_argument("--chapters", type=int, default=600)
    ap.add_argument("--rate", type=float, default=0.8,
                    help="每汉字折算token 数，默认 0.8")
    args = ap.parse_args()

    tok = int(args.chars * args.rate)
    per_ch_chars = args.chars / args.chapters

    print(f"正文规模：{args.chars:,} 汉字 / {args.chapters} 章")
    print(f"单章平均：{int(per_ch_chars):,} 字")
    print(f"token 换算率：{args.rate}（1 汉字 ≈ {args.rate} token）")
    print(f"全书 token≈ {tok:,}")

    # ---- 一、1M 装不装得下 ----
    hr("一、1M 上下文装不装得下")
    print(f"{'换算率':<20}{'全书 token':<16}{'占 1M 窗口':<16}{'结论'}")
    for r in (1.0, 0.8, 0.6):
        t = int(args.chars * r)
        pct = t / CONTEXT_LIMIT * 100
        mark = "✅ 装得下" if pct <= 100 else f"❌ 超出 {pct - 100:.0f}%"
        print(f"{r:<20}{t:>13,}      {pct:>8.0f}%      {mark}")
    print("\n→ 即使最乐观（0.6）也要占 90% 窗口。写第 600 章时前 599 章全文都在输入里。")

    # ---- 二、方案 A：每章塞全书 ----
    hr("二、方案 A：每章塞全书（依赖 1M 上下文）")
    print("关键：不是付一次，是**每章都重传一遍**。全书越大，翻账越贵。\n")
    print(f"{'计费场景':<28}{'全程':<14}{'每章':<12}{'每万字':<12}{'第600章单章':<14}")
    a_totals = {}
    for name, p in PRICES.items():
        if "Flash" in name:
            continue
        price = p["cache"] if p.get("use_cache") else p["in"]
        total = args.chapters * tok / 1e6 * price
        a_totals[name] = total
        print(f"{name:<28}{total:>9,.0f} 元  {total/args.chapters:>7.1f} 元  "
              f"{total/(args.chars/10000):>7.2f} 元  {tok/1e6*price:>7.2f} 元")

    # ---- 三、方案 B：只送精准召回 ----
    hr("三、方案 B：只送精准召回（检索层方案）")
    print(f"单章输入 {RECALL_IN:,} token（细纲+摘要+角色卡+债务提示+召回top6）")
    print(f"单章输出 {GEN_OUT:,} token\n")
    print(f"{'模型':<28}{'每章':<14}{'全程':<16}{'每万字':<12}")
    b_totals = {}
    for name, p in PRICES.items():
        if "缓存命中" in name:
            continue
        per = RECALL_IN / 1e6 * p["in"] + GEN_OUT / 1e6 * p["out"]
        total = per * args.chapters
        b_totals[name] = total
        print(f"{name:<28}{per:>9.3f} 元  {total:>9,.0f} 元  "
              f"{total/(args.chars/10000):>7.2f} 元")

    # ---- 四、倍数差距 ----
    hr("四、差距")
    a_best = min(a_totals.values())
    b_best = min(b_totals.values())
    a_name = [k for k, v in a_totals.items() if v == a_best][0]
    b_name = [k for k, v in b_totals.items() if v == b_best][0]
    print(f"方案 A 最省（{a_name}）：{a_best:,.0f} 元")
    print(f"方案 B 最省（{b_name}）：{b_best:,.0f} 元")
    print(f"→ 相差 {a_best/b_best:,.0f} 倍\n")

    print("一句话结论：")
    print("  1M 上下文解决的是『装不装得下』，没解决『付不付得起』。")
    print("  而检索层解决的正是后者——它让每章输入从 12 万 token 降到 5.6 千，")
    print("  便宜 200 倍以上，且规模越大差距越夸张。")
    print()
    print("  所以 1M 上下文不是检索层的替代品，而是检索层的补充：")
    print("  · 前期全书塞得下，可以先不用检索，省掉搭索引的功夫")
    print("  · 后期全书塞不下，就必须召回 —— 这时候检索层是必需品，不是可选项")
    per_ch_tok = per_ch_chars * args.rate
    crit = int(CONTEXT_LIMIT / per_ch_tok) if per_ch_tok else 0
    print(f"  · 临界点约 {crit} 章"
          f"（单章 ≈{int(per_ch_tok):,} token × 章数 ≈ 1M 窗口；超过就得切召回）")
    if crit >= args.chapters:
        print(f"    → 你的目标 {args.chapters} 章 {'在临界点内' if crit >= args.chapters else ''}"
              f"，理论上全书塞得下，但仍要先算上面那笔账。")
    else:
        print(f"    → 你的目标 {args.chapters} 章超出临界点 {args.chapters - crit} 章，"
              f"检索层是必需品。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())