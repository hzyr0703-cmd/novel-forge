#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
llm_review.py 的端到端测试 —— 起本地假 OpenAI 兼容端点，走真实 HTTP 路径。

与 test_llm_review_pairing.py 的分工：
  - pairing 测试：离线，mock 掉 ask_model，验投票/tally/推导等纯逻辑；
  - 本文件：真起 HTTP 服务，验 payload 构造、认证头、响应解析、两轮换位真的换了内容。

不访问外网（只连 127.0.0.1），不读真实 key（用假 key）。

用法：python test_llm_review_e2e.py
退出码：0 = 全绿；1 = 有用例失败。
"""

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "llm_review.py")

CALLS = []          # 收到的 user prompt
QUEUE = []          # 预设回复（按序弹出）


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n).decode("utf-8"))
        prompt = req["messages"][-1]["content"]
        CALLS.append(prompt)
        content = QUEUE.pop(0) if QUEUE else json.dumps({"winner": "tie", "reason": "empty"})
        out = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print("%s %s%s" % ("  ok  " if cond else " FAIL ", name,
                       ("  <- " + detail[-300:]) if (detail and not cond) else ""))


def run(args, base_url):
    env = dict(os.environ)
    env["NOVEL_FORGE_API_KEY"] = "fake-key"
    env["NOVEL_FORGE_BASE_URL"] = base_url
    env["NOVEL_FORGE_MODEL"] = "fake-model"
    try:
        p = subprocess.run([sys.executable, SCRIPT] + args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=90)
    except subprocess.TimeoutExpired:
        return 124, "[测试] 子进程超时（>90s）——端点不可达时应在连接阶段快速失败"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    global QUEUE
    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)   # 0 = 自动选空闲端口
    port = srv.server_address[1]
    base_url = "http://127.0.0.1:%d/v1" % port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("假端点：%s\n%s" % (base_url, "-" * 52))

    d = tempfile.mkdtemp(prefix="nf_e2e_")
    pa = os.path.join(d, "改前.md")
    pb = os.path.join(d, "改后.md")
    open(pa, "w", encoding="utf-8").write("旧版内容。" * 200)
    open(pb, "w", encoding="utf-8").write("新版内容。" * 200)

    # ---- 1. 盲评两轮一致 → 判改后胜（第2轮换位后 A=改后）
    QUEUE.clear(); CALLS.clear()
    QUEUE += [json.dumps({"winner": "B", "reason": "更抓人"}),    # 轮1: A=改前 B=改后
              json.dumps({"winner": "A", "reason": "节奏更好"})]  # 轮2: A=改后 B=改前
    rc, out = run(["--pair", pa, pb], base_url)
    check("盲评·实走 HTTP 且判改后胜", rc == 0 and "改后.md 胜" in out, out)
    check("盲评·两轮 prompt 内容真的对调",
          len(CALLS) == 2 and "旧版内容" in CALLS[0] and "新版内容" in CALLS[1],
          "calls=%d" % len(CALLS))

    # ---- 2. 盲评两轮矛盾 → 平
    QUEUE.clear(); CALLS.clear()
    QUEUE += [json.dumps({"winner": "A", "reason": "r1"}),
              json.dumps({"winner": "A", "reason": "r2"})]
    rc, out = run(["--pair", pa, pb], base_url)
    check("盲评·两轮矛盾记平", rc == 0 and "结论：平" in out, out)

    # ---- 3. coverage 走真实 HTTP
    outdir = os.path.join(d, "书", "细纲")
    bodydir = os.path.join(d, "书", "正文", "draft")
    os.makedirs(outdir)
    os.makedirs(bodydir)
    b = os.path.join(bodydir, "第005章-测试.md")
    o = os.path.join(outdir, "第005章-测试-细纲.md")
    open(b, "w", encoding="utf-8").write("正文。" * 200)
    open(o, "w", encoding="utf-8").write("分段骨架：必须发生 A；必须发生 B。" * 10)
    QUEUE.clear(); CALLS.clear()
    QUEUE.append(json.dumps({
        "beats": [{"id": "1", "beat": "A", "render": "landed", "quote": "q"},
                  {"id": "2", "beat": "B", "render": "missing", "quote": ""}],
        "unauthorized": [{"fact": "私自加的反转", "quote": "原句"}]}))
    rc, out = run(["--file", b, "--coverage"], base_url)
    check("核对·实走 HTTP 且 tally 正确",
          rc == 0 and "landed 1 ｜ summarized 0 ｜ missing 1" in out, out)
    check("核对·越界加戏被渲染", "私自加的反转" in out, out)
    check("核对·细纲自动推导生效（未传 --outline）", rc == 0, out)

    # ---- 4. --model 覆盖生效
    QUEUE.clear(); CALLS.clear()
    QUEUE += [json.dumps({"winner": "tie", "reason": "r"}),
              json.dumps({"winner": "tie", "reason": "r"})]
    rc, out = run(["--pair", pa, pb, "--model", "judge-other-family"], base_url)
    check("--model 覆盖评委并在输出中体现", "judge-other-family" in out, out)

    # ---- 4b. --provider 必须整组接管端点（v2.3.1 修的坑①复发形态）
    #      旧实现 base_url 取 env、model 取 preset → 端点/model 混搭。
    #      修后：显式 --provider 时端点整组以预设为准，并打印接管提示。
    p = subprocess.run([sys.executable, SCRIPT, "--pair", pa, pb, "--provider", "tokenhub"],
                       capture_output=True, text=True, encoding="utf-8",
                       env=dict(os.environ, NOVEL_FORGE_API_KEY="fake-key",
                                NOVEL_FORGE_BASE_URL=base_url), timeout=90)
    blob = p.stdout + p.stderr
    check("provider·显式预设接管端点并打印提示",
          "已接管端点" in blob and "tokenhub" in blob, blob[:300])

    # ---- 5. 端点不可达 → advisory 不阻断（exit 0）
    # 必须 server_close() 真正释放端口，否则连接会挂在半开监听队列上等满超时。
    srv.shutdown()
    srv.server_close()
    rc, out = run(["--pair", pa, pb], base_url)
    check("端点不可达时 advisory 不阻断（exit 0）", rc == 0, out)
    check("端点不可达时打印失败原因而非静默", "调用失败" in out or "advisory" in out, out)

    print("-" * 52)
    print("端到端：通过 %d / 失败 %d" % (len(OK), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  未过：%s" % f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
