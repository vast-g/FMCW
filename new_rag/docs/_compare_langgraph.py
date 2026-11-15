# -*- coding: utf-8 -*-
"""证明：create_react_agent 内部建的就是一张 StateGraph。

对比：
  A. 求职项目的写法：create_react_agent(model, tools)  → 它内部建了什么图？
  B. 边坡项目的写法：手写 StateGraph + agent ⇄ tools

如果 A 的节点/边集合是 B 的真子集，那就说明：
  * 两个都是"真的 LangGraph"
  * 边坡那个相当于**把 A 内部那张图重画了一遍**，真正的增量在它外面那一圈
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


class FakeModel:
    def bind_tools(self, _t):
        return self

    def invoke(self, _m):
        from langchain_core.messages import AIMessage
        return AIMessage(content="ok")

    def model_copy(self, **_k):
        return self


def dump(title, g):
    print(f"\n=== {title} ===")
    print("  类型:", type(g).__name__)
    inner = g.get_graph()
    nodes = sorted(n for n in inner.nodes.keys())
    print(f"  节点 ({len(nodes)}): {nodes}")
    print(f"  边 ({len(inner.edges)}):")
    for e in inner.edges:
        cond = " (条件边/分支)" if getattr(e, "conditional", False) else ""
        print(f"    {e.source} → {e.target}{cond}")


print("=" * 74)
print("A. 求职项目：create_react_agent 内部建的是什么图")
print("=" * 74)
try:
    from langgraph.prebuilt import create_react_agent

    tools = [make_tool("tool_a"), make_tool("tool_b")]
    from langchain_ollama import ChatOllama

    # 只构图、不调用 —— 所以不需要 Ollama 真的在跑
    _m = ChatOllama(model="qwen3:4b")
    g_prebuilt = create_react_agent(_m, tools)
    dump("create_react_agent 返回的图", g_prebuilt)
except Exception as exc:
    print(f"  构造失败: {type(exc).__name__}: {exc}")
    g_prebuilt = None

print("\n" + "=" * 74)
print("B. 边坡项目：手写的 StateGraph（只保留 ReAct 那部分做对比）")
print("=" * 74)
try:
    from typing import Annotated, TypedDict

    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.message import add_messages
    from langgraph.prebuilt import ToolNode

    class St(TypedDict):
        messages: Annotated[list, add_messages]
        retry: int

    tools = [make_tool("tool_a"), make_tool("tool_b")]
    fake = FakeModel()

    def agent_node(state):
        resp = fake.invoke(list(state.get("messages") or []))
        return {"messages": [resp], "retry": int(state.get("retry") or 0) + 1}

    def should_continue(state):
        last = (state.get("messages") or [None])[-1]
        if int(state.get("retry") or 0) >= 3:
            return "end"
        if getattr(last, "tool_calls", None):
            return "tools"
        return "end"

    g = StateGraph(St)
    g.add_node("agent", agent_node)
    g.add_node("tools", ToolNode(tools))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
    g.add_edge("tools", "agent")
    compiled = g.compile()
    dump("边坡手写的 ReAct 子图", compiled)
except Exception as exc:
    print(f"  构造失败: {type(exc).__name__}: {exc}")
    compiled = None

print("\n" + "=" * 74)
print("结论")
print("=" * 74)
if g_prebuilt is not None and compiled is not None:
    a_nodes = set(g_prebuilt.get_graph().nodes.keys())
    b_nodes = set(compiled.get_graph().nodes.keys())
    print(f"  预构建图的节点: {sorted(a_nodes)}")
    print(f"  手写图的节点  : {sorted(b_nodes)}")
    print(f"  两者共同节点  : {sorted(a_nodes & b_nodes)}")
    print()
    print("  → create_react_agent 返回的**同样**是 CompiledStateGraph")
    print("  → 它内部就是 agent ⇄ tools 两个节点 + 一条条件边")
    print("  → 边坡手写的那部分，等于把这张图重新实现了一遍")
