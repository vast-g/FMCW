"""结构化 Agent Trace。

## 为什么重做

原实现（`end_new.py::SimpleAgent._log_step`）有三个硬伤，导致"基于 Agent Trace 的
评测"无法成立：

1. **工具调用不入库**。ReAct 循环里检测到 `tool_calls` 后只推给了观测队列
   （`obs_queue.put`），没有调用 `_log_step`。于是 `agent_trace` 表里没有工具名，
   Tool Accuracy 无从计算。
2. **`tool_name` 恒为 None**。写库时用的是
   `tool_name=step_type if step_type in ("tool","observation")`，
   而真实调用点传的是 `"thought"/"observation"/"error"`，永远匹配不上 `"tool"`。
3. **静默丢数据**。整段包在 `except Exception: pass` 中，MySQL 不可用时
   评测拿到的是一张空表，却没有任何信号。

## 设计

一次请求 = 一条 `trace_run` + N 条 `trace_step`。两个维度分开：

* `trace_run` 承载**可评测字段**：route、predicted_tools、status、degraded、
  elapsed_ms、config_version —— Route Accuracy / Tool Accuracy / Task Completion
  都直接由它聚合得出，无需重跑模型。
* `trace_step` 承载**过程细节**：每一步的 kind/name/latency/ok/error_type，
  用于定位 Bad Case 的失败环节。

同时落一份 JSONL（`settings.trace_jsonl`），保证 MySQL 不可用时评测仍能进行 ——
可观测性组件本身不应该成为评测的单点故障。

## 用法

    from tracing import TraceRecorder, flush_pending

    rec = TraceRecorder(query="ESP8266 掉线", question_id=42)
    rec.set_route("react_agent")
    with rec.step("tool", "tool_search_kb") as s:
        out = tool_search_kb.invoke({"query": "..."})
        s.set_output(out)
    rec.finish(answer="...", status="ok")
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    insert,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import settings

log = logging.getLogger("slope.trace")


# ══════════════════════════════════════════════════════════════════
# 表结构（独立 Base：避免与业务表耦合，也不受既有表结构影响）
# ══════════════════════════════════════════════════════════════════
class _TraceBase(DeclarativeBase):
    pass


class TraceRun(_TraceBase):
    """一次 Agent 请求的汇总记录 —— 评测指标的聚合来源。"""

    __tablename__ = "trace_run"

    run_id = Column(String(32), primary_key=True)
    request_id = Column(String(64), index=True, default="")
    # 评测上下文字段
    question_id = Column(Integer, index=True, nullable=True)
    suite = Column(String(32), index=True, default="")  # testset / regression / online
    query = Column(Text)
    # 路由与工具（可评测）
    route = Column(String(48), index=True, default="")
    predicted_tools = Column(Text, default="[]")  # JSON 数组
    tool_call_count = Column(Integer, default=0)
    # 结果
    answer_digest = Column(Text, default="")
    # ⭐ 方案 A 的核心新增列：模型当时看到的上下文（所有 observation 拼接）。
    # Faithfulness / Context Recall 需要它做蕴含判定 —— 判定"回答有没有胡编"。
    # 单独成列而不是塞进 answer_digest：两者一个是"看到什么"、一个是"说了什么"，
    # 混在一起评测就无从区分"没看到却说了"和"看到了照着说"。
    context_text = Column(Text, default="")
    status = Column(String(24), index=True, default="ok")  # ok/error/timeout/degraded
    degraded = Column(Boolean, default=False)
    degrade_reason = Column(String(120), default="")
    error_type = Column(String(64), default="")
    elapsed_ms = Column(Float, default=0.0)
    # 环境指纹
    model = Column(String(64), default="")
    embed_model = Column(String(64), default="")
    config_version = Column(String(24), default="")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class TraceStep(_TraceBase):
    """一个推理步骤 —— **方案 A 之后已停用，不再写入**。

    留着这个类的原因只有一个：表里还有 879 行历史数据，想翻旧 trace 时
    还得有 ORM 能映射它。新数据一律不再产生 step 行。

    为什么停用：评测算指标真正消费的只有五样（路由、工具、
    **模型看到的上下文**、答案、降级），其中"上下文"已经在 `TraceRun.context_text`
    里一次性存下。逐步明细（每步耗时、每个工具的入参）属于归因信息，
    为它多维护一张表、多一次写入，收益不划算 —— 真要查还有控制台日志。

    推论：`metrics.py` 读上下文时优先用 `run["context_text"]`，
    旧 run 没有这一列时才回退去拼 steps，这样历史指标还能复算。
    """

    __tablename__ = "trace_step"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(32), index=True)
    step_index = Column(Integer, default=0)
    kind = Column(String(24), index=True, default="")  # route/tool/llm/retrieval/fallback/judge/error
    name = Column(String(96), default="")
    args_json = Column(Text, default="")
    output_digest = Column(Text, default="")
    latency_ms = Column(Float, default=0.0)
    ok = Column(Boolean, default=True)
    error_type = Column(String(64), default="")
    created_at = Column(DateTime, default=datetime.utcnow)


# ══════════════════════════════════════════════════════════════════
# 存储层：MySQL 主存 + JSONL 兜底
# ══════════════════════════════════════════════════════════════════
class _TraceStore:
    """线程安全的 trace 写入器。

    MySQL 不可用时**不抛异常**、不静默丢弃：降级为仅写 JSONL，
    并记录一次明确告警（而不是原来那种 `except: pass`）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._engine = None
        self._Session = None
        self._db_ok = False
        self._db_checked = False
        self._warned = False
        self._jsonl_path = Path(settings.trace_jsonl)

    # -- 初始化（惰性） --
    def _ensure_db(self) -> bool:
        if self._db_checked:
            return self._db_ok
        with self._lock:
            if self._db_checked:
                return self._db_ok
            self._db_checked = True
            if not settings.trace_enabled:
                self._db_ok = False
                return False
            try:
                self._engine = create_engine(
                    settings.database_url,
                    pool_size=2,
                    max_overflow=2,
                    pool_recycle=3600,
                    pool_pre_ping=True,
                    connect_args={"connect_timeout": settings.timeout_mysql_connect},
                )
                _TraceBase.metadata.create_all(self._engine)
                self._Session = sessionmaker(bind=self._engine)
                self._db_ok = True
            except Exception as exc:  # noqa: BLE001 - 需要兜底到 JSONL
                self._db_ok = False
                if not self._warned:
                    self._warned = True
                    log.warning(
                        "Agent Trace 无法写入 MySQL（%s: %s），已降级为仅写 JSONL：%s",
                        type(exc).__name__, exc, self._jsonl_path,
                    )
            return self._db_ok

    # -- 写 JSONL（始终执行） --
    def _append_jsonl(self, payload: dict) -> None:
        try:
            self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(payload, ensure_ascii=False, default=_json_default)
            with self._jsonl_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception as exc:  # noqa: BLE001
            if not self._warned:
                self._warned = True
                log.warning("Agent Trace JSONL 写入失败：%s", exc)

    # -- 对外：保存一次完整运行 --
    def save(self, run: dict) -> None:
        """落库一次请求（方案 A：只有 run 这一行，没有步骤表）。

        JSONL 先写（成败都不影响），MySQL 后写（失败只告警）。
        这个顺序不变 —— JSONL 是事实来源，MySQL 是便于查询的副本。
        """
        self._append_jsonl(dict(run))

        if not self._ensure_db():
            return
        try:
            with self._Session() as session:  # type: ignore[misc]
                session.execute(insert(TraceRun).values(**run))
                session.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("Agent Trace 落库失败（JSONL 已保存）：%s: %s", type(exc).__name__, exc)

    # -- 供评测读取 --
    def fetch_runs(self, suite: str | None = None) -> list[dict]:
        """从 JSONL 读取历史运行（评测的事实来源，避免依赖 DB 可用性）。"""
        if not self._jsonl_path.exists():
            return []
        out: list[dict] = []
        with self._jsonl_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if suite and obj.get("suite") != suite:
                    continue
                out.append(obj)
        return out


_store: Optional[_TraceStore] = None
_store_lock = threading.Lock()


def get_store() -> _TraceStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = _TraceStore()
    return _store


def _digest(text: str, limit: int = 500) -> str:
    text = "" if text is None else str(text)
    text = text.replace("\n", " ").strip()
    return text[:limit]


def _json_default(obj: Any):
    """JSONL 序列化兜底：datetime / set / 其他不可序列化类型。"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return str(obj)


# ══════════════════════════════════════════════════════════════════
# 记录器
# ══════════════════════════════════════════════════════════════════
@dataclass
class TraceRecorder:
    """一次请求的 trace 记录器。

    `route` / `predicted_tools` / `status` / `degraded` 是评测直接消费的字段，
    业务代码必须在对应位置显式写入 —— 这正是原实现缺失的埋点。
    """

    query: str
    question_id: Optional[int] = None
    suite: str = "online"
    request_id: str = ""

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    route: str = ""
    predicted_tools: list[str] = field(default_factory=list)
    status: str = "ok"
    degraded: bool = False
    degrade_reason: str = ""
    error_type: str = ""
    answer: str = ""

    # ⭐ 方案 A：不再保存逐步明细，只累积**模型看到的上下文**。
    # 原来这里是 `_steps: list[dict]`（N 行明细），评测要 join 才能拼出上下文；
    # 现在直接在一次请求里攒成一个字符串，落库就是一行。
    _context_parts: list[str] = field(default_factory=list, repr=False)
    _t0: float = field(default_factory=time.perf_counter, repr=False)
    _saved: bool = field(default=False, repr=False)

    # 上下文总长上限。observation 单条上限 2000 字符，多次调用会累积，
    # 这里整体再截一次 —— 评测做蕴含判定不需要无限长的上下文，
    # 而没有上限的话一次请求可能写进几十 KB。
    _CONTEXT_LIMIT = 8000

    # -- 写入可评测字段 --
    def set_route(self, route: str) -> None:
        """标记本次请求实际走的路由分支（Route Accuracy 的唯一来源）。

        方案 A 之后不再往步骤表写一条 route 记录 —— 它就是 run 行上的一个字段。
        """
        self.route = route

    def add_tool(self, name: str) -> None:
        """登记一次真实工具调用（Tool Accuracy 的唯一来源）。"""
        if name not in self.predicted_tools:
            self.predicted_tools.append(name)

    def add_context(self, text: str) -> None:
        """累积"模型看到的内容"（工具观测 / 检索片段）。

        这就是原来 `note_step("observation", ..., output=...)` 存进步骤表的
        那份数据，现在直接攒进一次请求的上下文里，落库时作为一个列。
        """
        s = str(text or "").strip()
        if s:
            self._context_parts.append(s)

    def mark_degraded(self, reason: str) -> None:
        """标记本次请求发生过降级（关键词兜底 / 基础模型兜底等）。"""
        self.degraded = True
        self.degrade_reason = _digest(reason, 120)
        if self.status == "ok":
            self.status = "degraded"

    def mark_error(self, error_type: str, detail: str = "") -> None:
        """标记本次请求出错。方案 A 之后只改状态字段，不写步骤行。"""
        self.status = "error"
        self.error_type = error_type
        if detail:
            self.add_context(f"[error] {detail}")

    def mark_timeout(self, detail: str = "") -> None:
        """标记本次请求超时。"""
        self.status = "timeout"
        self.error_type = "TimeoutError"
        if detail:
            self.add_context(f"[timeout] {detail}")

    def note_step(
        self,
        kind: str,
        name: str,
        args: dict | None = None,
        output: str = "",
        output_limit: int = 500,
    ) -> None:
        """**兼容垫片**：方案 A 之后不再记录逐步明细。

        只做一件事：`kind == "observation"` 时把 output 累积进上下文
        （那是 Faithfulness 唯一需要的过程数据），其余 kind 直接忽略。

        保留这个签名是为了让 `run()` 和 `slope_graph.py` 里那 ~10 处调用
        一行都不用改 —— 换的是内部实现，不是调用方式。
        """
        if kind == "observation":
            self.add_context(str(output or "")[:output_limit])

    # -- 收尾 --
    def finish(self, answer: str = "", status: str | None = None) -> dict:
        """落库并返回汇总记录。重复调用只保存一次。"""
        if self._saved:
            return {}
        self._saved = True
        self.answer = answer or ""
        if status:
            self.status = status

        run = {
            "run_id": self.run_id,
            "request_id": self.request_id,
            "question_id": self.question_id,
            "suite": self.suite,
            "query": _digest(self.query, 2000),
            "route": self.route,
            "predicted_tools": json.dumps(self.predicted_tools, ensure_ascii=False),
            "tool_call_count": len(self.predicted_tools),
            "answer_digest": _digest(self.answer, 2000),
            # 方案 A：模型看到的上下文，一次性拼好落在这一行里
            "context_text": ("\n\n".join(self._context_parts))[: self._CONTEXT_LIMIT],
            "status": self.status,
            "degraded": self.degraded,
            "degrade_reason": self.degrade_reason,
            "error_type": self.error_type,
            "elapsed_ms": round((time.perf_counter() - self._t0) * 1000, 2),
            "model": settings.llm_model,
            "embed_model": settings.llm_embed_model,
            "config_version": settings.config_version(),
            "created_at": datetime.utcnow(),
        }
        get_store().save(run)
        return run


__all__ = ["TraceRecorder", "TraceRun", "TraceStep", "get_store"]
