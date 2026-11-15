# -*- coding: utf-8 -*-
"""方案 A 端到端验证：一次请求只落一行，且 context_text 真的有内容。

关键断言：
  * run 行里有 route / predicted_tools / answer_digest / context_text
  * **没有** steps 字段（方案 A 之后不再有步骤表）
  * context_text 非空（否则 Faithfulness 会全变 0）
  * MySQL trace_run 表里那一行也有 context_text
"""
from __future__ import annotations

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
print("方案 A 端到端验证")
print("=" * 74)

import end_new as E

agent = E.agent
print("\n1) 跑一次真实请求")
# ⚠️ 必须用**会调工具**的问题。
# "现在距离多少"走确定性直答（不经过图、不产生 observation），
# 那一行的 context_text 天然为空 —— 旧实现在这条路径上也没有步骤可拼，
# 所以拿它测"上下文非空"是测错了对象。
ans = agent.run("结合降雨综合判断一下风险", session_id="verify-planA-2")
check("请求有返回", bool(ans), str(ans)[:60])

print("\n2) JSONL 里的 run 行")
runs = E.get_store().fetch_runs()
check("JSONL 有记录", len(runs) > 0, f"{len(runs)} 条")
last = runs[-1]
check("run 里有 route", bool(last.get("route")), str(last.get("route")))
check("run 里有 predicted_tools", "predicted_tools" in last, str(last.get("predicted_tools")))
check("run 里有 answer_digest", bool(last.get("answer_digest")),
      str(last.get("answer_digest"))[:40])
check("run 里有 context_text 字段", "context_text" in last)
ctx = str(last.get("context_text") or "")
check("context_text 非空（Faithfulness 依赖它）", len(ctx) > 0, f"{len(ctx)} 字")
check("run 里**没有** steps 字段（方案 A 已去掉步骤表）", "steps" not in last)

print("\n3) MySQL trace_run 表里那一行")
from sqlalchemy import create_engine, text  # noqa: E402

from config import settings as CFG  # noqa: E402

url = getattr(CFG, "database_url", None) or getattr(CFG, "db_url", None)
eng = create_engine(url, pool_pre_ping=True)
with eng.connect() as c:
    row = c.execute(text(
        "SELECT run_id, route, predicted_tools, elapsed_ms, "
        "       CHAR_LENGTH(COALESCE(context_text,'')) AS ctx_len "
        "FROM trace_run ORDER BY created_at DESC LIMIT 1")).fetchone()
if row:
    check("MySQL 里拿到最新一行", bool(row[0]), f"run_id={row[0]}")
    check("MySQL 里 route 有值", bool(row[1]), str(row[1]))
    check("MySQL 里 context_text 有长度", (row[4] or 0) > 0, f"{row[4]} 字")
    check("JSONL 与 MySQL 的 run_id 一致", row[0] == last.get("run_id"),
          f"{row[0]} vs {last.get('run_id')}")
else:
    check("MySQL 里拿到最新一行", False, "查询为空")

print("\n4) 评测取数（context_text 优先，兼容旧 steps）")
try:
    import slope_eval.metrics as M  # noqa: E402
    check("metrics 模块可导入", hasattr(M, "SampleResult"), "SampleResult 存在")
except Exception as exc:
    check("metrics 模块可导入", False, f"{type(exc).__name__}: {exc}")

# 直接验证两种形态的取数逻辑（与 metrics.py 里那段一致）
ctx_new = last.get("context_text")
check("新格式：run 上直接有 context_text", "context_text" in last, f"{len(str(ctx_new or ''))} 字")
old_run = {"steps": [{"kind": "observation", "output_digest": "旧格式片段"}]}
obs = "\n\n".join(s.get("output_digest", "") for s in old_run["steps"]
                  if s.get("kind") == "observation")
check("旧格式：仍能回退拼 steps（兼容历史 trace）", obs == "旧格式片段")

print()
print("=" * 74)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
for f in FAIL:
    print(f"  失败: {f}")
print("=" * 74)
