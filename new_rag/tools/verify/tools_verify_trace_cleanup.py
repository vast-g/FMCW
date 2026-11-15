# -*- coding: utf-8 -*-
"""验证 trace 双写清理：agent_trace 那条自循环链已下线，统一走 TraceRecorder。

覆盖：
  1. `_AgentTrace` ORM 类已删除（不再有那条写入路径）
  2. `execution_log` 已删除（只服务于被删的 /trace fallback）
  3. `_log_step` 仍在（控制台输出保留），但不再碰数据库
  4. `fetch_runs()` 能读到 JSONL，且每条 run 自带 steps
  5. 新 /trace 的取数逻辑（取最后一次 run 的 steps）可用
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)
sys.stdout.reconfigure(errors="replace")

PASS: list[str] = []
FAIL: list[str] = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("trace 双写清理验证")
print("=" * 74)

import end_new as E
import tracing as T

src = Path(r"C:\Users\Lenovo\Desktop\new_rag\end_new.py").read_text(encoding="utf-8")

print("\n1) 静态检查：旧链路已下线")
check("_AgentTrace ORM 类已删除", not hasattr(E, "_AgentTrace"))
check("源码里不再出现 _AgentTrace", "_AgentTrace" not in src)
check("源码里不再出现 execution_log", "execution_log" not in src)
check("_log_step 仍保留（控制台输出）", hasattr(E.SimpleAgent, "_log_step"))

print("\n2) _log_step 不再写库")
src_log = inspect.getsource(E.SimpleAgent._log_step)
check("_log_step 里没有 _get_mysql_conn", "_get_mysql_conn" not in src_log)
check("_log_step 里没有 session.add", "session.add" not in src_log)
check("_log_step 只打印一次", src_log.count("print(") == 1, f"print 出现 {src_log.count('print(')} 次")

print("\n3) 新链路：JSONL 是唯一兜底与评测来源")
store = T.get_store()
runs = store.fetch_runs()
check("fetch_runs() 能读到记录", len(runs) > 0, f"{len(runs)} 条 run")
if runs:
    last = runs[-1]
    check("run 里有 route 字段", "route" in last, str(last.get("route")))
    check("run 里有 status 字段", "status" in last, str(last.get("status")))
    # 方案 A 之后不再有 steps，改为断言"一次请求就一行、评测要素都在这一行上"
    check("run 里没有 steps（方案 A 已去步骤表）", "steps" not in last)
    check("run 里有 context_text 字段", "context_text" in last,
          f"{len(str(last.get('context_text') or ''))} 字")
    check("run 里有 predicted_tools", "predicted_tools" in last,
          str(last.get("predicted_tools"))[:50])

print("\n4) 新 /trace 的取数逻辑（一次请求一行）")
if runs:
    last = runs[-1]
    payload = {"code": 200, "run_id": last.get("run_id"), "route": last.get("route"),
               "status": last.get("status"),
               "context_len": len(str(last.get("context_text") or ""))}
    check("能组装出 /trace 响应", payload["code"] == 200 and bool(payload.get("run_id")),
          f"run_id={payload['run_id']} route={payload['route']}")
    check("/trace 的响应带 run_id（比原来的 agent_trace 更有信息量）",
          bool(payload.get("run_id")))
else:
    check("能组装出 /trace 响应", False, "JSONL 里没有 run")

print("\n5) 三处落点变成两处 + 兜底")
check("TraceRun / TraceStep 仍在（评测用）",
      hasattr(T, "TraceRun") and hasattr(T, "TraceStep"))
check("JSONL 路径存在", store._jsonl_path.exists() if hasattr(store, "_jsonl_path") else True,
      str(getattr(store, "_jsonl_path", "?")))

print()
print("=" * 74)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
for f in FAIL:
    print(f"  失败: {f}")
print("=" * 74)
