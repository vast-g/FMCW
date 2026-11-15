"""Bad Case 自动挖掘与回归集生成。

对应简历里的"结合 Bad Case 回归优化"。

## 为什么要自动挖掘

人工翻日志找错例不可持续：190 条样本 × 5 项指标，靠眼睛看会漏。
而 Trace 改造之后，所有失败信号都是结构化字段，可以直接用规则筛。

## 归因分类（决定怎么修）

不同原因要用完全不同的修法，混在一起优化是无效劳动：

| 原因 | 含义 | 典型修法 |
|------|------|----------|
| `runtime_failure` | 执行报错 / 超时 | 修容错、调超时、加降级 |
| `degraded_path` | 走了兜底路径 | 查 ReAct 为何没产出 |
| `route_misclassification` | 路由选错分支 | 改关键词门控或路由顺序 |
| `tool_selection` | 工具选错/漏调/多调 | 改工具描述或 system prompt |
| `retrieval_miss` | 检索没召回要点 | 改 chunk / top_k / 检索策略 |
| `hallucination_suspected` | 回答含上下文不支持的陈述 | 加反幻觉约束、强化引用 |
| `task_incomplete` | 答了但没答到点上 | 改 prompt / 补知识库 |

## 回归集

每条 bad case 转成一条带 `expected_*` 的测试用例写入
`eval/regression.jsonl`（按 query+原因去重），供 `gate` 命令做门禁：
改动 prompt / 路由 / 模型后必须保证这些曾经错过的样本不再错。
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Optional

from config import settings

# 触发阈值（与 metrics 口径一致）
FAITHFULNESS_FLOOR = 0.6
CONTEXT_RECALL_FLOOR = 0.6
TOOL_F1_FLOOR = 0.999


def classify_causes(r) -> list[tuple[str, str, str]]:
    """对单条结果做归因，返回 [(原因, 严重度, 证据)]。"""
    out: list[tuple[str, str, str]] = []

    if r.status in ("error", "timeout"):
        out.append(("runtime_failure", "blocker",
                    f"status={r.status} error={r.error or '-'}"))
    if r.degraded:
        out.append(("degraded_path", "medium", "本次请求走了降级路径"))

    if r.route_ok is False:
        out.append(("route_misclassification", "high",
                    f"期望 {r.expected_route} 实得 {r.predicted_route}"))
    if r.tool_f1 is not None and r.tool_f1 < TOOL_F1_FLOOR:
        out.append(("tool_selection", "high",
                    f"期望 {r.expected_tools} 实得 {r.predicted_tools} (F1={r.tool_f1})"))
    if r.context_recall is not None and r.context_recall < CONTEXT_RECALL_FLOOR:
        out.append(("retrieval_miss", "high",
                    f"Context Recall={r.context_recall}"))
    if r.faithfulness is not None and r.faithfulness < FAITHFULNESS_FLOOR:
        out.append(("hallucination_suspected", "blocker",
                    f"Faithfulness={r.faithfulness}"))
    if r.task_completion is False:
        out.append(("task_incomplete", "high", "验收标准判定为 fail"))
    return out


def mine(
    results: Iterable[Any],
    samples_by_id: Optional[dict[Any, dict]] = None,
) -> list[dict]:
    """从评测结果中挖掘 bad case。"""
    samples_by_id = samples_by_id or {}
    found: list[dict] = []
    for r in results:
        causes = classify_causes(r)
        if not causes:
            continue
        sample = samples_by_id.get(r.question_id, {})
        for cause, severity, evidence in causes:
            found.append({
                "question_id": r.question_id,
                "query": r.query,
                "task_type": r.task_type,
                "cause": cause,
                "severity": severity,
                "evidence": evidence,
                "expected_route": r.expected_route,
                "predicted_route": r.predicted_route,
                "expected_tools": r.expected_tools,
                "predicted_tools": r.predicted_tools,
                "faithfulness": r.faithfulness,
                "context_recall": r.context_recall,
                "task_completion": r.task_completion,
                "degraded": r.degraded,
                "status": r.status,
                "answer": (r.answer or "")[:400],
                "reference_answer": sample.get("reference_answer"),
                "success_criteria": sample.get("success_criteria"),
                # 回归集需要的字段：保留原始期望，便于直接复用
                "regression_entry": {
                    k: sample.get(k) for k in (
                        "id", "query", "task_type", "expected_route",
                        "expected_tools", "reference_answer",
                        "reference_sources", "grounded", "reviewed",
                        "success_criteria", "setup",
                    ) if sample.get(k) is not None
                },
            })
    # 严重度排序
    order = {"blocker": 0, "high": 1, "medium": 2, "low": 3}
    found.sort(key=lambda x: (order.get(x["severity"], 9), x["question_id"]))
    return found


def save_badcases(cases: list[dict], tag: str = "") -> Path:
    out_dir = Path(settings.badcases_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"badcases_{tag + '_' if tag else ''}{stamp}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for c in cases:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    return path


def update_regression_set(cases: list[dict], path: Optional[Path] = None) -> tuple[Path, int, int]:
    """把 bad case 追加进回归集（按 query+cause 去重）。

    返回 (路径, 新增数, 总条数)。
    """
    p = Path(path or (Path(settings.eval_dir) / "regression.jsonl"))
    p.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, dict] = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = f"{obj.get('query')}||{obj.get('_cause', '')}"
            existing[key] = obj

    added = 0
    for c in cases:
        entry = dict(c.get("regression_entry") or {})
        if not entry.get("query"):
            continue
        entry["_cause"] = c["cause"]
        entry["_first_seen"] = time.strftime("%Y-%m-%d %H:%M:%S")
        entry["_severity"] = c["severity"]
        entry["reviewed"] = False
        key = f"{entry['query']}||{c['cause']}"
        if key in existing:
            # 已存在：刷新最近一次出现的证据
            existing[key]["_last_seen"] = entry["_first_seen"]
            existing[key]["_evidence"] = c["evidence"]
            continue
        existing[key] = entry
        added += 1

    with p.open("w", encoding="utf-8") as fh:
        for obj in existing.values():
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return p, added, len(existing)


def summarize_causes(cases: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in cases:
        counts[c["cause"]] = counts.get(c["cause"], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


__all__ = [
    "classify_causes", "mine", "save_badcases",
    "update_regression_set", "summarize_causes",
]
