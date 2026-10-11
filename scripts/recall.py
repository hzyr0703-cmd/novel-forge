#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""recall.py — 派生检索层（可随时重建，不持有真源状态）

从 novel-forge 的追踪台账 + 正文目录派生一个检索索引，用来在写章前
捞回"这一章该想起什么"。核心约束：

1. **不写任何状态文件**。索引缓存在 `<project>/.recall-cache/`，随时可删可重建。
2. **零 API key 可跑**。默认 BM25；配置了 embedding 端点则升级为混合检索。
3. **纯派生**。删除 .recall-cache/ 后重跑 `index` 结果完全一致。

子命令：
    index   重建索引（默认 .recall-cache/recall_index.json）
    query   检索，输出排序后的片段
    status  查看索引规模与检索模式

用法：
    python recall.py --project <书稿根> index
    python recall.py --project <书稿根> query "老王 信" --top 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"
CACHE_DIRNAME = ".recall-cache"
INDEX_FILENAME = "recall_index.json"

# 中文按字bigram 切，英文数字按词切，兼顾中英混排
_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[a-zA-Z0-9]+")
_STOP = {
    "的", "了", "是", "在", "和", "与", "就", "都", "而", "及", "或",
    "一个", "没有", "我们", "你们", "他们", "这个", "那个", "什么",
    "可以", "因为", "所以", "但是", "如果", "已经", "还有", "一下",
    "the", "a", "an", "of", "to", "and", "is", "in", "it",
}


def _label(v: Any, fallback: str = "未标注") -> str:
    """把 None / 空值统一成可读标签，不输出 Python repr。"""
    if v is None:
        return fallback
    s = str(v).strip()
    return s if s and s.lower() != "none" else fallback


# 内部实现字段：检索算法用的，呈现给作者只会变噪声
_META_SKIP = {
    "chref", "chapter",
    # 这两个的语义已经写在伏笔正文里（"埋于第N章；计划第M章回收"），
    # meta 再显示一遍就是重复三遍
    "planted_chapter", "planned_resolution_chapter", "updated_chapter",
    "first_recorded_chapter", "characters",
}
_META_CN = {
    "due": "应兑现", "foreshadow_id": "伏笔编号", "planted_ch": "埋于",
    "target_ch": "计划兑现", "role": "定位", "importance": "重要度",
    "status": "状态", "reveal_status": "揭示状态", "story_time": "故事时间",
    "objective_fact": "客观事实", "reader_knowledge": "读者已知",
}


def _human_meta(meta: dict[str, Any] | None) -> str:
    """把内部 meta 压成一行人话；没有值得显示的就返回空串。

    早前的CLI 直接print(meta)，会把 Python dict 原样倒出来
    （{'chapter': 1, 'chref': 'ch1'}），既难读又暴露实现细节。
    """
    if not meta:
        return ""
    parts = []
    for k, v in meta.items():
        if k in _META_SKIP or v in (None, "", []):
            continue
        cn = _META_CN.get(k)
        if cn:
            parts.append(f"{cn}={_label(v)}")
        elif isinstance(v, str) and len(v) <= 20:
            # 没登记的短字符串也放行，但跳过明显的实现字段名
            if not any(s in k.lower() for s in ("ref", "id", "vec", "emb")):
                parts.append(v)
    return " · ".join(parts)


def _tokens(text: str) -> list[str]:
    """中英混排分词：CJK 取单字 + bigram，拉丁取小写词。"""
    text = (text or "").lower()
    out: list[str] = []
    for w in _WORD.findall(text):
        out.append(w)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        chars = list(run)
        out.extend(chars)
        out.extend(chars[i] + chars[i + 1] for i in range(len(chars) - 1))
    return [t for t in out if t not in _STOP and len(t) > 0]


# 章节号是精确定位符，不是普通词。
# 长篇里"亲子鉴定""顾长风"满地都是，不加这一维度会把600 章里的任意一章都算成同分，
# 而"第21章"这种精确引用必须能一击命中。
_CH_NOUN = r"(?:第\s*\d+\s*章|ch\.?\s*\d+|chapter\s*\d+|\d+\s*章)"
_CH_NUM = re.compile(r"\d+")
_CH_REF = re.compile(_CH_NOUN, re.IGNORECASE)


def _chapter_refs(text: str) -> tuple[set[str], set[str]]:
    """抽出显式章节引用，返回 (规范形如 'ch21', 原数字串如 {'21'})。"""
    norm: set[str] = set()
    nums: set[str] = set()
    for m in _CH_REF.finditer(text or ""):
        for d in _CH_NUM.findall(m.group(0)):
            norm.add(f"ch{d}")
            nums.add(d)
    return norm, nums


def _l2(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v)) or 1.0


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (_l2(a) * _l2(b))


# ---------------------------------------------------------------- 数据装载


def _load_state(project: Path) -> dict[str, Any]:
    """定位追踪台账。兼容 _tracking-state.json 与 tracking.json 两种命名。"""
    for name in ("_tracking-state.json", "tracking.json"):
        for cand in (project / name, project / "追踪" / name):
            if cand.is_file():
                try:
                    return json.loads(cand.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise SystemExit(f"[recall] 台账解析失败: {cand} ({exc})")
    return {}


def _chapter_files(project: Path) -> list[Path]:
    """扫描正文目录。支持 第1章.md / 第0001章-标题.md / 001_标题.md 等常见命名。"""
    out: list[Path] = []
    for root in ("正文", "chapters", "text"):
        d = project / root
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.md")):
            m = re.search(r"(\d+)", p.stem)
            if m:
                out.append((int(m.group(1)), p))  # type: ignore[arg-type]
    out.sort(key=lambda x: x[0])  # type: ignore[index,arg-type]
    return [p for _, p in out]  # type: ignore[misc]


def _rows(v: Any) -> list[dict[str, Any]]:
    """把台账里的集合字段统一成 list。

    真实台账两种形态都存在：
      · list  —— 手写 JSON 常见
      · dict  —— tracking_commit.py 提交后是 {id: row}（燃识 就是这种）
    早先只当 list 处理，dict 会被 for...in 遍历到 **key 字符串**，
    于是每个字段都取不到，整类条目静默消失（13 条伏笔全丢且不报错）。
    """
    if isinstance(v, dict):
        out = []
        for k, row in v.items():
            if isinstance(row, dict):
                out.append({"id": row.get("id", k), **row} if "id" not in row
                           else row)
        return out
    if isinstance(v, (list, tuple)):
        return [r for r in v if isinstance(r, dict)]
    return []


def _pick(state: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """按优先级取第一个存在且非空的键。

    台账字段名历史上出现过几种写法（foreshadow / foreshadowing、
    constraints 放顶层还是 context 下）。早先只认一种，
    另一种会**静默漏检**——不报错，只是永远检索不到，排查起来极难。
    """
    for k in keys:
        v = state.get(k)
        if v:
            return v
    return default if default is not None else []


def _ctx_of(state: dict[str, Any]) -> dict[str, Any]:
    """context 块。允许约束类字段直接写在顶层（两种写法都见过）。"""
    ctx = state.get("context") or {}
    return ctx if isinstance(ctx, dict) else {}


_ROLE_CN = {
    "identity": "身份", "desc": "简介", "location": "位置", "goal": "目标",
    "state": "状态", "role": "定位", "abilities_resources": "能力与资源",
    "relationships": "关系", "knowledge": "已知信息", "open_threads": "未了结",
}


def _as_text(v: Any) -> str:
    """把约束/风险这类条目拍成一句人话。

    早先直接 str(c)，台账若写成 {"id":..,"text":..} 就会在检索结果里
    倒出整个 Python dict。
    """
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        for k in ("text", "desc", "content", "summary", "note", "rule"):
            if v.get(k):
                return str(v[k]).strip()
        # 没有正文字段时，把标量字段拼起来，至少比dict 可读
        return "；".join(f"{k}={v[k]}" for k in v
                         if isinstance(v[k], (str, int, float)) and k != "id")
    if isinstance(v, (list, tuple)):
        return "；".join(_as_text(x) for x in v)
    return str(v).strip()


def _chunks_from_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    """把台账条目摊平成可检索片段。每条自带 kind 与 ref，便于回溯。"""
    chunks: list[dict[str, Any]] = []

    def add(kind: str, ref: str, title: str, body: str, extra: dict | None = None) -> None:
        body = (body or "").strip()
        if not body:
            return
        chunks.append(
            {
                "id": f"{kind}:{ref}",
                "kind": kind,
                "ref": ref,
                "title": title,
                "text": body,
                "meta": extra or {},
            }
        )

    ctx = _ctx_of(state)
    pos = ctx.get("position", {}) or {}
    if pos:
        add(
            "position",
            "current",
            "当前进度",
            f"卷：{pos.get('volume','?')}；故事时间：{pos.get('story_time','?')}；场景：{pos.get('scene','?')}",
        )
    # 约束类字段：context 下与顶层两种写法都接受
    for c in _pick(ctx, "long_term_constraints") or _pick(state, "constraints", "long_term_constraints"):
        add("constraint", "constraint", "长线约束", _as_text(c))
    for c in _pick(ctx, "continuity_risks") or _pick(state, "risks", "continuity_risks"):
        add("risk", "risk", "连贯性风险", _as_text(c))
    for c in _pick(ctx, "next_chapter_commitments") or _pick(state, "commitments", "next_chapter_commitments"):
        add("commitment", "commitment", "下章必须兑现", _as_text(c))
    for rc in _pick(ctx, "recent_chapters") or _pick(state, "recent", "recent_chapters"):
        add(
            "recent",
            _label(rc.get("chapter")),
            f"第{rc.get('chapter','?')}章",
            str(rc.get("summary", "")),
        )

    # 角色：dict 形态（character_snapshots）与 list 形态（characters）都支持
    snaps = state.get("character_snapshots") or {}
    if isinstance(snaps, dict) and snaps:
        for name, snap in snaps.items():
            if not isinstance(snap, dict):
                add("character", str(name), f"角色·{name}", str(snap))
                continue
            parts = []
            for key in ("identity", "location", "goal", "state"):
                if snap.get(key):
                    parts.append(f"{key}: {snap[key]}")
            for key in ("abilities_resources", "relationships", "knowledge", "open_threads"):
                vals = snap.get(key) or []
                if isinstance(vals, (list, tuple)):
                    vals = "；".join(str(v) for v in vals)
                if vals:
                    parts.append(f"{key}: {vals}")
            add("character", str(name), f"角色·{name}", " | ".join(parts))
    else:
        for c in _pick(state, "characters", "character_snapshots"):
            if not isinstance(c, dict):
                add("character", "character", "角色", _as_text(c))
                continue
            name = c.get("name") or c.get("id")
            parts = []
            for k in ("identity", "desc", "location", "goal", "state", "role",
                      "abilities_resources", "relationships", "knowledge",
                      "open_threads"):
                if not c.get(k):
                    continue
                parts.append(f"{_ROLE_CN.get(k, k)}：{_as_text(c[k])}")
            add("character", _label(name), f"角色·{_label(name)}", " | ".join(parts),
                {"role": c.get("role")})

    for f in _rows(_pick(state, "foreshadow", "foreshadowing")):
        if not isinstance(f, dict):
            continue
        # 章节字段两种写法：planted_chapter / planted_ch
        pc = f.get("planted_chapter", f.get("planted_ch"))
        rc_ = f.get("planned_resolution_chapter", f.get("target_ch",
                 f.get("due_chapter")))
        summ = f.get("summary") or f.get("desc") or _label(f.get("content"), "")
        add(
            "foreshadow",
            _label(f.get("id")),
            f"伏笔 {_label(f.get('id'))}",
            f"{summ}（状态：{_label(f.get('status'), '未标注')}；"
            f"埋于第{_label(pc, '?')}章；"
            f"计划第{_label(rc_, '?')}章回收；"
            f"重要度：{_label(f.get('importance'), '?')}）",
            {"planted_chapter": pc,
             "planned_resolution_chapter": rc_,
             "importance": f.get("importance"),
             "status": f.get("status"),
             "chref": f"ch{pc}" if isinstance(pc, int) else None},
        )

    for e in _rows(_pick(state, "timeline_events", "timeline")):
        rk = _label(e.get("reader_knowledge"), "")
        # 这一列常直接写成"读者已知xxx"（不带冒号）。若再套一层"读者已知："前缀，
        # 输出就成了"读者已知：读者已知xxx"。已自带前缀就原样用。
        rk_field = rk if rk.startswith("读者已知") else f"读者已知：{rk}"
        add(
            "timeline",
            _label(e.get("id")),
            f"真相 {_label(e.get('id'))}",
            f"故事时间：{_label(e.get('story_time'), '?')}；"
            f"客观事实：{_label(e.get('objective_fact'), '')}；"
            + (f"{rk_field}；" if rk else "")
            + f"揭示状态：{_label(e.get('reveal_status'), '?')}",
            {"reveal_status": e.get("reveal_status")},
        )
    return chunks


def _chunks_from_chapters(files: list[Path], max_chars: int = 1800) -> list[dict[str, Any]]:
    """按场景切段（空行 + 中文标题启发式），长章切块。"""
    chunks: list[dict[str, Any]] = []
    for path in files:
        m = re.search(r"(\d+)", path.stem)
        ch = int(m.group(1)) if m else 0
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        body = re.sub(r"^#.*$", "", body, flags=re.M).strip()
        paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        buf, idx = [], 0
        for para in paras:
            buf.append(para)
            if sum(len(x) for x in buf) >= max_chars:
                idx += 1
                chunks.append(
                    {
                        "id": f"chapter:{ch}:{idx}",
                        "kind": "chapter",
                        "ref": f"{ch}#{idx}",
                        "title": f"第{ch}章 · 第{idx}段",
                        "text": "\n\n".join(buf),
                        "meta": {"chapter": ch, "chref": f"ch{ch}"},
                    }
                )
                buf = []
        if buf:
            idx += 1
            chunks.append(
                {
                    "id": f"chapter:{ch}:{idx}",
                    "kind": "chapter",
                    "ref": f"{ch}#{idx}",
                    "title": f"第{ch}章 · 第{idx}段",
                    "text": "\n\n".join(buf),
                    "meta": {"chapter": ch, "chref": f"ch{ch}"},
                }
            )
    return chunks


# ---------------------------------------------------------------- 可选向量


def _read_env(project: Path | None = None) -> dict[str, str]:
    """合并环境变量与 .env。

    查找顺序（后者覆盖前者）：
      1. 进程环境变量
      2. 书稿根的 .env        ← 优先，因为 --project 才是用户真正操作的那个目录
      3. 当前工作目录的 .env   ← 兜底，方便在书稿根直接跑

    早期版本只读当前工作目录的 .env，导致"在别处运行 --project 指向书稿根"时
    配置静默失效、永远降级到 BM25，且没有任何提示。
    """
    env = dict(os.environ)
    seen: set[Path] = set()
    candidates = []
    if project is not None:
        candidates.append(project / ".env")
    candidates.append(Path(".env"))
    for p in candidates:
        try:
            rp = p.resolve()
        except OSError:
            continue
        if rp in seen or not rp.is_file():
            continue
        seen.add(rp)
        for line in rp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            # .env 优先于进程环境（书稿根的配置更贴近当前项目）
            if v:
                env[k] = v
    return env


def _embed_client(project: Path | None = None) -> dict[str, Any] | None:
    """读取 embedding 配置。缺失返回 None（降级 BM25）。"""
    env = _read_env(project)

    base = env.get("EMBED_BASE_URL", "").strip()
    key = env.get("EMBED_API_KEY", "").strip()
    model = env.get("EMBED_MODEL", "").strip()
    if not (base and key and model):
        return None

    try:
        import urllib.request
    except ImportError:
        return None

    return {
        "base_url": base.rstrip("/"),
        "api_key": key,
        "model": model,
        "url": urllib.request,
        # 智谱 embedding-3 支持 dimensions 自定义（256/512/1024/2048）。
        # 长篇检索 1024 维是精度与体积的最佳平衡；不填则用服务端默认。
        "dimensions": env.get("EMBED_DIM", "").strip(),
        # 单条输入 token 上限。智谱 embedding-3 单条最多 3072 token，
        # 中文约等于 2000 字，留余量后默认截断 1800 字符。
        "max_chars": int(env.get("EMBED_MAX_CHARS", "1800") or 1800),
    }


def _rerank_client(project: Path | None = None) -> dict[str, Any] | None:
    """读取 rerank 配置。缺失返回 None（跳过精排）。"""
    env = _read_env(project)
    base = env.get("RERANK_BASE_URL", "").strip()
    key = env.get("RERANK_API_KEY", "").strip() or env.get("EMBED_API_KEY", "").strip()
    model = env.get("RERANK_MODEL", "").strip()
    if not (base and key and model):
        return None
    try:
        import urllib.request
    except ImportError:
        return None
    return {
        "base_url": base.rstrip("/"),
        "api_key": key,
        "model": model,
        "url": urllib.request,
        # 候选池上限。超过就该截断而非期望服务端裁剪。
        "max_docs": int(env.get("RERANK_MAX_DOCS", "50") or 50),
    }


def _embed_batch(client: dict[str, Any], texts: list[str]) -> list[list[float]] | None:
    """批量求embedding。任何失败返回 None，由调用方降级到纯 BM25。"""
    try:
        payload: dict[str, Any] = {"model": client["model"], "input": texts}
        # 智谱 embedding-3 支持 dimensions，2048 精度最高但体积翻倍，
        # 1024 对长篇检索已经够用。不配置则沿用服务端默认。
        if client.get("dimensions"):
            payload["dimensions"] = int(client["dimensions"])
        req = client["url"].Request(
            f"{client['base_url']}/embeddings",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {client['api_key']}",
            },
            method="POST",
        )
        with client["url"].urlopen(req, timeout=60) as resp:
            payload_resp = json.loads(resp.read().decode("utf-8"))
        out = [item["embedding"] for item in payload_resp["data"]]
        return out or None
    except Exception as exc:  # noqa: BLE001 — 任何异常都不该让写章流程崩
        print(f"[recall] embedding 调用失败，降级为纯 BM25：{exc}", file=sys.stderr)
        return None


def _rerank(client: dict[str, Any], query: str, docs: list[str], top_n: int) -> list[tuple[int, float]] | None:
    """重排序。返回 [(原始下标, 相关性分数)]，失败返回 None。

    智谱 rerank 是自有格式（非 OpenAI 兼容），POST {base}/rerank，
    请求体 {model, query, documents, top_n}，响应 results[].relevance_score。
    """
    if not docs:
        return None
    capped = docs[: client.get("max_docs", 50)]
    try:
        body = {
            "model": client["model"],
            "query": query[:2000],
            "documents": capped,
            "top_n": min(top_n, len(capped)),
            "return_documents": False,
        }
        req = client["url"].Request(
            f"{client['base_url']}/rerank",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {client['api_key']}",
            },
            method="POST",
        )
        with client["url"].urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        out = [
            (int(r["index"]), float(r.get("relevance_score", 0.0)))
            for r in payload.get("results", [])
        ]
        return out or None
    except Exception as exc:  # noqa: BLE001
        print(f"[recall] rerank 调用失败，沿用向量排序：{exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------- 索引构建


def _build(project: Path, use_vector: bool, batch: int = 16) -> dict[str, Any]:
    state = _load_state(project)
    files = _chapter_files(project)
    chunks = _chunks_from_state(state) + _chunks_from_chapters(files)
    if not chunks:
        raise SystemExit("[recall] 没找到任何可索引内容，检查 追踪/ 与 正文/ 是否存在")

    tokens = [_tokens(c["title"] + "\n" + c["text"]) for c in chunks]
    df: Counter[str] = Counter()
    for toks in tokens:
        df.update(set(toks))

    mode = "bm25"
    dim_used: int | None = None
    if use_vector:
        client = _embed_client(project)
        if client is None:
            print("[recall] 未配置 EMBED_BASE_URL / EMBED_API_KEY / EMBED_MODEL，"
                  "跳过向量层（纯 BM25 可用）", file=sys.stderr)
        else:
            vecs: list[list[float]] = []
            ok = True
            cap = client.get("max_chars", 1800)
            for i in range(0, len(chunks), batch):
                piece = _embed_batch(client, [c["text"][:cap] for c in chunks[i : i + batch]])
                if piece is None:
                    ok = False
                    break
                vecs.extend(piece)
            if ok and vecs:
                for c, v in zip(chunks, vecs):
                    c["_vec"] = v
                mode = "hybrid"
                dim_used = len(vecs[0])
            else:
                print("[recall] 向量层构建失败，索引仍以 BM25 落盘", file=sys.stderr)

    rc = _rerank_client(project)
    index = {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "model": client["model"] if (use_vector and mode == "hybrid" and client) else None,
        "dim": dim_used,
        "rerank_model": rc["model"] if rc else None,
        "chunks": chunks,
        "df": dict(df),
        "avg_len": (sum(len(t) for t in tokens) / len(tokens)) if tokens else 1.0,
    }
    return index


def _save(project: Path, index: dict[str, Any]) -> Path:
    cache = project / CACHE_DIRNAME
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / INDEX_FILENAME
    path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _load_index(project: Path) -> dict[str, Any]:
    path = project / CACHE_DIRNAME / INDEX_FILENAME
    if not path.is_file():
        raise SystemExit(
            f"[recall] 索引不存在，先跑：python recall.py --project {project} index"
        )
    index = json.loads(path.read_text(encoding="utf-8"))
    # 维度守卫：换了 EMBED_MODEL 或 EMBED_DIM 之后，旧向量与新查询维度对不上，
    # 余弦会静默算错（_cosine 里 len 不等直接返回 0，表现为"向量层失效"而非报错）。
    if index.get("mode") == "hybrid":
        client = _embed_client(project)
        if client and index.get("model") != client["model"]:
            raise SystemExit(
                f"[recall] 索引由{index.get('model')} 构建，"
                f"当前配置是 {client['model']}。请重建：\n"
                f"         python recall.py --project {project} index"
            )
        want = client.get("dimensions") if client else None
        if want and index.get("dim") and int(want) != int(index["dim"]):
            raise SystemExit(
                f"[recall] 索引向量维度为 {index['dim']}，当前配置为 {want}。"
                f"请重建：\n         python recall.py --project {project} index"
            )
    return index


# ---------------------------------------------------------------- 检索


def _bm25(index: dict[str, Any], query: str, k1: float = 1.5, b: float = 0.75) -> list[float]:
    q = set(_tokens(query))
    n = len(index["chunks"]) or 1
    avg = index.get("avg_len") or 1.0
    df = index["df"]
    scores = []
    for c in index["chunks"]:
        toks = _tokens(c["title"] + "\n" + c["text"])
        if not toks:
            scores.append(0.0)
            continue
        tf = Counter(toks)
        length = len(toks)
        s = 0.0
        for term in q:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
            s += idf * (tf[term] * (k1 + 1)) / (tf[term] + k1 * (1 - b + b * length / avg))
        scores.append(s)
    return scores


def _search(
    index: dict[str, Any],
    query: str,
    top: int,
    kinds: list[str] | None,
    project: Path | None = None,
) -> list[dict]:
    pool = [
        (c, s)
        for c, s in zip(index["chunks"], _bm25(index, query))
        if not kinds or c["kind"] in kinds
    ]

    # 章节引用加权：命中 "第21章" 时，第 21 章的片段直接置顶。
    # +1000 压倒所有其它信号，因为显式章节引用是作者意图，不是模糊匹配。
    q_refs, q_nums = _chapter_refs(query)
    pinned: set[str] = set()
    if q_refs:
        boosted: list[tuple[dict, float]] = []
        for c, s in pool:
            cref = c.get("meta", {}).get("chref")
            text_refs, _ = _chapter_refs(c.get("title", ""))
            if cref and (cref in q_refs or cref.lstrip("ch") in q_nums):
                s = s + 1000.0
                pinned.add(c["id"])
            elif text_refs & q_refs:
                s = s + 100.0
            boosted.append((c, s))
        pool = boosted

    # 第二层：向量重排。粗排取 5 倍 top 作为候选池，避免单靠 BM25 错过语义近邻。
    if index.get("mode") == "hybrid":
        client = _embed_client(project)
        if client:
            pool.sort(key=lambda x: x[1], reverse=True)
            cand_n = min(len(pool), max(top * 5, 20))
            cand, base_scores = pool[:cand_n], [s for _, s in pool[:cand_n]]
            piece = _embed_batch(client, [query[:2000]])
            if piece:
                qv = piece[0]
                rescored = [
                    (c, s + 0.6 * _cosine(qv, c.get("_vec") or []))
                    for c, s in cand
                ]
                rescored.sort(key=lambda x: x[1], reverse=True)
                pool = rescored
            else:
                pool = list(zip(cand, base_scores))

    pool = [(c, s) for c, s in pool if s > 0]
    pool.sort(key=lambda x: x[1], reverse=True)

    # 第三层：rerank 定稿。只对未被章节号锁定的候选做精排——
    # 既然用户明确指定了章节，就别让模型把它排下去。
    rc = _rerank_client(project)
    if rc and pool:
        head = pool[: max(top * 3, 20)]
        rest = pool[len(head):]
        docs = [c.get("text", "")[:800] for c, _ in head]
        ranked = _rerank(rc, query, docs, len(head))
        if ranked:
            new_head = [
                (head[i][0], 1000.0 + score) if head[i][0]["id"] in pinned
                else (head[i][0], 500.0 + score)
                for i, score in ranked
            ]
            # rerank 可能少返回几条（异常/截断），把没返回的按原分追加在后
            got = {i for i, _ in ranked}
            for i, (c, s) in enumerate(head):
                if i not in got:
                    new_head.append((c, 400.0 + s))
            new_head.sort(key=lambda x: x[1], reverse=True)
            pool = new_head + rest

    pool = [(c, s) for c, s in pool if s > 0]
    return [
        {
            "rank": i + 1,
            "kind": c["kind"],
            "ref": c["ref"],
            "title": c["title"],
            "score": round(s, 4),
            "excerpt": c["text"][:220].replace("\n", " "),
            "meta": c.get("meta", {}),
        }
        for i, (c, s) in enumerate(pool[:top])
    ]


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="novel-forge 派生检索层（不持有真源状态）")
    ap.add_argument("--project", required=True, help="书稿根目录（含 追踪/ 与 正文/）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_idx = sub.add_parser("index", help="重建索引")
    p_idx.add_argument("--no-vector", action="store_true", help="强制只用 BM25")
    p_idx.add_argument("--quiet", action="store_true")

    p_q = sub.add_parser("query", help="检索")
    p_q.add_argument("text", help="查询串，如'老王 那封信'")
    p_q.add_argument("--top", type=int, default=6)
    p_q.add_argument("--kind", action="append", help="过滤类型，可重复：character/foreshadow/timeline/chapter/constraint/risk/commitment/position/recent")

    sub.add_parser("status", help="索引状态")

    args = ap.parse_args(argv)
    project = Path(args.project).expanduser().resolve()

    if args.cmd == "index":
        index = _build(project, use_vector=not args.no_vector)
        path = _save(project, index)
        by_kind = Counter(c["kind"] for c in index["chunks"])
        if not args.quiet:
            print(f"[recall] 索引已写入 {path}")
            print(f"[recall] 检索模式：{index['mode']}；片段总数：{len(index['chunks'])}")
            if index["mode"] == "hybrid":
                print(f"         向量模型：{index.get('model')}（{index.get('dim') or '?'} 维）")
            print(f"         rerank：{index.get('rerank_model') or '（未配置，仅向量排序）'}")
            for k, v in by_kind.most_common():
                print(f"         {k:<12}{v}")
        return 0

    if args.cmd == "query":
        index = _load_index(project)
        hits = _search(index, args.text, args.top, args.kind, project)
        if not hits:
            print("[recall] 无命中。检查 query，或重建索引后重试。")
            return 1
        layers = [index["mode"]]
        if index.get("rerank_model") and _rerank_client(project):
            layers.append(f"rerank:{index['rerank_model']}")
        print(f"[recall] 检索链路：{' → '.join(layers)} · 命中 {len(hits)} 条\n")
        for h in hits:
            print(f"[{h['rank']}] {h['title']}  (score={h['score']})")
            print(f"    {h['excerpt']}...")
            # meta 里有 chref/kind 这类内部字段，直接倒Python dict
            # 对作者是噪声（还暴露实现细节），只显示有叙事价值的项。
            extra = _human_meta(h["meta"])
            if extra:
                print(f"    {extra}")
            print()
        return 0

    if args.cmd == "status":
        if not (project / CACHE_DIRNAME / INDEX_FILENAME).is_file():
            print(f"[recall] 无索引。运行 index 子命令创建。")
            return 1
        index = _load_index(project)
        by_kind = Counter(c["kind"] for c in index["chunks"])
        print(f"模式: {index['mode']}")
        print(f"向量模型: {index.get('model') or '（无，纯 BM25）'}"
              + (f"（{index.get('dim')} 维）" if index.get("dim") else ""))
        rc = _rerank_client(project)
        print(f"rerank: {index.get('rerank_model') or '（未配置）'}"
              + ("" if rc or not index.get("rerank_model") else "  ⚠ 当前环境未读到 RERANK_* 配置，实际不生效"))
        print(f"片段总数: {len(index['chunks'])}")
        for k, v in by_kind.most_common():
            print(f"  {k:<12}{v}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())