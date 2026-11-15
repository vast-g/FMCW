"""评测报告生成。

产出两类内容：
1. **人看的 Markdown 报告** —— 指标表、分维度拆解、运行健康度、裁判可靠性、结论。
2. **机器读的 JSON** —— 由 runner.save_results 落盘，供回归门禁做基线比较。

报告刻意包含"裁判可靠性"一节：评测本身的置信度必须和指标一起呈现，
否则读者无法判断 0.85 的 Faithfulness 是真信号还是裁判噪声。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from config import settings


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _num(v: Optional[float], digits: int = 3) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def _delta(cur: Optional[float], base: Optional[float]) -> str:
    """与基线的差值，带方向标记。"""
    if cur is None or base is None:
        return "—"
    d = cur - base
    if abs(d) < 1e-9:
        return "±0"
    arrow = "▲" if d > 0 else "▼"
    return f"{arrow}{abs(d) * 100:.1f}pp"


def build_markdown(
    summary: dict[str, Any],
    results: list[Any],
    *,
    baseline: Optional[dict] = None,
    judge_stats: Optional[dict] = None,
    route_groups: Optional[dict] = None,
    task_groups: Optional[dict] = None,
    tag: str = "",
) -> str:
    s = summary
    L: list[str] = []
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    L.append("# 边坡监测 Agent 评测报告")
    if tag:
        L.append(f"> 评测批次：`{tag}`")
    L.append("")
    L.append(f"- 生成时间：{now}")
    L.append(f"- 配置指纹：`{settings.config_version()}`（配置变更后指标不可直接横向比较）")
    L.append(f"- 被测模型：`{settings.llm_model}`（本地 Ollama）")
    L.append(f"- 裁判模型：`{settings.effective_judge_model}`（{settings.judge_provider}）")
    L.append(f"- 样本总数：{s.get('total', 0)}")
    L.append("")

    # ── 核心指标 ──
    L.append("## 一、五项核心指标")
    L.append("")
    L.append("| 指标 | 本次 | 基线 | 变化 | 说明 |")
    L.append("|------|:----:|:----:|:----:|------|")
    rows = [
        ("Route Accuracy", "route_accuracy", "路由分支是否选对（确定性断言）"),
        ("Tool Accuracy (F1)", "tool_f1", "工具调用集合的精确率/召回率调和平均"),
        ("Tool Exact Match", "tool_exact_match", "工具调用集合与期望完全一致的比例"),
        ("Task Completion Rate", "task_completion_rate", "按验收标准判定任务完成"),
        ("Faithfulness", "faithfulness", "回答的原子陈述被检索上下文支持的比例"),
        ("Context Recall", "context_recall", "标准答案要点能在检索上下文中找到的比例"),
        ("Context Relevance", "context_relevance", "召回片段与问题相关的比例"),
    ]
    for label, key, desc in rows:
        cur, base = s.get(key), (baseline or {}).get(key)
        L.append(f"| {label} | {_pct(cur)} | {_pct(base)} | {_delta(cur, base)} | {desc} |")
    L.append("")

    ev = s.get("evaluated_samples", {})
    L.append(
        f"> 实际参与计算的样本数：Faithfulness {ev.get('faithfulness', 0)} / "
        f"Context Recall {ev.get('context_recall', 0)} / "
        f"Task Completion {ev.get('task_completion', 0)}。"
        "未参与计算的样本是缺少标准答案（reference_answer 为 null）的条目 —— "
        "**没有标准答案就不该给出 recall 分数**。"
    )
    L.append("")

    # ── 运行健康度 ──
    L.append("## 二、运行健康度")
    L.append("")
    L.append("| 指标 | 值 |")
    L.append("|------|:--:|")
    L.append(f"| 降级率 | {_pct(s.get('degraded_rate'))} |")
    L.append(f"| 错误率 | {_pct(s.get('error_rate'))} |")
    L.append(f"| 超时率 | {_pct(s.get('timeout_rate'))} |")
    L.append(f"| 延迟 P50 | {_num(s.get('latency_p50_ms'), 0)} ms |")
    L.append(f"| 延迟 P95 | {_num(s.get('latency_p95_ms'), 0)} ms |")
    L.append(f"| 延迟均值 | {_num(s.get('latency_mean_ms'), 0)} ms |")
    L.append("")

    # ── 裁判可靠性 ──
    L.append("## 三、裁判可靠性（评测自身的置信度）")
    L.append("")
    L.append("| 指标 | 值 |")
    L.append("|------|:--:|")
    L.append(f"| 判定一致率均值 | {_num(s.get('judge_agreement_mean'))} |")
    L.append(f"| unknown 判定总数 | {s.get('judge_unknown_total', 0)} |")
    L.append(f"| 完成度无法判定的样本 | {s.get('samples_with_unknown_completion', 0)} |")
    if judge_stats:
        L.append(f"| 裁判调用次数 | {judge_stats.get('calls', 0)} |")
        L.append(f"| 缓存命中 | {judge_stats.get('cache_hits', 0)} |")
        L.append(f"| JSON 解析失败 | {judge_stats.get('parse_failures', 0)} |")
    L.append("")
    L.append(
        "> `unknown` 是**有效输出**：它表示裁判无法判定。原实现把解析失败"
        "悄悄折算成某个分数，才产生了「所有配置都恰好 5.0 分」的退化报告。"
    )
    L.append("")

    # ── 分维度 ──
    if route_groups:
        L.append("## 四、按路由分支拆解")
        L.append("")
        L.append("| 期望路由 | 样本 | Route Acc | Tool F1 | Task Completion | Faithfulness |")
        L.append("|----------|:----:|:---------:|:-------:|:---------------:|:------------:|")
        for k, g in route_groups.items():
            L.append(
                f"| {k} | {g.get('total', 0)} | {_pct(g.get('route_accuracy'))} | "
                f"{_num(g.get('tool_f1'))} | {_pct(g.get('task_completion_rate'))} | "
                f"{_pct(g.get('faithfulness'))} |"
            )
        L.append("")

    if task_groups:
        L.append("## 五、按任务类型拆解（Top 12）")
        L.append("")
        L.append("| 任务类型 | 样本 | Route Acc | Task Completion | Faithfulness |")
        L.append("|----------|:----:|:---------:|:---------------:|:------------:|")
        items = sorted(task_groups.items(), key=lambda kv: -kv[1].get("total", 0))[:12]
        for k, g in items:
            L.append(
                f"| {k} | {g.get('total', 0)} | {_pct(g.get('route_accuracy'))} | "
                f"{_pct(g.get('task_completion_rate'))} | {_pct(g.get('faithfulness'))} |"
            )
        L.append("")

    # ── 失败样本 ──
    fails = [r for r in results if (r.route_ok is False) or (r.task_completion is False)]
    L.append("## 六、失败样本（最多列 15 条）")
    L.append("")
    if not fails:
        L.append("本次没有 Route 错误或 Task 未完成的样本。")
    else:
        L.append("| # | 问题 | 期望路由 | 实际路由 | 期望工具 | 实际工具 | 完成度 |")
        L.append("|---|------|----------|----------|----------|----------|:------:|")
        for r in fails[:15]:
            q = (r.query or "")[:38].replace("|", "/")
            rd = "✓" if r.route_ok else "✗"
            tc = {True: "pass", False: "fail", None: "?"}.get(r.task_completion, "?")
            L.append(
                f"| {r.question_id} | {q} | {r.expected_route} | {r.predicted_route} {rd} | "
                f"{','.join(r.expected_tools) or '—'} | {','.join(r.predicted_tools) or '—'} | {tc} |"
            )
    L.append("")

    L.append("## 七、口径说明")
    L.append("")
    L.append("- **Route / Tool 不用裁判**：路由是规则级联的确定输出，工具调用是集合比较，"
             "两者都有精确解，交给 LLM 打分只会引入噪声。")
    L.append("- **含标准答案才有 Context Recall**：`reference_answer` 为 null 的样本不参与该项。")
    L.append("- **评测条件**：无头模式 + 固定传感器夹具（`distance=1.25m`、西安坐标）。"
             "距离类路由依赖夹具，与真实硬件在线的结果可能不同。")
    L.append("- **配置指纹**变化（模型 / chunk / top_k 等）后，历史指标不可直接比较。")
    L.append("")
    return "\n".join(L)


def write_report(
    summary: dict[str, Any],
    results: list[Any],
    *,
    baseline: Optional[dict] = None,
    judge_stats: Optional[dict] = None,
    route_groups: Optional[dict] = None,
    task_groups: Optional[dict] = None,
    tag: str = "",
) -> Path:
    md = build_markdown(
        summary, results,
        baseline=baseline, judge_stats=judge_stats,
        route_groups=route_groups, task_groups=task_groups, tag=tag,
    )
    out_dir = Path(settings.reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"eval_report_{tag + '_' if tag else ''}{stamp}.md"
    path.write_text(md, encoding="utf-8")
    return path


def load_latest_results(before: Optional[str] = None) -> Optional[dict]:
    """读取最近一次（或指定时间之前最近一次）的结果 JSON，用作基线。"""
    out_dir = Path(settings.reports_dir)
    files = sorted(out_dir.glob("eval_results_*.json"))
    if before:
        files = [f for f in files if f.name < before] or files
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


__all__ = ["build_markdown", "write_report", "load_latest_results"]
