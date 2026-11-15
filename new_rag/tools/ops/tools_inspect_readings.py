# -*- coding: utf-8 -*-
"""看一眼 MySQL 里 sensor_reading 的实际规模和增长情况。

目的：确认"设备已接入"这件事在数据上是什么状态 ——
如果数据只在启动时 seed 一次就不再增长，那读侧再完善，
数据也是"一张静态快照"，不是真实接入的样子。
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

sys.stdout.reconfigure(errors="replace")

from sqlalchemy import create_engine, text

from config import settings as CFG

url = getattr(CFG, "database_url", None) or getattr(CFG, "db_url", None)
print(f"  连接串: {str(url)[:70]}...")
e = create_engine(url, pool_pre_ping=True)
with e.connect() as c:
    n = c.execute(text("select count(*) from sensor_reading")).scalar()
    mn, mx = c.execute(text("select min(ts), max(ts) from sensor_reading")).one()
    print(f"  读数总行数 : {n}")
    print(f"  最早时间   : {mn}")
    print(f"  最新时间   : {mx}")
    print()
    print("  各指标行数：")
    for m, cnt in c.execute(text(
            "select metric, count(*) c from sensor_reading group by metric order by metric")).fetchall():
        print(f"    {m:<22} {cnt}")
    print()
    print("  各点位行数：")
    for sid, cnt in c.execute(text(
            "select site_id, count(*) c from sensor_reading group by site_id order by site_id")).fetchall():
        print(f"    site_id={sid:<4} {cnt}")
    print()
    print("  最近 3 条（看时间是不是停在过去）：")
    for r in c.execute(text(
            "select site_id, metric, value, unit, ts from sensor_reading "
            "order by ts desc limit 3")).fetchall():
        print(f"    {r[0]} {r[1]:<20} {r[2]:<10} {r[3]:<6} {r[4]}")
