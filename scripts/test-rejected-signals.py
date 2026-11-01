#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X 级已否决信号的守卫测试（v3.0）。

作用：证明 `references/rejected-signals.md` 里「已否决」的特征**没有被偷偷加回规则表**。

两层守卫：

1. **源码守卫**：X-2 / X-7 被否决的词族，不许出现在对应的规则常量里。
   这一层防的是「下次有人觉得这个像 AI，顺手加回去」——文档写了没人看，测试会拦。
2. **行为守卫**：喂真实样本，断言这些特征**零命中**。
   源码守卫查的是「有没有加回某个词」，行为守卫查的是「同义换词有没有绕过去」
   （比如把「仿佛」换成「好似」再加一条规则，源码守卫看不出来，行为守卫看得出来）。

另外做一次双向核对：文档里出现的每个 X-ID 必须在本脚本的 X_IDS 里登记，
反之亦然。加一条 X 级结论要改两个地方——这是刻意的摩擦。

用法：python scripts/test-rejected-signals.py [--verbose]
退出码：0 全部通过；1 有回归。
"""

import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOC = os.path.join(ROOT, "references", "rejected-signals.md")
LINT = os.path.join(HERE, "check-ai-patterns.js")

# X-ID → 源码守卫。None = 只有文档结论、无对应规则可守（这些特征从来没进过 lint）。
# tokens 里的词，一律不许出现在 const_name 这个常量里。
X_IDS = {
    "X-1": None,   # 序数词小标题：网文单章无小标题，从未立规则
    "X-2": {"const": "REASONING_CHAIN_PATTERNS", "tokens": ["这意味着", "也就是说", "换句话说"]},
    "X-3": None,   # 空转句引列表：从未立规则
    "X-4": None,   # 概括替代：从未立规则
    "X-5": None,   # 精确数字堆砌：从未立规则
    "X-6": None,   # 行业黑话：cliche 只收网文套式，靠本篇文档约束
    "X-7": {"const": "CLICHE_PATTERNS", "tokens": ["仿佛", "犹如", "宛若", "如同"]},
    "X-8": None,   # 句长配额：约束在 check-distribution.py（诊断问句，非 KPI）
    "X-9": None,   # 「其」字用量：从未立规则
    "X-10": {"const": "__behavior__", "tokens": []},  # 破折号：条件 blocking，见行为用例
}

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def const_source(js, name):
    """取出 `const NAME = [...];` 或 `const NAME = Object.freeze({...});` 的源码片段。"""
    m = re.search(r"const\s+%s\s*=\s*" % re.escape(name), js)
    if not m:
        return None
    start = m.end()
    # 括号配平：从赋值处往后找第一对平衡的 [ ] 或 { }
    open_ch = None
    depth = 0
    for i in range(start, len(js)):
        ch = js[i]
        if open_ch is None:
            if ch in "[{":
                open_ch = ch
                depth = 1
            continue
        if ch == open_ch:
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return js[start:i + 1]
    return None


def strip_line_comments(src):
    """去掉 `//` 行注释后再比对。

    常量源码里刻意留着「原本是 /仿佛|犹如/，v3.0 删除」这类说明——那是结论的证据，
    不该被守卫当成「又加回去了」。所以守卫只查**生效的代码**，不查注释里的记录。
    """
    out = []
    for line in src.split("\n"):
        code = re.sub(r"//.*$", "", line)
        out.append(code)
    return "\n".join(out)


def run_lint(paths):
    p = subprocess.run(["node", LINT, "--json", *paths], capture_output=True, text=True, encoding="utf-8")
    if not p.stdout.strip():
        raise SystemExit("跑不动 node check-ai-patterns.js：%s" % (p.stderr or ""))
    return json.loads(p.stdout)["findings"]


def main():
    verbose = "--verbose" in sys.argv
    js = read(LINT)
    doc = read(DOC)

    # ---- 0. 文档与本脚本的 X-ID 双向一致
    doc_ids = set(re.findall(r"\*\*(X-\d+)\*\*", doc))
    check("文档里的 X-ID 全部在脚本登记", not (doc_ids - set(X_IDS)), "未登记：%s" % (doc_ids - set(X_IDS)))
    check("脚本的 X-ID 全部在文档有结论", not (set(X_IDS) - doc_ids), "文档缺：%s" % (set(X_IDS) - doc_ids))

    # ---- 1. 源码守卫
    for xid, guard in X_IDS.items():
        if not guard or guard["const"] == "__behavior__":
            continue
        src = const_source(js, guard["const"])
        if src is None:
            check("%s 守卫能定位 %s" % (xid, guard["const"]), False, "常量找不到")
            continue
        leaked = [t for t in guard["tokens"] if t in strip_line_comments(src)]
        check("%s 已否决词未回到 %s" % (xid, guard["const"]), not leaked,
              "又加回去了：%s" % "、".join(leaked))

    # ---- 2. 行为守卫（同义换词也拦得住）
    tmp = tempfile.mkdtemp(prefix="nf-rejected-")
    cases = {
        "x2": "这意味着他得走。也就是说没人留下来。换句话说，门已经关了。\n\n他把外套穿上。\n",
        "x2_syn": "这就代表他得走。换言之没人留下来。等于说门已经关了。\n\n他把外套穿上。\n",
        "x7_apart": ("她的眼睛像灯。\n\n他站着没动，手垂在身侧。\n\n声音像风一样从门缝进来。\n\n"
                     "他没回头。\n\n手指像铁一样凉。\n\n外面有人推车过去。\n"),
        "x10_quote": "“你——”\n\n他没说完。\n\n“我不知道。”老陈说，“就算知道——”\n\n“就算知道怎样？”\n",
    }
    paths = {}
    for key, body in cases.items():
        fp = os.path.join(tmp, key + ".md")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(body)
        paths[key] = fp

    findings = run_lint(list(paths.values()))
    by_file = {}
    for f in findings:
        by_file.setdefault(os.path.basename(f["file"]).split(".")[0], []).append(f)

    x2 = by_file.get("x2", [])
    check("X-2 行为：「这意味着/也就是说/换句话说」不报解释链",
          not [f for f in x2 if f["type"] == "reasoning-chain-tic"], json.dumps(x2, ensure_ascii=False)[:120])

    x2s = by_file.get("x2_syn", [])
    check("X-2 行为：同义换词（换言之/等于说）也不报",
          not [f for f in x2s if f["type"] == "reasoning-chain-tic"], json.dumps(x2s, ensure_ascii=False)[:120])

    x7 = by_file.get("x7_apart", [])
    check("X-7 行为：比喻分散在不同段落时不报",
          not [f for f in x7 if f["type"] == "metaphor-density-tic"], json.dumps(x7, ensure_ascii=False)[:120])

    x10 = by_file.get("x10_quote", [])
    check("X-10 行为：引号内破折号不判 blocking",
          not [f for f in x10 if f["type"] == "em-dash" and f["severity"] == "blocking"],
          json.dumps(x10, ensure_ascii=False)[:160])

    # ---- 3. 反向对照：成片堆叠仍要报（否则等于把规则删了）
    dense = os.path.join(tmp, "x7_dense.md")
    with open(dense, "w", encoding="utf-8") as f:
        f.write("她的眼睛像灯，声音像风，手指像铁。\n\n他站着没动。\n")
    d = run_lint([dense])
    check("对照：同段 3 个比喻仍报 metaphor-density-tic",
          any(f["type"] == "metaphor-density-tic" for f in d), json.dumps(d, ensure_ascii=False)[:120])

    # ---- 4. category 字段存在（X 级讨论的前提是「人类手法」与「AI 腔」分得开）
    allf = findings + d
    check("每条 finding 都带 category", all("category" in f for f in allf),
          "缺字段：%s" % [f["type"] for f in allf if "category" not in f][:5])
    check("比喻归 literary 类", all(f["category"] == "literary" for f in d if f["type"] == "metaphor-density-tic"),
          json.dumps([f.get("category") for f in d], ensure_ascii=False))

    # ---- 汇总
    failed = [r for r in results if not r[1]]
    for name, ok, detail in results:
        if verbose or not ok:
            print("%s %s%s" % ("PASS" if ok else "FAIL", name, ("  ← " + detail) if (detail and not ok) else ""))
    print("-" * 64)
    print("X 级守卫：%d/%d 通过" % (len(results) - len(failed), len(results)))
    if failed:
        print("回归项：%s" % "、".join(r[0] for r in failed))
        print("如果是有意推翻 X 级结论，请同时更新 references/rejected-signals.md 与本脚本的 X_IDS。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
