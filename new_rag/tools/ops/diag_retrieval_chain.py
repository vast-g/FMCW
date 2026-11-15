# -*- coding: utf-8 -*-
"""诊断：为什么「讲因素」类问题检索不到刚补的那篇文档。

链路是：Query Rewrite → FAISS + BM25 → RRF → 重排 → 取前 k。
任何一环把 query 改坏，都会导致"什么都搜不到"。
所以逐步打出来看。
"""
from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(errors="replace")

_ME = os.path.dirname(os.path.abspath(__file__))
_ROOT = _ME
while _ROOT != os.path.dirname(_ROOT):
    if os.path.exists(os.path.join(_ROOT, "config.py")):
        break
    _ROOT = os.path.dirname(_ROOT)
for _p in (_ROOT, _ME):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.chdir(_ROOT)

import end_new as E                            # noqa: E402

rag = E.ensure_rag()
print("=" * 78)
print("RAG 检索链路诊断")
print("=" * 78)
print(f"  rag_state = {E.rag_state}")
if rag is None:
    print("  RAG 未加载，退出")
    raise SystemExit(1)

vs = rag.vectorstore
n_chunks = getattr(vs.index, "ntotal", "?") if vs is not None else "?"
print(f"  索引块数  = {n_chunks}")

# 新文档有没有进索引
sources = set()
for d in getattr(rag, "_all_documents", []):
    src = os.path.basename(str((getattr(d, "metadata", {}) or {}).get("source", "")))
    if src:
        sources.add(src)
print(f"  索引里的文档数 = {len(sources)}")
print(f"  新文档是否在索引里: {'slope_instability_factors.txt' in sources}")

Q = "给我讲讲几个因素是怎么影响边坡是否滑坡的？"
print()
print("=" * 78)
print(f"问题：{Q}")
print("=" * 78)

print("\n1) Query Rewrite 输出")
if rag.rewriter is not None:
    qs = rag.rewriter.rewrite(Q)
    for i, q in enumerate(qs, 1):
        print(f"   [{i}] {q!r}")
    search_q = qs[0] if qs else Q
else:
    print("   （无 rewriter）")
    search_q = Q
print(f"   实际用于检索的是第 1 条：{search_q!r}")

print("\n2) 混合检索（FAISS + BM25 → RRF）")
if rag.ensemble_retriever is not None:
    docs = rag.ensemble_retriever.invoke(search_q)
    print(f"   召回 {len(docs)} 条")
    for i, d in enumerate(docs[:5], 1):
        src = os.path.basename(str((getattr(d, "metadata", {}) or {}).get("source", "")))
        txt = str(getattr(d, "page_content", ""))[:60].replace("\n", " ")
        print(f"   [{i}] {src:<34} {txt}")
else:
    print("   （无 ensemble_retriever）")

print("\n3) 直接向量检索（绕过改写，用原问题）")
if vs is not None:
    for i, d in enumerate(vs.similarity_search(Q, k=5), 1):
        src = os.path.basename(str((getattr(d, "metadata", {}) or {}).get("source", "")))
        txt = str(getattr(d, "page_content", ""))[:60].replace("\n", " ")
        print(f"   [{i}] {src:<34} {txt}")

print("\n4) 只用关键词检索（BM25，绕过向量）")
try:
    bm = rag.ensemble_retriever.bm25_retriever
    if bm is not None:
        for i, d in enumerate(bm.invoke(Q)[:5], 1):
            src = os.path.basename(str((getattr(d, "metadata", {}) or {}).get("source", "")))
            txt = str(getattr(d, "page_content", ""))[:60].replace("\n", " ")
            print(f"   [{i}] {src:<34} {txt}")
except Exception as exc:
    print(f"   BM25 调用失败: {type(exc).__name__}: {exc}")

print("\n5) 完整 ask() 的输出")
out = rag.ask(Q)
print(f"   返回长度 {len(str(out))}")
print(f"   {str(out)[:300]}")
