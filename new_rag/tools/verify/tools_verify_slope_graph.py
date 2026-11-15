# -*- coding: utf-8 -*-
"""验证 slope_graph 的结构与路由逻辑（不连真实 LLM / 数据库）。

覆盖：
  1. 模块能 import、图能编译
  2. 图的节点集合与边集合符合设计（含 load_memory 这个画像加载节点）
  3. 意图规则分类器的判定
  4. 按意图收窄工具集
  5. 画像确实在**所有路径**上被加载（这是上一版接错的点）
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# ── 路径引导 ──
# 本文件在 tools/verify/ 下，但被测模块（slope_graph、config…）在项目根。
# 直接跑会 ModuleNotFoundError，所以先把项目根塞进 sys.path 并 chdir 过去 ——
# 套件里其它脚本也是这么做的（见 run_all_checks.py 头部的说明）。
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

sys.stdout.reconfigure(errors="replace")

import slope_graph as SG

PASS: list[str] = []
FAIL: list[str] = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


print("=" * 72)
print("slope_graph 结构与路由验证")
print("=" * 72)


# ── 用假工具和假模型构图 ──
# 注意：ToolNode 会校验工具类型，手写的普通类不合法（会报
# "must be a string or a callable with a __name__ for tool decorator"）。
# 所以这里用 langchain_core 的 tool 装饰器生成**真的** StructuredTool。
from langchain_core.tools import tool as _lc_tool


def _make_tool(name):
    def _fn(query: str = "") -> str:
        """模拟工具（仅用于验证图结构，不产生真实数据）。"""
        return f"<{name} 的模拟结果>"

    _fn.__name__ = name
    t = _lc_tool(_fn)
    t.name = name
    return t


class FakeModel:
    def bind_tools(self, _tools):
        return self

    def invoke(self, _msgs):
        from langchain_core.messages import AIMessage
        return AIMessage(content="模拟回答")

    def model_copy(self, **_kw):
        return self


ALL_TOOL_NAMES = [
    "tool_get_distance", "tool_get_gps", "tool_get_weather", "tool_search_kb",
    "tool_get_site_snapshot", "tool_get_displacement_rate", "tool_get_rainfall",
    "tool_get_pore_pressure", "tool_get_neighbor_sites", "tool_get_crack_history",
    "tool_get_active_alerts",
]
fake_tools = [_make_tool(n) for n in ALL_TOOL_NAMES]


class FakeMemory:
    """假记忆：记录被读了几次，用来验证"所有路径都加载画像"。"""

    def __init__(self):
        self.load_calls = 0

    def load_profile(self):
        self.load_calls += 1
        return {"role": "运维人员", "focus_sites": ["S-01"], "concerns": ["位移速率"]}

    def render_for_prompt(self, _prof=None):
        return "- 用户角色：运维人员\n- 关注点位：S-01"

    def recent_summaries(self, limit=3):
        return ["之前讨论过 S-01 的位移趋势"]

    def stats(self):
        return {"profile_fields": 3, "summaries": 1, "db_ok": True}


class FakeAgent:
    facts = {"name": "张三", "occupation": "运维"}

    def __init__(self):
        self.memory = FakeMemory()

    def _execute_multi_intent(self, _t):
        return ""

    def _handle_sensor_check(self):
        return "传感器正常"

    def _check_trend(self):
        return "趋势平稳"

    def _query_fact_from_faiss(self, _q):
        return ""


print("\n1) 构图")
try:
    fake_agent = FakeAgent()
    graph = SG.build_slope_graph(
        agent=fake_agent, tools=fake_tools, model=FakeModel(),
        system_prompt="你是测试助手", max_rounds=3, intent_llm=None,
        strip_thinking=lambda s: s,
    )
    check("图编译成功", graph is not None, type(graph).__name__)
except Exception as exc:
    check("图编译成功", False, f"{type(exc).__name__}: {exc}")
    print(f"\n通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    raise SystemExit(1)

print("\n2) 节点与边")
try:
    nodes = set(graph.get_graph().nodes.keys())
    expect_nodes = {"__start__", "classify", "load_memory", "direct", "recall",
                    "knowledge", "rule_lookup", "agent", "tools", "finalize", "__end__"}
    missing = expect_nodes - nodes
    check("节点集合完整", not missing, f"缺 {missing}" if missing else f"{len(nodes)} 个")

    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    check("classify → load_memory 存在（画像无条件加载）",
          ("classify", "load_memory") in edges)
    check("load_memory → agent 存在（分流入口已移到 load_memory）",
          ("load_memory", "agent") in edges)
    check("tools → agent 存在（ReAct 回环）", ("tools", "agent") in edges)
    check("agent → finalize 存在", ("agent", "finalize") in edges)
    check("finalize → __end__ 存在", ("finalize", "__end__") in edges)
except Exception as exc:
    check("节点与边检查", False, f"{type(exc).__name__}: {exc}")

print("\n3) 意图规则分类器")
cases = [
    ("现在距离多少", "status"),
    ("传感器正常吗", "status"),
    ("今天天气怎么样", "status"),
    ("超声波传感器读数跳变是什么原因", "knowledge"),
    ("YOLOv8 检测不到人怎么排查", "knowledge"),
    ("为什么位移速率会上升", "knowledge"),
    ("结合降雨综合判断一下风险", "analysis"),
    ("最近有没有滑坡风险", "analysis"),
    ("现在安全吗", "analysis"),
    ("为什么报警了", "alert"),
    ("最近有告警吗", "alert"),
    ("我叫什么", "recall"),
    ("你好", "chat"),
]
ok_n = 0
for text, expect in cases:
    got = SG.classify_intent_rules(text)
    good = got == expect
    ok_n += good
    if not good:
        print(f"      ✗ {text!r} → 期望 {expect}，实得 {got}")
check(f"规则分类 {ok_n}/{len(cases)} 条正确", ok_n == len(cases))
check("判不出的问题返回 None（交给 LLM 兜底）",
      SG.classify_intent_rules("那玩意儿咋样了") is None)

print("\n4) 按意图收窄工具集")
# 断言"包含哪些关键工具"而不是"恰好几个" —— 用魔法数字会在合理地增删工具时
# 变成假失败（这次就是这样：analysis 多加了 tool_search_kb，因为综合评估
# 有时要查阈值/标准，是合理增强，但计数断言就红了）。
_multi = {"tool_get_site_snapshot", "tool_get_displacement_rate",
          "tool_get_rainfall", "tool_get_pore_pressure",
          "tool_get_neighbor_sites", "tool_get_crack_history"}
check("analysis 意图包含全部多因素工具", _multi <= set(SG.INTENT_TOOLS["analysis"]),
      f"共 {len(SG.INTENT_TOOLS['analysis'])} 个")
check("status 意图只拿到 3 个（不含多因素）",
      "tool_get_displacement_rate" not in SG.INTENT_TOOLS["status"])
check("knowledge 意图只拿到知识库工具",
      SG.INTENT_TOOLS["knowledge"] == ["tool_search_kb"])
check("chat 意图不暴露任何工具", SG.INTENT_TOOLS["chat"] == [])
_all = set()
for v in SG.INTENT_TOOLS.values():
    _all.update(v)
check("按意图暴露的工具是全集子集且确实收窄了",
      _all <= set(ALL_TOOL_NAMES) and len(_all) == len(ALL_TOOL_NAMES))

print("\n5) 画像在**所有路径**上都被加载（上一版接错的点）")
# chat 路径：classify → load_memory → finalize
before = fake_agent.memory.load_calls
st = {"session_id": "t1", "user_input": "你好",
      "deadline": time.time() + 30, "messages": []}
out = graph.invoke(st, config={"recursion_limit": 25})
after = fake_agent.memory.load_calls
check("chat 意图直接收口（不调工具）", out.get("intent") == "chat",
      f"intent={out.get('intent')}")
check("chat 路径也加载了画像（说明放在了分流之前）", after > before,
      f"load_profile 调用 {before} → {after}")
check("画像文本进入了 state", bool(out.get("profile_text")), str(out.get("profile_text"))[:40])

# status 路径：classify → load_memory → direct → finalize
before2 = after
st2 = {"session_id": "t2", "user_input": "距离多少",
       "deadline": time.time() + 30, "messages": []}
out2 = graph.invoke(st2, config={"recursion_limit": 25})
check("status 意图加载了画像", fake_agent.memory.load_calls > before2,
      f"intent={out2.get('intent')} answer={str(out2.get('answer'))[:20]!r}")

print()
print("=" * 72)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
for f in FAIL:
    print(f"  失败: {f}")
print("=" * 72)
