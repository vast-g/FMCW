# -*- coding: utf-8 -*-
"""清理 trace 双写：删掉 agent_trace 这条"写进去没人读"的链。

## 清理前的状态（4 个落点）

    trace_run      一次请求一行   ← TraceRecorder.finish()      评测用
    trace_step     一步一行       ← rec.note_step()             评测用
    agent_trace    一步一行       ← _log_step()  ← **冗余**     只被 /trace 读
    agent_trace.jsonl  同 trace_run+steps                       评测真正读这个

其中 agent_trace 和 trace_step 内容高度重叠，而且 /trace 接口
**前端一次都没调用过** —— 等于一条自循环：写了没人看。

## 清理后（2 个落点 + 1 个兜底）

    trace_run / trace_step  ← TraceRecorder（结构化，评测与 SQL 分析用）
    agent_trace.jsonl       ← 同源兜底，评测直接读它（不依赖 DB 可用性）

## 具体动作

  1. _log_step 去掉 MySQL 写入，只保留控制台打印
  2. 删掉 self.execution_log（它只服务于被删的 /trace fallback）
  3. 删掉 _AgentTrace ORM 类
  4. /trace 改为读 JSONL（和评测同一数据源），不再查 agent_trace 表
  5. agent_trace 表**保留**（681 行历史数据不删），但不再增长
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(errors="replace")

TARGET = Path(r"C:\Users\Lenovo\Desktop\new_rag\end_new.py")
BACKUP = TARGET.with_suffix(f".py.bak_trace_{time.strftime('%Y%m%d_%H%M%S')}")
shutil.copy2(TARGET, BACKUP)
print(f"已备份 → {BACKUP.name}")

text = TARGET.read_text(encoding="utf-8")
orig_len = len(text)
applied: list[str] = []


def sub(old: str, new: str, label: str) -> None:
    global text
    n = text.count(old)
    if n != 1:
        print(f"  [!!] {label}: 命中 {n} 次（期望 1），跳过")
        return
    text = text.replace(old, new, 1)
    applied.append(label)
    print(f"  [OK] {label}")


# ══════════════════════════════════════════════════════════════════
# 1) _log_step：去掉 MySQL 写入
# ══════════════════════════════════════════════════════════════════
OLD1 = '''    def _log_step(self, step_type, content):
        """记录推理步骤（内存 + MySQL agent_trace）"""
        ts = time.strftime("%H:%M:%S")
        self.execution_log.append({"time": ts, "type": step_type, "content": content})
        if len(self.execution_log) > 200:
            self.execution_log = self.execution_log[-200:]
        print(f"[LOG][{ts}][{step_type}] {content[:80]}")
        # ORM 持久化写入 agent_trace
        try:
            session = self._get_mysql_conn()
            session.add(_AgentTrace(
                request_id=getattr(self, "_current_trace_id", ""),
                step=len(self.execution_log),
                action=step_type,
                tool_name=step_type if step_type in ("tool", "observation") else None,
                tool_output=str(content)[:1000],
                created_at=datetime.utcnow(),
            ))
            session.commit()
            session.close()
        except Exception:
            pass
'''

NEW1 = '''    def _log_step(self, step_type, content):
        """打印一条推理步骤到控制台。

        ## 改造说明：这里原来还往 MySQL `agent_trace` 表写一份

        那套写入和评测用的 `trace_step` **内容高度重叠**（都是"一步一行、
        带工具名和输出"），属于"新机制上线时旧机制没下线"造成的双写。
        更关键的是：读它的 `/trace` 接口**前端一次都没调用过** ——
        整条链是自循环，写了没人看。

        现在持久化统一走 `TraceRecorder`（trace_run + trace_step + JSONL），
        这里只保留控制台输出，方便本地调试时看实时步骤流。
        """
        print(f"[LOG][{time.strftime('%H:%M:%S')}][{step_type}] {str(content)[:80]}")
'''
sub(OLD1, NEW1, "1) _log_step 去掉 MySQL 写入，只留控制台打印")


# ══════════════════════════════════════════════════════════════════
# 2) 删 self.execution_log 初始化
# ══════════════════════════════════════════════════════════════════
OLD2 = "        self.execution_log = []  # 可观测性：推理步骤日志\n"
sub(OLD2, "", "2) 删 self.execution_log 初始化（只服务于已删除的 /trace fallback）")


# ══════════════════════════════════════════════════════════════════
# 3) 删 _AgentTrace ORM 类
# ══════════════════════════════════════════════════════════════════
i0 = text.find("class _AgentTrace(_Base):")
i1 = text.find("class ", i0 + 10)
# 往前找到类定义所在的行首标记（可能带注释），往后到下一个 class
if i0 != -1 and i1 != -1:
    # 回退到该 class 前面的注释块起点
    head = text.rfind("\n\n", 0, i0)
    start = head + 2 if head != -1 else i0
    text = text[:start] + text[i1:]
    applied.append("3) 删 _AgentTrace ORM 类")
    print("  [OK] 3) 删 _AgentTrace ORM 类")
else:
    print(f"  [!!] 3) 定位失败 (i0={i0}, i1={i1})")


# ══════════════════════════════════════════════════════════════════
# 4) /trace 改读 JSONL
# ══════════════════════════════════════════════════════════════════
OLD4 = '''        @_api_app.get("/trace", tags=["Agent"])
        async def _trace():
            try:
                session = agent._get_mysql_conn()
                rows = session.query(_AgentTrace).order_by(_AgentTrace.id.desc()).limit(50).all()
                session.close()
                return {"code": 200, "total": len(rows),
                        "steps": [{"step": r.step, "action": r.action,
                                   "tool_name": r.tool_name, "tool_output": r.tool_output,
                                   "latency": r.latency, "time": str(r.created_at)}
                                  for r in reversed(rows)]}
            except Exception as e:
                log = getattr(agent, "execution_log", [])
                return {"code": 200, "total": len(log), "steps": log[-50:]}
'''

NEW4 = '''        @_api_app.get("/trace", tags=["Agent"])
        async def _trace(limit: int = 50):
            """最近一次请求的推理步骤。

            ## 改造说明

            改造前这里查 MySQL 的 `agent_trace` 表 —— 那张表只被
            `_log_step` 写、只被这个接口读，而**前端一次都没调用过**，
            等于一条自循环。内容还和评测用的 `trace_step` 重复。

            现在统一读 **JSONL**（`get_store().fetch_runs()`）：
              * 和评测**同一数据源** —— "接口看到的"和"评测看到的"不会不一致
              * 不依赖 MySQL 可用性（JSONL 是落库失败时的兜底，永远有）
              * 自带 run_id / route / status，比原来只有 action 更有信息量
            """
            try:
                runs = get_store().fetch_runs()
                if not runs:
                    return {"code": 200, "total": 0, "steps": [],
                            "message": "暂无 trace 记录"}
                last = runs[-1]
                steps = list(last.get("steps") or [])[-limit:]
                return {"code": 200,
                        "run_id": last.get("run_id"),
                        "route": last.get("route"),
                        "status": last.get("status"),
                        "degraded": last.get("degraded"),
                        "elapsed_ms": last.get("elapsed_ms"),
                        "total": len(steps),
                        "steps": steps}
            except Exception as e:
                return {"code": 500, "message": f"读取 trace 失败: {e}", "steps": []}
'''
sub(OLD4, NEW4, "4) /trace 改读 JSONL（与评测同源）")


# ══════════════════════════════════════════════════════════════════
# 5) import get_store
# ══════════════════════════════════════════════════════════════════
OLD5 = "from tracing import TraceRecorder"
NEW5 = "from tracing import TraceRecorder, get_store"
sub(OLD5, NEW5, "5) 导入 get_store")

TARGET.write_text(text, encoding="utf-8")
print()
print(f"完成：应用 {len(applied)} 处，{orig_len} → {len(text)} 字符")
