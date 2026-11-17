#!/usr/bin/env node
'use strict';

const fs = require('fs');
const { loadStyleWhitelist, maskStyleText } = require('./style-whitelist.js');
const path = require('path');

const USAGE = `Usage: node check-ai-patterns.js [--check] [--json] [--fail-on=blocking|all] [--diff=before.json] <file...>
       node check-ai-patterns.js --list-review-classes | --list-categories

Detect high-risk AI-flavor prose patterns that need human rewrite:
  - negative setup followed by positive flip in the same sentence
  - comma/semicolon/colon + positive flip
  - sentence break + positive flip
  - repeated negative setup followed by positive flip
  - em-dash (按功能改写), 碎句号 (连续短叙述句), 长段落 (按镜头断段)
  - 微动作复读 (「了下/了一下」式轻量补语高密度，电报体指纹)
  - 套式反应细节 (指尖/指节/目光等无功能微动作与「平静得像在念」式语气比喻成片)
  - 抽象总结复读 (命运/棋局/这一刻终于明白/才刚刚开始，AI 结尾腔)
  - 套词密度过高 (仿佛/一丝/深吸一口气/平静无波等禁用词聚集)
  - 比喻密度过高 (像/好像/仿佛/如同等比喻标记成片复现)
  - 解释链密度过高 (知道/明白/这意味着/必须/需要等判断链聚集)
  - 系统公告公文腔过密 (方括号系统/规则行里硬规则词聚集)
  - 过度精炼短段 (长文本里短叙述段过密且自然连接偏少)
  - 低连接密度 (引号外叙述功能词/白话连接偏少且中长句不足，像提纲/电报体)
  - 监控摄像头式动作清单 (同段连续摆放动作动词，缺少视角温度/情绪缓冲)
  - 音量反差腔 (声音不高/不大…却…, 实战漏网句式)
  - 否定排比 (没有X，没有Y…连排 / 没X…只是Y 先否定后肯定, 实战漏网句式)
  - 工整并列 (至于X不X，怎么X / 同动词「不V A，不V B」，含台词，advisory)
  - 反序对比 (是A，不是B — not-is 的反序变种, 实战漏网句式)
  - 预告式总结收尾 (文末窗口 没人知道/才刚刚开始/正朝着…压了过去, 实战漏网句式)
  - 章尾状态总结体 (文末窗口 这一夜注定/这一切都结束了/新的人生才刚刚开始/命运的齿轮)
  - 引号强调滥用 (叙述里 1-4 字短词加引号强调，密度型)
  - 群体同步词 (全场哗然/鸦雀无声/齐刷刷/不约而同单次即报；所有人/众人/全都等泛指词密度型；
    对应 references/human-reaction.md 的「群体必须分裂」——一群人被写成一个反应)
  - 装饰符号 (✦✨◆★➤ 一类符号当点缀或分隔，正文用空行)
  - 极端词过量 (叙述里 非常/极其/无比/十分/特别 等 ≥4 处，程度应靠具体画面给)

Book-local .deslop-whitelist literal spans are excluded from style scanning (no regex or ancestor inheritance).
Each finding carries severity: blocking by default for generation/deslop cleanup (not-is-comparison / voice-contrast / negation-parade / reverse-not-is / trailer-ending / trailer-summary / hard-banned-word). This is a local style/readability gate, not an AIGC detector score; functional human text can be marked for review instead of hard-edited for a detector.
em-dash is conditional: blocking only when the dash is OUTSIDE quotes AND the chapter density is >= 2/1000 chars; inside quotes (interrupted speech) it is always advisory.
Each finding also carries category: ai-tone（真 AI 腔，该改）/ cliche（网文水词，可删可留）/ literary（文学手法，多数保留）/ readability（节奏可读性，看语境）。
或 advisory (period-stutter / micro-action-tic / negation-pair-tic / stock-reaction-tic / action-list-tic / abstract-summary-tic / cliche-density-tic / metaphor-density-tic / reasoning-chain-tic / system-notice-formality-tic / overcompressed-prose-tic / low-connective-density-tic / quote-emphasis-tic / formulaic-parallelism / weak-verb-tic / emotion-label-tic / crowd-sync-tic / filter-verb-tic / decorative-symbol / intensifier-abuse，是提示，justified 的长推理/氛围段可保留)。
--fail-on=blocking 只在出现 blocking finding 时退出 1；默认 --fail-on=all 有任何 finding 即退出 1。
--diff=before.json 把本次命中与上一次 --json 输出按 type 对比，看改完是「修好了」还是「改过头了」
（某类命中变多 = 新毛病；blocking 归零但总数变多 = 转移）。
Each finding also carries review: mechanical（表面形态确定、就地改写即可）或 semantic（可能承担叙事功能，需读上下文判断是否保留）；blocking 一律 mechanical。--list-review-classes 输出完整分类表（JSON）。
HTML comments (<!-- ... -->, e.g. the 去味:跳过 exemption marker) are metadata, not prose, and are never scanned.

The script reports findings only. It never rewrites text, because the safe fix is
contextual: usually delete the negative setup, write the positive term directly,
or show it via action/detail.`;

const STOP_CHARS = new Set(['。', '！', '？', '!', '?', '\n']);
const SOFT_SEPARATORS = new Set(['，', ',', '、', '；', ';', '：', ':']);
const HARD_SEPARATORS = new Set(['。', '.', '！', '!', '？', '?']);
const MAX_NEGATIVE_SPAN = 80;
const MAX_POSITIVE_SPAN = 80;

// 破折号 blocking 的密度门槛（每千字）。低于它一律 advisory：单个破折号是正常标点。
// 放在文件顶部而不是 scanProsePatterns 旁边——模块加载期不会用到，但常量集中一处更好核对。
const DASH_BLOCKING_PER_KILO = 2;

// 碎句号：连续 STUTTER_MIN_RUN 个「叙述」短句（每句可见字数 ≤ STUTTER_MAX_SENTENCE）无呼吸。
// 只数叙述句，跳过对话/弹幕/系统播报（成片短句是这些体裁的正常形态，不算碎句号）。
const STUTTER_MIN_RUN = 6;
const STUTTER_MAX_SENTENCE = 5;
// 段长不在这里判：段长是「规格」（spec_check.py 的 blocking，90 字 + 每章 1 段 120 字豁免）。
// v2.8 前本文件另有一条 long-paragraph（>200 字 advisory），与 spec_check 的 90 字 blocking
// 对同一现象给出两种严重度——250 字的段落一个脚本说可留、一个判死。已删除，
// 单一事实源留给 spec_check.THRESHOLDS["paragraph_max"]。

// 群体同化（references/human-reaction.md H1/H4）：把一群人写成一个整体，是「人在演、
// 没人在活」最强的指纹。分两档，避免把正常中文一起打掉：
//   1) 强同步词：单次命中即报——这个词本身就是「全体同一反应」的缩写，没有可救的上下文。
//   2) 泛指词：所有人/众人/全都/大家都 反复出现才报——单次「所有人都看她」是正常中文。
// 引号内台词豁免（「你们全都得死」是人物口吻，不是叙述层同化）。
// 常量必须放在文件顶部：scanProsePatterns 在模块加载时即被调用，函数声明提升但 const 不提升。
const CROWD_HARD_SYNC = [
  '鸦雀无声', '一片死寂', '全场哗然', '一片哗然', '满座皆惊', '人人大惊',
  '齐刷刷', '不约而同', '无一例外', '无一人敢', '无人敢', '全都愣', '全都呆',
];
const CROWD_SOFT_SYNC = ['所有人', '众人', '全都', '大家都', '集体', '人人', '满场', '全场', '四下', '人群'];
const CROWD_SOFT_MIN = 4;

// 过滤器动词（references/narrative-distance.md）：把读者挡在「他看见／他感到」之外的感知动词。
// 单段堆叠 3 个 = 转述腔（最典型）；整章 ≥6/千字 = 全程解说词。
// 标定：正常中文网文实测 0.7–1.2/千字，阈值取 6 约为基线的 5 倍，误报风险低。
// 常量必须留在文件顶部——prose 扫描在模块加载时即被调用，const 不提升。
const FILTER_VERBS = [
  '看见', '看到', '听见', '听到', '感到', '觉得', '注意到', '发现',
  '察觉', '意识到', '闻到', '想起', '明白', '知道',
];
const FILTER_VERB_PARAGRAPH_MAX = 3;
const FILTER_VERB_DENSITY_MIN = 6;

// 装饰符号癖（references/human-texture.md 八·3）：网文正文不用 ✦✨◆★ 一类符号当点缀
// 或分隔，分隔场景用空行。★☆●○※ 不收，这些在系统文/注释里有正常用法，误报率高。
const DECORATIVE_SYMBOL_PATTERN = /[✦✧✨❖◆◇▪▫★➤➢▶►]/g;

// 极端词滥用（human-texture.md 八·1）：非常/极其/无比/十分/特别/极大/极为 一激动就堆，
// 程度应靠具体画面给。只扫引号外叙述（台词里「非常」是口语自然，豁免）；密度型 ≥4 处报一条。
const INTENSIFIER_PATTERN = /(?:非常|极其|极为|无比|十分|特别|格外|分外|相当|极度|异常|极大)/g;
const INTENSIFIER_MIN_HITS = 4;

// 微动作复读：「V了下 / V了一下 / 拍了两下 / 松了半圈」式轻量补语在叙述里高密度复现，
// 容易形成删减过头的电报体指纹。只扫引号外叙述；密度与次数双门槛同时达标才报，
// 单次出现是正常中文。
const MICRO_TIC_PATTERN = /了(?:[一两三几半])?[下阵圈道声眼口气会]/g;
const MICRO_TIC_MIN_HITS = 5;
// 「没A，也没B」「不A，也不B」否定对偶：七猫人类长篇约 0.07 处/千字，模型稿 0.4-1.5 处/千字；
// 写人物没做什么是英文式节奏，一章 3 处以上报一条提示。
const NEGATION_PAIR_PATTERN = /(?:没有|没|不)[^，。！？；,!?\n]{1,10}[，,；]\s*[^，。！？；,!?\n]{0,6}?也(?:没有|没|不)/g;
const NEGATION_PAIR_MIN_HITS = 3;
const MICRO_TIC_PER_KILO = 6;

// 弱动词分解式（中文原则库「欧化病②」，advisory）：
// 「进行/作出/加以/予以 + 抽象名词」把硬动词拆成虚动词+名词，是典型欧化腔，
// AI 稿偏好这种"安全语态"。单处出现可能是正常搭配（如"进行处理"在公文体对话里），
// 一章 ≥2 处才报，要求还原为硬动词（讨论→谈、分析→拆、处理→办）。
const WEAK_VERB_PATTERN = /(?:进行|作出|做出|加以|予以)了?[^，。！？；,!?\n]{0,10}?(?:讨论|沟通|分析|研究|处理|调整|说明|解释|介绍|评估|检查|审查|审理|审核|核实|核验|调研|排查|梳理|勘察|比对|盘点|汇报|安排|部署|反击|改造|训练|修复|清理|确认|甄别|总结|回应|表决|登记|验收|裁定|演练|测试)/g;
const WEAK_VERB_MIN_HITS = 2;

// 情绪贴标签（reader-reward 失败模式④「labeling emotions」，advisory）：
// 「感到/心头+情绪词」「一阵+情绪+涌上/袭来」直接告知情绪而非展示，模型稿高发。
// 真人稿也有（尤其过渡段），按章 ≥4 处才报一条分布级提示；修法是换角色特有行为。
const EMOTION_LABEL_PATTERN = /(?:(?:感到|觉得|感觉|心头|心中|心里)(?:一阵|有些|无比|说不出的|莫名的)?(?:愤怒|悲伤|难过|高兴|开心|恐惧|害怕|紧张|委屈|失望|痛苦|欣慰|羞愧|懊悔|恼火|烦躁|不安|愧疚|侥幸|心虚)|一阵(?:愤怒|悲伤|难过|喜悦|恐惧|酸楚|委屈|失落|心虚)(?:涌上|袭来|漫上|涌起|爬上|掠过))/g;
const EMOTION_LABEL_MIN_HITS = 4;

// 套式反应细节：不是禁写身体，而是提示成片出现的“部位 + 轻微动作/状态”、
// “胸口像被撞了一下”、喉结/眼圈/声音放轻等通用情绪尾巴，以及“平静语气 +
// 像在念/宣判”模板。此类句子词面变化大，不能逐词 blocking；按章聚集到 4 处才
// advisory，要求逐处做删除测试。正常受伤、打斗、生理反应若承担物理后果可保留。
const STOCK_REACTION_PATTERNS = [
  /(?:指尖|手指|指节|手背|掌心|拳头|袖口|衣角|裙角|下唇|嘴唇|唇角|嘴角|眉头|眼底|眸光|目光|视线|肩膀|呼吸)[^。！？!?\n]{0,16}(?:轻轻|微微|缓缓|悄然|不自觉|无意识|下意识|攥紧|握紧|收紧|绞紧|泛白|发白|叩|敲|摩挲|抿紧|抿成|移开|垂下|躲开|一颤|颤了?一下|停了?一下|顿了?一下)/g,
  /(?:语气|声音)[^。！？!?\n]{0,12}(?:平静|冷静|平淡|冷淡|淡漠|平直)[^。！？!?\n]{0,12}(?:像|仿佛|如同|好像)[^。！？!?\n]{0,16}(?:念|读|报|说|陈述|宣判|背诵)/g,
  /(?:胸口|心口)[^。！？!?\n]{0,16}(?:像|仿佛|如同|好像)[^。！？!?\n]{0,16}(?:撞|锤|压|攥|堵)[^。！？!?\n]{0,8}(?:一下|一记|一拳)?/g,
  /(?:声音|嗓音|语气)[^。！？!?\n]{0,12}(?:放轻|压低|发紧|发颤|很轻|轻了些)/g,
  /(?:喉结|喉头|喉咙)[^。！？!?\n]{0,10}(?:滚|动|紧|堵|发涩|发干)/g,
  /(?:眼眶|眼圈|鼻子)[^。！？!?\n]{0,8}(?:发红|红了|发热|发酸|一酸)/g,
  /(?:抿了?下唇|抿了?抿唇|抿了?下嘴|抿着笑)/g,
];
const STOCK_REACTION_MIN_HITS = 4;
// 校准（真人语料，<br> 已还原为换行）：qimao 长篇 5584 章 + heiyan 短篇整篇 3983 篇。
// 长篇章尺度（中位约 2100 字）per-kilo 1.0→1.5 误报 0.43%→0.39%，几乎不动；
// 短篇整篇 8000-20000 字下 MIN_HITS 形同虚设、只剩密度门，1.0 时误报 5.57%，
// 1.5 降到 1.46%。故取 1.5，把两个总体拉到同一量级（四份副本共用一组阈值）。
const STOCK_REACTION_PER_KILO = 1.5;

// 监控摄像头式动作清单：同一段连续堆叠通用动作动词（伸手/拿起/取过/挑开/放下/转身等），
// 且用逗号/顿号串联成步骤表时，读感像无视角温度的监控记录。只做 advisory；
// 打斗/追逐等功能性动作编排可保留或人工复核。
const ACTION_LIST_VERB_PATTERN = /伸手|抬手|探手|拿起|拿过|取出|取过|掏出|摸出|抓起|攥住|握住|捏住|按住|推开|拉开|打开|关上|放下|递给|挑开|掀开|扯开|拧开|倒出|端起|转身|回头|抬头|低头|弯腰|俯身|走到|走向|坐下|站起|看向|看着|盯着|扫过/g;
const ACTION_LIST_MIN_HITS = 5;
const ACTION_LIST_MIN_SEPARATORS = 4;

// 抽象总结复读：模板化段落常把角色当下经历拔成「命运/棋局/
// 这一刻终于明白/才刚刚开始」的作者总结。单个词可能服务题材；高密度聚集才报。
const ABSTRACT_SUMMARY_PATTERNS = [
  /这一刻[，,]?[^\n。！？!?]{0,24}(?:终于|才)(?:明白|意识到)/g,
  /从这一刻开始/g,
  /(?:命运|宿命)[^\n。！？!?]{0,28}(?:齿轮|棋局|獠牙|改写|推向|安排)/g,
  /早已[^\n。！？!?]{0,8}(?:布好|安排好)[^\n。！？!?]{0,8}(?:棋局|局)/g,
  /前所未有的(?:决意|清醒|勇气|力量|恐惧|平静|信念)/g,
  /(?:反击|复仇|战争|较量|故事|命运)[^\n。！？!?]{0,12}才刚刚开始/g,
  /(?:新的开始|全新的开始)/g,
];
const ABSTRACT_SUMMARY_MIN_HITS = 3;
const ABSTRACT_SUMMARY_PER_KILO = 4;

// 工程引用规则的前置豁免：故事世界内的合法分章引用（书/法典/碑文）不算工程残留。
// 判据（任一命中即豁免）：
//   1) 命中片段前 10 字内出现书名号对（《…》）或被 maskQuoted 打掉的引号占位（「？」串）；
//   2) 命中片段前 10 字内出现「卷/册/篇/回/书/典/经/谱/志/录/集/碑/文」载体名词。
// 注意两点：
//   - 《》不在 QUOTE_PAIRS 里，书名号段不会被 mask，故要直接找「《」；
//   - 「」引号段会被 mask 成「」，此时应判「？（占位串）」而非引号本身。
// 真残留（写作时忘了改的章号指代）几乎都在句子中段、无上述语境，故不受影响。
const IN_WORLD_REF_BEFORE = /[《》]|？{2,}|(?:卷|册|篇|回|书|典|经|谱|志|录|集|碑|文)[的之]?\s*$/;
function inWorldRefGuard(m, masked) {
  const before = masked.slice(Math.max(0, m.index - 10), m.index);
  return IN_WORLD_REF_BEFORE.test(before);
}

// ---- SKILL.md 第七节「硬禁词」逐条机检（2026-10 补）----
// 这批词此前只写在 SKILL.md 的禁止清单里、声明「出现即判blocking，机器必拦」，
// 但没有任何规则实现，属于文档承诺与代码不一致。逐条落地：
//   转折副词（不禁/忽然/蓦然/恍惚）
//   情绪直述词（非常/十分/极其/很X生/内心充满/感到愤怒）
// 章尾预告词走trailer-ending（已在文末窗口内），此处只补前两类。
// 逐处 blocking 的依据：这些词在叙述层不承担叙事功能，删掉不损失信息；
// 出现在台词里是人物口语（「你非常生气」），故只扫引号外。
// 校准：demo 前 20 章 +《万疆》20 章逐条人工复核，人物对话中的同类表达已豁免。
// v2.8 判据改造（质检报告 P1-11「同义词逃逸」）：
// 旧版只禁「忽然／非常」，放行「突然／特别」——两句话语义、节奏、画面完全相同，
// 门禁把 a 判死、把 b 判活。结果是模型学到的是「忽然→突然」的替换表，
// 而不是「用动作 beat 起跳」。这正是本技能想消灭的那种表层改写。
// 现在按「位置 + 词族全集」判，不再按单个词判：
//   1) 突变起跳副词：只在**段首／分句首**拦（它被当成句子的起跳器时）；
//      句中的「他忽然想起」是正常中文，不拦。
//   2) 程度副词 + 情绪形容词：只拦「副词直接修饰情绪词」这一位置，词族全收；
//      「非常时期」「十分之一」这类非情绪搭配不拦。
// 校准：a.md「他忽然抬头…他非常意外」与 b.md「他突然抬头…他特别意外」现同为 2 条 blocking。
// 加新词往词族里加，别新开一条只禁一个词的规则——那等于给模型留了替换表。

// 突变起跳副词族（同义全集）。含近义书面词，漏一个就等于漏一族。
const SUDDEN_ADVERBS = [
  '忽然', '突然', '蓦然', '猛然', '骤然', '霍然', '倏然', '陡然', '猛地', '腾地', '冷不丁',
];
const SUDDEN_ADVERB_PATTERN = new RegExp(`(?:${SUDDEN_ADVERBS.join('|')})`, 'g');

// 分句边界：句末/句中标点、换行，或引号掩码后的占位符（maskQuoted 用 ？ 占位）。
// 「起跳位置」＝这些字符之后（或文本开头）紧跟副词，副词承担了句子的开头。
const CLAUSE_BREAK_CHARS = new Set([
  '。', '！', '？', '!', '?', '…', '\n',
  '，', ',', '；', ';', '：', ':', '、',
  '」', '』', '”', '"', '）', ')',
]);

function atClauseStart(masked, index) {
  if (index === 0) return true;
  return CLAUSE_BREAK_CHARS.has(masked[index - 1]);
}

// 程度副词族 + 情绪形容词族：只在紧邻位置（可夹一个「的/地/得」）判 blocking。
// 用形容词白名单而不是「任意后接字」，是为了不误伤「非常时期」「十分之一」。
const INTENSIFIER_ADVERBS = [
  '非常', '十分', '极其', '极为', '无比', '格外', '特别', '异常', '分外', '极度', '相当', '太',
];
const FLAT_EMOTION_ADJS = [
  '生气', '愤怒', '恼火', '高兴', '开心', '快乐', '伤心', '难过', '痛苦', '悲伤',
  '兴奋', '紧张', '害怕', '恐惧', '惊恐', '惊讶', '震惊', '吃惊', '意外', '诧异',
  '失望', '绝望', '满意', '得意', '尴尬', '愧疚', '不安', '焦虑', '无奈', '委屈',
  '疑惑', '困惑', '茫然', '失落', '遗憾', '厌烦', '烦躁', '疲倦', '疲惫',
];
const INTENSIFIER_EMOTION_PATTERN = new RegExp(
  `(?:${INTENSIFIER_ADVERBS.join('|')})(?:的|地|得)?(?:${FLAT_EMOTION_ADJS.join('|')})`,
  'g'
);

const HARD_BANNED_PATTERNS = [
  // 突变起跳：位置规则，句中用法放行（「他忽然想起」不算）
  { pattern: SUDDEN_ADVERB_PATTERN, kind: '突变起跳', guard: (m, masked) => !atClauseStart(masked, m.index),
    advice: '别用「忽然/突然」起跳。改成动作 beat：先给声音、触感或别人的反应，读者自己会感到突然。换同义词（忽然→突然）不算改。' },
  // 状态直述：写不清的状态，落到具体感受
  { pattern: /恍惚/g, kind: '状态直述', advice: '不要用「恍惚」概括状态，落到具体感受：看不清、听不真、反应慢了半拍。' },
  // 程度副词 + 情绪形容词：位置规则，不再逐个禁词。
  // 「非常时期」「十分之一」「只剩十分钟」不在位置内，自动放行；
  // 否定式「不太高兴」「不十分满意」是克制的写法，放行。
  { pattern: INTENSIFIER_EMOTION_PATTERN, kind: '情绪直述',
    guard: (m, masked) => m.index > 0 && masked[m.index - 1] === '不',
    advice: '程度副词+情绪词＝替读者定强度。删掉副词，写能看出强度的动作或后果。换同义词（非常→特别/格外）不算改。' },
  { pattern: /很(?:生气|伤心|高兴|愤怒|难过|兴奋|紧张|害怕|惊讶)/g, kind: '情绪直述', advice: '情绪直述词删掉，改为身体反应或选择变化（他摔了门/她没接话）。' },
  { pattern: /内心(?:充满|涌起|深藏|一片)/g, kind: '情绪直述', advice: '「内心…」是叙述者盖章，改为让读者从动作里读出来。' },
  { pattern: /感到(?:愤怒|悲伤|喜悦|恐惧|惊讶|兴奋|绝望|不满)/g, kind: '情绪直述', advice: '「感到X」是替读者总结，改为具体生理反应或行为后果。' },
  // 工程引用残留：正文里出现「第 N 章」＝写作时用章号指代情节，忘了改写成情节描述。
  // 全半角 + 可含空格都算（实测《燃识》第014章残留「第 5 章在食堂」三处，全角规则会漏判）。
  // guard：豁免「文内合法引用」——故事世界里也有书/法典/碑文分章时不该误报
  //   （如「他翻开《指南》第九章」「法典第十一章：不可妄言」）。
  //   判据：命中片段紧跟在书名号/引号/卷册类词之后 → 视为文内指代，跳过。
  //   漏网风险极低：真残留几乎都出现在句子中段，不会带《》「」前缀。
  { pattern: /第\s*[0-9０-９]+\s*章/g, kind: '工程引用', advice: '章号不能进正文。改写成情节指代（「上次在食堂」/「那天他给我治伤」），读者不知道你有第几章。', guard: inWorldRefGuard },
  { pattern: /第\s*[零一二三四五六七八九十百千]+\s*章/g, kind: '工程引用', advice: '章号不能进正文。改写成情节指代，读者不知道你有第几章。', guard: inWorldRefGuard },
  { pattern: /(?:上一章|下一章|前一章|后一章|本章|这章|本卷|这一卷)/g, kind: '工程引用', advice: '「本章/上一章/本卷」是工程视角词汇，改成情节或时间的自然说法（「那天之后」/「再往前」）。' },
];

// 套词密度：单个「仿佛/一丝」可能是正常中文，高密度聚集才会形成模板腔。
// 词表只收本 repo banned-words 中已明确标为高危的形态，避免把普通功能词一网打尽。
const CLICHE_PATTERNS = [
  // 第一组原本是 /仿佛|犹如|宛若|如同/（纯比喻标记）——v3.0 删除：
  // ① 这批词已被 METAPHOR_MARKER_PATTERN 数过一遍，同词两处计数；
  // ② 更关键的是方向反了：lieflat 配对复测人类比喻标记 0.38/千字 vs AI 0.16，
  //    人类多 2.4 倍。把它们放进「AI 套词黑名单」等于惩罚爱用比喻的作者。
  // 详见 references/rejected-signals.md X-7。
  /一丝|一抹|些许|几分|隐约/g,
  /深吸一口气|缓缓|微微|轻轻|淡淡/g,
  /眼中闪过|嘴角勾起|眸光微微一闪|指节泛白|目光锐利|眼神锐利/g,
  /心中涌起一股|心头一震|心中一动|心下了然|心中暗道|心中一凛/g,
  /不容置疑|不容置喙|不易察觉|显而易见|毫无疑问|不可否认/g,
  /声音不大[，,]?却带着|语气平静无波|平静无波|声音平直|听不出情绪/g,
  /不知何时|唾手可得|无声翻涌|沉默(?:在[^。！？!?\n]{0,16})?蔓延|难以言说/g,
  /散发着一股|冰冷的光|格外刺眼|深邃而冰冷/g,
];
const CLICHE_DENSITY_MIN_HITS = 8;
const CLICHE_DENSITY_PER_KILO = 12;

// 比喻密度（v3.0 改判据）：不再按「全章每千字密度」判，改「同一段里 ≥3 个比喻标记」。
// 两个理由：① 方向反了——lieflat 配对复测人类 0.38/千字 vs AI 0.16，人类多 2.4 倍，
// 按全章密度判等于惩罚爱用比喻的作者；② 真正像 AI 的不是「一章里有几个比喻」，
// 是「一段里连着比三下」——比喻成片堆叠才是修辞模板。分段落判也顺带给出可定位的行号。
// 只做 advisory，修法是删到必要数量并回到具体画面，不是把“像”换成另一组比喻词。
const METAPHOR_MARKER_PATTERN = /好像|像是|仿佛|宛如|如同|犹如|(?<![不头图画影录摄肖])像(?![头像素])/g;
const METAPHOR_LIKE_PHRASE_PATTERN = /(?:死|水|冰|火|潮水|石头|木头|机器|纸|铁|鬼|死人|刀|针|网|墙)一样/g;
// 一段里允许多少个比喻标记：2 个以内正常（并列递进也常见），第 3 个起才算成片。
const METAPHOR_PER_PARAGRAPH_MAX = 2;
const METAPHOR_MAX_FINDINGS = 5;

// 解释链密度：常见“他知道/他明白/必须需要”连续替读者推理，读感像报告。
// 单个判断词可服务推理；高密度聚集才提示回到角色当下证据。
// connector 组只留真 AI 腔的连接词。v3.0 移出「这意味着/也就是说/换句话说」：
// lieflat 配对复测三项全反（人类 0.027 vs AI 0.016/千字），小说里「也就是说」
// 多半是角色口语转述。见 references/rejected-signals.md X-2。
const REASONING_CHAIN_PATTERNS = [
  { key: 'mental', core: true, pattern: /(?<![不没未无])(?:他|她|我)?(?:知道|明白|意识到|清楚|判断|确认|分析)/g },
  { key: 'connector', core: true, pattern: /真正的问题(?:在于)?|问题在于|关键在于|在这种情况下|按照这个逻辑|只有这样|想到这里/g },
  { key: 'modal', core: true, pattern: /(?:(?<!不)(?:必须|需要|应该|只要|就会|可能|可以|能够|无法)|不能)[^。！？!?\n]{0,16}(?:判断|确认|承担|维持|稳住|控制|扩大|失控|带来|造成|理解|默认|回家|进门|核对|筛选|减少|建立|风险|结果|秩序|责任)/g },
  { key: 'abstract', core: false, pattern: /(?:任务|条件|风险|来源|逻辑|局面|结果|责任|秩序|规则|信息不足|决策能力)/g },
];
const REASONING_CHAIN_MIN_HITS = 8;
const REASONING_CHAIN_CORE_MIN_HITS = 4;
const REASONING_CHAIN_MIN_BUCKETS = 2;
const REASONING_CHAIN_PER_KILO = 18;

// 系统公告公文腔：只看成片方括号规则/面板行里的硬规则词。
// 这不是特定题材词表；单条严肃规则、日常叙述或普通对话不触发。
const NOTICE_FORMAL_PATTERNS = [
  /不得|必须|不可|禁止|严禁|应当|须|需|务必/g,
  /当前|本公告|本规则|本系统|提示|任务失败|临时权限|权限|状态|等级/g,
  /维持|公共区域|秩序|优先|惩罚|处罚|违规|指令|执行/g,
  /被视为|同样计入|计入|承担|责任|单位|撤回|转发|截图/g,
];
const NOTICE_FORMAL_CORE_PATTERN = /不得|必须|不可|禁止|严禁|应当|须|需|务必|被视为|同样计入|计入/g;
const NOTICE_FORMAL_MIN_LINES = 4;
const NOTICE_FORMAL_MIN_HITS = 12;
const NOTICE_FORMAL_CORE_MIN_HITS = 5;
const NOTICE_FORMAL_PER_KILO = 60;

// 过度精炼短段：过度处理样本里常见大量 15 字以内叙述段，且“的/了/就/着/过/呢/吧/啊”等
// 自然连接偏少；对照文本通常保留更多自然连接。此项只做 advisory，禁止机械注水。
const OVERCOMPRESSED_PROSE_PARTICLE_PATTERN = /[的了就着过呢吧啊呀嘛]/g;
const OVERCOMPRESSED_PROSE_MIN_CHARS = 1200;
const OVERCOMPRESSED_PROSE_MIN_PARAS = 45;
const OVERCOMPRESSED_PROSE_SHORT_MAX_CHARS = 15;
const OVERCOMPRESSED_PROSE_SHORT_RATIO = 0.58;
const OVERCOMPRESSED_PROSE_PARTICLE_PER_KILO = 85;

// 短对话堆砌（2026-11-17 补，来源：番茄作家学院「投稿前必查：告别文风呆板」课程 2 下）。
// 判据照课程原话：多组短对话连续出现、一句一段、台词之间没有动作/神态/场景穿插，像对着剧本念词。
// 只数「纯台词段」——整段除引号里的台词外没有任何引号外叙述；长台词不算（长台词自带信息量）。
// 课程的明确豁免：念信/读纸条/内心独白不是对话；短信、电话、弹幕、论坛留言、围观喊话本就一句一句，
// 属于合理写法 → 所以此规则永远是 advisory，命中后先判体裁功能，再决定合并或插动作。
const SHORT_DIALOGUE_RUN_MIN = 4;
const SHORT_DIALOGUE_MAX_CHARS = 22;
const DIALOGUE_OPEN_CHARS = new Set(['「', '“', '『', '"']);
const DIALOGUE_CLOSE_CHARS = new Set(['」', '”', '』', '"']);

// 低连接密度：单纯低功能词会误抓有大量中长句的文本；
// 因此必须叠加“中长句不足”，并只看引号外叙述。这是 overcompressed 的短窗口补充，只做 advisory。
const LOW_CONNECTIVE_FUNCTION_TERMS = ['的', '了', '就', '在', '是', '也', '都', '还', '又', '把', '被', '给', '这个', '那个', '里面', '以后', '时候', '现在', '因为', '所以', '但是', '不过', '然后', '已经', '还是', '起来', '出来', '下去'];
const LOW_CONNECTIVE_PLAIN_TERMS = ['的', '了', '就', '也', '还', '又', '这个', '那个', '东西', '事情', '时候', '里面', '以后', '一下', '一点', '有点', '还是'];
const LOW_CONNECTIVE_MIN_CHARS = 800;
const LOW_CONNECTIVE_FUNCTION_PER_KILO = 100;
const LOW_CONNECTIVE_PLAIN_PER_KILO = 65;
const LOW_CONNECTIVE_LONG_SENTENCE_CHARS = 30;
const LOW_CONNECTIVE_LONG_SENTENCE_RATIO = 0.08;

// either-or「不是A就是B / 不是A也是B」里紧贴的「是」是连词的一部分，不是肯定项系动词。
// 含「不」以沿用「不是A，也不是B」第二个否定段不算翻转的旧排除。
const COMPACT_EITHER_OR_PREV = new Set(['不', '就', '也']);
// 句尾语气/反问助词；「…，是吗 / 是吧 / 是嘛」是反问尾巴，不是否定后的肯定翻转。
const TAG_PARTICLES = new Set(['吗', '吧', '嘛']);
// 段首确认语；「不是第一次来。是的，他还记得……」里的「是的/是啊」
// 是承接确认，不是「不是 A，是 B」的肯定翻转。
const AFFIRMATION_TAG_PARTICLES = new Set(['的', '啊', '呀', '呢']);
const AFFIRMATION_TAG_BOUNDARY = new Set(['', '，', ',', '。', '.', '！', '!', '？', '?', '、', '；', ';', '：', ':', '\n', '\r', '\t', ' ']);

// 成对引号（台词/系统播报/弹幕）的字符对，stripQuoted 与 quotedRanges 共用一份来源。
// 引号片段一律不跨行（字符类里排掉 \n）：正文漏一个收引号很常见（多段台词只在末段收尾、
// 全半角引号混用都会漏），若允许跨行配对，一个未闭合的开引号会把后面成百上千字全算成
// 「引号内」，让 quotedRanges 的消费方（not-is 跨行扫描）把整段叙述静默豁免掉。
const QUOTE_PAIRS = [['「', '」'], ['『', '』'], ['【', '】'], ['“', '”'], ['‘', '’'], ['"', '"'], ["'", "'"]];
const QUOTE_SOURCES = QUOTE_PAIRS.map(([open, close]) => `${escapeRegExp(open)}[^${escapeRegExpCharClass(close)}\\n]*${escapeRegExp(close)}`);

// ---- 实战测试漏网句式（来源：实战写作抓到的真实漏网例句；2026-07 校准）----
// 校准基线：《万疆》真人正文 20 章（第1/10/20/…/190章）+ demo 前 20 章。
// blocking 规则要求真人语料命中 ≈0（每 20 章 ≤1 处且人工判定确属该句式）；数据见各规则注释。

// 音量反差腔（实战漏网 A）：「声音不高，第一句却稳稳压住了整个大厅。」
// 旧网只有套词密度桶里的「声音不大，却带着」，音量词/转折词一换就漏。
// 引号外叙述逐处 blocking；修法是删掉音量铺垫，直接写声音落进场子的具体效果。
// 校准：《万疆》20 章 0 命中，demo 前 20 章 0 命中。
const VOICE_CONTRAST_PATTERN = /声音(?:并)?不[大高响亮][^。！？!?\n]{0,16}[却但偏]/g;

// 否定排比（实战漏网 B）：「没有伴奏，没有和声，没有提词器。」同句 ≥2 个「没有X，」连排；
// 变体「他没炫技，没有那种…架势。他只是唱」先否定铺垫、再用「只是/只会/只有」收肯定。
// 只收「没/没有」段，不收「不X」段——真人叙述里「不哭不闹」类太常见，收进来误报换不来收益。
// 光杆「没」还得挡两类非否定用法，否则正常叙述会被判成排比：
//   1) 黏着语素（沉没/淹没/埋没/出没/隐没…）——前字排除，「船沉没在雾里，没人回头，…只有…」不算；
//   2) 时间惯用语（没多久/没过多久/没等X）——后字排除，「没多久，没等她撑伞，…只有…」不算。
// 「没有X」段不带这两种歧义（黏着语素后接不出「有」，时间惯用语已被后字排除覆盖），
// 第一条连排式照旧不加护栏。
// 校准：《万疆》20 章 0 命中，demo 前 20 章 0 命中。
const NEGATION_PARADE_PATTERNS = [
  /(?:没有[^。！？!?\n，,]{1,12}[，,]){2}/g,
  /(?<![沉淹埋出隐湮吞覆漫泯])没(?!有?过?多久)(?:有)?[^。！？!?\n，,]{1,12}[，,]\s*没(?!有?过?多久)(?:有)?[^。！？!?\n，,]{1,16}[，,。.][^。！？!?\n，,]{0,6}只(?:是|会|有)/g,
];
const CROSS_NEGATION_START = /^不是[^。！？!?\n]{1,24}[。！？!?]?$/;
const CROSS_NEGATION_MIDDLE = /^(?:也|还)不是[^。！？!?\n]{1,24}[。！？!?]?$/;
const CROSS_NEGATION_END = /^只是[^。！？!?\n]{1,32}[。！？!?]?$/;

// 两类常见但不能直接判错的工整框架，只做 advisory。与 blocking 规则不同，这里故意扫描
// 台词：自然点单「不放辣，不放葱」靠对象最短长度排除；更长的同动词清单交语义审查判断功能。
const DECISION_FRAME_PATTERN = /至于([\u3400-\u9fff]{1,3})不\1[，,]\s*怎么\1/g;
const REPEATED_NEGATIVE_VERB_PATTERN = /不([\u3400-\u9fff]{1,2})([\u3400-\u9fff]{2,8})[，,]\s*不\1([\u3400-\u9fff]{2,8})/g;

// 反序对比腔（实战漏网 C）：「是真嗓子，不是修音修出来的」——not-is-comparison 的反序变种。
// 复用 not-is 的排除基建：引号内剥离（maskQuoted）、「是的/是啊」确认语（isAffirmationTagAt）；
// 前字排除从 either-or 的 不/就/也 扩展到全部「X是」连词/副词合成词（还是/只是/可是/但是/
// 于是/倒是/像是/若是/要是/正是/便是/总是/老是/更是/最是/算是/怕是/凡是/或是/即是/自是/
// 竟是/原是/本是/仍是/许是/净是/光是/单是/尽是）；「是不是」问句起头与「不是吗/不是么/
// 不是吧」反问尾巴单独排除。
// 校准：《万疆》20 章 0 命中，demo 前 20 章 0 命中，按 blocking 实现。
const REVERSE_NOT_IS_PATTERN = /是([^。！？!?\n，,]{1,12})[，,]\s*(?:而)?不是([^。！？!?\n]{1,20})/g;
const REVERSE_NOT_IS_PREV_EXCLUDE = new Set([...COMPACT_EITHER_OR_PREV, '还', '只', '可', '但', '于', '倒', '像', '若', '要', '正', '便', '总', '老', '更', '最', '算', '怕', '凡', '或', '即', '自', '竟', '原', '本', '仍', '许', '净', '光', '单', '尽']);

// 预告式总结收尾（实战漏网 D）：「没人知道，这才刚刚开头。」「一场…震惊接力，正朝着…缓缓压了过去。」
// 章尾替读者预告下一章走向是 AI 收尾腔。只扫文末窗口（剥引号后可见字数，按行取整），
// 正文中段的「没人知道」多为普通叙述，不误伤；引号内台词（「没人知道…」）不计。
// 「正式拉开序幕/帷幕」是场内事件的报幕式陈述（真人语料「钟声再度响起，比赛正式拉开序幕」），
// 不是叙述者预告，前置 lookbehind 排除。
// 校准：《万疆》20 章排除「正式拉开序幕」2 处报幕句后 0 命中，demo 前 20 章 0 命中。
// 2026-10 补：词表原先只收「没人知道 / 谁也不知道 / 谁也没想到」，
// 漏掉同义高频形态「没有人知道」「没人会知道」「谁都不清楚」——都是同一类章尾预告。
// 下方 (?<!不) 前查保证「没有人不知道」这类双重否定不被误收。
const TRAILER_ENDING_PATTERN = /没人(?:会)?(?:知道|想到|留意到)|没有人(?:会)?(?:知道|想到)|谁(?:也|都)?(?:不)?(?:知道|清楚|想到)|殊不知|(?:这|那)?才刚刚开(?:始|头)|正(?:朝着|向着)[^。！？!?\n]{0,24}(?:压|涌|袭|逼)(?:了?过去|了?过来|来)|(?<!正式)拉开(?:序幕|帷幕)|即将(?:开始|来临|降临)/g;
// 章尾窗口按「行数」而非纯字数收口：2000–3000 字章的最后 ~12 行就是章尾区，
// 章末钩子本来就该落在这里。原实现按 600 可见字往后扫，在 2000 字章里等于覆盖
// 末尾 30%，导致中后段的预告句（如「这一切都结束了」）落在窗口外被漏掉。
// 章尾是掉追更的关键位置，宁可窗口小一点也不能漏。
const TRAILER_ENDING_WINDOW_LINES = 14;

// 章尾状态总结体：把细纲「结尾设定」原样写成总结句收章（「这一夜注定无人入眠」
// 「这一切都结束了」「新的人生才刚刚开始」「命运的齿轮」）。与 trailer-ending 共用文末窗口，
// 区别是它盖章过去、trailer-ending 预告将来；收的都是 banned-words 已按名禁掉的形态。
// 不收「(这|那)一刻…终于明白」：真人语料里那是正常的认知节拍，短篇第一人称审判句还是卖点
// （short-craft「审判金句 / 心死余韵」），密度型由 advisory 的 abstract-summary-tic 兜。
// 各分支都要求落在句末断言位，否则会吃进条件从句（等这一切结束了，我们就…）、动补
// （这一切都说明得非常清楚）、成语跨匹配（这一刻…命中注定）、系表（这一战的结果是注定的）、
// 及物用法（就这样…才结束了这个话题）、场内报幕（就这样…宣布…圆满落幕）和否定认知
// （他不知道这一切意味着什么）——最后一类靠 (?!什么) 排掉间接疑问，那是盖章的反面。
// 校准（文末 600 字窗口，命中逐条人工复核）：qimao 章中段 20000 章命中 1 处（0.005%）、
// heiyan 整篇 3999 篇命中 22 处（0.550%，全部是上列禁用形态）；同批既有 trailer-ending
// 分别命中 1.345% / 6.602%——本规则误报面显著小于已上线的同窗口规则。短篇整篇即收口，
// 基线天然高于长篇章中段，故两个总体分别报数。
const TRAILER_SUMMARY_PATTERN = /这一(?:夜|天|刻|战|年|局|役)[，,]?[^。！？!?，,\n]{0,6}(?<!命中)(?<!是)注定[^。！？!?\n]{0,8}[。！]|就这样[，,][^。！？!?，,\n]{0,8}(?:一切|全部)[^。！？!?，,\n]{0,4}(?:结束了|落幕|收场)[。！]|这一切[，,]?[^。！？!?，,\n]{0,6}(?:都)?(?:说明|意味着|结束了)(?!的)(?:(?!什么)[^。！？!?\n]){0,6}[。！]|(?:新的篇章|新的旅程|崭新的篇章|新的人生)[^。！？!?\n]{0,6}(?:开始|拉开|展开)|命运[^。！？!?\n]{0,6}齿轮/g;

// 引号强调滥用（实战漏网 E，advisory 密度型，风格照 metaphor-density-tic）：
// 叙述里短词加引号强调（他是被请来"把关"的）。只数叙述层 1-4 字成对引号片段；
// 排除项：【】系统面板载体、引语动词（说|道|问|喊|答|念|叫|回|吼|嘀咕，加细 骂|写|读|唱）
// 前 6 字/后 3 字邻接的极短台词、引号内含句读的台词、引号外无叙述的行（独立台词/
// 弹幕流/拟声词连发）、引号套引号（台词内强调）。全文 ≥3 处报一条——单处强调是
// 正常修辞，密度高才是模板腔。
// 校准：demo 前 20 章 0 章过阈值；《万疆》20 章 2 章过阈值（海报标语“我在番城”系列、
// “邀战书”等转述载体，真人也这么写），所以该规则只做 advisory，不升 blocking。
const QUOTE_EMPHASIS_MIN_HITS = 3;
const QUOTE_EMPHASIS_MAX_VISIBLE = 4;
const QUOTE_EMPHASIS_SPEECH_VERB_PATTERN = /[说道问喊答念叫回吼骂写读唱嘀咕]/;

// review 分类：下游按它分流。mechanical = 命中的表面形态本身就是问题，写作端就地改写即可；
// semantic = 同一形态可能承担叙事功能，要读上下文判断保留还是改（交语义审查）。blocking 只能是
// mechanical（blocking 的含义就是不需判断、必须清零）。新增 finding type 必须在此登记，否则运行期
// 直接报错退出（scripts/test-ai-patterns.sh 另从源码抽 type 与本表双向核对）。
//   type                         review      理由
//   hard-banned-word              mechanical  SKILL.md 第七节硬禁词表逐条实现，blocking
//   not-is-comparison            mechanical  固定句式模板「不是A，而是B」，blocking
//   voice-contrast               mechanical  固定反差模板「声音不大…却」，blocking
//   negation-parade              mechanical  固定否定排比模板，blocking
//   reverse-not-is               mechanical  固定反序对比模板「是A，不是B」，blocking
//   trailer-ending               mechanical  文末窗口固定预告短语，blocking
//   trailer-summary              mechanical  文末窗口固定总结短语，blocking
//   em-dash                      mechanical  标点；条件 blocking（引号外 + 全章 ≥2/千字）
//   period-stutter               mechanical  连续短句计数，合句即可
//   micro-action-tic             mechanical  固定补语「了下/了一下」密度
//   negation-pair-tic            semantic    「没A，也没B」否定对偶，留真正要强调的
//   cliche-density-tic           mechanical  固定禁用词表密度
//   stock-reaction-tic           semantic    身体反应可能承担物理后果或情绪，要逐处做删除测试
//   action-list-tic              semantic    打斗/追逐等功能性动作编排可保留
//   abstract-summary-tic         semantic    作者总结/上帝视角，是否越位要看语境
//   metaphor-density-tic         semantic    比喻是否服务画面、删哪几个要读上下文
//   reasoning-chain-tic          semantic    替读者推理的解释链，长推理段可能是正当的
//   system-notice-formality-tic  semantic    系统/规则文本本就可以公文腔，看体裁功能
//   overcompressed-prose-tic     semantic    短段过密可能是节奏选择，禁止机械注水
//   low-connective-density-tic   semantic    同上，连接词补不补要看读感
//   quote-emphasis-tic           semantic    标语/转述载体的引号是正常写法
//   formulaic-parallelism        semantic    工整并列可能承担辩解或悬念排除
//   short-dialogue-run           semantic    论坛/短信/围观喊话本就一句一句，先判体裁功能
const REVIEW_CLASSES = Object.freeze({
  'hard-banned-word': 'mechanical',
  'not-is-comparison': 'mechanical',
  'voice-contrast': 'mechanical',
  'negation-parade': 'mechanical',
  'reverse-not-is': 'mechanical',
  'trailer-ending': 'mechanical',
  'trailer-summary': 'mechanical',
  'em-dash': 'mechanical',
  'period-stutter': 'mechanical',
  'micro-action-tic': 'mechanical',
  'negation-pair-tic': 'semantic',
  'weak-verb-tic': 'mechanical',
  'emotion-label-tic': 'semantic',
  'cliche-density-tic': 'mechanical',
  'stock-reaction-tic': 'semantic',
  'action-list-tic': 'semantic',
  'abstract-summary-tic': 'semantic',
  'metaphor-density-tic': 'semantic',
  'reasoning-chain-tic': 'semantic',
  'system-notice-formality-tic': 'semantic',
  'overcompressed-prose-tic': 'semantic',
  'low-connective-density-tic': 'semantic',
  'quote-emphasis-tic': 'semantic',
  'formulaic-parallelism': 'semantic',
  'short-dialogue-run': 'semantic',
  'crowd-sync-tic': 'semantic',
  'filter-verb-tic': 'semantic',
  'decorative-symbol': 'mechanical',
  'intensifier-abuse': 'mechanical',
});

// category：处置方式分类，与 severity 正交。severity 说「拦不拦」，category 说「该不该改」。
// 混在一个 advisory 里的后果是模型无从判断：`not-is-comparison`（真 AI 腔，必须改）与
// `metaphor-density-tic`（文学手法，多数该保留）同样是一行 advisory，改错了反而更假。
//   ai-tone     真 AI 腔：句式/推理方式/程度加码，改了才对
//   cliche      网文水词：该删，但删了只是「不那么水」，不改也是人写的
//   literary    文学手法：比喻/引号强调/过滤动词，先判断，多数保留
//   readability 可读性：碎句号/段落呼吸/连接密度，看节奏，不是 AI 指纹
const CATEGORIES = Object.freeze({
  'hard-banned-word': 'ai-tone',
  'not-is-comparison': 'ai-tone',
  'voice-contrast': 'ai-tone',
  'negation-parade': 'ai-tone',
  'reverse-not-is': 'ai-tone',
  'trailer-ending': 'ai-tone',
  'trailer-summary': 'ai-tone',
  'negation-pair-tic': 'ai-tone',
  'emotion-label-tic': 'ai-tone',
  'abstract-summary-tic': 'ai-tone',
  'reasoning-chain-tic': 'ai-tone',
  'formulaic-parallelism': 'ai-tone',
  'intensifier-abuse': 'ai-tone',
  'em-dash': 'readability',
  'period-stutter': 'readability',
  'weak-verb-tic': 'readability',
  'action-list-tic': 'readability',
  'system-notice-formality-tic': 'readability',
  'overcompressed-prose-tic': 'readability',
  'low-connective-density-tic': 'readability',
  'short-dialogue-run': 'readability',
  'decorative-symbol': 'readability',
  'micro-action-tic': 'cliche',
  'cliche-density-tic': 'cliche',
  'stock-reaction-tic': 'cliche',
  'crowd-sync-tic': 'cliche',
  'metaphor-density-tic': 'literary',
  'quote-emphasis-tic': 'literary',
  'filter-verb-tic': 'literary',
});

// 两张表必须键集一致：新增 finding type 时，review 与 category 都要登记。
// 这里在模块加载期就断言，避免「加了规则忘了分类」到运行期才炸（或更糟：静默缺字段）。
{
  const missCat = Object.keys(REVIEW_CLASSES).filter((t) => !CATEGORIES[t]);
  const missRev = Object.keys(CATEGORIES).filter((t) => !REVIEW_CLASSES[t]);
  if (missCat.length || missRev.length) {
    console.error(`internal error: REVIEW_CLASSES 与 CATEGORIES 键集不一致`
      + `（缺 category: ${missCat.join(', ') || '无'}；缺 review: ${missRev.join(', ') || '无'}）`);
    process.exit(2);
  }
}

function categoryOf(type) {
  return CATEGORIES[type];
}

const options = {
  json: false,
  files: [],
  failOn: 'all',
  diff: null,
};

for (let i = 2; i < process.argv.length; i += 1) {
  const arg = process.argv[i];
  if (arg === '--check') {
    // Accepted for symmetry with normalize-punctuation.js; detection is always check-only.
  } else if (arg === '--list-review-classes') {
    process.stdout.write(`${JSON.stringify(REVIEW_CLASSES, null, 2)}\n`);
    process.exit(0);
  } else if (arg === '--list-categories') {
    process.stdout.write(`${JSON.stringify(CATEGORIES, null, 2)}\n`);
    process.exit(0);
  } else if (arg === '--diff') {
    const next = process.argv[i + 1];
    if (!next || next.startsWith('-')) die('--diff 需要一个 before.json 路径（上一次 --json 的输出）');
    options.diff = next;
    i += 1;
  } else if (arg.startsWith('--diff=')) {
    options.diff = arg.slice('--diff='.length);
  } else if (arg === '--json') {
    options.json = true;
  } else if (arg.startsWith('--fail-on=')) {
    const v = arg.slice('--fail-on='.length);
    if (v !== 'blocking' && v !== 'all') die(`--fail-on must be 'blocking' or 'all'`);
    options.failOn = v;
  } else if (arg === '-h' || arg === '--help') {
    process.stdout.write(`${USAGE}\n`);
    process.exit(0);
  } else if (arg.startsWith('-')) {
    die(`Unknown option: ${arg}`);
  } else {
    options.files.push(arg);
  }
}

if (options.files.length === 0) {
  die('No files provided');
}

let failed = false;
const allFindings = [];

for (const file of options.files) {
  const fullPath = path.resolve(file);
  let input;
  try {
    input = fs.readFileSync(fullPath, 'utf8');
  } catch (error) {
    failed = true;
    if (!options.json) console.error(`${file}: unable to read (${error.message})`);
    continue;
  }

  let whitelist;
  try { whitelist = loadStyleWhitelist(fullPath); }
  catch (error) { die(`${file}: unable to read .deslop-whitelist (${error.message})`); }
  const findings = scanDocument(maskStyleText(input, whitelist)).map((finding) => ({
    file,
    ...finding,
    review: reviewClass(finding.type),
    category: categoryOf(finding.type),
  }));
  allFindings.push(...findings);
}

const diff = options.diff ? buildDiff(options.diff, allFindings) : null;

if (options.json) {
  const payload = { findings: allFindings };
  if (diff) payload.diff = diff;
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
} else {
  for (const finding of allFindings) {
    console.log(`${finding.file}:${finding.line}:${finding.column}: [${finding.severity}] ${finding.type} (${finding.category}): ${finding.message} (${finding.excerpt})`);
  }
  if (diff) printDiff(diff);
}

if (failed) process.exit(2);
// --fail-on=blocking 只在出现 blocking finding 时退出 1（advisory 仅报告）；默认 all 沿用「有任何 finding 即 1」。
const hasBlocking = allFindings.some((f) => f.severity === 'blocking');
if (options.failOn === 'blocking' ? hasBlocking : allFindings.length > 0) process.exit(1);

// 改前／改后对比（v3.0，--diff）：before.json 来自上一次 `--json` 输出。
// 目的不是再看一遍命中，而是看「改完有没有改过头」——某个类型从 2 涨到 7，说明这轮修法
// 在制造新毛病；blocking 归零但 advisory 翻倍，说明是转移而不是修好。
// 只做计数对比，不做逐段文本 diff：改一句往往挪动整段，文本 diff 全是噪声。
function buildDiff(beforePath, afterFindings) {
  let before;
  try {
    before = JSON.parse(fs.readFileSync(path.resolve(beforePath), 'utf8'));
  } catch (error) {
    die(`--diff 读不了 ${beforePath}：${error.message}`);
  }
  const beforeFindings = Array.isArray(before) ? before : (before.findings || []);
  const tally = (list) => {
    const map = new Map();
    for (const f of list) {
      const cur = map.get(f.type) || { count: 0, blocking: 0, advisory: 0 };
      cur.count += 1;
      if (f.severity === 'blocking') cur.blocking += 1; else cur.advisory += 1;
      map.set(f.type, cur);
    }
    return map;
  };
  const b = tally(beforeFindings);
  const a = tally(afterFindings);
  const types = [...new Set([...b.keys(), ...a.keys()])].sort();
  const rows = types.map((type) => {
    const x = b.get(type) || { count: 0, blocking: 0, advisory: 0 };
    const y = a.get(type) || { count: 0, blocking: 0, advisory: 0 };
    const sample = afterFindings.find((f) => f.type === type) || beforeFindings.find((f) => f.type === type) || {};
    return {
      type,
      category: sample.category || null,
      before: x.count,
      after: y.count,
      delta: y.count - x.count,
      before_blocking: x.blocking,
      after_blocking: y.blocking,
    };
  });
  return {
    before: {
      total: beforeFindings.length,
      blocking: beforeFindings.filter((f) => f.severity === 'blocking').length,
    },
    after: {
      total: afterFindings.length,
      blocking: afterFindings.filter((f) => f.severity === 'blocking').length,
    },
    rows,
  };
}

function printDiff(diff) {
  const { before, after, rows } = diff;
  console.log('');
  console.log(`前后对比：${before.total} → ${after.total} 处（blocking ${before.blocking} → ${after.blocking}）`);
  if (rows.length === 0) {
    console.log('  两版都没有命中。');
    return;
  }
  console.log(`  ${'type'.padEnd(30)} ${'before'.padStart(6)} ${'after'.padStart(6)} ${'Δ'.padStart(4)}`);
  for (const r of rows) {
    const mark = r.delta > 0 ? `+${r.delta}` : String(r.delta);
    const label = r.category ? `${r.type} (${r.category})` : r.type;
    console.log(`  ${label.padEnd(30)} ${String(r.before).padStart(6)} ${String(r.after).padStart(6)} ${mark.padStart(4)}`);
  }
  const grew = rows.filter((r) => r.delta > 0 && r.after >= 2);
  if (grew.length) {
    console.log('');
    console.log(`注意：${grew.map((r) => r.type).join('、')} 比改前更多——可能是这轮修法在制造新毛病，不是修好了。`);
  }
  if (after.blocking === 0 && after.total > before.total) {
    console.log('注意：blocking 清零但总命中变多，属于「转移」不是「修好」。');
  }
}

function reviewClass(type) {
  const review = REVIEW_CLASSES[type];
  if (!review) {
    console.error(`internal error: finding type '${type}' has no review class (add it to REVIEW_CLASSES)`);
    process.exit(2);
  }
  return review;
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function escapeRegExpCharClass(text) {
  return text.replace(/[\\\]^-]/g, '\\$&');
}

function die(message) {
  console.error(message);
  console.error(USAGE.trimEnd());
  process.exit(2);
}

// unclosedOpenings：到文末都没闭合的 `<!--` 位置（"行号:列号"）。未闭合的注释不是注释，
// 否则一个漏写的 `-->` 会把后面整章藏起来；这些位置按正文扫描，只把 `<!--` 本身抹掉。
function scanDocument(input, unclosedOpenings = new Set()) {
  const lines = input.split(/\r?\n/);
  const findings = [];
  let fence = null;
  let inFrontMatter = hasYamlFrontMatter(lines);
  let commentOpen = false;
  let commentStart = null;
  let block = [];
  const proseLines = [];

  const flushBlock = () => {
    if (block.length === 0) return;
    findings.push(...scanBlock(block));
    block = [];
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.trim();

    if (inFrontMatter) {
      if (index > 0 && trimmed === '---') inFrontMatter = false;
      continue;
    }

    const fenceMarker = parseFenceMarker(trimmed);
    if (fence) {
      if (fenceMarker && fenceMarker.char === fence.char && fenceMarker.length >= fence.length) {
        fence = null;
      }
      continue;
    }

    if (fenceMarker && !commentOpen) {
      flushBlock();
      fence = fenceMarker;
      continue;
    }

    // HTML 注释（如 `<!-- 去味:跳过 -->` 豁免标记）是元信息不是正文：注释段等长换成空格
    // 保列号，整行都是注释的直接跳过；注释可跨行。
    const masked = maskHtmlComments(line, commentOpen, index, unclosedOpenings);
    commentOpen = masked.open;
    if (masked.openedAt !== null) commentStart = masked.openedAt;
    if (masked.text !== line && !masked.text.trim()) continue;

    // Markdown 标题行是脚手架不是正文：`# 第 1 章 零` 这类章标题若当正文，
    // 「第 N 章」工程引用规则会误报。标题本就不该进导出层，直接跳过。
    if (/^\s{0,3}#{1,6}\s/.test(masked.text)) continue;

    block.push({ text: masked.text, lineNo: index + 1 });
    proseLines.push({ text: masked.text, lineNo: index + 1 });
  }

  if (commentOpen && commentStart !== null) {
    return scanDocument(input, new Set([...unclosedOpenings, commentStart]));
  }

  flushBlock();
  findings.push(...scanProsePatterns(proseLines));
  findings.sort((a, b) => a.line - b.line || a.column - b.column);
  return findings;
}

// 段落级检测：碎句号（连续短叙述句）、长段落、破折号（按功能改写，非机械替换）。
function scanProsePatterns(proseLines) {
  const findings = [];

  // 破折号先全章收集再定级（v3.0）：判据是「位置 + 密度」，不是「出现即 blocking」。
  // 旧版一律 blocking，实测 137 字标准对话段 4 处全判死——其中 2 处在引号内，是中文表示
  // 「话说一半被打断」的唯一写法，改了读者分不清谁打断谁。依据见
  // references/rejected-signals.md X-10：破折号合并口径不显著，拆开后不同模型方向相反。
  const dashHits = [];
  let dashChars = 0;
  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    dashChars += visibleLength(trimmed);
    const ranges = quotedRanges(text);
    const dashPattern = /——|—|--+/g;
    let dash;
    while ((dash = dashPattern.exec(text)) !== null) {
      dashHits.push({
        line: lineNo,
        column: dash.index + 1,
        quoted: insideRanges(dash.index, ranges),
        excerpt: compact(text.slice(Math.max(0, dash.index - 8), dash.index + dash[0].length + 8)),
      });
    }
  }
  if (dashHits.length && dashChars) {
    const perKilo = (dashHits.length / dashChars) * 1000;
    const dense = perKilo >= DASH_BLOCKING_PER_KILO;
    for (const hit of dashHits) {
      findings.push({
        line: hit.line,
        column: hit.column,
        type: 'em-dash',
        severity: (!hit.quoted && dense) ? 'blocking' : 'advisory',
        message: hit.quoted
          ? '破折号在引号内（对话被打断／拖长音）：这是中文里打断话的唯一写法，不改标点。'
            + '要收的是「连叙述带台词全靠破折号串场」，不是这一处。'
          : (dense
            ? `破折号 ${perKilo.toFixed(1)}/千字（≥${DASH_BLOCKING_PER_KILO} 警戒线）：引号外破折号按功能改写——`
              + '打断→动作 beat／短句，拖长音→省略或动作，插入说明→逗号／冒号；勿一律改句号。'
            : `破折号 ${perKilo.toFixed(1)}/千字，未到 ${DASH_BLOCKING_PER_KILO}/千字 警戒线：`
              + '单个破折号是正常标点，别为过检删。'),
        excerpt: hit.excerpt,
      });
    }
  }

  findings.push(...findHardBanned(proseLines));
  findings.push(...findVoiceContrast(proseLines));
  findings.push(...findNegationParade(proseLines));
  findings.push(...findFormulaicParallelism(proseLines));
  findings.push(...findReverseNotIs(proseLines));
  findings.push(...findTrailerEnding(proseLines));
  findings.push(...findQuoteEmphasisTic(proseLines));
  findings.push(...findPeriodStutter(proseLines));
  findings.push(...findShortDialogueRun(proseLines));
  findings.push(...findMicroActionTic(proseLines));
  findings.push(...findNegationPairTic(proseLines));
  findings.push(...findStockReactionTic(proseLines));
  findings.push(...findActionListTic(proseLines));
  findings.push(...findAbstractSummaryTic(proseLines));
  findings.push(...findClicheDensityTic(proseLines));
  findings.push(...findMetaphorDensityTic(proseLines));
  findings.push(...findReasoningChainTic(proseLines));
  findings.push(...findNoticeFormalityTic(proseLines));
  findings.push(...findOvercompressedProseTic(proseLines));
  findings.push(...findLowConnectiveDensityTic(proseLines));
  findings.push(...findWeakVerbTic(proseLines));
  findings.push(...findEmotionLabelTic(proseLines));
  findings.push(...findCrowdSyncTic(proseLines));
  findings.push(...findFilterVerbTic(proseLines));
  findings.push(...findDecorativeSymbol(proseLines));
  findings.push(...findIntensifierAbuse(proseLines));
  return findings;
}

// 音量反差腔（实战漏网 A）：引号外叙述逐处 blocking，位置与摘录取自原文
// （maskQuoted 等长占位保偏移；命中片段不含问号占位符，故不会落进占位区）。
function findHardBanned(proseLines) {
  const findings = [];
  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    // 只扫引号外叙述层：台词里的「你非常生气」是人物口语，不算叙述腔
    const masked = maskQuoted(text);
    for (const rule of HARD_BANNED_PATTERNS) {
      const { pattern, kind, advice } = rule;
      pattern.lastIndex = 0;
      let m;
      while ((m = pattern.exec(masked)) !== null) {
        // 量词/正常用法豁免（如「只剩十分钟」不是程度副词）
        if (typeof rule.guard === 'function' && rule.guard(m, masked)) continue;
        findings.push({
          line: lineNo,
          column: m.index + 1,
          type: 'hard-banned-word',
          severity: 'blocking',
          message: `硬禁词（${kind}）：${advice}`,
          excerpt: compact(masked.slice(m.index, m.index + m[0].length)),
        });
      }
    }
  }
  return findings;
}

function findVoiceContrast(proseLines) {
  const findings = [];

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    const masked = maskQuoted(text);
    VOICE_CONTRAST_PATTERN.lastIndex = 0;
    let match;
    while ((match = VOICE_CONTRAST_PATTERN.exec(masked)) !== null) {
      findings.push({
        line: lineNo,
        column: match.index + 1,
        type: 'voice-contrast',
        severity: 'blocking',
        message: '音量反差腔：「声音不大/不高…却/但…」是 AI 高频反差模板；删掉音量铺垫，直接写声音落进场子的具体效果（谁停了手、哪排安静了）。',
        excerpt: compact(text.slice(match.index, match.index + match[0].length)),
      });
    }
  }

  return findings;
}

// 否定排比（实战漏网 B）：同句「没有X，」连排 / 先否定后「只是」收肯定。
// 可能在同一片文字上重叠命中，按区间去重只报一次。
function findNegationParade(proseLines) {
  const findings = [];

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    const masked = maskQuoted(text);

    const spans = [];
    for (const pattern of NEGATION_PARADE_PATTERNS) {
      pattern.lastIndex = 0;
      let match;
      while ((match = pattern.exec(masked)) !== null) {
        spans.push([match.index, match.index + match[0].length]);
      }
    }
    spans.sort((a, b) => a[0] - b[0]);

    let lastEnd = -1;
    for (const [start, end] of spans) {
      if (start < lastEnd) {
        lastEnd = Math.max(lastEnd, end);
        continue;
      }
      lastEnd = end;
      findings.push({
        line: lineNo,
        column: start + 1,
        type: 'negation-parade',
        severity: 'blocking',
        message: '否定排比：「没有X，没有Y…」/「没X，没有Y，只是Z」是 AI 高频排比模板；删掉否定清单，直接写现场实际有什么，最多留一个最有信息量的否定。',
        excerpt: compact(text.slice(start, end)),
      });
    }
  }

  return findings;
}

function findFormulaicParallelism(proseLines) {
  const findings = [];

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    for (const [pattern, message] of [
      [DECISION_FRAME_PATTERN, '「至于X不X，怎么X」把同一决定拆成工整栏目；若只是复述细纲，压成角色当下的一次判断或直接动作。'],
      [REPEATED_NEGATIVE_VERB_PATTERN, '同动词「不V A，不V B」容易写成否定清单；含台词也要按语境复核，保留真正有功能的一项即可。'],
    ]) {
      pattern.lastIndex = 0;
      let match;
      while ((match = pattern.exec(text)) !== null) {
        findings.push({
          line: lineNo,
          column: match.index + 1,
          type: 'formulaic-parallelism',
          severity: 'advisory',
          message,
          excerpt: compact(match[0]),
        });
      }
    }
  }

  // 跨段「不是A / 也不是B / 只是C」既可能是细纲复述，也可能是正常的
  // 辩解、悬念排除或情绪递进。纯句法无法稳定区分，因此只给 advisory，交给语义复核。
  const window = [];
  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed) continue;
    if (isDivider(trimmed) || isStructural(trimmed)) {
      window.length = 0;
      continue;
    }
    if (window.length && lineNo - window[window.length - 1].lineNo > 2) window.length = 0;
    window.push({ text: maskQuoted(trimmed), original: trimmed, lineNo });
    if (window.length > 3) window.shift();
    if (window.length !== 3) continue;
    if (!CROSS_NEGATION_START.test(window[0].text)
      || !CROSS_NEGATION_MIDDLE.test(window[1].text)
      || !CROSS_NEGATION_END.test(window[2].text)) continue;
    findings.push({
      line: window[0].lineNo,
      column: 1,
      type: 'formulaic-parallelism',
      severity: 'advisory',
      message: '跨段「不是… / 也不是… / 只是…」可能是工整否定铺排，也可能承担辩解或悬念排除；通读语境，只在重复细纲或拖慢画面时改写。',
      excerpt: compact(window.map((entry) => entry.original).join(' / ')),
    });
  }

  return findings;
}

// 反序对比腔（实战漏网 C）：「是A，不是B」。排除基建复用 not-is-comparison：
// 引号内剥离、「是的/是啊」确认语；前字合成词与反问尾巴见 REVERSE_NOT_IS_PREV_EXCLUDE 注释。
function findReverseNotIs(proseLines) {
  const findings = [];

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    const masked = maskQuoted(text);
    REVERSE_NOT_IS_PATTERN.lastIndex = 0;
    let match;
    while ((match = REVERSE_NOT_IS_PATTERN.exec(masked)) !== null) {
      const start = match.index;
      // 「就是/也是/还是/只是/可是…」里的「是」是合成词一部分，不是肯定项系动词。
      if (REVERSE_NOT_IS_PREV_EXCLUDE.has(masked[start - 1])) continue;
      // 「是不是…」问句起头。
      if (masked[start + 1] === '不') continue;
      // 「是的，…不是…」承接确认语（复用 not-is 的判定）。
      if (isAffirmationTagAt(masked, start)) continue;
      // 「…，不是吗/不是么/不是吧」反问尾巴。
      if (/^[吗么吧]/.test(match[2])) continue;
      findings.push({
        line: lineNo,
        column: start + 1,
        type: 'reverse-not-is',
        severity: 'blocking',
        message: '反序对比腔：「是A，不是B」与「不是A，是B」同族；删掉后置否定，直接写 A 的具体表现，或用细节让读者自己对比。',
        excerpt: compact(text.slice(start, start + match[0].length)),
      });
    }
  }

  return findings;
}

// 预告式总结收尾（实战漏网 D）：只扫文末窗口。从文末往回收集叙述行，
// 直到剥引号后的可见字数达到窗口大小（按行取整，边界行整行计入）。
function findTrailerEnding(proseLines) {
  const windowLines = [];
  let counted = 0;

  for (let i = proseLines.length - 1; i >= 0 && counted < TRAILER_ENDING_WINDOW_LINES; i -= 1) {
    const { text } = proseLines[i];
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    windowLines.unshift(proseLines[i]);
    counted += 1;
  }

  const findings = [];
  for (const { text, lineNo } of windowLines) {
    const masked = maskQuoted(text);
    TRAILER_ENDING_PATTERN.lastIndex = 0;
    let match;
    while ((match = TRAILER_ENDING_PATTERN.exec(masked)) !== null) {
      findings.push({
        line: lineNo,
        column: match.index + 1,
        type: 'trailer-ending',
        severity: 'blocking',
        message: '预告式总结收尾：「没人知道/才刚刚开始/正朝着…压了过去」是 AI 章尾预告腔；结尾停在具体动作、画面或一句台词上，悬念让事件自己挂住，别替读者预告下一章。',
        excerpt: compact(text.slice(match.index, match.index + match[0].length)),
      });
    }
    TRAILER_SUMMARY_PATTERN.lastIndex = 0;
    let summaryMatch;
    while ((summaryMatch = TRAILER_SUMMARY_PATTERN.exec(masked)) !== null) {
      findings.push({
        line: lineNo,
        column: summaryMatch.index + 1,
        type: 'trailer-summary',
        severity: 'blocking',
        message: '章尾状态总结体：「这一夜注定…/这一切都结束了/新的人生才刚刚开始/命运的齿轮」是把细纲的结尾设定原样写成了总结句；结尾设定要落成最后一个具体动作、画面或台词，别替读者盖章。',
        excerpt: compact(text.slice(summaryMatch.index, summaryMatch.index + summaryMatch[0].length)),
      });
    }
  }

  return findings;
}

// 引号强调滥用（实战漏网 E）：统计叙述层 1-4 字成对引号强调片段，全文只报一条
// （密度型分布指纹）。台词类排除见 QUOTE_EMPHASIS_* 常量注释。
function findQuoteEmphasisTic(proseLines) {
  let hits = 0;
  let firstLine = null;
  const samples = [];

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    // 引号外没有叙述的行（独立台词/弹幕流/拟声词连发「“叮咚~”“叮咚~”」）整行跳过：
    // 强调滥用是叙述层指纹，没有叙述就无所谓强调。
    if (visibleLength(stripQuoted(trimmed)) === 0) continue;
    const ranges = quotedRanges(text);

    for (const [start, end] of ranges) {
      if (text[start] === '【') continue; // 系统面板/公告载体，不是强调引号
      // 引号套引号：台词内部的强调属于角色语言，不算叙述层强调滥用。
      if (ranges.some(([s2, e2]) => s2 <= start && end <= e2 && (s2 !== start || e2 !== end))) continue;
      const inner = text.slice(start + 1, end - 1);
      const visible = visibleLength(inner);
      if (visible < 1 || visible > QUOTE_EMPHASIS_MAX_VISIBLE) continue;
      if (/[。！？!?…，,；;：:]/.test(inner)) continue; // 含句读的是台词/播报，不是强调
      const before = text.slice(Math.max(0, start - 6), start);
      const after = text.slice(end, end + 3);
      if (QUOTE_EMPHASIS_SPEECH_VERB_PATTERN.test(before) || QUOTE_EMPHASIS_SPEECH_VERB_PATTERN.test(after)) continue; // 引语动词邻接=极短台词
      hits += 1;
      if (firstLine === null) firstLine = lineNo;
      if (samples.length < 6 && !samples.includes(inner)) samples.push(inner);
    }
  }

  if (hits < QUOTE_EMPHASIS_MIN_HITS) return [];

  return [{
    line: firstLine,
    column: 1,
    type: 'quote-emphasis-tic',
    severity: 'advisory',
    message: `引号强调滥用：叙述里 1-4 字短词加引号强调 ${hits} 处；只留真正反讽/转述必要的一两处，其余去掉引号直接写，或换成具体动作让读者自己品。`,
    excerpt: compact(samples.join(' ')),
  }];
}

// 否定对偶：统计引号外叙述里「没A，也没B」式句子，全文只报一条。
function findNegationPairTic(proseLines) {
  let hits = 0;
  let firstLine = null;
  const samples = [];
  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    const narrative = stripQuoted(trimmed);
    NEGATION_PAIR_PATTERN.lastIndex = 0;
    let match;
    while ((match = NEGATION_PAIR_PATTERN.exec(narrative)) !== null) {
      hits += 1;
      if (firstLine === null) firstLine = lineNo;
      if (samples.length < 4) samples.push(match[0]);
    }
  }
  if (hits < NEGATION_PAIR_MIN_HITS) return [];
  return [{
    line: firstLine,
    column: 1,
    type: 'negation-pair-tic',
    severity: 'advisory',
    message: `否定对偶偏多：「没A，也没B」式叙述 ${hits} 处；写人物没做什么是英文式节奏，改成他做了什么（留一两处真正要强调的）。`,
    excerpt: compact(samples.join(' | ')),
  }];
}

// 微动作复读：统计引号外叙述里「了X量词」轻量补语的密度。次数与每千字密度双门槛，
// 全文只报一条（这是分布级指纹，不是逐处问题）。
function findMicroActionTic(proseLines) {
  let hits = 0;
  let narrativeChars = 0;
  let firstLine = null;
  const samples = [];

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    const narrative = stripQuoted(trimmed);
    narrativeChars += visibleLength(narrative);
    MICRO_TIC_PATTERN.lastIndex = 0;
    let match;
    while ((match = MICRO_TIC_PATTERN.exec(narrative)) !== null) {
      hits += 1;
      if (firstLine === null) firstLine = lineNo;
      if (samples.length < 6 && !samples.includes(match[0])) samples.push(match[0]);
    }
  }

  if (narrativeChars === 0 || hits < MICRO_TIC_MIN_HITS) return [];
  const perKilo = (hits / narrativeChars) * 1000;
  if (perKilo < MICRO_TIC_PER_KILO) return [];

  return [{
    line: firstLine,
    column: 1,
    type: 'micro-action-tic',
    severity: 'advisory',
    message: `微动作复读：「了下/了一下」式轻量补语 ${hits} 处（${perKilo.toFixed(1)}/千字）；同一反应模板高密度复现是机械指纹，合并动作 beat、换具体细节，别每个动作都补一个轻反应尾巴。`,
    excerpt: compact(samples.join(' ')),
  }];
}

// 套式反应细节：统计引号外叙述中通用的部位/声线反应与固定语气比喻。
// 这是删除测试的候选集，不是身体描写黑名单；全篇只报一条，保留有动作后果、
// 伤势、人物习惯或情节功能的细节。
function findStockReactionTic(proseLines) {
  let hits = 0;
  let narrativeChars = 0;
  let firstLine = null;
  const samples = [];

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    const narrative = stripQuoted(trimmed);
    narrativeChars += visibleLength(narrative);

    for (const pattern of STOCK_REACTION_PATTERNS) {
      pattern.lastIndex = 0;
      let match;
      while ((match = pattern.exec(narrative)) !== null) {
        hits += 1;
        if (firstLine === null) firstLine = lineNo;
        const sample = sentenceAround(narrative, match.index);
        if (samples.length < 6 && sample && !samples.includes(sample)) samples.push(sample);
      }
    }
  }

  if (narrativeChars === 0 || hits < STOCK_REACTION_MIN_HITS) return [];
  const perKilo = (hits / narrativeChars) * 1000;
  if (perKilo < STOCK_REACTION_PER_KILO) return [];

  return [{
    line: firstLine,
    column: 1,
    type: 'stock-reaction-tic',
    severity: 'advisory',
    message: `套式反应细节：指尖/指节/喉结/眼圈/声音放轻等通用反应或“平静得像在念”式语气比喻 ${hits} 处（${perKilo.toFixed(1)}/千字）；逐处做删除测试，只标注情绪、不改变选择、关系、物件或动作结果的删掉，不要换部位或同义动作。`,
    excerpt: compact(samples.join(' | ')),
  }];
}

function findActionListTic(proseLines) {
  const findings = [];

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    const narrative = stripQuoted(trimmed).trim();
    if (!narrative) continue;

    ACTION_LIST_VERB_PATTERN.lastIndex = 0;
    const verbs = [];
    let match;
    while ((match = ACTION_LIST_VERB_PATTERN.exec(narrative)) !== null) {
      verbs.push(match[0]);
    }

    if (verbs.length < ACTION_LIST_MIN_HITS) continue;
    const separators = (narrative.match(/[，、；;]/g) || []).length;
    if (separators < ACTION_LIST_MIN_SEPARATORS) continue;

    findings.push({
      line: lineNo,
      column: 1,
      type: 'action-list-tic',
      severity: 'advisory',
      message: `监控摄像头式动作清单：同段连续动作动词 ${verbs.length} 个、分隔符 ${separators} 个；合并琐碎步骤，只保留有情绪/情节功能的动作，必要时用角色犹豫、误判或环境反馈做缓冲。`,
      excerpt: compact(verbs.slice(0, 8).join(' ')),
    });
  }

  return findings;
}

// 套词密度：统计引号外叙述中的高危禁用词聚集。不是逐词替换器；只在密度高到
// 形成模板腔时提示，修法是删总结、换具体动作/物件/对话，不是同义词轮换。
function findDecorativeSymbol(proseLines) {
  const findings = [];

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    DECORATIVE_SYMBOL_PATTERN.lastIndex = 0;
    let match;
    while ((match = DECORATIVE_SYMBOL_PATTERN.exec(text)) !== null) {
      findings.push({
        line: lineNo,
        column: match.index + 1,
        type: 'decorative-symbol',
        severity: 'advisory',
        message: `装饰符号「${match[0]}」：正文不用符号点缀或分隔场景，改用空行。`,
        excerpt: compact(text.slice(Math.max(0, match.index - 8), match.index + match[0].length + 8)),
      });
    }
  }

  return findings;
}

function findIntensifierAbuse(proseLines) {
  let hits = 0;
  let narrativeChars = 0;
  let firstLine = null;
  const samples = [];

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    const narrative = maskQuoted(trimmed);
    narrativeChars += visibleLength(narrative);

    INTENSIFIER_PATTERN.lastIndex = 0;
    let match;
    while ((match = INTENSIFIER_PATTERN.exec(narrative)) !== null) {
      hits += 1;
      if (firstLine === null) firstLine = lineNo;
      if (samples.length < 8 && !samples.includes(match[0])) samples.push(match[0]);
    }
  }

  if (hits < INTENSIFIER_MIN_HITS) return [];

  return [{
    line: firstLine,
    column: 1,
    type: 'intensifier-abuse',
    severity: 'advisory',
    message: `极端词过量：叙述里「${samples.join('/')}」等共 ${hits} 处；删九成，程度靠具体画面给（「三天没合眼」代替「非常大的压力」）。`,
    excerpt: compact(samples.join(' ')),
  }];
}

function findCrowdSyncTic(proseLines) {
  const findings = [];
  const softCounts = new Map();
  let softFirstLine = null;

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    const narrative = maskQuoted(trimmed);

    for (const phrase of CROWD_HARD_SYNC) {
      let idx = narrative.indexOf(phrase);
      while (idx !== -1) {
        findings.push({
          line: lineNo,
          column: idx + 1,
          type: 'crowd-sync-tic',
          severity: 'advisory',
          message: `群体同步词「${phrase}」：一群人被写成了一个反应。拆成 3 种以上立场（领头/跟风/尴尬/回避/私语/走神），或指明某一个人做了某个多余的动作。`,
          excerpt: compact(narrative.slice(Math.max(0, idx - 8), idx + phrase.length + 8)),
        });
        idx = narrative.indexOf(phrase, idx + phrase.length);
      }
    }

    for (const phrase of CROWD_SOFT_SYNC) {
      let idx = narrative.indexOf(phrase);
      while (idx !== -1) {
        softCounts.set(phrase, (softCounts.get(phrase) || 0) + 1);
        if (softFirstLine === null) softFirstLine = lineNo;
        idx = narrative.indexOf(phrase, idx + phrase.length);
      }
    }
  }

  const softTotal = [...softCounts.values()].reduce((sum, n) => sum + n, 0);
  if (softTotal >= CROWD_SOFT_MIN) {
    const top = [...softCounts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 4)
      .map(([phrase, count]) => `${phrase}×${count}`)
      .join(' ');
    findings.push({
      line: softFirstLine,
      column: 1,
      type: 'crowd-sync-tic',
      severity: 'advisory',
      message: `群体泛指词过密（${softTotal} 处：${top}）：群众始终是个代词，说明还没拆成具体的人。挑 3 处换成有名有姓、自己有事的角色。`,
      excerpt: compact(top),
    });
  }

  return findings;
}

function findFilterVerbTic(proseLines) {
  const findings = [];
  let total = 0;
  let narrativeChars = 0;
  let firstLine = null;

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    const narrative = maskQuoted(trimmed);
    narrativeChars += (narrative.match(/[\u4e00-\u9fff]/g) || []).length;

    const distinct = new Set();
    for (const verb of FILTER_VERBS) {
      let idx = narrative.indexOf(verb);
      while (idx !== -1) {
        distinct.add(verb);
        total += 1;
        if (firstLine === null) firstLine = lineNo;
        idx = narrative.indexOf(verb, idx + verb.length);
      }
    }

    // 只数「不同种」——同一动词连续三次（他想起…，想起…，想起…）是排比，是节奏，不是转述腔。
    if (distinct.size >= FILTER_VERB_PARAGRAPH_MAX) {
      findings.push({
        line: lineNo,
        column: 1,
        type: 'filter-verb-tic',
        severity: 'advisory',
        message: `一段里堆了 ${distinct.size} 种过滤器动词（${[...distinct].join('/')}）：读者被挡在「他看见／他感到」外面一臂远。删掉感知动词，把被感知的东西直接摆出来（见 references/narrative-distance.md）。`,
        excerpt: compact(narrative.slice(0, 40)),
      });
    }
  }

  if (narrativeChars >= 500) {
    const density = (total / narrativeChars) * 1000;
    if (density >= FILTER_VERB_DENSITY_MIN) {
      findings.push({
        line: firstLine || 1,
        column: 1,
        type: 'filter-verb-tic',
        severity: 'advisory',
        message: `过滤器动词密度 ${density.toFixed(1)}/千字（阈值 ${FILTER_VERB_DENSITY_MIN}，正常基线 0.7–1.2）：整章叙述站得太远。至少在情绪转折处把距离收到第 4 档，用人物自己的话去想。`,
        excerpt: compact(`全章 ${total} 处，叙述 ${narrativeChars} 字`),
      });
    }
  }

  return findings;
}

function findClicheDensityTic(proseLines) {
  let hits = 0;
  let narrativeChars = 0;
  let firstLine = null;
  const samples = [];

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    const narrative = stripQuoted(trimmed);
    narrativeChars += visibleLength(narrative);

    for (const pattern of CLICHE_PATTERNS) {
      pattern.lastIndex = 0;
      let match;
      while ((match = pattern.exec(narrative)) !== null) {
        hits += 1;
        if (firstLine === null) firstLine = lineNo;
        if (samples.length < 8 && !samples.includes(match[0])) samples.push(match[0]);
      }
    }
  }

  if (narrativeChars === 0 || hits < CLICHE_DENSITY_MIN_HITS) return [];
  const perKilo = (hits / narrativeChars) * 1000;
  if (perKilo < CLICHE_DENSITY_PER_KILO) return [];

  return [{
    line: firstLine,
    column: 1,
    type: 'cliche-density-tic',
    severity: 'advisory',
    message: `套词密度过高：高危 AI 套词 ${hits} 处（${perKilo.toFixed(1)}/千字）；不要同义词轮换，改成角色当下可见的动作、物件、对话和具体后果。`,
    excerpt: compact(samples.join(' ')),
  }];
}

// 比喻密度：统计引号外叙述中“像/好像/仿佛/如同”等比喻标记。
// 单个比喻不是问题；高密度成片时才提示，避免把文本改成另一种修辞模板。
function findMetaphorDensityTic(proseLines) {
  // 逐段计数：一段里 ≥3 个比喻标记才算成片（见上方常量注释）。
  const hits = [];
  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    const narrative = stripQuoted(trimmed);
    const marks = [];
    let match;

    METAPHOR_MARKER_PATTERN.lastIndex = 0;
    while ((match = METAPHOR_MARKER_PATTERN.exec(narrative)) !== null) marks.push(match[0]);

    METAPHOR_LIKE_PHRASE_PATTERN.lastIndex = 0;
    while ((match = METAPHOR_LIKE_PHRASE_PATTERN.exec(narrative)) !== null) {
      const prefix = narrative.slice(Math.max(0, match.index - 8), match.index);
      if (/好像|像是|像|仿佛|宛如|如同|犹如/.test(prefix)) continue;
      marks.push(match[0]);
    }

    if (marks.length <= METAPHOR_PER_PARAGRAPH_MAX) continue;
    hits.push({ line: lineNo, count: marks.length, marks, text: compact(narrative) });
  }

  return hits.slice(0, METAPHOR_MAX_FINDINGS).map((hit) => ({
    line: hit.line,
    column: 1,
    type: 'metaphor-density-tic',
    severity: 'advisory',
    message: `同一段里比喻标记 ${hit.count} 处（${hit.marks.join('/')}）：比喻成片堆叠才是模板腔，`
      + '一处比喻是正常修辞，别按全章密度删。保留最有叙事功能的那个，其余回到具体动作、物件、声音或后果，不要换成新比喻。',
    excerpt: hit.text.slice(0, 60),
  }));
}

// 解释链密度：统计引号外叙述中“知道/明白/这意味着/必须需要”等判断链。
// 全篇只报一条；修法不是补结构虚词，而是把判断落到动作、物件、对话和现场反馈。
function findReasoningChainTic(proseLines) {
  let hits = 0;
  let coreHits = 0;
  let narrativeChars = 0;
  let firstLine = null;
  const samples = [];
  const buckets = new Set();

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    const narrative = stripQuoted(trimmed);
    narrativeChars += visibleLength(narrative);

    for (const { pattern, key, core } of REASONING_CHAIN_PATTERNS) {
      pattern.lastIndex = 0;
      let match;
      while ((match = pattern.exec(narrative)) !== null) {
        hits += 1;
        if (core) coreHits += 1;
        buckets.add(key);
        if (firstLine === null) firstLine = lineNo;
        const sample = compact(match[0]);
        if (samples.length < 8 && !samples.includes(sample)) samples.push(sample);
      }
    }
  }

  if (narrativeChars === 0 || hits < REASONING_CHAIN_MIN_HITS) return [];
  if (coreHits < REASONING_CHAIN_CORE_MIN_HITS || buckets.size < REASONING_CHAIN_MIN_BUCKETS) return [];
  const perKilo = (hits / narrativeChars) * 1000;
  if (perKilo < REASONING_CHAIN_PER_KILO) return [];

  return [{
    line: firstLine,
    column: 1,
    type: 'reasoning-chain-tic',
    severity: 'advisory',
    message: `解释链密度过高：知道/明白/这意味着/必须/需要等判断链 ${hits} 处（${perKilo.toFixed(1)}/千字）；像逻辑报告时，把判断落到角色当下可见的动作、物件、对话和现场反馈。`,
    excerpt: compact(samples.join(' | ')),
  }];
}

// 系统/规则行如果连续像 API 文档或政府公文，读者容易闻到机器味。
// 修法不是删除规则，而是保留功能后把一部分硬词改成白话或具体后果。
function findNoticeFormalityTic(proseLines) {
  let hits = 0;
  let noticeChars = 0;
  let noticeLines = 0;
  let coreHits = 0;
  let firstLine = null;
  const samples = [];

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!/^【[^】]+】$/.test(trimmed)) continue;
    noticeLines += 1;
    noticeChars += visibleLength(trimmed);

    NOTICE_FORMAL_CORE_PATTERN.lastIndex = 0;
    while (NOTICE_FORMAL_CORE_PATTERN.exec(trimmed) !== null) coreHits += 1;

    for (const pattern of NOTICE_FORMAL_PATTERNS) {
      pattern.lastIndex = 0;
      let match;
      while ((match = pattern.exec(trimmed)) !== null) {
        hits += 1;
        if (firstLine === null) firstLine = lineNo;
        const sample = compact(match[0]);
        if (samples.length < 8 && !samples.includes(sample)) samples.push(sample);
      }
    }
  }

  if (noticeLines < NOTICE_FORMAL_MIN_LINES || noticeChars === 0 || hits < NOTICE_FORMAL_MIN_HITS || coreHits < NOTICE_FORMAL_CORE_MIN_HITS) return [];
  const perKilo = (hits / noticeChars) * 1000;
  if (perKilo < NOTICE_FORMAL_PER_KILO) return [];

  return [{
    line: firstLine,
    column: 1,
    type: 'system-notice-formality-tic',
    severity: 'advisory',
    message: `系统公告公文腔过密：方括号规则行中硬规则词 ${hits} 处（${perKilo.toFixed(1)}/千字）；保留为角色看见的屏幕/公告/规则载体，只在载体内部白话化部分硬词，或补角色当场看懂的具体后果，不改成叙述者解释。`,
    excerpt: compact(samples.join(' | ')),
  }];
}

// 长文本整体过于“精炼”：短段很多、自然连接偏少，读起来像处理过的梗概/分镜表。
// 修法是通读后补断裂处，不是为凑阈值全局加“的/了/就”。
function findOvercompressedProseTic(proseLines) {
  let narrativeChars = 0;
  let narrativeParas = 0;
  let shortParas = 0;
  let particles = 0;
  let firstLine = null;
  const samples = [];

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed) || /^【[^】]+】$/.test(trimmed)) continue;
    const narrative = stripQuoted(trimmed).trim();
    const len = visibleLength(narrative);
    if (len === 0) continue;

    if (firstLine === null) firstLine = lineNo;
    narrativeParas += 1;
    narrativeChars += len;
    if (len <= OVERCOMPRESSED_PROSE_SHORT_MAX_CHARS) {
      shortParas += 1;
      if (samples.length < 6) samples.push(narrative);
    }

    OVERCOMPRESSED_PROSE_PARTICLE_PATTERN.lastIndex = 0;
    while (OVERCOMPRESSED_PROSE_PARTICLE_PATTERN.exec(narrative) !== null) particles += 1;
  }

  if (narrativeChars < OVERCOMPRESSED_PROSE_MIN_CHARS || narrativeParas < OVERCOMPRESSED_PROSE_MIN_PARAS) return [];
  const shortRatio = shortParas / narrativeParas;
  if (shortRatio < OVERCOMPRESSED_PROSE_SHORT_RATIO) return [];
  const particlePerKilo = (particles / narrativeChars) * 1000;
  if (particlePerKilo >= OVERCOMPRESSED_PROSE_PARTICLE_PER_KILO) return [];

  return [{
    line: firstLine,
    column: 1,
    type: 'overcompressed-prose-tic',
    severity: 'advisory',
    message: `过度精炼短段：叙述段 ${narrativeParas} 个，其中 ${shortParas} 个≤${OVERCOMPRESSED_PROSE_SHORT_MAX_CHARS}字（${(shortRatio * 100).toFixed(0)}%），自然连接 ${particlePerKilo.toFixed(1)}/千字偏少；先通读判断，确有提纲感再补断裂处和必要结构虚词，有意短镜头可留，别机械注水。`,
    excerpt: compact(samples.join(' | ')),
  }];

}

// 低连接密度：长文本/中短窗口里，引号外叙述的功能词和白话连接同时偏低，且缺少中长承接句，
// 会呈现“提纲/电报体”分布。修法是恢复必要连接和句群，不是全局补词。
function findLowConnectiveDensityTic(proseLines) {
  let bodyChars = 0;
  let functionHits = 0;
  let plainHits = 0;
  let firstLine = null;
  const sentences = [];
  const samples = [];

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;

    // 只看引号外叙述。台词/弹幕/系统播报可以天然短促，混入统计会把体裁特征误当电报体。
    const narrative = stripQuoted(trimmed).trim();
    const narrativeLen = visibleLength(narrative);
    if (narrativeLen === 0) continue;

    if (firstLine === null) firstLine = lineNo;
    bodyChars += narrativeLen;
    functionHits += countTerms(narrative, LOW_CONNECTIVE_FUNCTION_TERMS);
    plainHits += countTerms(narrative, LOW_CONNECTIVE_PLAIN_TERMS);

    for (const sentence of splitSentences(narrative)) {
      const len = visibleLength(sentence);
      if (len === 0) continue;
      sentences.push(len);
      if (len <= 12 && samples.length < 6) samples.push(sentence);
    }
  }

  if (bodyChars < LOW_CONNECTIVE_MIN_CHARS || sentences.length === 0) return [];
  const functionPerKilo = (functionHits / bodyChars) * 1000;
  if (functionPerKilo >= LOW_CONNECTIVE_FUNCTION_PER_KILO) return [];
  const plainPerKilo = (plainHits / bodyChars) * 1000;
  if (plainPerKilo >= LOW_CONNECTIVE_PLAIN_PER_KILO) return [];
  const longSentenceRatio = sentences.filter((len) => len >= LOW_CONNECTIVE_LONG_SENTENCE_CHARS).length / sentences.length;
  if (longSentenceRatio >= LOW_CONNECTIVE_LONG_SENTENCE_RATIO) return [];

  return [{
    line: firstLine,
    column: 1,
    type: 'low-connective-density-tic',
    severity: 'advisory',
    message: `低连接密度：引号外叙述功能词 ${functionPerKilo.toFixed(1)}/千字、白话连接 ${plainPerKilo.toFixed(1)}/千字，且≥${LOW_CONNECTIVE_LONG_SENTENCE_CHARS}字承接句仅 ${(longSentenceRatio * 100).toFixed(0)}%；容易像提纲/电报体。通读后补必要连接和中长句群，别机械注水。`,
    excerpt: compact(samples.join(' | ')),
  }];
}

// 抽象总结复读：统计引号外叙述中的高抽象收束模板。全篇只报一条，提醒回到角色
// 当下可见的文件、动作、对话或物理后果；不要用命运大词替读者总结。
function findAbstractSummaryTic(proseLines) {
  let hits = 0;
  let narrativeChars = 0;
  let firstLine = null;
  const samples = [];

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    const narrative = stripQuoted(trimmed);
    narrativeChars += visibleLength(narrative);

    for (const pattern of ABSTRACT_SUMMARY_PATTERNS) {
      pattern.lastIndex = 0;
      let match;
      while ((match = pattern.exec(narrative)) !== null) {
        hits += 1;
        if (firstLine === null) firstLine = lineNo;
        const sample = compact(match[0]);
        if (samples.length < 6 && !samples.includes(sample)) samples.push(sample);
      }
    }
  }

  if (narrativeChars === 0 || hits < ABSTRACT_SUMMARY_MIN_HITS) return [];
  const perKilo = (hits / narrativeChars) * 1000;
  if (perKilo < ABSTRACT_SUMMARY_PER_KILO) return [];

  return [{
    line: firstLine,
    column: 1,
    type: 'abstract-summary-tic',
    severity: 'advisory',
    message: `抽象总结复读：命运/棋局/这一刻终于明白/才刚刚开始等作者总结 ${hits} 处（${perKilo.toFixed(1)}/千字）；回到角色当下可见的文件、动作、对话或物理后果，别替读者盖章。`,
    excerpt: compact(samples.join(' | ')),
  }];
}

// 短对话堆砌：连续 ≥SHORT_DIALOGUE_RUN_MIN 段「纯台词短段」＝一句一段、无动作神态穿插。
// 纯台词段 = 去掉引号内容后，引号外没有任何叙述字符；短台词 = 引号内可见字符 ≤ 22。
function findShortDialogueRun(proseLines) {
  const findings = [];
  let runLen = 0;
  let runStartLine = null;
  const runSamples = [];

  const flush = () => {
    if (runLen >= SHORT_DIALOGUE_RUN_MIN) {
      findings.push({
        line: runStartLine,
        column: 1,
        type: 'short-dialogue-run',
        severity: 'advisory',
        message: `短对话堆砌：连续 ${runLen} 段纯台词短段、中间没有动作/神态/场景穿插；`
          + '先判定体裁（论坛留言、短信弹幕、围观喊话、念信读纸属合理写法，可留）；'
          + '若是人物面对面来回，就把其中几段合并、或插一个动作/神态/环境细节，别让两人对着念词。',
        excerpt: compact(runSamples.join(' | ')),
      });
    }
    runLen = 0;
    runStartLine = null;
    runSamples.length = 0;
  };

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    // 空行是段落间呼吸，不打断「连续台词」的计数（台词本来就是一句一段）。
    if (!trimmed) continue;
    if (isDivider(trimmed) || isStructural(trimmed) || /^【[^】]+】$/.test(trimmed)) {
      flush();
      continue;
    }
    const outside = stripQuoted(trimmed).trim();
    const first = trimmed[0];
    const pureDialogue = outside.length === 0
      && DIALOGUE_OPEN_CHARS.has(first)
      && DIALOGUE_CLOSE_CHARS.has(trimmed[trimmed.length - 1]);
    if (!pureDialogue) {
      flush();
      continue;
    }
    const inner = trimmed.slice(1, -1);
    if (visibleLength(inner) > SHORT_DIALOGUE_MAX_CHARS) {
      flush();
      continue;
    }
    if (runLen === 0) runStartLine = lineNo;
    runLen += 1;
    if (runSamples.length < 6) runSamples.push(trimmed);
  }
  flush();
  return findings;
}

function findPeriodStutter(proseLines) {
  const findings = [];
  let runLen = 0;
  let runStartLine = null;
  let runSample = [];

  const flush = () => {
    if (runLen >= STUTTER_MIN_RUN) {
      findings.push({
        line: runStartLine,
        column: 1,
        type: 'period-stutter',
        severity: 'advisory',
        message: `碎句号：连续 ${runLen} 个短句无呼吸；按目标句长把碎句合并成中长句、补回画面与连接（见本 skill 句长/疏密节奏规则）。`,
        excerpt: compact(runSample.join(' ')),
      });
    }
    runLen = 0;
    runStartLine = null;
    runSample = [];
  };

  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed) continue; // 空行是一句一段排版，不打断叙述连贯
    if (isDivider(trimmed) || isStructural(trimmed)) {
      flush(); // 分隔线/markdown 结构行：重置碎句计数
      continue;
    }
    const narrative = stripQuoted(trimmed);
    if (visibleLength(narrative) === 0) {
      flush(); // 纯对话/弹幕/系统播报：成片短句是正常形态，重置碎句计数
      continue;
    }
    // 只数引号外叙述句：混合行（叙述+引号内物件/短台词）的引号外片段仍参与碎句计数。
    for (const sentence of splitSentences(narrative)) {
      if (visibleLength(sentence) <= STUTTER_MAX_SENTENCE) {
        if (runLen === 0) runStartLine = lineNo;
        runLen += 1;
        if (runSample.length < 6) runSample.push(sentence);
      } else {
        flush();
      }
    }
  }
  flush();
  return findings;
}

function isDivider(trimmed) {
  return /^-{3,}$/.test(trimmed) || /^[*_]{3,}$/.test(trimmed);
}

// markdown 结构行（标题/列表/引用/表格）不是叙述正文，长段落/碎句号/破折号检测都跳过。
function isStructural(trimmed) {
  return /^(#{1,6}\s|>\s?|[-*+]\s|\d+[.)]\s|\|)/.test(trimmed)
    || /^第[零一二三四五六七八九十百千万\d]+章(?:\s|_|$)/.test(trimmed);
}

// 去掉成对引号内的片段（台词/系统播报），只留引号外叙述。碎句号判定用：纯对话/弹幕成片短句
// 是体裁正常形态（豁免），但「叙述 + 引号内物件/短台词」混合行的引号外叙述仍要参与短句计数。
function stripQuoted(text) {
  let out = text;
  for (const src of QUOTE_SOURCES) out = out.replace(new RegExp(src, 'g'), '');
  return out;
}

// 把成对引号片段（含引号）替换为等长问号占位：既豁免引号内台词/播报，又保住原文
// 偏移量，供逐处 blocking 规则定位与截取原文摘录（stripQuoted 会移位，不适合定位）。
// 占位字符用「？」而不是「。」：占位既要截断各规则的 [^。！？!?…] 否定类（？与句号在每条
// 规则的否定类里等效），又不能落在任何规则的接受位。句号占位会替 trailer-summary 的句末
// [。！] 伪造出终止符，让「这一战注定是「血屠」的开端，…」这类引号里放代号/绰号的叙述行
// 被误报，且报出的『这一战注定是。』在原文里 grep 不到。占位长度不变，故偏移与摘录窗口不漂移。
function maskQuoted(text) {
  let out = text;
  for (const src of QUOTE_SOURCES) {
    out = out.replace(new RegExp(src, 'g'), (m) => '？'.repeat(m.length));
  }
  return out;
}

// 返回引号内片段（含引号本身）的 [start, end) 区间，供 not-is 对比句豁免台词用。
function quotedRanges(text) {
  const ranges = [];
  for (const src of QUOTE_SOURCES) {
    const re = new RegExp(src, 'g');
    let match;
    while ((match = re.exec(text)) !== null) ranges.push([match.index, match.index + match[0].length]);
  }
  return ranges;
}

function insideRanges(pos, ranges) {
  return ranges.some(([start, end]) => pos >= start && pos < end);
}

function splitSentences(trimmed) {
  return trimmed
    .split(/[。！？!?]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function sentenceAround(text, index) {
  let start = index;
  while (start > 0 && !STOP_CHARS.has(text[start - 1])) start -= 1;
  let end = index;
  while (end < text.length && !STOP_CHARS.has(text[end])) end += 1;
  return compact(text.slice(start, end).trim());
}

function visibleLength(sentence) {
  const matched = sentence.match(/[一-鿿Ａ-ｚA-Za-z0-9]/g);
  return matched ? matched.length : 0;
}

function countTerms(text, terms) {
  let count = 0;
  for (const term of terms) {
    let index = text.indexOf(term);
    while (index !== -1) {
      count += 1;
      index = text.indexOf(term, index + term.length);
    }
  }
  return count;
}

// 把行内 HTML 注释（含 `<!--`、`-->` 本身）换成等长空格；openBefore 表示上一行留下未闭合注释。
// unclosedOpenings 里的 `<!--` 到文末都没闭合：只抹掉这四个字符，后面照常当正文。
// openedAt 是本行最后一个开始注释的 `<!--` 位置（"行号:列号"），没有则为 null。
function maskHtmlComments(line, openBefore, lineIndex = 0, unclosedOpenings = new Set()) {
  let out = '';
  let open = openBefore;
  let cursor = 0;
  let openedAt = null;
  while (cursor < line.length) {
    if (open) {
      const close = line.indexOf('-->', cursor);
      const end = close === -1 ? line.length : close + 3;
      out += ' '.repeat(end - cursor);
      cursor = end;
      if (close !== -1) open = false;
      continue;
    }
    const start = line.indexOf('<!--', cursor);
    if (start === -1) {
      out += line.slice(cursor);
      break;
    }
    out += line.slice(cursor, start) + '    ';
    cursor = start + 4;
    const key = `${lineIndex}:${start}`;
    if (unclosedOpenings.has(key)) continue;
    open = true;
    openedAt = key;
  }
  return { text: out, open, openedAt };
}

function parseFenceMarker(trimmedLine) {
  const match = /^(?:`{3,}|~{3,})/.exec(trimmedLine);
  if (!match) return null;
  return { char: match[0][0], length: match[0].length };
}

function hasYamlFrontMatter(lines) {
  if (!lines[0] || lines[0].trim() !== '---') return false;
  let sawYamlField = false;
  for (let i = 1; i < Math.min(lines.length, 40); i += 1) {
    const trimmed = lines[i].trim();
    if (trimmed === '---') return sawYamlField;
    if (/^[A-Za-z0-9_-]+:\s*/.test(trimmed)) sawYamlField = true;
  }
  return false;
}

function scanBlock(block) {
  const text = block.map((entry) => entry.text).join('\n');
  const lineStarts = [];
  let cursor = 0;

  for (const entry of block) {
    lineStarts.push({ offset: cursor, lineNo: entry.lineNo });
    cursor += entry.text.length + 1;
  }

  return findNotIsComparisons(text, (offset) => positionForOffset(lineStarts, offset));
}

function positionForOffset(lineStarts, offset) {
  let low = 0;
  let high = lineStarts.length - 1;

  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    const current = lineStarts[mid];
    const next = lineStarts[mid + 1];

    if (offset < current.offset) {
      high = mid - 1;
    } else if (next && offset >= next.offset) {
      low = mid + 1;
    } else {
      return {
        line: current.lineNo,
        column: offset - current.offset + 1,
      };
    }
  }

  return { line: lineStarts[0].lineNo, column: 1 };
}

function findNotIsComparisons(text, getPosition) {
  const findings = [];
  const quoted = quotedRanges(text);
  let offset = 0;

  while (offset < text.length) {
    const start = text.indexOf('不是', offset);
    if (start === -1) break;

    // 引号内是台词/系统播报：口语里「不是A，是B」是自然辩解/反问，不算叙述层 AI 对比句式
    // （与碎句号一致豁免引号内容）。
    if (insideRanges(start, quoted)) {
      offset = start + 2;
      continue;
    }

    // Avoid the common yes/no question fragment “是不是”.
    if (start > 0 && text[start - 1] === '是') {
      offset = start + 2;
      continue;
    }

    const candidate = text.slice(start);
    const markerEnd = findPositiveFlipEnd(candidate);

    if (markerEnd === -1) {
      offset = start + 2;
      continue;
    }

    const raw = trimTrailingNoise(extractFinding(candidate, markerEnd));
    if (raw.length >= 4) {
      const position = getPosition(start);
      findings.push({
        line: position.line,
        column: position.column,
        type: 'not-is-comparison',
        severity: 'blocking',
        message: '高频 AI 对比句式；删掉否定铺垫，直接写后项，或改成动作/细节呈现。',
        excerpt: compact(raw),
      });
    }

    offset = start + Math.max(raw.length, 2);
  }

  return findings;
}

function findPositiveFlipEnd(candidate) {
  let index = 2; // after “不是”
  let scanned = 0;
  let crossedSeparator = false;

  while (index < candidate.length && scanned <= MAX_NEGATIVE_SPAN) {
    const char = candidate[index];

    if (startsWithAt(candidate, index, '而是')) return index + 2;

    if (SOFT_SEPARATORS.has(char)) {
      const next = skipGap(candidate, index + 1);
      if (startsWithAt(candidate, next, '而是')) return next + 2;
      if (candidate[next] === '是' && !TAG_PARTICLES.has(candidate[next + 1]) && !isAffirmationTagAt(candidate, next)) return next + 1;
      crossedSeparator = true;
    }

    if (HARD_SEPARATORS.has(char)) {
      const next = skipGap(candidate, index + 1);
      if (candidate[next] === '是' && !TAG_PARTICLES.has(candidate[next + 1]) && !isAffirmationTagAt(candidate, next)) return next + 1;
      if (char !== '.') break;
      crossedSeparator = true;
    }

    if (STOP_CHARS.has(char)) break;

    // Catch compact forms such as “不是A是B”, but only within the first clause —
    // before any separator. After a separator the trailing “是” of a conjunction
    // (只是/可是/但是/还是/于是/倒是/总是…) is part of that word, not a positive
    // copula (issue #166 false-positive class). Post-separator flips are still
    // caught when separator-adjacent (“，是”/“，而是”) by the separator branches
    // above; subject-present flips like “，他是”/“，那是” are intentionally NOT
    // caught here — there is no separator-local way to tell them from a
    // conjunction without a word list, and on a hard rescan-to-0 gate a false
    // positive (forcing a rewrite of good prose) costs more than missing this
    // rarer form. The “是” in the either-or idiom “不是A就是B / 也是B” is part of
    // the 就是/也是 conjunction, not a copula, so 就/也 are excluded too. Also never
    // treat the “是” inside a second negative fragment (“不是A，也不是B”) as the flip.
    if (char === '是' && !COMPACT_EITHER_OR_PREV.has(candidate[index - 1]) && !crossedSeparator) {
      return index + 1;
    }

    index += 1;
    scanned += 1;
  }

  return -1;
}

function extractFinding(candidate, markerEnd) {
  let end = markerEnd;
  const limit = Math.min(candidate.length, markerEnd + MAX_POSITIVE_SPAN);

  while (end < limit) {
    if (STOP_CHARS.has(candidate[end])) break;
    end += 1;
  }

  return candidate.slice(0, end);
}

function startsWithAt(text, index, needle) {
  return text.slice(index, index + needle.length) === needle;
}

function isAffirmationTagAt(text, index) {
  if (text[index] !== '是') return false;
  const particle = text[index + 1];
  if (!AFFIRMATION_TAG_PARTICLES.has(particle)) return false;
  const boundary = text[index + 2] || '';
  return AFFIRMATION_TAG_BOUNDARY.has(boundary);
}

// 跳过行内空白与换行（含空行/段落间距），停在下一个实义字符。原实现只吞一个换行，
// 会漏掉跨空行的「不是A。（空行）是B」这类分段揭示句。
function skipGap(text, index) {
  while (index < text.length && (isInlineSpace(text[index]) || text[index] === '\n')) index += 1;
  return index;
}

function isInlineSpace(char) {
  return char === ' ' || char === '\t' || char === '\r';
}

function trimTrailingNoise(text) {
  return text.replace(/[\s|）)】\]]+$/u, '');
}

function compact(text) {
  const normalized = text.replace(/\s+/g, ' ').trim();
  return normalized.length > 80 ? `${normalized.slice(0, 77)}...` : normalized;
}

// 弱动词分解式（中文原则库「欧化病②」）：全文只报一条，阈值 ≥2 处。
function findWeakVerbTic(proseLines) {
  let hits = 0;
  let firstLine = null;
  const samples = [];
  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    // 对话里角色用公文体是 characterization，只扫叙述
    const narrative = stripQuoted(trimmed);
    WEAK_VERB_PATTERN.lastIndex = 0;
    let match;
    while ((match = WEAK_VERB_PATTERN.exec(narrative)) !== null) {
      hits += 1;
      if (firstLine === null) firstLine = lineNo;
      if (samples.length < 4) samples.push(match[0]);
    }
  }
  if (hits < WEAK_VERB_MIN_HITS) return [];
  return [{
    line: firstLine,
    column: 1,
    type: 'weak-verb-tic',
    severity: 'advisory',
    message: `弱动词分解式 ${hits} 处（欧化病，references/chinese-prose-craft.md 第二节）：「进行/作出/加以+抽象名词」还原为硬动词；对话内角色腔不算。`,
    excerpt: compact(samples.join(' | ')),
  }];
}

// 情绪贴标签（reader-reward 失败模式④）：分布级提示，全文只报一条，阈值 ≥4 处。
function findEmotionLabelTic(proseLines) {
  let hits = 0;
  let firstLine = null;
  const samples = [];
  for (const { text, lineNo } of proseLines) {
    const trimmed = text.trim();
    if (!trimmed || isDivider(trimmed) || isStructural(trimmed)) continue;
    const narrative = stripQuoted(trimmed);
    EMOTION_LABEL_PATTERN.lastIndex = 0;
    let match;
    while ((match = EMOTION_LABEL_PATTERN.exec(narrative)) !== null) {
      hits += 1;
      if (firstLine === null) firstLine = lineNo;
      if (samples.length < 4) samples.push(match[0]);
    }
  }
  if (hits < EMOTION_LABEL_MIN_HITS) return [];
  return [{
    line: firstLine,
    column: 1,
    type: 'emotion-label-tic',
    severity: 'advisory',
    message: `情绪贴标签 ${hits} 处（references/reader-reward.md 模式④）：告知情绪而非展示；逐处做删除测试，换成该角色特有的行为细节（注意避开攥拳/咬唇等通用手势）。`,
    excerpt: compact(samples.join(' | ')),
  }];
}
