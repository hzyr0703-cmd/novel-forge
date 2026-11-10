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
    if not out:
        print('[OK] 未检出 ≥%d 字跨章原样复用。' % n)
        return
    print(f'[复用] 检出 {len(out)} 处 ≥{n} 字跨章原样复用（报警不阻断）：')
    for chs, ph in sorted(out, key=lambda x: -len(x[1]))[:40]:
        print(f"  章 {list(chs)}: 「{ph}」")

if __name__ == '__main__':
    main()
