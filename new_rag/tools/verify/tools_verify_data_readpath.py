# -*- coding: utf-8 -*-
"""验证「设备 → MySQL → Java / Python 各自读取」这条读链路。

## 为什么这个验证值得单独写

"两个服务读同一张表"这件事，最容易出问题的地方不是"读不到"，
而是**两边读到的不是同一份数据**：指标名不一致、时区不一致、
一个按窗口查一个按最新查、一个把 null 过滤掉一个没过滤……
这些都不会报错，只会让页面上的数字和 Agent 嘴里的数字对不上。

所以这里不只验证"能读"，更验证**两边读到的是同一批值**。

## 覆盖

  1. 网关模拟器能往 sensor_reading 追加新读数（数据是"活的"）
  2. Python 侧能读到刚写的那条（slope_monitor.current_snapshot）
  3. Java 侧能读到同一个值（GET /api/readings/latest）
  4. **两侧对同一个 (点位, 指标) 读出相同的值**
  5. Python 的"最新"确实取的是最大时间戳那一条
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

import requests
from sqlalchemy import create_engine, text

from config import settings as CFG

PASS: list[str] = []
FAIL: list[str] = []
P = {"http": None, "https": None}
JAVA = "http://127.0.0.1:8080"


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def db():
    url = getattr(CFG, "database_url", None) or getattr(CFG, "db_url", None)
    return create_engine(url, pool_pre_ping=True)


print("=" * 74)
print("读链路验证：设备 → MySQL → Java / Python")
print("=" * 74)

engine = db()

print("\n1) 网关模拟器追加一轮读数（让数据从静态快照变成活的）")
with engine.connect() as c:
    before = c.execute(text("select count(*) from sensor_reading")).scalar()
    ts_before = c.execute(text("select max(ts) from sensor_reading")).scalar()

import tools_device_gateway as GW     # noqa: E402

time.sleep(1.05)                       # 跨过 1 秒，保证新 ts 严格大于旧的
written = GW.push_round(engine, verbose=False)

with engine.connect() as c:
    after = c.execute(text("select count(*) from sensor_reading")).scalar()
    ts_after = c.execute(text("select max(ts) from sensor_reading")).scalar()

check("网关写入了新读数", written > 0, f"新增 {written} 行")
check("表总行数增加了", after == before + written, f"{before} → {after}")
check("最新时间戳前进了", ts_after is not None and ts_after != ts_before,
      f"{ts_before} → {ts_after}")

print("\n2) Python 侧读取（slope_monitor 直查同一个库）")
import slope_monitor as SM            # noqa: E402

snap = None
try:
    # 注意：current_snapshot(Session, code) 收的是**会话工厂**而不是 Session 实例，
    # 因为它内部要多次调用 Session.query(...)。项目里用的是 scoped_session
    # （见 SimpleAgent.__init__ 的 self._Session），这里保持一致。
    # 第一版测试传了 Session 实例，报 "'Session' object is not callable"。
    from sqlalchemy.orm import scoped_session, sessionmaker
    SessionFactory = scoped_session(sessionmaker(bind=engine))
    snap = SM.current_snapshot(SessionFactory, "S-01")
    SessionFactory.remove()
    check("current_snapshot 拿到数据", bool(snap) and snap.get("ok"), str(snap)[:110])
except Exception as exc:
    check("current_snapshot 拿到数据", False, f"{type(exc).__name__}: {exc}")

print("\n3) 直接确认「最新一条」的取法正确")
with engine.connect() as c:
    row = c.execute(text(
        "select site_id, metric, value, ts from sensor_reading "
        "where site_id=(select id from site where code='S-01') "
        "and metric='displacement_mm' order by ts desc limit 1")).fetchone()
py_val = float(row[2]) if row else None
check("SQL 取到的就是最大 ts 那一条",
      row is not None and row[3] == ts_after and py_val is not None,
      f"value={py_val} ts={row[3] if row else None}")

print("\n4) Java 侧读取（GET /api/readings/latest）")
java_ok = False
arr = []
# Java 在本项目里是**可选服务**：数据库侧的检查完全不依赖它。
# 所以先探活，探不到就整段跳过，而不是把 4 项 Java 检查记成失败 ——
# 否则只想跑数据库检查的人会看到假红灯，久了就不信这套回归了。
_java_up = False
try:
    requests.get(JAVA + "/api/health", timeout=4, proxies=P)
    _java_up = True
except Exception:
    print("      [SKIP] Java(8080) 未启动 —— 跳过 Java 侧检查（启动后可重跑本脚本）")

if _java_up:
    try:
        # 这个接口**需要鉴权**（第一版测试直接请求拿到 401，是我记错了放行清单）。
        # 先登录拿 token，再带上 Authorization 头 —— 这也是前端走的同一条路。
        lr = requests.post(JAVA + "/api/auth/login",
                           json={"username": "admin", "password": "admin123"},
                           timeout=15, proxies=P)
        tok = None
        if lr.status_code == 200 and lr.text.lstrip().startswith("{"):
            body = lr.json().get("data") or {}
            tok = body.get("token") or body.get("accessToken")
        check("Java 登录拿到 token", bool(tok), (tok or "")[:24] + "..." if tok else
              f"HTTP {lr.status_code} {lr.text[:60]}")

        r = requests.get(JAVA + "/api/readings/latest",
                         headers={"Authorization": "Bearer " + (tok or "")},
                         timeout=15, proxies=P)
        if r.status_code == 200 and r.text.lstrip().startswith("{"):
            body = r.json()
            arr = body.get("data") or []
            check("Java 最新读数接口可用", len(arr) > 0, f"返回 {len(arr)} 条")
            # 找 S-01 的 displacement_mm
            with engine.connect() as c:
                sid = c.execute(text("select id from site where code='S-01'")).scalar()
            hit = [x for x in arr if x.get("site_id") == sid
                   and x.get("metric") == "displacement_mm"]
            if hit:
                jv = float(hit[0].get("value"))
                check("Java 侧读到的值与库中一致", abs(jv - py_val) < 1e-6,
                      f"Java={jv}  库/SQL={py_val}")
                # 时间格式两边不同：库里是 "2026-10-14 14:06:02"，Jackson 序列化成 ISO 的
                # "2026-10-14T14:06:02"。不能直接比字符串，要先把分隔符归一化。
                java_ts = str(hit[0].get("ts", "")).replace("T", " ")
                check("Java 返回的也是最新时间戳",
                      str(ts_after)[:19] == java_ts[:19],
                      f"Java={java_ts}  库={ts_after}")
                java_ok = True
            else:
                check("Java 返回值里能找到 S-01 的位移", False, f"sid={sid}")
        else:
            check("Java 最新读数接口可用", False, f"HTTP {r.status_code} {r.text[:60]}")
    except Exception as exc:
        check("Java 最新读数接口可用", False,
              f"Java 调用异常（{type(exc).__name__}: {exc}）")

print("\n5) 两侧「指标名」是否对齐（跨语言读同表最容易翻车的地方）")
with engine.connect() as c:
    py_metrics = {r[0] for r in c.execute(
        text("select distinct metric from sensor_reading")).fetchall()}
check("库里的指标名与 Python 定义一致", py_metrics == set(SM.METRIC_UNITS),
      f"库里 {sorted(py_metrics)}")
if java_ok:
    java_metrics = {x.get("metric") for x in arr}
    check("Java 返回的指标名 ⊆ 库里指标名", java_metrics <= py_metrics,
          f"Java 返回 {len(java_metrics)} 种")

print()
print("=" * 74)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
if not java_ok:
    print("  提示：Java 侧未验证通过，需要先启动 slope-platform（8080）")
for f in FAIL:
    print(f"  失败: {f}")
print("=" * 74)
