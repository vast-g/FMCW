# -*- coding: utf-8 -*-
"""验证：create_agent 返回的图能不能再改（加节点/加边）。

这是回答"求职项目能不能自己组装边"的关键 ——
如果能改，那两个项目其实都能；如果不能改，那求职只能在图**外面**编排。
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


from langchain_ollama import ChatOllama

from langchain.agents import create_agent

model = ChatOllama(model="qwen3:4b")
tools = [make_tool("tool_a")]

print("=" * 74)
print("1) create_agent 返回对象的类型与可用方法")
print("=" * 74)
agent = create_agent(model, tools)
print("  类型:", type(agent).__name__)
print("  模块:", type(agent).__module__)
for m in ("add_node", "add_edge", "add_conditional_edges", "compile", "invoke", "get_graph"):
    print(f"  有 {m:<22} ? {hasattr(agent, m)}")

print("\n" + "=" * 74)
print("2) 试着往这个图里加一个节点")
print("=" * 74)
try:
    agent.add_node("my_node", lambda s: s)
    print("  ✓ 竟然可以加 —— 说明编译后仍可编辑")
except Exception as exc:      # noqa: BLE001
    print(f"  ✗ 加不了：{type(exc).__name__}: {exc}")

print("\n" + "=" * 74)
print("3) 对比：StateGraph 构建器（未编译）能加吗")
print("=" * 74)
from typing import Annotated, TypedDict          # noqa: E402

from langgraph.graph import StateGraph           # noqa: E402
from langgraph.graph.message import add_messages  # noqa: E402


class St(TypedDict):
    messages: Annotated[list, add_messages]


g = StateGraph(St)
print("  构建器类型:", type(g).__name__)
print("  有 add_node ?", hasattr(g, "add_node"))
print("  有 add_edge ?", hasattr(g, "add_edge"))
print("  有 add_conditional_edges ?", hasattr(g, "add_conditional_edges"))
print("  有 compile ?", hasattr(g, "compile"))

print("\n" + "=" * 74)
print("4) 关键：已编译的图能不能当作别人图里的一个节点（subgraph 组合）")
print("=" * 74)
try:
    outer = StateGraph(St)
    outer.add_node("pre", lambda s: {"messages": []})           # 自己的前置节点
    outer.add_node("agent", agent)                              # ⭐ 把整张图当一个节点
    from langgraph.graph import START, END                      # noqa: E402
    outer.add_edge(START, "pre")
    outer.add_edge("pre", "agent")
    outer.add_edge("agent", END)
    compiled = outer.compile()
    print("  ✓ 可以！外层节点:", sorted(compiled.get_graph().nodes.keys()))
    print("    → 这叫 subgraph 组合：预构建的 agent 可以是别人图里的一个节点")
except Exception as exc:      # noqa: BLE001
    print(f"  ✗ 不行：{type(exc).__name__}: {exc}")
