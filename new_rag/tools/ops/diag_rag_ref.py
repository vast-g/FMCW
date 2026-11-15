# -*- coding: utf-8 -*-
"""查：为什么知识类问题会答"知识库未加载"。

## 背景

刚把「讲因素/讲原理」类问题正确路由到知识库，就暴露了下一个问题 ——
`tool_search_kb` 返回"知识库未加载"。而日志里明明打过 "[RAG] loaded"。

这说明**构建 RAG 的实例**和**工具去取 RAG 的地方**不是同一个引用
（这个项目之前踩过一次同类坑：`import end_new` 导致模块被加载两遍，
工具拿到的是另一个还没预热的实例）。

一次性把三个地方都打出来对比：
  1. 模块级全局 `rag`
  2. agent 实例上的 `rag` 属性
  3. 工具实际用的那个（`_self_module().rag`）
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
os.environ.setdefault("SLOPE_HEADLESS", "1")

import end_new as E

print("=" * 74)
print("RAG 引用一致性检查")
print("=" * 74)

print(f"\n0) 触发懒加载（访问 agent）")
agent = E.agent
print(f"  agent 类型: {type(agent).__name__}")

print(f"\n1) 模块级全局 rag")
print(f"   E.rag              = {type(E.rag).__name__ if E.rag is not None else None}")
print(f"   E.rag_state        = {getattr(E, 'rag_state', '(无此变量)')}")
rag_obj = E.rag
if rag_obj is not None:
    vs = getattr(rag_obj, "vectorstore", None)
    print(f"   vectorstore        = {type(vs).__name__ if vs is not None else None}")
    if vs is not None and hasattr(vs, "index"):
        print(f"   FAISS 向量数       = {getattr(vs.index, 'ntotal', '?')}")

print(f"\n2) agent 实例上的 rag 属性")
a_rag = getattr(agent, "rag", "(无此属性)")
print(f"   agent.rag          = {type(a_rag).__name__ if a_rag is not None else None}")
print(f"   与 E.rag 是同一对象: {a_rag is rag_obj}")

print(f"\n3) 工具实际取的那个（_self_module().rag）")
try:
    mod = E._self_module()
    print(f"   _self_module()     = {mod.__name__}")
    m_rag = getattr(mod, "rag", "(无此属性)")
    print(f"   mod.rag            = {type(m_rag).__name__ if m_rag is not None else None}")
    print(f"   与 E.rag 是同一对象: {m_rag is rag_obj}")
except Exception as exc:
    print(f"   _self_module() 失败: {type(exc).__name__}: {exc}")

print(f"\n4) 直接调工具，看返回什么")
try:
    out = E.tool_search_kb.invoke({"query": "滑坡的因素"})
    print(f"   返回长度: {len(str(out))}")
    print(f"   返回开头: {str(out)[:220]}")
except Exception as exc:
    print(f"   调用失败: {type(exc).__name__}: {exc}")
