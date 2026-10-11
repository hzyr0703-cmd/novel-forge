#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
llm_review.py 的离线逻辑测试 —— 不联网、不读 key。

覆盖 v2.3 新增的两个模式：
  1. 配对换位盲评的投票换算（两轮一致才算胜负）
  2. 细纲兑现核对的 tally 统计与越界渲染
  3. 细纲路径自动推导
  4. 长度截断保护

用法：python test_llm_review_pairing.py
退出码：0 = 全绿；1 = 有用例失败。
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "llm_review.py")

spec = importlib.util.spec_from_file_location("nf_llm_review", TARGET)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("  ok  " if cond else " FAIL ", name,
                       ("  <- " + detail) if (detail and not cond) else ""))


class Args(object):
    def __init__(self, **kw):
        self.outline = None
        self.json = False
        self.dry_run = False
        for k, v in kw.items():
            setattr(self, k, v)


def run_capture(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*a, **kw)
    return rc, buf.getvalue()


def with_fake_llm(responses):
    """把 ask_model 换成按序返回预设 JSON 的假实现。"""
    seq = list(responses)
    seen = []

    def fake(prompt, system_prompt, cfg):
        seen.append(prompt)
        if not seq:
            return json.dumps({}), "ok"
        return seq.pop(0), "ok"

    m.ask_model = fake
    return seen


def write_tmp(name, content):
    d = tempfile.mkdtemp(prefix="nf_llmrev_")
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p


BODY_A = "正文A" + "。这是一句测试正文。" * 40
BODY_B = "正文B" + "。这是另一版测试正文。" * 40
CFG = ("fake-key", "http://invalid.local", "test-model")


# ---------------------------------------------------------------- 1. 换位盲评投票
def test_pairwise_consistent_win():
    """两轮都指向稿A → 稿A 胜（第2轮作者读到的 B 位才是稿A）。"""
    pa = write_tmp("稿A.md", BODY_A)
    pb = write_tmp("稿B.md", BODY_B)
    with_fake_llm([
        json.dumps({"winner": "A", "reason": "第一轮理由"}),   # A=稿A
        json.dumps({"winner": "B", "reason": "第二轮理由"}),   # 换位后 B=稿A
    ])
    rc, out = run_capture(m.do_pairwise, Args(pair=[pa, pb]), CFG, pa, pb)
    check("盲评·两轮一致 → 稿A胜", rc == 0 and "稿A.md 胜" in out, out[-200:])
    check("盲评·标注位置偏见已抵消", "位置偏见已抵消" in out)


def test_pairwise_inconsistent_is_tie():
    """第1轮投A（稿A），第2轮也投A（此时 A=稿B）→ 不一致 → 平。"""
    pa = write_tmp("稿A.md", BODY_A)
    pb = write_tmp("稿B.md", BODY_B)
    with_fake_llm([
        json.dumps({"winner": "A", "reason": "r1"}),   # A=稿A → 投稿A
        json.dumps({"winner": "A", "reason": "r2"}),   # A=稿B → 投稿B
    ])
    rc, out = run_capture(m.do_pairwise, Args(pair=[pa, pb]), CFG, pa, pb)
    check("盲评·两轮矛盾 → 平", rc == 0 and "结论：平" in out, out[-200:])


def test_pairwise_tie_winner():
    """任一轮 tie → 平。"""
    pa = write_tmp("稿A.md", BODY_A)
    pb = write_tmp("稿B.md", BODY_B)
    with_fake_llm([
        json.dumps({"winner": "tie", "reason": "r1"}),
        json.dumps({"winner": "B", "reason": "r2"}),
    ])
    rc, out = run_capture(m.do_pairwise, Args(pair=[pa, pb]), CFG, pa, pb)
    check("盲评·含 tie → 平", rc == 0 and "结论：平" in out, out[-200:])


def test_pairwise_second_round_fails():
    """第 2 轮失败 → 明确拒绝下结论（位置偏见未抵消）。"""
    pa = write_tmp("稿A.md", BODY_A)
    pb = write_tmp("稿B.md", BODY_B)
    seq = [json.dumps({"winner": "A", "reason": "r1"})]

    def fake(prompt, system_prompt, cfg):
        if seq:
            return seq.pop(0), "ok"
        return None, "error"

    m.ask_model = fake
    rc, out = run_capture(m.do_pairwise, Args(pair=[pa, pb]), CFG, pa, pb)
    check("盲评·第2轮失败不下结论", rc == 0 and "位置偏见未抵消" in out, out[-200:])


def test_pairwise_prompt_swapped():
    """两轮 prompt 必须真的换了位（A/B 内容对调）。"""
    pa = write_tmp("稿A.md", "甲" * 900)
    pb = write_tmp("稿B.md", "乙" * 900)
    seen = with_fake_llm([
        json.dumps({"winner": "tie", "reason": "r1"}),
        json.dumps({"winner": "tie", "reason": "r2"}),
    ])
    run_capture(m.do_pairwise, Args(pair=[pa, pb]), CFG, pa, pb)
    ok = len(seen) == 2 and "甲" in seen[0] and "乙" in seen[1]
    check("盲评·两轮 prompt 内容对调", ok)


# ---------------------------------------------------------------- 2. 细纲兑现核对
def test_coverage_tally():
    body = write_tmp("第009章-测试.md", BODY_A)
    outline = write_tmp("第009章-测试-细纲.md", "分段骨架：\n" + "必须发生：某事。" * 20)
    with_fake_llm([json.dumps({
        "beats": [
            {"id": "1", "beat": "b1", "render": "landed", "quote": "q1"},
            {"id": "2", "beat": "b2", "render": "summarized", "quote": "q2"},
            {"id": "3", "beat": "b3", "render": "missing", "quote": ""},
            {"id": "4", "beat": "b4", "render": "landed", "quote": "q4"},
            {"id": "5", "beat": "b5", "render": "怪值", "quote": ""},
        ],
        "unauthorized": [{"fact": "新反转", "quote": "原句"}],
    })])
    rc, out = run_capture(m.do_coverage, Args(coverage=True), CFG, body, outline)
    check("核对·tally 计数正确", "landed 2 ｜ summarized 1 ｜ missing 1（合计 5）" in out, out[:400])
    check("核对·非法 render 不计入 tally", "怪值" in out)
    check("核对·越界新增渲染", "新反转" in out and "越界新增" in out)


def test_coverage_no_unauthorized():
    body = write_tmp("第009章-测试.md", BODY_A)
    outline = write_tmp("第009章-测试-细纲.md", "分段骨架：\n" + "必须发生：某事。" * 20)
    with_fake_llm([json.dumps({"beats": [], "unauthorized": []})])
    rc, out = run_capture(m.do_coverage, Args(coverage=True), CFG, body, outline)
    check("核对·无越界时明确报未检出", "越界新增：未检出" in out, out[:300])


def test_coverage_short_outline_rejected():
    body = write_tmp("第009章-测试.md", BODY_A)
    outline = write_tmp("第009章-测试-细纲.md", "太短")
    rc, out = run_capture(m.do_coverage, Args(coverage=True), CFG, body, outline)
    check("核对·细纲过短拒绝", rc == 2)


# ---------------------------------------------------------------- 3. 细纲路径推导
def test_resolve_outline():
    root = tempfile.mkdtemp(prefix="nf_books_")
    os.makedirs(os.path.join(root, "正文", "draft"))
    os.makedirs(os.path.join(root, "细纲"))
    body = os.path.join(root, "正文", "draft", "第007章-协议.md")
    open(body, "w", encoding="utf-8").write(BODY_A)
    want = os.path.join(root, "细纲", "第007章-协议-细纲.md")
    open(want, "w", encoding="utf-8").write("细纲内容" * 30)
    got = m.resolve_outline(body)
    check("推导·同书根精确命中", got == want, "got=%r" % got)

    os.remove(want)
    alt = os.path.join(root, "细纲", "第007章-改-细纲.md")
    open(alt, "w", encoding="utf-8").write("细纲内容" * 30)
    got2 = m.resolve_outline(body)
    check("推导·按章号兜底匹配", got2 == alt, "got=%r" % got2)

    lonely = write_tmp("第007章-协议.md", BODY_A)
    check("推导·无细纲目录返回 None", m.resolve_outline(lonely) is None)


# ---------------------------------------------------------------- 4. 截断保护
def test_trim_body():
    short = "字" * 100
    t, cut = m.trim_body(short)
    check("截断·短文本不动", t == short and cut is False)
    long_text = "字" * (m.MAX_BODY_CHARS + 500)
    t2, cut2 = m.trim_body(long_text)
    check("截断·超长截断并标位", len(t2) == m.MAX_BODY_CHARS and cut2 is True)


# ---------------------------------------------------------------- 5. 关键词
def test_chapter_label():
    check("章号·三位数", m.chapter_label("第012章-载格.md") == "12")
    check("章号·无章号", m.chapter_label("序章.md") is None)


def test_resolve_chapter_naming():
    """--book --chapter 要能认出「第007章-协议.md」，且第1章不误配第11章。"""
    root = tempfile.mkdtemp(prefix="nf_chap_")
    os.makedirs(os.path.join(root, "正文", "draft"))
    p7 = os.path.join(root, "正文", "draft", "第007章-协议.md")
    open(p7, "w", encoding="utf-8").write("正文")
    check("章节定位·识别「第N章-章名.md」", m.resolve_chapter(root, 7) == p7)

    p11 = os.path.join(root, "正文", "draft", "第011章-名单.md")
    open(p11, "w", encoding="utf-8").write("正文")
    try:
        got = m.resolve_chapter(root, 1)
        misplaced = got is not None
    except SystemExit:
        misplaced = False
    check("章节定位·第1章不误配第11章", not misplaced)

    p7b = os.path.join(root, "正文", "draft", "第007章.md")
    open(p7b, "w", encoding="utf-8").write("正文")
    check("章节定位·精确名优先于带章名", m.resolve_chapter(root, 7) == p7b)


def test_extract_json_robust():
    """智谱 glm-4.6 等模型会夹带思维链/围栏/说明，JSON 必须能抠出来。"""
    cases = [
        ('纯JSON', '{"winner": "A"}', {'winner': 'A'}),
        ('json围栏', '```json\n{"winner":"B"}\n```', {'winner': 'B'}),
        ('思维链+JSON', '<think>想想</think>\n{"winner":"tie"}', {'winner': 'tie'}),
        ('未闭合思维链', '<think>想不完\n{"winner":"A"}', {'winner': 'A'}),
        ('前后夹带说明', '结果如下：\n{"a":{"b":1}}\n以上。', {'a': {'b': 1}}),
        ('嵌套JSON', '{"beats":[{"id":"1"}],"n":2}', {'beats': [{'id': '1'}], 'n': 2}),
        ('含字符串花括号', '{"quote":"他喊{停}","id":"1"}', {'quote': '他喊{停}', 'id': '1'}),
        # ---- v2.3.1 修的两个坑（回归防线，勿删）----
        # 坑A：未闭合思维链里带示例 JSON，旧实现返回思维链里的假 JSON
        ('坑A·未闭合think里含假JSON', '<think>{"fake":1} 然后 {"real":2}', {'real': 2}),
        ('坑A·思维链示例+换行真答案',
         '<think>想不完，示例 {"x":1}\n结果：{"winner":"A"}', {'winner': 'A'}),
        # 坑B：顶层数组被当对象降级，类型静默改变
        ('坑B·顶层数组不降级', '[{"a":1}]', [{'a': 1}]),
        ('坑B·顶层数组多元素', '[{"a":1},{"b":2}]', [{'a': 1}, {'b': 2}]),
        # 取最后一个顶层候选（思维链草稿在前、真答案在后）
        ('两个顶层对象取后者', '草稿 {"winner":"A"}\n最终 {"winner":"B"}', {'winner': 'B'}),
        ('数组在对象之后', '{"a":1}\n[{"b":2}]', [{'b': 2}]),
    ]
    for name, raw, want in cases:
        check("提取JSON·%s" % name, m.extract_json(raw) == want, "got=%r" % m.extract_json(raw))
    check("提取JSON·空串返回 None", m.extract_json("") is None)
    check("提取JSON·非JSON返回 None", m.extract_json("我不知道") is None)
    check("提取JSON·只有思维链返回 None", m.extract_json("<think>想不完") is None)


def test_thinking_payload():
    """关闭思考时 payload 必须带 thinking=disabled；开启时不能带。"""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured['body'] = json.loads(req.data.decode('utf-8'))

        class R(object):
            def read(self_inner):
                return json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False
        return R()

    orig = m.urllib.request.urlopen
    m.urllib.request.urlopen = fake_urlopen
    try:
        m.call_llm("http://x/v1", "k", "glm-4.6", "p", "s", thinking=False)
        check("思考关闭·payload 含 thinking=disabled",
              captured['body'].get("thinking") == {"type": "disabled"},
              str(captured['body'])[:120])
        m.call_llm("http://x/v1", "k", "glm-4.6", "p", "s", thinking=True)
        check("思考开启·payload 不含 thinking", "thinking" not in captured['body'],
              str(captured['body'])[:120])
    finally:
        m.urllib.request.urlopen = orig


def test_empty_content_diagnosed():
    """模型只回思维链、content 为空时，报错要说人话（别伪装成 JSON 解析失败）。"""
    def fake_urlopen(req, timeout=None):
        class R(object):
            def read(self_inner):
                return json.dumps({"choices": [{"message": {
                    "content": "", "reasoning_content": "想了很久…"}}]}).encode()

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False
        return R()

    orig = m.urllib.request.urlopen
    m.urllib.request.urlopen = fake_urlopen
    try:
        content, err = m.call_llm("http://x/v1", "k", "glm-4.6", "p", "s")
        check("空content·返回 None 且给出思维链诊断",
              content is None and err and "思维链" in err, "err=%r" % err)
    finally:
        m.urllib.request.urlopen = orig


def test_provider_presets():
    """供应商预设必须齐备，且智谱端点不能是 token-hub。"""
    check("预设·含 zhipu", "zhipu" in m.PROVIDERS)
    check("预设·zhipu 端点正确",
          m.PROVIDERS["zhipu"]["base_url"] == "https://open.bigmodel.cn/api/paas/v4",
          m.PROVIDERS["zhipu"]["base_url"])
    check("预设·zhipu 默认关思考", m.PROVIDERS["zhipu"]["thinking"] == "disabled")
    check("预设·含 tokenhub", "tokenhub" in m.PROVIDERS)


def test_provider_beats_env():
    """v2.3.1 修的坑：显式 --provider 必须整组接管端点+模型。

    旧实现 base_url 取 env、model 取 preset，会「端点/model 混搭」——
    用户 export 过 token-hub 的 BASE_URL 后加 --provider zhipu 仍然 401。
    """
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(here, "llm_review.py")
    body = write_tmp("第00X章.md", "正文。" * 100)
    env = dict(os.environ)
    env["NOVEL_FORGE_API_KEY"] = "k"
    env["NOVEL_FORGE_BASE_URL"] = "https://lai-hub.lenovomm.com/v1"   # 假装用户导出过旧网关
    env["NOVEL_FORGE_MODEL"] = "glm-5.3-flash"
    p = subprocess.run([sys.executable, script, "--file", body, "--provider", "zhipu",
                        "--dry-run"], capture_output=True, text=True, encoding="utf-8",
                       env=env, timeout=60)
    err = p.stderr or ""
    # dry-run 不打印配置，改看提示行：应明确告知端点已被预设接管
    check("provider·显式预设时提示接管端点", "已接管端点" in err or p.returncode == 0,
          "stderr=%r" % err[:200])


def main():
    print("llm_review.py 离线逻辑测试（不联网）\n" + "-" * 52)
    for fn in (test_pairwise_consistent_win, test_pairwise_inconsistent_is_tie,
               test_pairwise_tie_winner, test_pairwise_second_round_fails,
               test_pairwise_prompt_swapped, test_coverage_tally,
               test_coverage_no_unauthorized, test_coverage_short_outline_rejected,
               test_resolve_outline, test_trim_body, test_chapter_label,
               test_resolve_chapter_naming, test_extract_json_robust,
               test_thinking_payload, test_empty_content_diagnosed,
               test_provider_presets, test_provider_beats_env):
        fn()
    print("-" * 52)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  未过：%s" % f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
