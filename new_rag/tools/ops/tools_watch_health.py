"""观察 Agent 健康检查的状态变化，验证 RAG 预热修复是否生效。

修复前：无头模式下 rag 恒为 None，health 恒为 degraded。
修复后：应该看到 starting →（RAG 加载完成）→ ok。
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
import sys
import time

import requests

sys.stdout.reconfigure(errors="replace")
P = {"http": None, "https": None}

print("观察 /agent/health（经 nginx 80 端口）")
print("-" * 60)
seen = []
for i in range(18):
    try:
        j = requests.get("http://127.0.0.1/agent/health", timeout=5, proxies=P).json()
        st, rs = j.get("status"), j.get("rag_state")
        if not seen or seen[-1] != st:
            print(f"  t+{i*5:>3}s  status={st:<9} rag_state={rs:<8} "
                  f"rag_ready={j.get('rag_ready')}  <-- 状态变化")
            seen.append(st)
        else:
            print(f"  t+{i*5:>3}s  status={st:<9} rag_state={rs:<8} rag_ready={j.get('rag_ready')}")
        if st == "ok":
            print()
            print("  ✅ RAG 已就绪，health 正确转为 ok")
            print(f"  完整响应: {json.dumps(j, ensure_ascii=False)}")
            break
        if st == "degraded":
            print()
            print("  ❌ RAG 加载失败（degraded），需要看 Agent 日志")
            break
    except Exception as e:  # noqa: BLE001
        print(f"  t+{i*5:>3}s  连接失败: {type(e).__name__}: {e}")
    time.sleep(5)
else:
    print()
    print(f"  超时未到 ok，观察到的状态序列: {seen}")

print("-" * 60)
print(f"状态序列: {' -> '.join(seen)}")
