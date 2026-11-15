"""超时控制与异常处理基础设施。

## 改造前的问题

* **超时是散的**：`OllamaLLM(timeout=15/20/30)` 硬编码在 7 个不同位置，
  没有请求级总预算。用户一次提问最坏情况会串行等好几个 30s。
* **异常被吞掉**：全文件 12 处裸 `except:` + 45 处 `except Exception`，
  其中 7 处直接 `pass`。失败没有任何信号，线上只能看到"回答不对"。
* **降级不可观测**：`_basic_ask` / `_keyword_fallback` 这类兜底路径存在，
  但走没走、走了几次，没有任何记录。

## 本模块提供

1. **错误分类**（`classify`）：把异常分成"可重试 / 可降级 / 致命"三类，
   不同类别走不同策略，而不是一律吞掉。
2. **统一重试**（`retry_call`）：指数退避 + 抖动，重试过程写入 trace。
3. **请求级预算**（`Deadline`）：一次请求一个总 deadline 向下传递，
   每层只允许消耗"剩余预算"，从根上杜绝串行超时叠加。
4. **降级记录**（`Degradation`）：降级必须带原因，写入 trace，
   使"降级率"成为一个可监控指标。
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TypeVar

from config import settings

log = logging.getLogger("slope.resilience")

T = TypeVar("T")


# ══════════════════════════════════════════════════════════════════
# 错误分类
# ══════════════════════════════════════════════════════════════════
class ErrorKind:
    RETRYABLE = "retryable"      # 瞬时故障：网络抖动、超时、连接被重置
    DEGRADABLE = "degradable"    # 该能力不可用，但可换更弱的路径继续
    FATAL = "fatal"              # 配置错误、模型不存在：重试无意义


# 异常类型 → 分类。用类型名匹配，避免强依赖各库的异常层级。
_RETRYABLE_NAMES = {
    "TimeoutError", "ReadTimeout", "ConnectTimeout", "ConnectionError",
    "ConnectionResetError", "ConnectionRefusedError", "RemoteProtocolError",
    "ProtocolError", "NewConnectionError", "MaxRetryError", "HTTPError",
    "ServiceUnavailable", "ResponseError", "APIError", "InternalServerError",
}
_DEGRADABLE_NAMES = {
    "OutputParserException", "ValidationError", "JSONDecodeError",
    "KeyError", "IndexError", "AttributeError", "ValueError",
    "RecursionError", "GraphRecursionError", "ToolException",
    "OperationalError",  # 数据库暂不可用 → 降级为无持久化运行
}
_FATAL_NAMES = {
    "ModuleNotFoundError", "ImportError", "PermissionError",
    "FileNotFoundError", "NotADirectoryError",
}


def classify(exc: BaseException) -> str:
    """把异常归类为 RETRYABLE / DEGRADABLE / FATAL。"""
    name = type(exc).__name__
    if name in _FATAL_NAMES:
        return ErrorKind.FATAL
    if name in _RETRYABLE_NAMES:
        return ErrorKind.RETRYABLE
    if name in _DEGRADABLE_NAMES:
        return ErrorKind.DEGRADABLE
    # 兜底：未知异常按可降级处理，但保留类型名以便归因
    return ErrorKind.DEGRADABLE


# ══════════════════════════════════════════════════════════════════
# 请求级预算
# ══════════════════════════════════════════════════════════════════
@dataclass
class Deadline:
    """一次请求的时间预算。

    每层调用前应通过 `budget(cap)` 申请"本次最多能用多久"，
    返回值不会超过总预算的剩余量。这样即使内部串行多次调用，
    也不会突破请求级上限。
    """

    total_seconds: float
    _t0: float = field(default_factory=time.perf_counter)

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self._t0

    @property
    def remaining(self) -> float:
        return max(0.0, self.total_seconds - self.elapsed)

    @property
    def expired(self) -> bool:
        return self.remaining <= 0.0

    def budget(self, cap_seconds: float) -> float:
        """取本次调用的超时上限 = min(该步骤上限, 剩余预算)。

        剩余不足 0.5s 时返回一个极小值，让调用方快速失败而非继续等待。
        """
        rem = self.remaining
        if rem <= 0.5:
            return 0.01
        return max(0.01, min(float(cap_seconds), rem))


def new_deadline(seconds: Optional[float] = None) -> Deadline:
    return Deadline(total_seconds=float(seconds or settings.timeout_request_budget))


# ══════════════════════════════════════════════════════════════════
# 降级记录
# ══════════════════════════════════════════════════════════════════
@dataclass
class Degradation:
    """一次降级事件。业务代码每次走兜底路径都应记录。"""

    reason: str
    from_stage: str = ""
    to_stage: str = ""
    error_type: str = ""


class DegradationLog:
    """一次请求内的降级记录集合。"""

    def __init__(self) -> None:
        self.events: list[Degradation] = []

    def record(self, reason: str, from_stage: str = "", to_stage: str = "",
               error_type: str = "") -> Degradation:
        ev = Degradation(reason=reason, from_stage=from_stage,
                         to_stage=to_stage, error_type=error_type)
        self.events.append(ev)
        log.warning("[降级] %s → %s | 原因=%s | %s",
                    from_stage or "-", to_stage or "-", reason, error_type)
        return ev

    @property
    def happened(self) -> bool:
        return bool(self.events)

    def summary(self) -> str:
        return "; ".join(f"{e.from_stage}->{e.to_stage}:{e.reason}" for e in self.events)[:120]


# ══════════════════════════════════════════════════════════════════
# 重试
# ══════════════════════════════════════════════════════════════════
def retry_call(
    fn: Callable[[], T],
    *,
    what: str = "call",
    attempts: Optional[int] = None,
    deadline: Optional[Deadline] = None,
    on_degrade: Optional[DegradationLog] = None,
    fallback: Optional[Callable[[], T]] = None,
) -> T:
    """带指数退避的重试；超出预算或不可重试时按策略处理。

    * 可重试异常 → 退避后重试，直到用完 attempts / 预算
    * 可降级异常 → 若提供 fallback 则调用 fallback，否则继续抛出
    * 致命异常 → 立即抛出

    所有失败都写日志（不再静默）。
    """
    max_attempts = attempts if attempts is not None else settings.retry_max_attempts
    last_exc: Optional[BaseException] = None

    for i in range(max_attempts + 1):
        # 预算耗尽：不再重试
        if deadline is not None and deadline.expired:
            if on_degrade is not None:
                on_degrade.record("请求预算耗尽", from_stage=what, to_stage="fallback",
                                  error_type="DeadlineExceeded")
            if fallback is not None:
                return fallback()
            raise TimeoutError(f"{what}: 请求预算耗尽（{deadline.total_seconds}s）")

        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001 - 需要分类后再决定
            last_exc = exc
            kind = classify(exc)
            log.warning("[%s] 第 %d/%d 次失败：%s: %s（分类=%s）",
                        what, i + 1, max_attempts + 1, type(exc).__name__, exc, kind)

            if kind == ErrorKind.FATAL:
                raise
            if kind == ErrorKind.DEGRADABLE:
                if fallback is not None:
                    if on_degrade is not None:
                        on_degrade.record(f"{type(exc).__name__}", from_stage=what,
                                          to_stage="fallback", error_type=type(exc).__name__)
                    return fallback()
                raise
            # RETRYABLE：还有次数就退避重试
            if i < max_attempts:
                backoff = settings.retry_backoff_base * (2 ** i)
                backoff += random.uniform(0, backoff * 0.25)  # 抖动，避免同步重试
                if deadline is not None:
                    backoff = min(backoff, max(0.0, deadline.remaining - 0.1))
                if backoff > 0:
                    time.sleep(backoff)
                continue
            # 次数用尽
            if fallback is not None:
                if on_degrade is not None:
                    on_degrade.record(f"{type(exc).__name__} 重试耗尽", from_stage=what,
                                      to_stage="fallback", error_type=type(exc).__name__)
                return fallback()
            raise

    raise last_exc if last_exc else RuntimeError(f"{what} 失败")


__all__ = [
    "ErrorKind", "classify", "Deadline", "new_deadline",
    "Degradation", "DegradationLog", "retry_call",
]
