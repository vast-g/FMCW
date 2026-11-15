# -*- coding: utf-8 -*-
"""验证方案 B：5 轮滑动窗口 + 滚动摘要 + 事实改精确查。

覆盖：
  1. 滑动窗口：超过 10 条只保留 10 条，且写回后端的也是 10 条
  2. 挤出回调：被挤掉的条目确实交给了回调（不是静默丢了）
  3. 攒批触发：挤出累计到 _EVICT_BATCH 才触发一次滚动摘要
  4. 挤出回调抛异常不影响对话
  5. _query_fact 走字典精确查（不再依赖 FAISS）
  6. 摘要能进 state 的 summary_text（真的被注入，不再是死字段）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)
sys.stdout.reconfigure(errors="replace")

PASS: list[str] = []
FAIL: list[str] = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("方案 B 验证：滑动窗口 + 滚动摘要")
print("=" * 74)

import end_new as E


class FakeStore:
    """假会话后端：只在内存里记最后一次写入的内容。"""

    def __init__(self):
        self.saved = {}
        self.saves = 0

    def load(self, sid):
        return list(self.saved.get(sid, []))

    def save(self, sid, items):
        self.saved[sid] = list(items)
        self.saves += 1


print("\n1) 滑动窗口（5 轮 = 10 条）")
store = FakeStore()
evicted_seen: list[list] = []


def on_evict(sid, items):
    evicted_seen.append(list(items))


h = E._PersistentHistory(store, "s1", [], on_evict=on_evict)
# 模拟 8 轮对话 = 16 条
for r in range(1, 9):
    h.append(("用户", f"第{r}轮问题"))
    h.append(("AI", f"第{r}轮回答"))

check("内存里只剩 10 条", len(h) == E._PersistentHistory.MAX_ENTRIES, f"实际 {len(h)}")
check("写回后端的也是 10 条", len(store.saved.get("s1", [])) == 10,
      f"实际 {len(store.saved.get('s1', []))}")
check("保留的是最近的（第 4-8 轮）",
      h[0] == ("用户", "第4轮问题") and h[-1] == ("AI", "第8轮回答"),
      f"首={h[0][1]} 尾={h[-1][1]}")

print("\n2) 挤出的内容交给了回调（不是静默丢掉）")
flat = [x for batch in evicted_seen for x in batch]
check("回调被调用过", len(evicted_seen) > 0, f"{len(evicted_seen)} 次")
check("挤出的是最早的内容（第 1-3 轮）",
      all(x[1].startswith(("第1轮", "第2轮", "第3轮")) for x in flat),
      f"挤出 {len(flat)} 条")
check("挤出条数 = 总追加 - 窗口 = 16-10 = 6", len(flat) == 6, f"实际 {len(flat)}")

print("\n3) 挤出回调抛异常不影响对话")
store2 = FakeStore()


def bad_evict(sid, items):
    raise RuntimeError("模拟回调故障")


h2 = E._PersistentHistory(store2, "s2", [], on_evict=bad_evict)
try:
    for i in range(12):
        h2.append(("用户", f"m{i}"))
    check("回调抛异常时 append 仍正常", len(h2) == 10, f"窗口 {len(h2)} 条")
    check("数据仍然写回了后端", len(store2.saved.get("s2", [])) == 10)
except Exception as exc:
    check("回调抛异常时 append 仍正常", False, f"{type(exc).__name__}: {exc}")

print("\n4) 攒批触发滚动摘要（_EVICT_BATCH = 4 条）")
# 直接测 _on_history_evict：用 __new__ 造一个轻量实例，避开完整初始化
agent = object.__new__(E.SimpleAgent)
agent._evict_buffer = {}
# 必须挂一个 memory：_on_history_evict 现在有前置判断 ——
# 没有结构化记忆后端时摘要没地方存，直接返回（避免白调 LLM 和刷日志）。
# 测试里给一个空壳即可，_roll_summary 会被下面替换掉，不会真的跑。
agent.memory = object()
triggered: list[tuple] = []


def fake_roll(sid, batch):
    triggered.append((sid, list(batch)))


agent._roll_summary = fake_roll

agent._on_history_evict("s3", [("用户", "a"), ("AI", "b")])       # 2 条 → 不触发
check("2 条时不触发", len(triggered) == 0, f"缓冲 {len(agent._evict_buffer['s3'])} 条")
agent._on_history_evict("s3", [("用户", "c"), ("AI", "d")])       # 累计 4 条 → 触发
check("累计 4 条时触发一次", len(triggered) == 1, f"触发 {len(triggered)} 次")
check("触发时带上了全部 4 条", len(triggered[0][1]) == 4 if triggered else False)
check("触发后缓冲清空", len(agent._evict_buffer.get("s3", [])) == 0)
check("_EVICT_BATCH 是 4", E.SimpleAgent._EVICT_BATCH == 4)

print("\n4b) 没有记忆后端时不触发（避免白调 LLM）")
agent_nomem = object.__new__(E.SimpleAgent)
agent_nomem._evict_buffer = {}
agent_nomem.memory = None
agent_nomem._on_history_evict("s9", [("用户", "x"), ("AI", "y")] * 3)
check("memory 为 None 时不起摘要线程、不建缓冲",
      len(agent_nomem._evict_buffer) == 0, f"缓冲 {agent_nomem._evict_buffer}")

print("\n5) _query_fact 走字典精确查（不再依赖 FAISS）")
agent.facts = {"name": "张三", "occupation": "运维"}
# 注意：两个调用点传的都是**字面量 "用户信息"**（不是用户原话），
# 所以这里必须测 "用户信息" —— 改造前它一个关键词分支都不命中，
# 全靠 FAISS 兜底才返回结果。测 "我叫什么" 是测不到真实路径的。
check("综合身份查询命中（这是真实调用路径）",
      agent._query_fact("用户信息") == "用户的名字: 张三；用户的职业: 运维",
      str(agent._query_fact("用户信息")))
check("问名字命中", agent._query_fact("我叫什么") == "用户的名字: 张三；用户的职业: 运维",
      str(agent._query_fact("我叫什么")))
check("问职业命中", agent._query_fact("我的职业") == "用户的职业: 运维",
      str(agent._query_fact("我的职业")))
check("无事实时返回 None", agent._query_fact("今天天气") is None)
check("旧的 fact_store 属性已不存在", not hasattr(agent, "fact_store"))

print("\n6) 摘要能进 state 的 summary_text（真正被注入）")
import slope_graph as SG


class FakeMem:
    def __init__(self, profile, summaries):
        self._p = profile
        self._s = summaries

    def load_profile(self):
        return dict(self._p)

    def render_for_prompt(self, _p=None):
        return "- 用户角色：运维人员" if self._p else ""

    def recent_summaries(self, limit=3):
        return list(self._s)[:limit]

    def stats(self):
        return {"profile_fields": len(self._p), "summaries": len(self._s), "db_ok": True}


class FakeAgent2:
    facts = {}
    memory = FakeMem({"role": "运维人员"}, ["之前讨论过 S-01 的位移趋势和降雨耦合风险"])

    def _execute_multi_intent(self, _t):
        return ""

    def _handle_sensor_check(self):
        return "传感器正常"

    def _check_trend(self):
        return "趋势平稳"

    def _query_fact(self, _q):
        return None


from langchain_core.tools import tool as _lc_tool


def _mk(name):
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

    def invoke(self, msgs):
        from langchain_core.messages import AIMessage
        # 把收到的系统提示记下来，供断言检查摘要有没有真的进去
        self.last_system = str(msgs[0].content) if msgs else ""
        return AIMessage(content="模拟回答")

    def model_copy(self, **_k):
        return self


fm = FakeModel()
g = SG.build_slope_graph(
    agent=FakeAgent2(),
    tools=[_mk(n) for n in ("tool_get_distance", "tool_get_gps", "tool_get_weather",
                            "tool_search_kb", "tool_get_site_snapshot",
                            "tool_get_displacement_rate", "tool_get_rainfall",
                            "tool_get_pore_pressure", "tool_get_neighbor_sites",
                            "tool_get_crack_history", "tool_get_active_alerts")],
    model=fm, system_prompt="你是测试助手", max_rounds=2, strip_thinking=lambda s: s,
)
out = g.invoke({"session_id": "s", "user_input": "结合降雨综合判断一下风险",
                "deadline": 9e18, "messages": []},
               config={"recursion_limit": 20})
check("state 里有 summary_text", bool(out.get("summary_text")),
      str(out.get("summary_text"))[:50])
check("state 里有 profile_text", bool(out.get("profile_text")))
check("摘要确实进了系统提示", "之前对话的摘要" in getattr(fm, "last_system", ""),
      "系统提示里找到摘要标题" if "之前对话的摘要" in getattr(fm, "last_system", "") else "没找到")
check("画像也进了系统提示", "用户长期画像" in getattr(fm, "last_system", ""))

print()
print("=" * 74)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
for f in FAIL:
    print(f"  失败: {f}")
print("=" * 74)
