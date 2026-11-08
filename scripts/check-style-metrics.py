#!/usr/bin/env python3
"""文风体检（2026-11-08 本地新增）：按第二轮基线量化单章/区间的文风指标。

用法：
  python3 check-style-metrics.py --book <项目目录> --chapter N
  python3 check-style-metrics.py --book <项目目录> --range 41-75
低于基线只报警不阻断（写作判断优先），但报告必须如实呈现。
基线：对话占比 常规章≥12% / 任何章≥5%；长句(≥35字)占比≥15%；句长标准差≥13；
限频模板词单章各≤1；视线动词单章≤3；面板行（【）战斗/结算章≥2、其他章≥1（仅提示）。
"""
import argparse, glob, os, re, statistics, sys

LIMIT_WORDS = ['站了一会儿', '看了一会儿', '把本子合上', '在心里过了一遍', '了一下']
GAZE = ['看着', '盯着', '望着', '看了一眼', '看了看', '看向']
INNER = ['心里想', '在心里', '心头', '心里']

def chapter_metrics(text):
    t = re.sub(r'\s', '', text)
    sents = [s for s in re.split(r'[。！？]', text) if s.strip()]
    slens = [len(re.sub(r'\s', '', s)) for s in sents] or [0]
    dlg = sum(len(m) for m in re.findall(r'「[^」]*」|“[^”]*”', text))
    tail = t[-300:]
    return {
        'chars': len(t),
        'avg_sent': statistics.mean(slens),
        'sent_sd': statistics.pstdev(slens) if len(slens) > 1 else 0.0,
        'long_ratio': sum(1 for x in slens if x >= 35) / len(slens),
        'dlg_ratio': dlg / len(t) if t else 0.0,
        'panel': text.count('【'),
        'limits': {w: text.count(w) for w in LIMIT_WORDS},
        'gaze': sum(text.count(w) for w in GAZE),
        'inner': sum(text.count(w) for w in INNER),
        'notebook_ending': bool(re.search(r'(账|行情本|笔记本|拼法账)', tail)),
    }

def report(ch, m, quiet=False):
    warns = []
    if m['dlg_ratio'] < 0.05: warns.append(f"对话 {m['dlg_ratio']*100:.1f}% < 5% 硬底线")
    elif m['dlg_ratio'] < 0.12: warns.append(f"对话 {m['dlg_ratio']*100:.1f}% < 12% 常规线（独处/纯战斗章可豁免）")
    if m['long_ratio'] < 0.15: warns.append(f"长句比 {m['long_ratio']*100:.0f}% < 15%")
    if m['sent_sd'] < 13: warns.append(f"句长标准差 {m['sent_sd']:.1f} < 13（节奏偏平）")
    for w, c in m['limits'].items():
        if c > 1: warns.append(f"限频词「{w}」{c} 次 > 1")
    if m['gaze'] > 3: warns.append(f"视线动词 {m['gaze']} 次 > 3")
    if m['inner'] > 2 + m['limits']['了一下'] * 0:  # 心里/心头标签
        pass
    if m['panel'] == 0: warns.append("面板行 0（游戏层隐形，仅提示）")
    if not quiet:
        print(f"ch{ch}: 字数 {m['chars']} | 句长 {m['avg_sent']:.1f}±{m['sent_sd']:.1f} | 长句 {m['long_ratio']*100:.0f}% | "
              f"对话 {m['dlg_ratio']*100:.1f}% | 面板 {m['panel']} | 章末账本式 {'是' if m['notebook_ending'] else '否'}")
        for w in warns: print(f"    ⚠ {w}")
    return warns

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--book', required=True)
    ap.add_argument('--chapter', type=int)
    ap.add_argument('--range', dest='rng')
    args = ap.parse_args()
    finals = {}
    for f in glob.glob(os.path.join(args.book, '正文/final/*.md')):
        m = re.search(r'第(\d+)章', f)
        if m: finals[int(m.group(1))] = f
    if args.chapter: chapters = [args.chapter]
    elif args.rng:
        a, b = args.rng.split('-'); chapters = list(range(int(a), int(b) + 1))
    else: chapters = sorted(finals)
    total_warn = 0; nb_end = 0
    for ch in chapters:
        if ch not in finals: print(f"ch{ch}: 无定稿"); continue
        m = chapter_metrics(open(finals[ch], encoding='utf-8').read())
        total_warn += len(report(ch, m, quiet=len(chapters) > 12))
        nb_end += 1 if m['notebook_ending'] else 0
    if len(chapters) > 12:
        print(f"区间 {chapters[0]}-{chapters[-1]}：共 {len(chapters)} 章，报警合计 {total_warn} 条，账本式章末 {nb_end} 章")
    return 0

if __name__ == '__main__':
    sys.exit(main())
