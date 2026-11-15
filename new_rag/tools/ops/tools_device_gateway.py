# -*- coding: utf-8 -*-
"""现场数据网关模拟器 —— 持续往 sensor_reading 追加读数。

## 它解决什么问题

改造前，`sensor_reading` 里的 1440 行是**启动时一次性 seed 出来的静态快照**：
时间范围固定在过去 72 小时，之后再也不增长。这带来两个问题：

1. **读侧看不出来"是不是活的"**。Java 的 `/api/readings/latest` 和 Python 的
   `current_snapshot()` 读到的永远是同一批数据，分不清"设备在正常上报"还是
   "设备早就掉线了，只是库里还留着旧数据"。
2. **告警规则引擎没有增量可判**。规则要的是"最近 1 小时速率"，静态快照里
   永远只有 seed 那一刻算出来的结论。

真实部署里，这一层是**现场网关按采样周期上报**（雷达/雨量计/孔压计 → 网关 → 入库）。
本脚本就是那个网关的替身：按固定周期追加新读数，让下游读侧面对的是**持续增长的数据**。

## 与真实网关的边界

* 真实网关是**设备主动上行**；这里是定时器模拟，采样周期与量纲对齐真实设备。
* 真实网关会带设备自身的时间戳与质量标记；这里同样写 `ts` 与 `quality`，
  字段语义与实体定义一致（ok / suspect / bad）。
* **写的是同一张表、同一套指标名**，所以 Java 和 Python 不需要为"模拟"做任何特殊处理 ——
  这正是要验证的点：读侧面对的是数据，不关心数据从哪来。

## 用法

    python tools_device_gateway.py --once            # 只追加一轮（测试用）
    python tools_device_gateway.py --interval 60     # 每 60 秒追加一轮（常驻）
    python tools_device_gateway.py --rounds 5        # 追加 5 轮后退出
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
import argparse
import math
import random
import sys
import time
from datetime import datetime, timedelta

sys.stdout.reconfigure(errors="replace")

from sqlalchemy import create_engine, select, func, text

from config import settings as CFG

# 与 slope_monitor 保持一致的指标定义（含单位）—— 读侧靠这套名字对齐，
# 两处不一致会让 Java 查不到 Python 写的行。
METRICS: dict[str, str] = {
    "displacement_mm": "mm",
    "rainfall_mm": "mm",
    "pore_pressure_kpa": "kPa",
    "crack_width_mm": "mm",
    "temperature_c": "℃",
}

# 每个指标的"日常波动幅度"，用于生成看起来合理的新读数
JITTER = {
    "displacement_mm": 0.02,
    "rainfall_mm": 0.4,
    "pore_pressure_kpa": 0.15,
    "crack_width_mm": 0.004,
    "temperature_c": 0.2,
}


def _engine():
    url = getattr(CFG, "database_url", None) or getattr(CFG, "db_url", None)
    return create_engine(url, pool_pre_ping=True, pool_recycle=1800)


def _last_values(conn) -> dict[tuple[int, str], float]:
    """取每个 (点位, 指标) 的当前值作为下一轮的基准。

    ★ 这一点很关键：新读数必须在**上一条的基础上**小幅漂移，
    而不是重新随机。否则位移会在两个值之间来回跳，
    规则引擎的"速率"会算出巨大的正负交替值，触发一堆假告警。
    """
    rows = conn.execute(text(
        "SELECT r.site_id, r.metric, r.value FROM sensor_reading r "
        "JOIN (SELECT site_id, metric, MAX(ts) mts FROM sensor_reading "
        "      GROUP BY site_id, metric) m "
        "  ON r.site_id=m.site_id AND r.metric=m.metric AND r.ts=m.mts"
    )).fetchall()
    return {(int(r[0]), str(r[1])): float(r[2]) for r in rows}


def _sites(conn) -> list[int]:
    return [int(r[0]) for r in conn.execute(
        text("SELECT id FROM site ORDER BY id")).fetchall()]


def push_round(engine, *, verbose: bool = True) -> int:
    """追加一轮读数（每个点位每个指标各一条）。返回写入行数。"""
    now = datetime.now().replace(microsecond=0)
    written = 0
    with engine.begin() as conn:
        sites = _sites(conn)
        if not sites:
            print("  ✗ 库里没有点位（site 表为空），先初始化监测数据再跑网关")
            return 0
        base = _last_values(conn)

        for sid in sites:
            for metric, unit in METRICS.items():
                prev = base.get((sid, metric))
                if prev is None:
                    prev = 0.0 if metric != "temperature_c" else 20.0
                # 带小幅漂移 + 偶发跳变（模拟真实设备：大部分时候平稳，偶尔有扰动）
                drift = random.gauss(0, JITTER.get(metric, 0.01))
                if random.random() < 0.03:          # 3% 概率出现一次明显跳变
                    drift *= 8
                val = max(0.0, prev + drift)
                # 值域保护：别让温度漂到 -50 或降雨量漂到几万
                if metric == "temperature_c":
                    val = min(45.0, max(-20.0, val))
                elif metric == "rainfall_mm":
                    val = min(60.0, val)
                # 质量标记：跳变过大的点标 suspect，下游判异常时先看这个字段
                quality = "suspect" if abs(drift) > 5 * JITTER.get(metric, 0.01) else "ok"
                conn.execute(text(
                    "INSERT INTO sensor_reading (site_id, metric, value, unit, ts, quality) "
                    "VALUES (:sid, :metric, :val, :unit, :ts, :q)"),
                    {"sid": sid, "metric": metric, "val": round(val, 3),
                     "unit": unit, "ts": now, "q": quality})
                written += 1
    if verbose:
        print(f"  ✓ {now:%Y-%m-%d %H:%M:%S}  写入 {written} 条"
              f"（{len(sites)} 点位 × {len(METRICS)} 指标）")
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description="现场数据网关模拟器（往 sensor_reading 追加读数）")
    ap.add_argument("--once", action="store_true", help="只追加一轮")
    ap.add_argument("--rounds", type=int, default=0, help="追加 N 轮后退出（0=不限）")
    ap.add_argument("--interval", type=float, default=60.0, help="每轮间隔秒数（默认 60）")
    args = ap.parse_args()

    if args.once:
        args.rounds = 1
        args.interval = 0

    engine = _engine()
    print("=" * 72)
    print("现场数据网关模拟器")
    print("=" * 72)
    print(f"  目标表   : sensor_reading")
    print(f"  采样周期 : {args.interval or 0:.0f}s")
    print(f"  轮数     : {'不限' if args.rounds == 0 else args.rounds}")
    print()

    n = 0
    try:
        while True:
            push_round(engine)
            n += 1
            if args.rounds and n >= args.rounds:
                break
            time.sleep(max(0.0, args.interval))
    except KeyboardInterrupt:
        print("\n  收到中断，停止上报")

    print(f"\n  共追加 {n} 轮")
    with engine.connect() as conn:
        total = conn.execute(text("select count(*) from sensor_reading")).scalar()
        mx = conn.execute(text("select max(ts) from sensor_reading")).scalar()
    print(f"  当前总行数: {total}   最新时间: {mx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
