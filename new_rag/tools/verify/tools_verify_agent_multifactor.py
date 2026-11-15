"""端到端：问一个需要综合多因素的风险问题，看 Agent 是否真的用上新工具。

关注三件事：
  1. 它调了哪些工具（是否用上新数据源，而不是只看距离）
  2. 回答是否是多因素综合（速率/降雨/孔压/邻点/裂缝）
  3. 是否守住了边界（只给评估建议，不宣称"已自动报警"）
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
import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("SLOPE_HEADLESS", "1")
sys.stdout.reconfigure(errors="replace")

import end_new as E  # noqa: E402

agent = E.agent._get()

QUESTION = "最近下了好几天雨，现在边坡有没有滑坡风险？帮我综合判断一下"
SID = "__tooltest__"

print("=" * 72)
print(f"问题：{QUESTION}")
print("=" * 72)

t0 = time.time()
ans = agent.run(QUESTION, session_id=SID)
dt = time.time() - t0

print(f"\n耗时 {dt:.1f}s")
print("\n── 回答 ──")
print(ans)

# ── 从 Trace 读这次实际调用了哪些工具 ──
print()
print("=" * 72)
print("本次实际调用的工具（来自 Trace）")
print("=" * 72)
tools_used: list[str] = []
try:
    from tracing import get_store
    runs = get_store().fetch_runs(suite="online")
    if runs:
        last = runs[-1]
        tools_used = list(last.get("predicted_tools") or [])
        print(f"  route = {last.get('route')}")
        print(f"  tools = {tools_used}")
        steps = last.get("steps") or []
        for s in steps:
            if isinstance(s, dict) and s.get("kind") == "tool":
                print(f"    - {s.get('tool_name') or s.get('name')}")
except Exception as e:  # noqa: BLE001
    print(f"  读取 Trace 失败: {type(e).__name__}: {e}")

# ── 断言 ──
print()
print("=" * 72)
print("断言")
print("=" * 72)
NEW = {"tool_get_displacement_rate", "tool_get_rainfall", "tool_get_pore_pressure",
       "tool_get_neighbor_sites", "tool_get_crack_history", "tool_get_site_snapshot"}
used_new = NEW & set(tools_used)
txt = str(ans)

checks = [
    (f"用上了新增的多因素工具（{sorted(used_new)}）", len(used_new) >= 2),
    ("回答提到位移速率/加速", ("速率" in txt) or ("加速" in txt)),
    ("回答提到降雨", "雨" in txt),
    ("回答提到孔隙水压力或孔压", ("孔隙水压力" in txt) or ("孔压" in txt)),
    ("回答提到多点/邻点/同步", any(k in txt for k in ("多点", "邻点", "同步", "S-02", "S-03"))),
    ("没有宣称'系统已自动报警'（守住边界）", "已自动报警" not in txt and "已报警" not in txt),
]
for label, ok in checks:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

print()
print(f"  用到的新工具数：{len(used_new)}/{len(NEW)}")
