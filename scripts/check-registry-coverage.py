#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
登记覆盖检查（v2.4.2 新增，advisory-only，永不阻断）

背景：全部确定性引擎（recall/factconflicts/debt/truth）都以「事实已登记进台账」
为前提。commit 是自觉动作，漏登 = 后续所有检测对该事实失明——这是长篇漏检的
头号来源（比算法召回率更早失效）。本脚本做三个方向的覆盖审计：

  1. 章节静默扫描：last_committed 内哪些章在时间线里零登记（first/updated 均不含）
  2. 角色提及未登记：某注册角色在末章正文出现 ≥3 次，但该章没有任何时间线条目
     提到它 → 可能漏登记了变化（也可能是静态出场，人工判断）
  3. 疑似新角色未建卡：正文高频出现「百家姓 + 1-2 字」的姓名形态 token，
     既不在 characters 也不在任何 timeline.characters → 疑似新角色

输出全部 advisory；退出码 0（正常完成）或 2（文件/台账缺失，无法审计）。
用法：
  python check-registry-coverage.py --book <书目录> [--chapter N] [--recent 10]
"""
import argparse
import json
import os
import re
import sys

# 百家姓常用姓（覆盖绝大多数中文网文角色姓）
SURNAMES = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳唐罗薛"
    "雷贺倪汤滕殷常乐时傅皮卞齐康伍余元卜顾孟平黄穆萧尹姚邵湛汪祁毛"
    "狄米贝明臧计伏成戴宋庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危"
    "江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝管卢莫柯房裘"
    "缪干解应宗宣丁贲邓郁单杭洪包诸左石崔吉钮龚程嵇邢滑裴陆荣翁荀羊"
    "于惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬"
)
COMMON_WORDS = set((
    "我们 你们 他们 她们 自己 什么 这个 那个 其中 以后 后来 当时 已经 这样 那样 "
    "大家 对方 两人 三人 说法 做法 想法 心情 情绪 身体 地方 东西 事情 问题 时候 "
    "一下 第一 第二 上午 下午 中午 晚上 今天 昨天 明天 这里 那里 哪个 每次 某人 "
    "时间 现在 如何 如果 虽然 因此 所以 于是 仍然 忽然 突然 其实 确实 平常 "
    "平时 临时 随时 即使 即将 既然 纵然 依旧 照样 起来 出来 下来 过来 回来 "
    "一阵 一声 一眼 一步 一次 一边 一面 一点 一些 今天 明天 之前 之后 之间"
).split())
# 第二字命中即否决姓名读法：量词/方位/通名（「张纸」「李上」「王里」类误报）
FUNCTION2 = set(
    "纸条张块件只条根支枝棵颗粒滴层叠排列行页段落个同学们师生"
    "边面里中上下前后左右内外东西南北旁边附近周围之间上面下面"
    "大小多少高低长短粗细新旧好坏真假早晚快慢深浅厚薄轻重远近"
    "老人手头心口天日年月时分秒点次遍趟声句话语字图照片水火"
    "饭菜茶酒杯碗瓶壶桌椅门窗车路灯船飞机桥街巷院子房间屋"
)


def load_state(book):
    p = os.path.join(book, "追踪", "_tracking-state.json")
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def read_chapter(book, chapter):
    """与 forge.py find_chapter_file / check-distribution v2.4.2 同口径：
    递归搜 正文/，final 优先，兼容零填充与标题后缀。"""
    body = os.path.join(book, "正文")
    if not os.path.isdir(body):
        return None
    found = []
    for dirpath, _dirs, files in os.walk(body):
        for name in files:
            if not name.lower().endswith(".md"):
                continue
            nums = re.findall(r"(\d+)", name)
            if nums and int(nums[0]) == chapter:
                found.append(os.path.join(dirpath, name))
    found.sort(key=lambda p: (0 if os.sep + "final" + os.sep in p else 1, p))
    if not found:
        return None
    with open(found[0], "r", encoding="utf-8") as f:
        return f.read()


def main():
    ap = argparse.ArgumentParser(description="登记覆盖检查（advisory-only）")
    ap.add_argument("--book", required=True)
    ap.add_argument("--chapter", type=int, default=None, help="默认 last_committed_chapter")
    ap.add_argument("--recent", type=int, default=10, help="静默章扫描回看范围")
    args = ap.parse_args()

    state = load_state(args.book)
    if state is None:
        print("[覆盖] 找不到 追踪/_tracking-state.json，无法审计", file=sys.stderr)
        return 2
    last = args.chapter or state.get("last_committed_chapter")
    if not last:
        print("[覆盖] 台账无 last_committed_chapter 且未指定 --chapter", file=sys.stderr)
        return 2

    tl = state.get("timeline", {})
    known_chars = set(state.get("characters", {}).keys())
    tl_chars_by_ch = {}   # chapter -> set(登记角色)
    chs_touched = set()   # 有任何登记活动的章
    for entry in tl.values():
        for c in (entry.get("first_recorded_chapter"), entry.get("updated_chapter")):
            if isinstance(c, int):
                chs_touched.add(c)
                tl_chars_by_ch.setdefault(c, set()).update(entry.get("characters") or [])

    warnings = []

    # 1) 章节静默扫描
    silent = [n for n in range(max(1, last - args.recent + 1), last + 1)
              if n not in chs_touched]
    if silent:
        warnings.append("静默章（近 %d 章内时间线零登记）：%s"
                        % (args.recent, "、".join(str(n) for n in silent)))

    # 2) 末章提及未登记
    text = read_chapter(args.book, last)
    if text is None:
        print("[覆盖] 找不到第 %d 章正文（final/draft/正文 递归搜索无果）" % last, file=sys.stderr)
        return 2
    registered_tl = tl_chars_by_ch.get(last, set())
    for name in sorted(known_chars):
        cnt = text.count(name)
        if cnt >= 3 and name not in registered_tl:
            warnings.append("角色「%s」在第 %d 章出现 %d 次，但该章时间线无任何条目登记其变化"
                            "（若本章确有性格/位置/认知/资源变化 → 漏登记；纯静态出场可忽略）"
                            % (name, last, cnt))

    # 3) 疑似新角色未建卡
    #    贪婪 {2,3} 会吃掉边界（「看清赵铁生」→「清赵铁」），改为：
    #    按连续汉字串切分，取所有以常见姓氏开头的 2-3 字滑窗计数。
    all_known = set(known_chars)
    for entry in tl.values():
        all_known.update(entry.get("characters") or [])
    cand = {}
    for run in re.finditer(r"[\u4e00-\u9fa5]{2,}", text):
        s = run.group()
        for i, ch in enumerate(s[:-1]):
            if ch not in SURNAMES:
                continue
            for L in (2, 3):
                if i + L <= len(s):
                    tok = s[i:i + L]
                    if tok in COMMON_WORDS or tok in all_known:
                        continue
                    if tok[1] in FUNCTION2:
                        continue
                    if any(tok in k or k in tok for k in all_known):
                        continue
                    cand[tok] = cand.get(tok, 0) + 1
    for tok, cnt in sorted(cand.items(), key=lambda x: -x[1]):
        if cnt >= 3:
            warnings.append("「%s」在第 %d 章出现 %d 次且不在台账角色表 → 疑似新角色未建卡/未登记"
                            % (tok, last, cnt))

    print("登记覆盖检查  书：%s ｜ last_committed=%d ｜ 时间线条目 %d"
          % (state.get("book_title", "?"), last, len(tl)))
    if warnings:
        print("  [!] %d 条覆盖提示（advisory，逐条人工判断）：" % len(warnings))
        for w in warnings:
            print("    · " + w)
    else:
        print("  [OK] 近 %d 章登记覆盖完整，末章无提及未登记、无疑似新角色" % args.recent)
    print("  本脚本只产提示，不参与门禁退出码。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
