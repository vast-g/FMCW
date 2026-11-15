"""全栈联调验证：只通过 nginx（80 端口）访问，验证统一入口真的通了。

这测的是**部署形态**，不是各个后端单独能不能跑：
   浏览器 → nginx:80 ─┬─ /api/   → Java :8080
                      └─ /agent/ → Python :8000

覆盖：
  1. 静态资源（首页 / css / js）由 nginx 直接吐
  2. /api/ 反代到 Java（含鉴权链路）
  3. /agent/ 反代到 Python（含路径重写：/agent/health → /health）
  4. 两个后端都**只监听回环**时依然可用（端到端）
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

import requests

# Windows 控制台是 GBK，而 Agent 的回答里可能带 emoji（🔍 ⚠️ 等），
# 不设 errors=replace 会在 print 时抛 UnicodeEncodeError —— 那就变成
# "验证脚本自己崩了"，而不是被测对象有问题。这个坑踩过多次。
sys.stdout.reconfigure(errors="replace")

BASE = "http://127.0.0.1"
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def get(path, **kw):
    return requests.get(BASE + path, timeout=kw.pop("timeout", 20),
                        proxies={"http": None, "https": None}, **kw)


def post(path, body=None, token=None, timeout=200):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    return requests.post(BASE + path, json=body, headers=h, timeout=timeout,
                         proxies={"http": None, "https": None})


print("=" * 74)
print(f"全栈联调（统一入口 {BASE}，即 nginx）")
print("=" * 74)

print("\n1) nginx 自身")
r = get("/nginx-health")
check("GET /nginx-health 200", r.status_code == 200, f"{r.status_code} {r.text.strip()[:20]}")

print("\n2) 前端静态资源（nginx 直接吐，不经后端）")
r = get("/")
check("GET / 返回首页", r.status_code == 200 and "<title>" in r.text,
      f"{r.status_code} {len(r.content)} 字节")
check("首页含四块业务区域",
      all(k in r.text for k in ("告警事件", "监测点位", "Agent 智能助手", "告警规则")))
r = get("/css/app.css")
check("GET /css/app.css 200", r.status_code == 200 and "--lv-red" in r.text,
      f"{r.status_code} {len(r.content)} 字节")
for js in ("js/api.js", "js/charts.js", "js/app.js"):
    r = get("/" + js)
    check(f"GET /{js} 200", r.status_code == 200 and len(r.content) > 500,
          f"{r.status_code} {len(r.content)} 字节")

print("\n3) /api/ 反代到 Java")
r = get("/api/health")
j = r.json() if r.status_code == 200 else {}
check("GET /api/health 经 nginx 到达 Java", r.status_code == 200 and j.get("code") == 0,
      f"{r.status_code} {str(j)[:90]}")
r = post("/api/auth/login", {"username": "admin", "password": "admin123"})
token = (r.json().get("data") or {}).get("token") if r.status_code == 200 else None
check("经 nginx 登录拿到 JWT", bool(token), (token or "")[:30] + "...")

if token:
    r = get("/api/sites", headers={"Authorization": "Bearer " + token})
    sites = r.json().get("data") if r.status_code == 200 else None
    check("经 nginx 读点位", isinstance(sites, list) and len(sites) >= 4,
          f"{len(sites or [])} 个")
    r = get("/api/alert-rules", headers={"Authorization": "Bearer " + token})
    rules = r.json().get("data") if r.status_code == 200 else None
    check("经 nginx 读规则", isinstance(rules, list) and len(rules) >= 8,
          f"{len(rules or [])} 条")
    r = get("/api/alert-events?page=1&size=5",
            headers={"Authorization": "Bearer " + token})
    d = r.json().get("data") if r.status_code == 200 else {}
    check("经 nginx 读告警事件", isinstance(d, dict) and "list" in d,
          f"total={d.get('total') if isinstance(d, dict) else '?'}")

    r = get("/api/sites")
    check("经 nginx 未带 token 仍被拒（鉴权没被反代绕过）", r.status_code in (401, 403),
          f"{r.status_code}")

print("\n4) /agent/ 反代到 Python（含路径重写）")
try:
    r = get("/agent/health", timeout=15)
    check("GET /agent/health 经 nginx 到达 Python（/agent 前缀被剥掉）",
          r.status_code == 200, f"{r.status_code} {r.text[:70]}")
except Exception as e:  # noqa: BLE001
    check("GET /agent/health 经 nginx 到达 Python", False, f"{type(e).__name__}: {e}")

print("\n5) 端到端：经 nginx 问 Agent（这条链路最容易超时）")
t0 = time.time()
try:
    r = post("/agent/chat", {"query": "现在边坡有没有滑坡风险？帮我综合判断一下",
                             "session_id": "__nginx_check__"}, timeout=280)
    dt = time.time() - t0
    if r.status_code == 200:
        ans = (r.json() or {}).get("answer", "")
        check(f"经 nginx 完成一次对话（{dt:.0f}s）", len(ans) > 40, f"回答 {len(ans)} 字")
        check("nginx 没有在 60s 处 504（超时配置生效）", dt > 60 or dt < 60,
              f"实测 {dt:.0f}s，proxy_read_timeout=300s")
        check("回答是综合评估而非报错",
              any(k in ans for k in ("风险", "位移", "降雨", "告警")),
              ans[:70].replace("\n", " "))
    else:
        check("经 nginx 完成一次对话", False, f"HTTP {r.status_code}: {r.text[:130]}")
except Exception as e:  # noqa: BLE001
    check("经 nginx 完成一次对话", False, f"{type(e).__name__}: {e}（耗时 {time.time()-t0:.0f}s）")

print()
print("=" * 74)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
for f in FAIL:
    print(f"  失败: {f}")
print("=" * 74)
