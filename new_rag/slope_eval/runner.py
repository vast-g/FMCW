"""评测执行器：驱动 Agent 跑测试集，收集 Trace，计算指标。

设计要点：

* **评测与 Agent 解耦**。runner 只依赖 `end_new.agent.run()` 和 `tracing` 的
  Trace 输出，不关心 Agent 内部实现。Agent 改造后产生的 `route` /
  `predicted_tools` 字段就是评测的输入。
* **可断点续跑**。每条样本跑完即写 Trace，评测阶段从 Trace 读取，
  因此模型中途挂掉不需要从头再来（小模型跑 190 题要很久，这点很关键）。
* **无头运行**。通过 `SLOPE_HEADLESS=1` 让 Agent 不创建窗口、不起采集线程。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from config import settings

log = logging.getLogger("slope.runner")


# ══════════════════════════════════════════════════════════════════
# 数据集
# ══════════════════════════════════════════════════════════════════
def load_jsonl(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"数据集不存在：{p}")
    out = []
    with p.open("r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{p} 第 {i} 行不是合法 JSON：{exc}") from exc
    return out


def load_testset(
    name: str = "testset",
    *,
    limit: Optional[int] = None,
    only_routes: Optional[Iterable[str]] = None,
    task_types: Optional[Iterable[str]] = None,
) -> list[dict]:
    """加载评测集。

    支持合并 `testset.jsonl` 与 `testset_extra.jsonl`（后者补充规则类路由的
    覆盖度——原始 190 题只覆盖了 react_agent 与 event_query 两个分支）。
    """
    paths = [Path(settings.eval_dir) / f"{name}.jsonl"]
    extra = Path(settings.eval_dir) / f"{name}_extra.jsonl"
    if extra.exists():
        paths.append(extra)

    samples: list[dict] = []
    for p in paths:
        if p.exists():
            samples.extend(load_jsonl(p))

    # 去重（以 id 为准，后加载的覆盖前者）
    dedup: dict[Any, dict] = {}
    for s in samples:
        dedup[s.get("id")] = s
    samples = list(dedup.values())

    if only_routes:
        keep = set(only_routes)
        samples = [s for s in samples if s.get("expected_route") in keep]
    if task_types:
        keep = set(task_types)
        samples = [s for s in samples if s.get("task_type") in keep]
    samples.sort(key=lambda s: (s.get("id") is None, s.get("id")))
    if limit:
        samples = samples[:limit]
    return samples


# ══════════════════════════════════════════════════════════════════
# 驱动 Agent
# ══════════════════════════════════════════════════════════════════
class AgentDriver:
    """在无头模式下驱动 `end_new.SimpleAgent`。"""

    def __init__(self, warmup: bool = True) -> None:
        os.environ.setdefault("SLOPE_HEADLESS", "1")
        import end_new  # 延迟导入：确保环境变量先生效

        self.mod = end_new
        if warmup:
            self.warmup()

    def warmup(self) -> None:
        t0 = time.perf_counter()
        self.mod.ensure_rag()
        _ = self.mod.agent.stats  # 触发延迟实例化
        log.info("Agent 预热完成 %.1fs", time.perf_counter() - t0)

    def apply_fixture(self, fixture: Optional[dict] = None) -> None:
        """注入传感器夹具。

        无头评测没有真实硬件（`wifi_thread` 不启动），`current_distance` 恒为 0，
        于是 `get_distance()` 返回"暂无数据"、`_execute_multi_intent` 因过滤
        "暂无"而返回 None，距离类问题会穿透到 ReAct —— 同一条问题在有无硬件时
        路由不同，指标就不可复现。

        评测时注入固定夹具，保证同一份测试集每次得到一致的路由判定。
        夹具值写入报告，避免把"夹具条件下的指标"误当成"真实硬件条件下的指标"。
        """
        fx = dict(DEFAULT_FIXTURE)
        fx.update(fixture or {})
        self.mod.current_distance = float(fx["distance"])
        self.mod.current_lat = float(fx["lat"])
        self.mod.current_lng = float(fx["lng"])
        seq = fx.get("trend")
        if seq:
            now = time.time()
            n = len(seq)
            self.mod.dist_log = [(now - (n - i), float(v)) for i, v in enumerate(seq)]
        else:
            self.mod.dist_log = []

    def run_one(self, sample: dict, suite: str = "testset") -> str:
        """执行一条样本（含可选的前置 setup 查询），返回 Trace run_id。"""
        agent = self.mod.agent
        # 每条样本用**独立会话**。
        # 原实现所有样本共用隐式的 "default" 会话，靠 reset_history() 兜底；
        # 改成显式会话后有两个好处：
        #   1. 隔离更彻底 —— setup 铺垫与正式提问天然不串到别的样本
        #   2. 顺带真实覆盖新增的会话隔离代码路径（否则那段逻辑评测里从没被走过）
        sid = f"eval-{suite}-{sample.get('id')}"
        try:
            agent.enter_session(sid)
            agent.history.clear()
        except Exception as exc:  # noqa: BLE001
            log.warning("设置评测会话失败：%s", exc)
        # setup：先跑若干条铺垫对话（不计入评测），用于构造事实库状态
        for pre in sample.get("setup") or []:
            try:
                agent.run(pre, session_id=sid)
            except Exception as exc:  # noqa: BLE001
                log.warning("setup 查询失败：%s: %s", pre, exc)
        agent._eval_question_id = sample.get("id")
        agent._eval_suite = suite
        return agent.run(sample.get("query", ""), session_id=sid)

    def reset_history(self) -> None:
        """清空会话内历史，避免样本间上下文污染。

        这点很重要：Agent 的上下文继承逻辑（第 7 步）会把上一轮用户输入
        拼进当前输入，如果不隔离，后面的样本会被前面的样本带偏，
        评出来的指标反映的不是单题能力。
        """
        try:
            self.mod.agent.history.clear()
        except Exception as exc:  # noqa: BLE001
            log.warning("清空 history 失败：%s", exc)


# 默认传感器夹具：距离 1.25m（安全区，> DIST_THRESHOLD_SAFE=0.5），西安坐标
DEFAULT_FIXTURE: dict[str, Any] = {
    "distance": 1.25,
    "lat": 34.2410,
    "lng": 108.9400,
    "trend": None,
}


def collect_runs(suite: str = "testset") -> dict[int, dict]:
    """从 Trace 存储读取某次评测的所有运行记录，按 question_id 索引。"""
    from tracing import get_store

    runs = get_store().fetch_runs(suite=suite)
    by_qid: dict[int, dict] = {}
    for r in runs:
        qid = r.get("question_id")
        if qid is None:
            continue
        # 同题多次运行取最后一次
        by_qid[int(qid)] = r
    return by_qid


# ══════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════
def run_suite(
    samples: list[dict],
    *,
    suite: str = "testset",
    driver: Optional[AgentDriver] = None,
    isolate: bool = True,
    progress: Optional[Callable[[int, int, dict], None]] = None,
) -> dict[int, dict]:
    """跑完整套样本，返回 question_id → trace run 的映射。

    即使某条样本抛异常，也会继续跑下一条（异常已经写进 Trace），
    不会让整套评测中断。
    """
    drv = driver or AgentDriver()
    total = len(samples)
    for i, s in enumerate(samples, 1):
        if isolate:
            drv.reset_history()
        # 每条样本前重置夹具：保证题与题之间硬件状态一致
        try:
            drv.apply_fixture(s.get("fixture"))
        except Exception as exc:  # noqa: BLE001
            log.warning("注入夹具失败：%s", exc)
        t0 = time.perf_counter()
        try:
            drv.run_one(s, suite=suite)
        except Exception as exc:  # noqa: BLE001
            log.warning("样本 #%s 执行异常：%s: %s", s.get("id"), type(exc).__name__, exc)
        if progress:
            progress(i, total, s)
        else:
            log.info("[%d/%d] #%s %.1fs", i, total, s.get("id"), time.perf_counter() - t0)
    return collect_runs(suite=suite)


def evaluate_suite(
    samples: list[dict],
    runs_by_qid: dict[int, dict],
    *,
    use_judge: bool = True,
    judge_samples: int = 1,
    judge_model: Optional[str] = None,
):
    """对已收集的 Trace 计算指标。"""
    from .judge import Judge, JudgeCache, make_client
    from .metrics import Evaluator, summarize

    # ⚠️ 必须用 make_client() 按 JUDGE_PROVIDER 解析，**不能**硬编码 OllamaClient()。
    #
    # 踩过的坑：这里原先是 `client=OllamaClient()`，于是出现了一种很难查的现象 ——
    #   * `python -m slope_eval judge-test` 走的是 make_client()，自检**通过**
    #   * 但真正跑评测时裁判仍打到本地 Ollama，把远端模型名当本地模型查，
    #     报 `HTTP 404: {"error":"model 'deepseek-flash' not found"}`
    #     （这个错误格式是 Ollama 专有的，一度让人以为是 DeepSeek 侧模型名写错）
    # 结果：自检绿、实跑全 unknown。
    judge = Judge(
        client=make_client(),
        cache=JudgeCache(),
        model=judge_model,
        samples=judge_samples,
    )
    ev = Evaluator(judge, use_judge=use_judge)
    results = ev.evaluate_all(samples, runs_by_qid)
    judge.flush()
    return results, summarize(results), judge.stats


def save_results(results, summary: dict, *, tag: str = "") -> Path:
    """把逐条结果与汇总落盘，供报告与回归门禁使用。"""
    from dataclasses import asdict

    out_dir = Path(settings.reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = f"eval_results_{tag + '_' if tag else ''}{stamp}.json"
    path = out_dir / name
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config_version": settings.config_version(),
        "settings": {
            "llm_model": settings.llm_model,
            "llm_embed_model": settings.llm_embed_model,
            "judge_model": settings.effective_judge_model,
            "rag_top_k": settings.rag_top_k,
            "chunk_size": settings.chunk_size,
        },
        "summary": summary,
        "samples": [asdict(r) for r in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


__all__ = [
    "load_jsonl", "load_testset", "AgentDriver", "collect_runs",
    "run_suite", "evaluate_suite", "save_results",
]
