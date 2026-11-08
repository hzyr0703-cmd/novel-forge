#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
可选 LLM 语义审稿层 —— 默认关闭，有 API key 才启用。

红线边界（不要破坏）：
  1. 核心门禁（spec_check / lint / crossrepeat / factconflicts / platform）
     保持零 API、零网络、确定性——本脚本的任何输出**不参与**它们的退出码。
  2. 本脚本只做 advisory 语义审稿（人工审稿清单第 1–7 问的预审 + 反模式线索），
     第 8–10 问是作者身份问，永远人工，机器不代答。
  3. 零第三方依赖：仅标准库 urllib 直发 OpenAI 兼容 /chat/completions；
     不引入 openai SDK。
  4. 隐私：启用后**正文段落会发送到所配端点**。书稿是敏感创作资产，
     是否启用、发到哪个端点，由作者自行决定。

配置（环境变量）：
  NOVEL_FORGE_API_KEY   必填（启用开关；缺省 = 跳过并打印人工清单）
  NOVEL_FORGE_BASE_URL  默认 https://lai-hub.lenovomm.com/v1（token-hub 网关）
  NOVEL_FORGE_MODEL     默认 glm-5.3-flash（flash 档即可，审稿不需要旗舰）

供应商预设（`--provider`，省得记 base_url，避开踩过的坑）：
  zhipu        端点 open.bigmodel.cn/api/paas/v4 + 模型 glm-4.6（思考自动关闭）
  zhipu-flash  同上端点 + glm-4-flash（更快更省）
  tokenhub     自有网关 + glm-5.3-flash
  ⚠️ 智谱 key 打 token-hub 会 401；glm-4.6 / glm-4.5-* 默认开「思考」会让 content 为空，
     本脚本默认发 thinking={"type":"disabled"} 关掉（`--thinking` 可强制打开）。

用法：
  python llm_review.py --file 正文/draft/第N章.md               ← 单稿语义审稿
  python llm_review.py --book <书目录> --chapter N              ← 同上，自动定位
  python llm_review.py --file 正文/draft/第N章.md --coverage    ← 细纲兑现核对（自动找细纲）
  python llm_review.py --file <正文> --coverage --outline <细纲> ← 手动指定细纲
  python llm_review.py --pair <稿A.md> <稿B.md> [--outline <细纲>] ← 配对换位盲评
  python llm_review.py ... --provider zhipu                     ← 一键切智谱官方端点
  python llm_review.py --file ... --dry-run   ← 只打印将发送的 prompt，不联网
  python llm_review.py --file ... --json      ← 机器可读输出
  python llm_review.py ... --model <名>       ← 临时覆盖评委模型（与写手换家族）

三种模式：
  1. 单稿审稿（默认）：人工审稿 1–7 问预审 + 语义反模式线索。
  2. 细纲兑现核对（--coverage）：情节点逐条判 landed/summarized/missing，
     并列正文里细纲未授权、会影响后续章的新剧情事实。不评文笔，只核事实。
  3. 配对换位盲评（--pair）：同章两版稿随机分 A/B，**交换顺序各评一次，
     两次一致才算胜负**——位置偏见被对称抵消，评委不知道哪份是哪个版本。
     评委必须与写手不同模型家族（用 --model 换）。

退出码：0 = 正常/跳过/调用失败（advisory 层不阻断）；2 = 用法或文件错误。

设计原则：只读（不写任何文件）、纯标准库、失败不阻断流水线。
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_BASE_URL = "https://lai-hub.lenovomm.com/v1"
DEFAULT_MODEL = "glm-5.3-flash"
TIMEOUT = 60  # 本地修订 2026-11-06：原 240s 在端点不可达时单次调用可挂 4 分钟（--pair 两轮近 8 分钟）；60s 快速失败，advisory 层不值得长挂

# 供应商预设：--provider 一键配好端点与模型，省得记 base_url（踩过的坑）
#   智谱官方：key 打 token-hub 会 401，必须用 open.bigmodel.cn
#   注意 glm-4.6 / glm-4.5-* 默认开「思考」，content 会是空的（只剩 reasoning_content），
#   必须发 thinking={"type":"disabled"} 关掉，否则表现为「超时 / 回复不是有效 JSON」。
PROVIDERS = {
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.6",
        "thinking": "disabled",
    },
    "zhipu-flash": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "thinking": "disabled",
    },
    "tokenhub": {
        "base_url": DEFAULT_BASE_URL,
        "model": DEFAULT_MODEL,
        "thinking": None,
    },
}

# ---------------------------------------------------------------- 审稿口径
# 底稿来源：SKILL.md 第十一节人工审稿清单（第 1–7 问）+ references/anti-pattern-checklist.md
# 精判方向：非功能对话精判（spec_check 粗判只报数，这里给语义判断）。

SYSTEM_PROMPT = (
    "你是严格的网文审稿编辑。只依据给出的正文与检查清单判断，"
    "每条结论必须引用原文证据；拿不准就写「不确定」，禁止编造。"
)

REVIEW_PROMPT = """审这一章网文正文，逐项检查并输出 JSON（不要输出其他文字）：

【人工审稿预审（第 1–7 问）】
1. 闲笔：有没有至少一处不推进主线/爽点/伏笔的纯气氛、环境、人物小动作？
2. 非功能对话：有没有至少一句「删了不影响剧情」的对话（寒暄/跑题/口癖/
   答非所问/重复）？给出原句；一句都没有就明确说没有。
3. 句长波动：有没有长短句交替的呼吸节奏，还是全篇一个节奏？
4. 「没用但真实」的细节：有没有一个不服务剧情但让场景可信的细节？
5. 钩子：章末钩子是自然落下还是硬塞的？
6. 对话像不像人说的：有没有翻译腔、每句都带信息量、人人说话一个味？
7. 删掉测试：这章删掉，读者会不会觉得少了什么？

【语义级反模式】（有则给出原文证据，无则写「未检出」）
- 话疗：反派/BOSS 被主角几句话说服（豁免：盟友说服、有铺垫的退意）
- 循环模式：同类冲突或同类解法在本章内明显重复
- 同族结构陷阱：反派降智 / 高潮段变薄 / 工具人盟友 / 代价蒸发

输出 JSON 结构：
{
  "闲笔": {"有无": true/false, "证据": "原文引用"},
  "非功能对话": {"有无": true/false, "例句": "原文引用或 null"},
  "句长波动": {"评价": "..."},
  "没用但真实的细节": {"有无": true/false, "证据": "..."},
  "钩子": {"自然度": "自然/硬塞/不确定", "理由": "..."},
  "对话人味": {"评价": "..."},
  "删掉测试": {"评价": "..."},
  "反模式": {"话疗": "未检出/证据", "循环模式": "...", "同族陷阱": "..."},
  "总评": "两句话以内",
  "修改建议": ["最多三条，每条具体可执行"]
}

正文如下：
"""

# ---------------------------------------------------------------- 细纲兑现核对
# 口径来源：细纲的「分段骨架」表（段/字数/内容/必须发生/禁止发生）是情节点权威来源。
# 宁严勿宽：半场景半概括的，关键交锋被概括就判 summarized。

COVERAGE_SYSTEM = (
    "你是网文责编，只核对事实，不评文笔。拿不准就按较弱的判定写，禁止编造。"
)

COVERAGE_PROMPT = """你是网文责编，只核对事实，不评文笔。下面是一章的细纲和成稿正文。

任务一：把细纲里的情节点逐条列出（优先用「分段骨架」表的「必须发生」列逐行拆；
没有该表就按「本章任务」逐条拆），对每条在正文里找到对应内容并判定呈现方式：
- landed：演成了场景——读者看到逐拍的动作、对话或物件变化，关键交锋用人物的原话呈现；
- summarized：只用一两句概括带过（例如「他讲了十分钟证据」「两人争了几句」），或只给结果不给过程；
- missing：正文里完全没有。
宁严勿宽：半场景半概括的，关键交锋被概括就判 summarized。

任务二：列出正文里细纲没有授权、且**会影响后续章节**的新剧情事实
（新主线事件、新反转、新能力或规则、改变细纲已定结果、提前写后续章剧情）。
现场细节、路人、一次性对话不算。

只输出一个 JSON 对象，不要任何其他文字：
{{"beats": [{{"id": "1", "beat": "情节点一句话", "render": "landed|summarized|missing",
  "quote": "最能说明判定的正文原句（≤40字）"}}],
  "unauthorized": [{{"fact": "一句话", "quote": "正文原句片段"}}]}}

===== 细纲 =====
{outline}

===== 正文 =====
{body}
"""

# ---------------------------------------------------------------- 配对换位盲评
# 两次调用：第 1 轮 A=稿1/B=稿2，第 2 轮 A=稿2/B=稿1（换位）。
# 两次一致才判胜负，不一致记平——位置偏见被对称抵消，无需随机化，结果可复现。

PAIRWISE_SYSTEM = (
    "你是网文资深编辑，只依据成品判断，不因长短本身加减分，理由必须引用原文片段。"
)

PAIRWISE_PROMPT = """你是番茄小说的资深编辑。下面是两版第 {chapter} 章正文（A 与 B），来源未知。
请站在追更读者的角度判断哪一版更好看：更想往下读、人物更像活人、场面更具体、
更不像 AI 写的。只看成品，不因长短本身加减分。

只输出一个 JSON 对象，不要任何其他文字：
{{"winner": "A|B|tie", "reason": "两三句具体理由，必须引用原文片段"}}
{outline_block}
===== A =====
{a}

===== B =====
{b}
"""


def read(path):
    if not os.path.exists(path):
        sys.stderr.write("[LLM审稿] 文件不存在：%s\n" % path)
        sys.exit(2)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def strip_scaffold(text):
    """去 frontmatter/HTML 注释/标题行——发往端点的内容尽量只有散文正文。"""
    out, in_fm = [], False
    for i, raw in enumerate(text.splitlines()):
        s = raw.strip()
        if i == 0 and s == "---":
            in_fm = True
            continue
        if in_fm:
            if s == "---":
                in_fm = False
            continue
        if re.match(r"^(#{1,6}\s|>\s|[-*_]{3,}$)", s) or re.match(r"^第[零一二三四五六七八九十百千万\d]+章", s):
            continue
        out.append(re.sub(r"<!--.*?-->", "", raw))
    return "\n".join(out)


def resolve_chapter(book, chapter):
    """定位正文。兼容「第001章.md」与「第001章-章名.md」两种命名。"""
    names = ("第%03d章.md" % chapter, "第%d章.md" % chapter)
    pat = re.compile(r"^第0*%d章(?:[^0-9].*)?\.md$" % chapter)
    for sub in (("正文", "final"), ("正文", "draft"), ("正文",)):
        d = os.path.join(book, *sub)
        for nm in names:
            p = os.path.join(d, nm)
            if os.path.exists(p):
                return p
        if os.path.isdir(d):
            hits = sorted(f for f in os.listdir(d) if pat.match(f))
            if hits:
                return os.path.join(d, hits[0])
    sys.stderr.write("[LLM审稿] 找不到第 %d 章（试过 final/draft/正文，"
                     "兼容「第N章.md」与「第N章-章名.md」）\n" % chapter)
    sys.exit(2)


def resolve_outline(body_path):
    """从正文路径推导同书细纲：<书根>/细纲/<正文stem>-细纲.md，失败按章号 glob。"""
    stem = os.path.splitext(os.path.basename(body_path))[0]
    root = os.path.dirname(os.path.abspath(body_path))
    outdir = None
    for _ in range(5):
        cand = os.path.join(root, "细纲")
        if os.path.isdir(cand):
            outdir = cand
            break
        parent = os.path.dirname(root)
        if parent == root:
            break
        root = parent
    if outdir is None:
        return None
    p = os.path.join(outdir, stem + "-细纲.md")
    if os.path.exists(p):
        return p
    m = re.match(r"第0*(\d+)章", stem)
    if m:
        want = int(m.group(1))
        for name in sorted(os.listdir(outdir)):
            mm = re.match(r"第0*(\d+)章", name)
            if mm and int(mm.group(1)) == want:
                return os.path.join(outdir, name)
    return None


MAX_BODY_CHARS = 16000


def trim_body(text):
    """超长时截断并警告——防单次调用爆上下文。返回 (文本, 是否截断)。"""
    if len(text) <= MAX_BODY_CHARS:
        return text, False
    return text[:MAX_BODY_CHARS], True


def chapter_label(path):
    m = re.search(r"第0*(\d+)章", os.path.basename(path))
    return m.group(1) if m else None


def call_llm(base_url, api_key, model, user_prompt, system_prompt=SYSTEM_PROMPT, thinking=False):
    """OpenAI 兼容 /chat/completions。返回 (content, None) 或 (None, 错误信息)。

    thinking=False 时对支持思考的模型（智谱 glm-4.6/4.5-* 等）发关闭指令，
    否则响应里 content 为空、内容全在 reasoning_content，看起来就像超时或空回复。
    """
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }
    if not thinking:
        payload["thinking"] = {"type": "disabled"}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        return None, str(exc)
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None, "响应缺少 choices/message：%s" % json.dumps(data, ensure_ascii=False)[:200]
    content = msg.get("content") or ""
    if not content.strip():
        # 典型症状：模型只输出了思维链，正文为空 —— 给出可诊断的提示，别让它伪装成「不是有效 JSON」
        if msg.get("reasoning_content"):
            return None, ("模型只返回了思维链（reasoning_content），content 为空。"
                          "该模型默认开「思考」，请确认已发送 thinking={'type':'disabled'}，"
                          "或改用非思考模型（如 glm-4-flash）。")
        return None, "模型返回空 content（响应：%s）" % json.dumps(data, ensure_ascii=False)[:200]
    return content, None


def _scan_balanced_json(text):
    """扫出文本里所有「括号配对合法」的 JSON 候选，按出现顺序返回 (起点, 片段)。

    用括号深度计数 + 字符串状态机，能正确处理字符串内的括号与转义。
    内部还嵌套的对象也会同时被扫出，由调用方按「最外层优先」筛掉。
    """
    outs = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch not in "{[": 
            i += 1
            continue
        open_ch, close_ch = ("{", "}") if ch == "{" else ("[", "]")
        depth, in_str, esc, j = 0, False, False, i
        end = None
        while j < n:
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    end = j
                    break
            j += 1
        if end is not None:
            outs.append((i, text[i:end + 1]))
            i += 1        # 继续扫，内部嵌套候选也会被收集
        else:
            i += 1
    return outs


def extract_json(content):
    """从模型回复里抠 JSON。

    容忍四种形态：```json 围栏、前后夹带说明、推理模型把思维链写进 content、
    顶层是数组的 JSON。失败返回 None。

    策略：剥已闭合思维链 → 优先取围栏块 → 否则扫所有配对合法候选，
    **只保留最外层（不被别的候选包住）的候选，从中取最后一个能解析的**。
    取最后一个而非第一个很重要：思维链开头的示例 JSON 会冒充结果
    （曾把 <think>{"fake":1} 里的 fake 当真结果），而真正的答案在末尾。
    """
    if not content:
        return None
    # 1) 剥掉所有「已闭合」的思维链块（可能多个、可能混排）
    text = re.sub(r"<(?:think|thinking|reasoning)>.*?</(?:think|thinking|reasoning)>",
                  "", content, flags=re.S | re.I)
    # 2) 优先抓围栏块（围栏是模型明确的「这是答案」标记）
    fences = re.findall(r"```(?:json)?\s*([\{\[].*?[\}\]])\s*```", text, flags=re.S)
    for chunk in reversed(fences):
        try:
            parsed = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(parsed, (dict, list)):
            return parsed
    # 3) 扫所有配对合法的候选，筛出最外层（不被任何其它候选包含），取最后一个可解析
    cands = _scan_balanced_json(text)
    outer = [(s, c) for (s, c) in cands
             if not any(o != (s, c) and o[0] <= s and s + len(c) <= o[0] + len(o[1])
                        for o in cands)]
    if not outer:
        outer = cands
    for _, chunk in reversed(outer):
        try:
            parsed = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(parsed, (dict, list)):
            return parsed
    return None


def render_human(review, chapter_name):
    lines = ["=" * 72,
             "novel-forge 可选 LLM 语义审稿（advisory · 不参与门禁退出码）",
             "=" * 72,
             "  章节 : %s" % chapter_name, "-" * 72]
    for key in ("闲笔", "非功能对话", "句长波动", "没用但真实的细节", "钩子",
                "对话人味", "删掉测试", "反模式", "总评"):
        if key in review:
            lines.append("  [%s] %s" % (key, json.dumps(review[key], ensure_ascii=False)))
    sugg = review.get("修改建议") or []
    if sugg:
        lines.append("-" * 72)
        for i, s in enumerate(sugg, 1):
            lines.append("  [建议%d] %s" % (i, s))
    lines.append("-" * 72)
    lines.append("  第 8–10 问（复述/亲手写/不像我写的）是作者身份问，永远人工。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 通用调用壳

def print_no_key_hint(mode_name, fallback_lines):
    print("[LLM审稿] 未配置 NOVEL_FORGE_API_KEY —— 本层默认关闭（红线：核心门禁零 API）。")
    print("  %s改为人工执行：" % mode_name)
    for line in fallback_lines:
        print("    - %s" % line)
    print("  启用方法：export NOVEL_FORGE_API_KEY=... （端点/模型可用"
          " NOVEL_FORGE_BASE_URL / NOVEL_FORGE_MODEL / --model 覆盖）。")


def _think_tag(cfg):
    """输出用的思考状态标注。"""
    on = len(cfg) > 3 and cfg[3]
    return "（思考开启）" if on else "（思考已关）"


def ask_model(prompt, system_prompt, cfg):
    """统一调用。cfg = (api_key, base_url, model, thinking)。返回 (content, status)。"""
    api_key, base_url, model = cfg[0], cfg[1], cfg[2]
    thinking = cfg[3] if len(cfg) > 3 else False
    if not api_key:
        return None, "skip"
    content, err = call_llm(base_url, api_key, model, prompt, system_prompt, thinking)
    if err:
        shown = err if len(err) < 200 else err[:200] + "…"
        print("[LLM审稿] 调用失败（advisory 层不阻断）：%s" % shown)
        return None, "error"
    return content, "ok"


# ---------------------------------------------------------------- 模式：细纲兑现核对

def do_coverage(args, cfg, body_path, outline_path):
    body = strip_scaffold(read(body_path)).strip()
    outline = read(outline_path).strip()
    if len(re.sub(r"\s", "", outline)) < 50:
        sys.stderr.write("[LLM审稿] 细纲内容过短（<50 字），不核：%s\n" % outline_path)
        return 2
    body_trimmed, cut = trim_body(body)
    if cut:
        sys.stderr.write("[LLM审稿] 正文超 %d 字符，已截断后送审\n" % MAX_BODY_CHARS)
    prompt = COVERAGE_PROMPT.format(outline=outline, body=body_trimmed)

    if args.dry_run:
        print(prompt)
        print("\n<!-- dry-run：未联网。去掉 --dry-run 实审 -->")
        return 0

    content, status = ask_model(prompt, COVERAGE_SYSTEM, cfg)
    if status != "ok":
        if status == "skip":
            print_no_key_hint("细纲兑现核对", [
                "打开细纲「分段骨架」表，逐行对正文打勾：演成场景 / 一句话带过 / 没写",
                "扫一遍正文有没有细纲没授权的加戏（新反转、新规则、提前写后续章）",
            ])
        return 0

    data = extract_json(content)
    if data is None:
        print("[LLM审稿] 回复不是有效 JSON，原始回复：\n%s" % content)
        return 0

    beats = data.get("beats") or []
    tally = {"landed": 0, "summarized": 0, "missing": 0}
    for b in beats:
        r = (b.get("render") or "").strip()
        if r in tally:
            tally[r] += 1
    unauthorized = data.get("unauthorized") or []

    if args.json:
        print(json.dumps({"mode": "coverage",
                          "file": os.path.basename(body_path),
                          "outline": os.path.basename(outline_path),
                          "model": cfg[2], "tally": tally,
                          "beats": beats, "unauthorized": unauthorized},
                         ensure_ascii=False, indent=1))
        return 0

    out = ["=" * 72,
           "novel-forge 细纲兑现核对（advisory · 不参与门禁退出码）",
           "=" * 72,
           "  正文 : %s" % os.path.basename(body_path),
           "  细纲 : %s" % os.path.basename(outline_path),
           "  评委 : %s%s" % (cfg[2], _think_tag(cfg)),
           "-" * 72,
           "  情节点：landed %d ｜ summarized %d ｜ missing %d（合计 %d）"
           % (tally["landed"], tally["summarized"], tally["missing"], len(beats))]
    for b in beats:
        r = (b.get("render") or "?").strip()
        mark = {"landed": "✓", "summarized": "~", "missing": "✗"}.get(r, "?")
        q = b.get("quote") or ""
        out.append("    [%s %-10s] %s%s"
                   % (mark, r, (b.get("beat") or "")[:44],
                      ("  ｜「%s」" % q[:40]) if q else ""))
    out.append("-" * 72)
    if unauthorized:
        out.append("  越界新增（细纲未授权、会影响后续章）：")
        for i, u in enumerate(unauthorized, 1):
            out.append("    %d. %s ｜「%s」" % (i, u.get("fact") or "", (u.get("quote") or "")[:40]))
    else:
        out.append("  越界新增：未检出")
    out.append("-" * 72)
    out.append("  口径：✓=演成场景 ｜ ~=概括带过 ｜ ✗=没写。缺的多说明细纲没落地；")
    out.append("        越界新增多说明模型在自行加戏，要回看追踪台账。")
    print("\n".join(out))
    return 0


# ---------------------------------------------------------------- 模式：配对换位盲评

def do_pairwise(args, cfg, path_a, path_b):
    text_a = strip_scaffold(read(path_a)).strip()
    text_b = strip_scaffold(read(path_b)).strip()
    ta, cuta = trim_body(text_a)
    tb, cutb = trim_body(text_b)
    if cuta or cutb:
        sys.stderr.write("[LLM审稿] 有正文超 %d 字符，已截断后送评\n" % MAX_BODY_CHARS)

    ch = chapter_label(path_a) or chapter_label(path_b)
    outline_block = ""
    if args.outline:
        outline = read(args.outline).strip()
        outline_block = "\n===== 本章细纲（供判断情节点是否落地，两版共用） =====\n%s\n" % outline

    def build(first, second):
        return PAIRWISE_PROMPT.format(chapter=ch or "?",
                                      outline_block=outline_block,
                                      a=first, b=second)

    p1 = build(ta, tb)   # 第 1 轮：A=稿A B=稿B
    p2 = build(tb, ta)   # 第 2 轮：换位 A=稿B B=稿A

    if args.dry_run:
        print("### 第 1 轮（A=%s，B=%s）" % (os.path.basename(path_a), os.path.basename(path_b)))
        print(p1)
        print("\n### 第 2 轮·换位（A=%s，B=%s）" % (os.path.basename(path_b), os.path.basename(path_a)))
        print(p2)
        print("\n<!-- dry-run：未联网。两轮都实跑，一致才算胜负 -->")
        return 0

    c1, s1 = ask_model(p1, PAIRWISE_SYSTEM, cfg)
    if s1 != "ok":
        if s1 == "skip":
            print_no_key_hint("配对换位盲评", [
                "先读稿B再读稿A，写下哪版更好；隔一会儿再先读稿A后读稿B复核",
                "两次判断一致才算胜负，不一致就记平——位置会骗人",
                "评委尽量换个模型家族，别让写手评自己",
            ])
        return 0
    r1 = extract_json(c1)
    c2, s2 = ask_model(p2, PAIRWISE_SYSTEM, cfg)
    if s2 != "ok":
        print("[LLM审稿] 第 2 轮（换位）失败，单轮不足以下结论（位置偏见未抵消）。")
        return 0
    r2 = extract_json(c2)
    if r1 is None or r2 is None:
        print("[LLM审稿] 有回复不是有效 JSON，原始回复：\n第1轮：%s\n第2轮：%s" % (c1, c2))
        return 0

    name_a = os.path.basename(path_a)
    name_b = os.path.basename(path_b)

    def vote(winner, pos_a, pos_b):
        """把 A/B 判定换算回稿件名。"""
        w = (winner or "").strip().upper()
        if w == "A":
            return pos_a
        if w == "B":
            return pos_b
        return None

    v1 = vote(r1.get("winner"), name_a, name_b)   # 第1轮位置
    v2 = vote(r2.get("winner"), name_b, name_a)   # 第2轮换位
    if v1 is not None and v1 == v2:
        verdict, consistent = v1, True
    else:
        verdict, consistent = "平", False

    if args.json:
        print(json.dumps({"mode": "pairwise", "model": cfg[2],
                          "file_a": name_a, "file_b": name_b,
                          "winner": verdict, "consistent": consistent,
                          "rounds": [
                              {"order": "A=%s, B=%s" % (name_a, name_b),
                               "winner": r1.get("winner"), "reason": r1.get("reason")},
                              {"order": "A=%s, B=%s" % (name_b, name_a),
                               "winner": r2.get("winner"), "reason": r2.get("reason")},
                          ]}, ensure_ascii=False, indent=1))
        return 0

    out = ["=" * 72,
           "novel-forge 配对换位盲评（advisory · 不参与门禁退出码）",
           "=" * 72,
           "  稿件A : %s" % name_a,
           "  稿件B : %s" % name_b,
           "  评委  : %s（须与写手不同模型家族）%s" % (cfg[2], _think_tag(cfg)),
           "-" * 72,
           "  第 1 轮  A=%s  B=%s  →  %s" % (name_a, name_b, r1.get("winner")),
           "    %s" % (r1.get("reason") or ""),
           "  第 2 轮  A=%s  B=%s（换位）  →  %s" % (name_b, name_a, r2.get("winner")),
           "    %s" % (r2.get("reason") or ""),
           "-" * 72]
    if consistent:
        out.append("  结论：%s 胜（两轮一致，位置偏见已抵消）" % verdict)
    else:
        out.append("  结论：平（两轮不一致或含 tie）—— 两版差距不明显，别硬分胜负")
    print("\n".join(out))
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="可选 LLM 语义审稿层（默认关闭：无 NOVEL_FORGE_API_KEY 即跳过；只产 advisory）")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="正文 md 路径")
    src.add_argument("--book", help="书目录（配合 --chapter）")
    src.add_argument("--pair", nargs=2, metavar=("稿A", "稿B"),
                     help="配对换位盲评两个正文文件（交换顺序各评一次，一致才算胜负）")
    ap.add_argument("--chapter", type=int, help="章号（配合 --book）")
    ap.add_argument("--coverage", action="store_true",
                    help="细纲兑现核对：情节点逐条判 landed/summarized/missing + 越界加戏检测")
    ap.add_argument("--outline", help="细纲 md 路径（coverage 缺省时按正文路径自动推导）")
    ap.add_argument("--provider", choices=sorted(PROVIDERS),
                    help="供应商预设，一键配好端点+模型（zhipu / zhipu-flash / tokenhub）。"
                         "智谱 key 打 token-hub 会 401，用这个省得记 base_url")
    ap.add_argument("--model", help="覆盖评委模型（优先于 --provider / NOVEL_FORGE_MODEL；换家族用）")
    ap.add_argument("--thinking", action="store_true",
                    help="强制开启模型思考链（默认关闭）。glm-4.6/4.5-* 默认开思考会让 content 为空，"
                         "本脚本默认发 thinking={'type':'disabled'} 关掉")
    ap.add_argument("--dry-run", action="store_true", help="只打印将发送的 prompt，不联网")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    if args.pair and args.coverage:
        sys.stderr.write("[LLM审稿] --pair 与 --coverage 不能同时用\n")
        return 2
    if args.pair and args.chapter is not None:
        sys.stderr.write("[LLM审稿] --pair 不接受 --chapter\n")
        return 2

    preset = PROVIDERS.get(args.provider, {})
    api_key = os.environ.get("NOVEL_FORGE_API_KEY", "").strip()
    env_url = os.environ.get("NOVEL_FORGE_BASE_URL")
    env_model = os.environ.get("NOVEL_FORGE_MODEL")
    # 显式给了 --provider 就整组以预设为准：否则会出现「端点取 env、模型取 preset」
    # 的静默错配（旧坑：智谱 key 打到别的网关 → 401 且无提示）。
    if preset:
        base_url = preset.get("base_url") or DEFAULT_BASE_URL
        model = args.model or preset.get("model") or DEFAULT_MODEL
        if env_url and env_url != base_url:
            sys.stderr.write("[LLM审稿] 提示：--provider %s 已接管端点（%s），"
                             "环境变量 NOVEL_FORGE_BASE_URL（%s）本次忽略\n"
                             % (args.provider, base_url, env_url))
    else:
        base_url = env_url or DEFAULT_BASE_URL
        model = args.model or env_model or DEFAULT_MODEL
    thinking = args.thinking
    cfg = (api_key, base_url, model, thinking)

    # ---- 模式 3：配对换位盲评
    if args.pair:
        return do_pairwise(args, cfg, args.pair[0], args.pair[1])

    path = args.file
    if args.book:
        if args.chapter is None:
            sys.stderr.write("[LLM审稿] --book 需配合 --chapter\n")
            return 2
        path = resolve_chapter(args.book, args.chapter)

    # ---- 模式 2：细纲兑现核对
    if args.coverage:
        outline = args.outline or resolve_outline(path)
        if not outline:
            sys.stderr.write(
                "[LLM审稿] 找不到配套细纲（试过 <书根>/细纲/<章名>-细纲.md 与按章号匹配）；"
                "用 --outline 指定\n")
            return 2
        return do_coverage(args, cfg, path, outline)

    # ---- 模式 1：单稿语义审稿（原有）
    raw = read(path)
    prose = strip_scaffold(raw).strip()
    if len(re.sub(r"\s", "", prose)) < 200:
        sys.stderr.write("[LLM审稿] 有效正文过短（<200 字），不审\n")
        return 2
    user_prompt = REVIEW_PROMPT + prose

    if args.dry_run:
        print(user_prompt)
        print("\n<!-- dry-run：未联网。配置 NOVEL_FORGE_API_KEY / NOVEL_FORGE_BASE_URL /"
              " NOVEL_FORGE_MODEL 后去掉 --dry-run 实审 -->")
        return 0

    content, status = ask_model(user_prompt, SYSTEM_PROMPT, cfg)
    if status != "ok":
        if status == "skip":
            print_no_key_hint("单稿语义审稿", [
                "闲笔？非功能对话？句长波动？没用但真实的细节？",
                "钩子自然度？对话像人说的？删掉测试？",
            ])
        return 0
    review = extract_json(content)
    if review is None:
        print("[LLM审稿] 回复不是有效 JSON，原始回复：\n%s" % content)
        return 0
    if args.json:
        print(json.dumps({"file": os.path.basename(path), "model": model,
                          "review": review}, ensure_ascii=False, indent=1))
    else:
        print(render_human(review, os.path.basename(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
