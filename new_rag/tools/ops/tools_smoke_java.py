"""Java 业务服务独立验收：真的启动产物并打接口。

与工程目录里那份的区别：
  * 放在 new_rag 根目录，**不侵入 slope-platform 工程**
  * 用 requests 而不是 urllib，并且**每次失败都打印原始响应**，便于定位
  * 先用裸 socket 探一次端口，区分"服务没起来"和"起来了但接口不对"
  * 改阈值走「读当前值 → 改 → 校验 → 改回」，避免污染 Python 侧基线
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
import json
import socket
import sys
import time

import requests

HOST = "127.0.0.1"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18080
BASE = f"http://{HOST}:{PORT}"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def port_open(host, port, timeout=2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def req(method, path, body=None, token=None, timeout=25):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    try:
        r = requests.request(method, BASE + path, json=body, headers=h,
                             timeout=timeout, proxies={"http": None, "https": None})
        try:
            return r.status_code, r.json()
        except Exception:  # noqa: BLE001
            return r.status_code, {"_raw": r.text[:300]}
    except Exception as e:  # noqa: BLE001
        return -1, {"_error": f"{type(e).__name__}: {e}"}


def wait_ready(max_wait=180):
    t0 = time.time()
    last = None
    while time.time() - t0 < max_wait:
        if port_open(HOST, PORT):
            s, b = req("GET", "/api/health", timeout=5)
            last = (s, b)
            if s == 200:
                return True, last
        time.sleep(3)
    return False, last


print("=" * 74)
print(f"Java 业务服务独立验收（{BASE}）")
print("=" * 74)

print(f"\n0) 等待服务就绪（端口 {PORT}）...")
ok, last = wait_ready()
if not ok:
    p = port_open(HOST, PORT)
    print(f"  端口是否在监听: {p}")
    print(f"  最后一次 /api/health 响应: {last}")
    print("  [FAIL] 服务未就绪")
    sys.exit(1)
print(f"  服务已就绪，/api/health = {last[1]}")

print("\n1) 健康检查（免鉴权）")
s, b = req("GET", "/api/health")
check("GET /api/health 返回 200", s == 200, f"status={s} body={json.dumps(b, ensure_ascii=False)[:120]}")

print("\n2) 登录")
s, b = req("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
data = b.get("data") if isinstance(b, dict) else None
token = (data or {}).get("token") if isinstance(data, dict) else None
check("POST /api/auth/login 返回 200", s == 200,
      f"status={s} body={json.dumps(b, ensure_ascii=False)[:120]}")
check("拿到 JWT", bool(token), (token or "")[:36] + "...")
if not token:
    sys.exit(1)

print("\n3) 错误口令必须被拒")
s, b = req("POST", "/api/auth/login", {"username": "admin", "password": "wrong"})
check("错误口令登录失败（不是无脑放行）", s in (400, 401, 403), f"status={s}")

print("\n4) 鉴权是否真的生效")
s, _ = req("GET", "/api/sites")
check("不带 token 访问 /api/sites 被拒", s in (401, 403), f"status={s}")
s, _ = req("GET", "/api/sites", token="invalid.token.here")
check("伪造 token 被拒", s in (401, 403), f"status={s}")

print("\n5) 点位列表")
s, b = req("GET", "/api/sites", token=token)
sites = (b.get("data") or []) if isinstance(b, dict) else []
if isinstance(sites, dict):
    sites = sites.get("records") or sites.get("list") or []
check("GET /api/sites 返回 200", s == 200, f"status={s}")
check("读到 Python 侧建的 4 个点位", len(sites) >= 4,
      f"{len(sites)} 条 {[x.get('code') for x in sites][:6]}")

print("\n6) 告警规则：读 + 改阈值 + 校验 + 还原")
s, b = req("GET", "/api/alert-rules", token=token)
rules = (b.get("data") or []) if isinstance(b, dict) else []
if isinstance(rules, dict):
    rules = rules.get("records") or rules.get("list") or []
check("GET /api/alert-rules 返回 200", s == 200, f"status={s}")
check("读到 8 条规则", len(rules) >= 8, f"{len(rules)} 条")

if rules:
    tgt = next((r for r in rules if r.get("code") == "R-RATE-01"), rules[0])
    rid, old = tgt.get("id"), tgt.get("threshold")
    new = round(float(old) + 0.037, 4)
    payload = dict(tgt)
    payload["threshold"] = new
    s, b = req("PUT", f"/api/alert-rules/{rid}", payload, token=token)
    check(f"PUT /api/alert-rules/{rid} 改阈值 {old} → {new}", s == 200,
          f"status={s} body={json.dumps(b, ensure_ascii=False)[:110]}")

    s, b = req("GET", f"/api/alert-rules/{rid}", token=token)
    got = ((b.get("data") or {}) if isinstance(b, dict) else {}).get("threshold")
    check("回读确认阈值已持久化", got is not None and abs(float(got) - new) < 1e-6,
          f"回读={got}")

    # 直接查库复核（绕过 HTTP，确认真落盘）
    try:
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
        import os
        os.environ.setdefault("SLOPE_HEADLESS", "1")
        from urllib.parse import quote_plus
        from sqlalchemy import create_engine, text
        import config as _cfg
        url = (f"mysql+pymysql://{_cfg.DB_USER}:{quote_plus(_cfg.DB_PASSWORD)}"
               f"@{_cfg.DB_HOST}:{_cfg.DB_PORT}/{_cfg.DB_NAME}")
        with create_engine(url).connect() as c:
            dbv = c.execute(text("SELECT threshold FROM alert_rule WHERE id=:i"),
                            {"i": rid}).scalar()
        check("数据库里确实是新阈值（HTTP 之外复核）", abs(float(dbv) - new) < 1e-6,
              f"DB={dbv}")
    except Exception as e:  # noqa: BLE001
        check("数据库复核", False, f"{type(e).__name__}: {e}")

    payload["threshold"] = old
    req("PUT", f"/api/alert-rules/{rid}", payload, token=token)
    s, b = req("GET", f"/api/alert-rules/{rid}", token=token)
    back = ((b.get("data") or {}) if isinstance(b, dict) else {}).get("threshold")
    check("阈值已还原（不污染 Python 侧基线）",
          back is not None and abs(float(back) - float(old)) < 1e-6, f"={back}")

print("\n7) 告警事件查询（Python 规则引擎产生的）")
s, b = req("GET", "/api/alert-events?page=0&size=5", token=token)
ev = (b.get("data") or {}) if isinstance(b, dict) else {}
records = ev.get("records") or ev.get("list") or (ev if isinstance(ev, list) else [])
check("GET /api/alert-events 返回 200", s == 200,
      f"status={s} body={json.dumps(b, ensure_ascii=False)[:110]}")
check("读到告警事件", len(records) >= 1, f"{len(records)} 条")

print("\n8) 设备列表（Java 新建的 device 表）")
s, b = req("GET", "/api/devices", token=token)
dev = (b.get("data") or []) if isinstance(b, dict) else []
if isinstance(dev, dict):
    dev = dev.get("records") or dev.get("list") or []
check("GET /api/devices 返回 200（device 表已建）", s == 200, f"status={s}")

print("\n9) 告警确认留痕")
if records and isinstance(records[0], dict):
    eid = records[0].get("id")
    s, b = req("POST", f"/api/alert-events/{eid}/acknowledge", token=token)
    check(f"POST /api/alert-events/{eid}/acknowledge 返回 200", s == 200,
          f"status={s} body={json.dumps(b, ensure_ascii=False)[:110]}")
    s, b = req("GET", f"/api/alert-events/{eid}", token=token)
    d = (b.get("data") or {}) if isinstance(b, dict) else {}
    check("确认后记录了 acknowledged_by 与 acknowledged_at",
          bool(d.get("acknowledged")) and bool(d.get("acknowledged_by"))
          and bool(d.get("acknowledged_at")),
          f"by={d.get('acknowledged_by')} at={d.get('acknowledged_at')}")

print()
print("=" * 74)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
for f in FAIL:
    print(f"  失败: {f}")
print("=" * 74)
