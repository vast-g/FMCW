"""实测新增的多因素监测工具：输出是否可读、是否真的提供了区分信息。"""
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
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("SLOPE_HEADLESS", "1")
sys.stdout.reconfigure(errors="replace")

import end_new as E  # noqa: E402

E.agent._get()  # 触发实例化

CASES = [
    ("危点快照", E.tool_get_site_snapshot, {"site": "S-01"}),
    ("稳点快照", E.tool_get_site_snapshot, {"site": "S-04"}),
    ("危点位移", E.tool_get_displacement_rate, {"site": "S-01", "hours": 12}),
    ("稳点位移", E.tool_get_displacement_rate, {"site": "S-04", "hours": 12}),
    ("降雨", E.tool_get_rainfall, {"hours": 24}),
    ("危点孔压", E.tool_get_pore_pressure, {"site": "S-01", "hours": 12}),
    ("邻点对比", E.tool_get_neighbor_sites, {"site": "S-01", "hours": 6}),
    ("裂缝", E.tool_get_crack_history, {"site": "S-01", "days": 7}),
    ("默认点位（空参）", E.tool_get_displacement_rate, {}),
    ("不存在的点位", E.tool_get_displacement_rate, {"site": "S-99"}),
]

print("=" * 72)
print("多因素监测工具实测")
print("=" * 72)
for name, fn, args in CASES:
    print(f"\n── {name} ──")
    try:
        out = fn.invoke(args)
        for line in str(out).split("\n"):
            print(f"   {line}")
    except Exception as e:  # noqa: BLE001
        print(f"   ❌ {type(e).__name__}: {e}")

print()
print("=" * 72)
print("断言")
print("=" * 72)
from slope_monitor import displacement_rate as dr  # noqa: E402

S = E.agent.monitor_session
danger = dr(S, "S-01", 12)
steady = dr(S, "S-04", 12)
ok1 = "趋势：**加速**" in E.tool_get_displacement_rate.invoke({"site": "S-01"})
# 注意不能写成 `"加速" not in 输出` —— 输出里有"加速度"这个正常字段，
# 会误判。要断言的是**趋势字段的取值**。
ok2 = "趋势：**匀速**" in E.tool_get_displacement_rate.invoke({"site": "S-04"})
ok3 = "同步加速" in E.tool_get_neighbor_sites.invoke({"site": "S-01"})
ok4 = "不可用" in E.tool_get_displacement_rate.invoke({"site": "S-99"})
ok5 = "S-01" in E.tool_get_displacement_rate.invoke({})   # 空参回落主监测点
for label, ok in [
    ("危点判定为加速", ok1),
    ("稳点判定为匀速（不是加速）", ok2),
    ("邻点对比识别同步加速", ok3),
    ("不存在的点位如实报不可用（而非编造）", ok4),
    ("空参数回落到主监测点", ok5),
]:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
print()
print(f"  参考：S-01 速率 {danger['rate_mm_per_h']}mm/h（{danger['trend']}） vs "
      f"S-04 {steady['rate_mm_per_h']}mm/h（{steady['trend']}）")
