"""验证 slope_monitor：建表、造数、各查询函数是否合理。"""
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
from urllib.parse import quote_plus

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("SLOPE_HEADLESS", "1")
sys.stdout.reconfigure(errors="replace")

from sqlalchemy.orm import sessionmaker  # noqa: E402

import config as _cfg  # noqa: E402
import slope_monitor as M  # noqa: E402

url = (f"mysql+pymysql://{_cfg.DB_USER}:{quote_plus(_cfg.DB_PASSWORD)}"
       f"@{_cfg.DB_HOST}:{_cfg.DB_PORT}/{_cfg.DB_NAME}")
eng = M.build_engine(url)
S = sessionmaker(bind=eng)

n_site, n_read = M.seed_if_empty(S, force=True)
print("=" * 70)
print(f"建表与造数：点位 {n_site} 个，读数 {n_read} 条")
print("=" * 70)

print("\n1) 位移速率与趋势")
for code in ("S-01", "S-02", "S-03", "S-04"):
    r = M.displacement_rate(S, code, hours=12)
    if r.get("ok"):
        print(f"  {code}: 累计 {r['cumulative_mm']}mm | "
              f"当前速率 {r['rate_mm_per_h']}mm/h | "
              f"前段 {r['rate_prev_mm_per_h']} | "
              f"加速度 {r['acceleration_mm_per_h2']} | 趋势 {r['trend']}")

print("\n2) 降雨汇总（24h）")
print(f"  {M.rainfall_summary(S, 24)}")

print("\n3) 孔隙水压力")
for code in ("S-01", "S-03"):
    print(f"  {code}: {M.pore_pressure(S, code, 12)}")

print("\n4) 邻点对比（判断整体滑动 vs 局部）")
nc = M.neighbor_compare(S, "S-01", hours=6)
if nc.get("ok"):
    for p in nc["peers"]:
        print(f"    {p['site']:<6} {p['name']:<16} {p['rate_mm_per_h']:>7} mm/h")
    print(f"  加速点位={nc['accelerating_sites']} | 是否同步={nc['synchronized']}")

print("\n5) 裂缝历史（7 天）")
for code in ("S-01", "S-04"):
    print(f"  {code}: {M.crack_history(S, code, 7)}")

print("\n6) 当前快照")
print(f"  {M.current_snapshot(S, 'S-01')}")

# ── 断言：模拟数据要能区分"危险"和"平稳"，否则工具没有信息量 ──
print()
print("=" * 70)
a = M.displacement_rate(S, "S-02", 12)
b = M.displacement_rate(S, "S-04", 12)
ok = a["rate_mm_per_h"] > b["rate_mm_per_h"]
print(f"[{'PASS' if ok else 'FAIL'}] 加速点位速率 > 平稳点位 "
      f"({a['rate_mm_per_h']} vs {b['rate_mm_per_h']})")
ok2 = a["trend"] == "加速" and b["trend"] != "加速"
print(f"[{'PASS' if ok2 else 'FAIL'}] 趋势判定能区分（{a['trend']} vs {b['trend']}）")
ok3 = nc.get("synchronized") is True
print(f"[{'PASS' if ok3 else 'FAIL'}] 邻点对比识别出同步加速")
r24 = M.rainfall_summary(S, 24)
ok4 = r24["cumulative_mm"] > 50
print(f"[{'PASS' if ok4 else 'FAIL'}] 24h 累计雨量达到强降雨量级（{r24['cumulative_mm']}mm）")
