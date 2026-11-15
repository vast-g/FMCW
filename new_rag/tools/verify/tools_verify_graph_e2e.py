# -*- coding: utf-8 -*-
"""端到端验证：显式控制流图 + 结构化长期画像是否真的生效。

覆盖四件事：
  1. 图确实被构建出来了（而不是回退到 create_agent）
  2. 不同问法被分到不同意图，且**每个意图拿到的工具集确实不同**
  3. 跑一次真实问题，图能产出答案（不降级）
  4. 长期画像能被写入并读回（③）

注意：这里会真的调用本地 Ollama（qwen3:4b），单个问题约 20~90 秒。
所以只用 2 个问题，避免把时间全花在等待上。
"""
from __future__ import annotations


# ── 路径引导（本脚本已整理进 tools/ 子目录）──────────────────────────
# 项目根**不能靠"上跳固定层数"来算** —— 脚本可能在 tools/（一层）
# 也可能在 tools/verify/ 或 tools/ops/（两层），层数一变就算错。
# 所以改成「向上找标志文件」：一路往上，第一个含 config.py 的目录就是项目根。
#
# 需要它做三件事：
#   1) 项目根加进 sys.path → import config / end_new 才能成功
#   2) 本目录与同级 tools/* 也加进去 → verify/ 与 ops/ 之间可以互相 import
#   3) chdir 到项目根 → rag_docs / reports/ 这类相对路径仍指向同一个地方
import os as _os
import sys as _sys

_ME = _os.path.dirname(_os.path.abspath(__file__))
_ROOT = _ME
while _ROOT != _os.path.dirname(_ROOT):
    if _os.path.exists(_os.path.join(_ROOT, "config.py")):
        break
    _ROOT = _os.path.dirname(_ROOT)

for _p in (_ROOT, _ME):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
for _sib in ("verify", "ops"):
    _p = _os.path.join(_ROOT, "tools", _sib)
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.append(_p)
_os.chdir(_ROOT)
# ────────────────────────────────────────────────────────────────
import sys
import time

sys.stdout.reconfigure(errors="replace")

import end_new as E

PASS: list[str] = []
FAIL: list[str] = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("端到端验证：控制流图 + 长期画像")
print("=" * 74)

print("\n0) 取 agent 实例（首次访问会做懒加载）")
t0 = time.time()
agent = E.agent
print(f"      加载耗时 {time.time() - t0:.1f}s")

print("\n1) 图是否构建成功")
graph = getattr(agent, "slope_graph", None)
check("slope_graph 已构建（未回退 create_agent）", graph is not None,
      type(graph).__name__ if graph else "None —— 说明构图失败，主路径会退回旧实现")

print("\n2) 意图分类（规则优先，不花 LLM）")
import slope_graph as SG

cases = [
    ("现在距离多少？", "status", ["tool_get_distance", "tool_get_gps", "tool_get_weather"]),
    ("结合降雨综合判断一下风险", "analysis", None),
    ("超声波传感器读数跳变是什么原因", "knowledge", ["tool_search_kb"]),
    ("最近有告警吗", "alert", None),
    ("你好", "chat", []),
]
for text, expect_intent, expect_tools in cases:
    got = SG.classify_intent_rules(text)
    ok = got == expect_intent
    extra = ""
    if ok and expect_tools is not None:
        actual = SG.INTENT_TOOLS.get(got, [])
        ok = actual == expect_tools
        extra = f"工具 {actual}"
    check(f"{text[:20]!r} → {expect_intent}", ok, extra or f"实得 {got}")

print("\n3) 工具收窄确实生效（这是 ① 号改造的核心）")
full = set(SG.INTENT_TOOLS["analysis"])
narrow = set(SG.INTENT_TOOLS["status"])
check("status 拿不到多因素工具", not (narrow & {"tool_get_displacement_rate",
                                              "tool_get_pore_pressure",
                                              "tool_get_neighbor_sites"}))
check("analysis 能拿到全部多因素工具", {"tool_get_displacement_rate",
                                        "tool_get_pore_pressure",
                                        "tool_get_neighbor_sites"} <= full)
check("knowledge 只给知识库工具", SG.INTENT_TOOLS["knowledge"] == ["tool_search_kb"])
check("chat 不给任何工具", SG.INTENT_TOOLS["chat"] == [])

print("\n4) 长期画像（③）")
mem = getattr(agent, "memory", None)
check("memory 已挂载", mem is not None,
      "None —— 初始化失败或未接线" if mem is None else type(mem).__name__)
if mem is not None:
    try:
        st = mem.stats()
        check("stats() 可调用且 db_ok", bool(st.get("db_ok")),
              f"画像字段 {st.get('profile_fields')} / 摘要 {st.get('summaries')}")
        # 往返：写一份画像再读回，确认接线用的是同一个库同一张表
        probe = {"role": "端到端验证", "focus_sites": ["S-01"], "concerns": ["位移速率"]}
        mem.save_profile(probe)
        got = mem.load_profile()
        check("画像写入后可读回", got.get("role") == "端到端验证", str(got)[:80])
        check("render_for_prompt 能渲染", "端到端验证" in (mem.render_for_prompt(got) or ""))
        # 清理探针数据，避免污染真实画像
        mem.save_profile({})
        check("探针数据已清理", not mem.load_profile().get("role"))
    except Exception as exc:
        check("长期画像往返", False, f"{type(exc).__name__}: {exc}")

print("\n5) 真实跑一次请求（走图，约 30~90 秒）")
try:
    t1 = time.time()
    ans = agent.run("现在距离多少？", session_id="e2e-verify")
    dt = time.time() - t1
    check(f"图路径产出答案（{dt:.0f}s）", bool(ans) and len(str(ans)) > 3,
          f"{len(str(ans))} 字")
    print("      答案节选：" + str(ans)[:120].replace("\n", " "))
    check("记录了本次意图", "last_intent" in agent.stats,
          f"intent={agent.stats.get('last_intent')}")
except Exception as exc:
    check("真实请求", False, f"{type(exc).__name__}: {exc}")

print()
print("=" * 74)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
for f in FAIL:
    print(f"  失败: {f}")
print("=" * 74)
