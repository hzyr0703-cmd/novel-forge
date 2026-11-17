#!/usr/bin/env python3
"""跨章标志句复用扫描（2026-11-10 本地新增）：找定稿层中 ≥12 字、在 2 章以上原样出现的短语。

用法：python3 check-phrase-reuse.py --book <项目目录> [--min-len 12]
只报警不阻断（P30/P31）：同义复述与标志句跨章复用是新 AI 指纹，交付报告须如实呈现。
清洗口径：去空白与标点后按汉字/字母数字串取 n-gram；【】面板行整行剔除。
"""
import argparse, glob, os, re, sys
from collections import defaultdict

def clean(text):
    lines = [L for L in text.split('\n') if '【' not in L]
    s = '\n'.join(lines)
    s = re.sub(r'\[DRAFT\]|第\d+章.*', '', s)
    return re.sub(r'[^一-鿿A-Za-z0-9]', '', s)

# 有意锚点白名单（2026-11-10 定版）：伏笔挂点与母题复现，命中只标注不计问题。
WHITELIST = ['词条断口与承接口纹路不合请复检', '听见有人喊你的工号不要回头',
             '记着三千一百二十八的那一页', '活下去攒够拼法弄清自己为什么是空的',
             '半件是桥两头都搭着两头都不算到岸', '拼法账账页上熔字后面那一行']

def _common(a, b):
    best = 0
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i+k < len(a) and j+k < len(b) and a[i+k] == b[j+k]:
                k += 1
            best = max(best, k)
    return best

def is_anchor(ph):
    return any(_common(ph, w) >= 10 for w in WHITELIST)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--book', required=True)
    ap.add_argument('--min-len', type=int, default=12)
    args = ap.parse_args()
    n = args.min_len
    files = sorted(glob.glob(os.path.join(args.book, '正文/final/第*章.md')))
    gram_ch = defaultdict(set)
    texts = {}
    for f in files:
        ch = int(re.search(r'第(\d+)章', f).group(1))
        t = clean(open(f, encoding='utf-8').read())
        texts[ch] = t
        for i in range(len(t) - n + 1):
            gram_ch[t[i:i+n]].add(ch)
    shared = {g: chs for g, chs in gram_ch.items() if len(chs) > 1}
    # extend seed grams into maximal phrases per chapter-set
    phrases = {}
    for g, chs in shared.items():
        key = tuple(sorted(chs))
        phrases.setdefault(key, []).append(g)
    out = []
    for chs, grams in phrases.items():
        # build maximal strings by merging overlapping grams found in the first chapter's text
        t0 = texts[chs[0]]
        spans = []
        for g in grams:
            i = t0.find(g)
            if i >= 0: spans.append((i, i + n))
        spans.sort()
        merged = []
        for a, b in spans:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        for a, b in merged:
            if b - a >= n:
                out.append((chs, t0[a:b]))
    anchors = [(c, p) for c, p in out if is_anchor(p)]
    probs = [(c, p) for c, p in out if not is_anchor(p)]
    if not probs:
        print('[OK] 无问题级跨章复用（另有 %d 处有意锚点复现，不计）。' % len(anchors))
        return
    print(f'[复用] 问题级 {len(probs)} 处 ≥{n} 字跨章原样复用（报警不阻断）；有意锚点 {len(anchors)} 处已分开：')
    for chs, ph in sorted(probs, key=lambda x: -len(x[1]))[:40]:
        print(f"  章 {list(chs)}: 「{ph}」")
    for chs, ph in sorted(anchors, key=lambda x: -len(x[1]))[:10]:
        print(f"  [锚点] 章 {list(chs)}: 「{ph}」")

if __name__ == '__main__':
    main()
