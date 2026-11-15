"""五项核心指标的实现。

对应简历里的：Faithfulness、Context Recall、Tool Accuracy、Route Accuracy、
Task Completion Rate。

## 指标口径（为什么这么定）

| 指标 | 口径 | 数据来源 |
|---|---|---|
| Route Accuracy | 预测路由 == 期望路由（确定性断言） | `trace_run.route` |
| Tool Accuracy | 期望工具集 vs 实调工具集的 Precision/Recall/F1 | `trace_run.predicted_tools` |
| Task Completion Rate | 按任务类型的验收标准判定 pass/fail | 裁判 + 规则断言 |
| Faithfulness | 回答拆成原子陈述，逐条判断能否被检索上下文蕴含 | 裁判（蕴含） |
| Context Recall | 标准答案拆句，逐句判断能否在检索上下文中找到支撑 | 裁判（蕴含） |

三个关键取舍：

1. **Route / Tool 走确定性断言，不走裁判**。路由是规则级联的确定输出，
   用 LLM 去"打分"只会引入噪声；工具调用是集合比较，本来就有精确解。
   把裁判留给真正主观的两项（Faithfulness / Context Recall），
   既省调用又更可信。

2. **判定结果是三值**（supported / not_supported / unknown），
   而不是 0-10 分。`unknown` 单独统计——原实现把"解析失败"直接当成某个分数，
   这是那份 5.0/5.0/5.0 报告的根源。

3. **Context Recall 必须有标准答案**。没有 reference 就没有 recall，
   本模块对这种样本返回 None 而不是编一个分数。
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Optional

from .judge import Judge, split_claims


# ══════════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════════
@dataclass
class SampleResult:
    """单条测试样本的评测结果。"""

    question_id: int
    query: str
    task_type: str = ""

    # 路由
    expected_route: str = ""
    predicted_route: str = ""
    route_ok: Optional[bool] = None

    # 工具
    expected_tools: list[str] = field(default_factory=list)
    predicted_tools: list[str] = field(default_factory=list)
    tool_precision: Optional[float] = None
    tool_recall: Optional[float] = None
    tool_f1: Optional[float] = None
    tools_exact: Optional[bool] = None

    # 运行状态
    status: str = "ok"
    degraded: bool = False
    elapsed_ms: float = 0.0
    answer: str = ""
    context: str = ""

    # 裁判得出（None = 未评/无法评）
    faithfulness: Optional[float] = None
    context_recall: Optional[float] = None
    context_relevance: Optional[float] = None
    task_completion: Optional[bool] = None
    completion_unknown: bool = False

    # 裁判可靠性观测
    judge_unknowns: int = 0
    judge_agreements: list[float] = field(default_factory=list)

    error: str = ""


def tool_scores(expected: list[str], predicted: list[str]) -> tuple[float, float, float, bool]:
    """集合视角的工具调用评分。

    空期望 + 空预测 = 正确（该样本本就不需要工具）。
    空期望 + 有预测 = 误调用，precision=0。
    """
    e, p = set(expected), set(predicted)
    exact = e == p
    if not e and not p:
        return 1.0, 1.0, 1.0, True
    tp = len(e & p)
    precision = tp / len(p) if p else 0.0
    recall = tp / len(e) if e else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return round(precision, 4), round(recall, 4), round(f1, 4), exact


def split_context(text: str, min_len: int = 20, max_chunks: int = 12) -> list[str]:
    """把检索上下文切成片段，用于 Context Relevance / Precision。"""
    if not text:
        return []
    parts = [p.strip() for p in text.split("\n\n")]
    chunks = [p for p in parts if len(p) >= min_len]
    return chunks[:max_chunks]


# ══════════════════════════════════════════════════════════════════
# 评测器
# ══════════════════════════════════════════════════════════════════
class Evaluator:
    """对一批样本计算五项指标。"""

    def __init__(self, judge: Judge, *, use_judge: bool = True) -> None:
        self.judge = judge
        self.use_judge = use_judge

    # -- 单样本 --
    def evaluate_sample(self, sample: dict, run: Optional[dict]) -> SampleResult:
        res = SampleResult(
            question_id=sample.get("id", -1),
            query=sample.get("query", ""),
            task_type=sample.get("task_type", ""),
            expected_route=sample.get("expected_route", ""),
            expected_tools=list(sample.get("expected_tools") or []),
        )

        # 1) 运行缺失 → 记为失败，但不静默
        if run is None:
            res.status = "missing"
            res.error = "未找到对应的 trace 运行记录"
            res.route_ok = False
            res.tool_precision = res.tool_recall = res.tool_f1 = 0.0
            res.tools_exact = False
            res.task_completion = False
            return res

        res.predicted_route = run.get("route", "") or ""
        res.status = run.get("status", "ok")
        res.degraded = bool(run.get("degraded"))
        res.elapsed_ms = float(run.get("elapsed_ms") or 0.0)
        res.answer = run.get("answer_digest", "") or ""
        res.error = run.get("error_type", "") or ""

        # 工具集来自 trace（改造后才会真的有值）
        import json as _json

        try:
            res.predicted_tools = list(_json.loads(run.get("predicted_tools") or "[]"))
        except Exception:  # noqa: BLE001
            res.predicted_tools = []

        # 检索上下文 = 模型当时看到的内容
        #
        # 方案 A 之后它不再是"把 steps 里所有 observation 拼起来"，
        # 而是 run 行上的一列 `context_text`（由 TraceRecorder 在请求过程中
        # 直接累积、落库时一次写入）。
        #
        # 保留了对旧数据的兼容：老 run 没有这一列时，回退去拼 steps ——
        # 否则历史 trace 的 Faithfulness 会全部变成 0，看起来像"指标崩了"。
        ctx = run.get("context_text")
        if ctx:
            res.context = str(ctx)
        else:
            obs = [
                s.get("output_digest", "")
                for s in run.get("steps", [])
                if s.get("kind") == "observation" and s.get("output_digest")
            ]
            res.context = "\n\n".join(obs)

        # 2) 确定性指标
        res.route_ok = (res.predicted_route == res.expected_route)
        res.tool_precision, res.tool_recall, res.tool_f1, res.tools_exact = tool_scores(
            res.expected_tools, res.predicted_tools
        )

        # 3) 裁判指标
        if self.use_judge:
            self._judge_sample(sample, res)
        return res

    def _judge_sample(self, sample: dict, res: SampleResult) -> None:
        mc = self.judge.client

        # -- Faithfulness：回答的每条陈述是否被检索上下文支持 --
        claims = split_claims(res.answer)
        if claims:
            if not res.context.strip():
                # 没有检索上下文却有回答 → 无据可依，忠实度为 0
                res.faithfulness = 0.0
            else:
                supported = 0
                for c in claims:
                    v, agreement = self.judge.entailment(res.context, c)
                    res.judge_agreements.append(agreement)
                    if v == "supported":
                        supported += 1
                    elif v == "unknown":
                        res.judge_unknowns += 1
                res.faithfulness = round(supported / len(claims), 4)

        # -- Context Recall：标准答案的每句是否能在检索上下文中找到支撑 --
        ref = sample.get("reference_answer")
        if ref:
            ref_claims = split_claims(ref)
            if ref_claims:
                if not res.context.strip():
                    res.context_recall = 0.0
                else:
                    hit = 0
                    for c in ref_claims:
                        v, agreement = self.judge.entailment(res.context, c)
                        res.judge_agreements.append(agreement)
                        if v == "supported":
                            hit += 1
                        elif v == "unknown":
                            res.judge_unknowns += 1
                    res.context_recall = round(hit / len(ref_claims), 4)

        # -- Context Relevance（检索精度）：召回的片段是否切题 --
        chunks = split_context(res.context)
        if chunks:
            rel = 0
            for ch in chunks:
                v, agreement = self.judge.relevance(res.query, ch)
                res.judge_agreements.append(agreement)
                if v == "relevant":
                    rel += 1
                elif v == "unknown":
                    res.judge_unknowns += 1
            res.context_relevance = round(rel / len(chunks), 4)

        # -- Task Completion：按验收标准判定 --
        criteria = sample.get("success_criteria") or self._default_criteria(sample)
        verdict, agreement = self.judge.task_completion(
            res.query, res.answer, criteria, res.context
        )
        res.judge_agreements.append(agreement)
        if verdict == "pass":
            res.task_completion = True
        elif verdict == "fail":
            res.task_completion = False
        else:
            res.task_completion = None
            res.completion_unknown = True
            res.judge_unknowns += 1

    @staticmethod
    def _default_criteria(sample: dict) -> str:
        """未显式给出验收标准时，按任务类型给默认标准。"""
        route = sample.get("expected_route", "")
        if route in ("casual", "self_info", "fact_write", "note_write"):
            return "回答与用户意图一致、语气自然即可通过；不要求引用知识库。"
        if route == "sensor_check":
            return "回答必须基于传感器/距离数据给出明确状态判断，不得编造具体数值。"
        if route == "trend_analysis":
            return "回答必须给出当前趋势或状态结论，可引用距离数值。"
        if route == "slip_judgment":
            return "回答必须给出是否安全的明确判断及依据，不得含糊其辞。"
        if route == "event_query":
            return "回答必须给出历史事件/记录相关信息，或明确说明暂无记录。"
        return (
            "回答必须直接回应用户问题，且事实性内容与知识库一致；"
            "不得编造知识库中没有的参数、数值或流程。"
        )

    # -- 批量 --
    def evaluate_all(
        self, samples: list[dict], runs_by_qid: dict[int, dict]
    ) -> list[SampleResult]:
        out = []
        for s in samples:
            out.append(self.evaluate_sample(s, runs_by_qid.get(s.get("id"))))
        return out


# ══════════════════════════════════════════════════════════════════
# 汇总
# ══════════════════════════════════════════════════════════════════
def _mean(values: list[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(statistics.fmean(vals), 4) if vals else None


def _rate(flags: list[bool]) -> Optional[float]:
    vals = [f for f in flags if f is not None]
    return round(sum(1 for v in vals if v) / len(vals), 4) if vals else None


def summarize(results: list[SampleResult]) -> dict[str, Any]:
    """汇总五项核心指标 + 运行健康度 + 裁判可靠性。"""
    n = len(results)
    if n == 0:
        return {"total": 0}

    lat = [r.elapsed_ms for r in results if r.elapsed_ms > 0]
    agreements = [a for r in results for a in r.judge_agreements]

    summary: dict[str, Any] = {
        "total": n,
        # ── 五项核心指标 ──
        "route_accuracy": _rate([r.route_ok for r in results]),
        "tool_f1": _mean([r.tool_f1 for r in results]),
        "tool_exact_match": _rate([r.tools_exact for r in results]),
        "tool_precision": _mean([r.tool_precision for r in results]),
        "tool_recall": _mean([r.tool_recall for r in results]),
        "task_completion_rate": _rate([r.task_completion for r in results]),
        "faithfulness": _mean([r.faithfulness for r in results]),
        "context_recall": _mean([r.context_recall for r in results]),
        "context_relevance": _mean([r.context_relevance for r in results]),
        # ── 运行健康度 ──
        "degraded_rate": _rate([r.degraded for r in results]),
        "error_rate": _rate([r.status == "error" for r in results]),
        "timeout_rate": _rate([r.status == "timeout" for r in results]),
        "latency_p50_ms": round(statistics.median(lat), 1) if lat else None,
        "latency_p95_ms": (
            round(sorted(lat)[max(0, int(len(lat) * 0.95) - 1)], 1) if lat else None
        ),
        "latency_mean_ms": round(statistics.fmean(lat), 1) if lat else None,
        # ── 裁判可靠性（衡量评测本身可不可信）──
        "judge_agreement_mean": _mean(agreements),
        "judge_unknown_total": sum(r.judge_unknowns for r in results),
        "samples_with_unknown_completion": sum(
            1 for r in results if r.completion_unknown
        ),
        "evaluated_samples": {
            "faithfulness": sum(1 for r in results if r.faithfulness is not None),
            "context_recall": sum(1 for r in results if r.context_recall is not None),
            "task_completion": sum(1 for r in results if r.task_completion is not None),
        },
    }
    return summary


def by_group(results: list[SampleResult], key) -> dict[str, dict]:
    """按维度分组汇总（如按 expected_route / task_type / grounded）。"""
    groups: dict[str, list[SampleResult]] = {}
    for r in results:
        groups.setdefault(str(key(r)), []).append(r)
    return {k: summarize(v) for k, v in sorted(groups.items())}


__all__ = [
    "SampleResult", "Evaluator", "summarize", "by_group",
    "tool_scores", "split_context",
]
