"""验证 RAG 修复的实质收益：知识库检索是否真的可用。

修复前：API 模式下 rag 恒为 None，tool_search_kb 永远返回"知识库未加载" ——
        **但工具照样被"调用"**，所以 Tool F1 这类指标看不出来，只有看回答内容才发现。
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

sys.stdout.reconfigure(errors="replace")
P = {"http": None, "https": None}
BASE = "http://127.0.0.1"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def chat(q, sid):
    t0 = time.time()
    r = requests.post(BASE + "/agent/chat", json={"query": q, "session_id": sid},
                      timeout=280, proxies=P)
    dt = time.time() - t0
    if r.status_code != 200:
        return None, dt, f"HTTP {r.status_code}: {r.text[:150]}"
    return (r.json() or {}).get("answer", ""), dt, ""


print("=" * 74)
print("RAG 可用性验证（经 nginx）")
print("=" * 74)

print("\n0) 健康检查")
j = requests.get(BASE + "/agent/health", timeout=8, proxies=P).json()
check("status == ok", j.get("status") == "ok", str(j.get("status")))
check("rag_state == ready", j.get("rag_state") == "ready", str(j.get("rag_state")))
check("rag_ready == true", j.get("rag_ready") is True, str(j.get("rag_ready")))

print("\n1) 知识类问题（应命中知识库）")
ans, dt, err = chat("超声波测距传感器读数异常跳变，一般是什么原因？怎么排查？", "kb-check-1")
if err:
    check("知识类问题返回", False, err)
else:
    check(f"知识类问题有回答（{dt:.0f}s）", len(ans) > 60, f"{len(ans)} 字")
    bad = "知识库未加载" in ans
    check("回答里没有出现「知识库未加载」", not bad,
          "❌ 仍出现，说明 RAG 没接上" if bad else "已修复")
    # 知识类回答应该有成段的技术内容，而不是"我不知道"
    check("回答有实质技术内容（>200 字）", len(ans) > 200, f"{len(ans)} 字")
    print()
    print("  ── 回答节选 ──")
    for line in ans[:400].split("\n")[:8]:
        print("   ", line)

print()
print("=" * 74)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
for f in FAIL:
    print(f"  失败: {f}")
print("=" * 74)
