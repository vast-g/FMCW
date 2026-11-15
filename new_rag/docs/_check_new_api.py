# -*- coding: utf-8 -*-
"""确认 langchain.agents.create_agent 与 langgraph.prebuilt.create_react_agent 的接口差异。

要确认三件事，才能安全替换：
  1. 返回类型是否一样（都是 CompiledStateGraph？）
  2. 图的节点/边是否一样（仍能接受 {"messages": [...]} 输入？）
  3. 调用签名是否兼容（位置参数 model, tools 能不能用）
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(errors="replace")


def make_tool(name):
    from langchain_core.tools import tool as _lc_tool

    def _fn(query: str = "") -> str:
        """模拟工具。"""
        return f"<{name}>"

    _fn.__name__ = name
    t = _lc_tool(_fn)
    t.name = name
    return t


def describe(label, agent):
    print(f"\n=== {label} ===")
    print("  类型:", type(agent).__name__)
    inner = agent.get_graph()
    print("  节点:", sorted(inner.nodes.keys()))
    print("  边  :", [(e.source, e.target) for e in inner.edges])
    # 输入形状
    try:
        import inspect
        sig = inspect.signature(agent.ainvoke)
        print("  ainvoke 参数:", list(sig.parameters.keys()))
    except Exception as exc:      # noqa: BLE001
        print("  ainvoke 签名读不到:", exc)


tools = [make_tool("tool_a"), make_tool("tool_b")]

from langchain_ollama import ChatOllama

model = ChatOllama(model="qwen3:4b")   # 只构图，不调用

print("=" * 74)
print("旧 API：langgraph.prebuilt.create_react_agent")
print("=" * 74)
try:
    from langgraph.prebuilt import create_react_agent
    old = create_react_agent(model, tools)
    describe("create_react_agent(model, tools)", old)
except Exception as exc:      # noqa: BLE001
    print("  失败:", type(exc).__name__, exc)
    old = None

print("\n" + "=" * 74)
print("新 API：langchain.agents.create_agent")
print("=" * 74)
try:
    from langchain.agents import create_agent
    # 先试和旧 API 完全一样的位置参数写法
    new = create_agent(model, tools)
    describe("create_agent(model, tools)", new)
except Exception as exc:      # noqa: BLE001
    print("  位置参数写法失败:", type(exc).__name__, exc)
    try:
        from langchain.agents import create_agent
        new = create_agent(model=model, tools=tools)
        describe("create_agent(model=..., tools=...)", new)
    except Exception as exc2:  # noqa: BLE001
        print("  关键字写法也失败:", type(exc2).__name__, exc2)

# 签名对比
print("\n" + "=" * 74)
print("签名对比")
print("=" * 74)
import inspect  # noqa: E402

try:
    from langgraph.prebuilt import create_react_agent as _old
    print("  旧:", str(inspect.signature(_old))[:220])
except Exception:  # noqa: BLE001
    pass
try:
    from langchain.agents import create_agent as _new
    print("  新:", str(inspect.signature(_new))[:220])
except Exception:  # noqa: BLE001
    pass
