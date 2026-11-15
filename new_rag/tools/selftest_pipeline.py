"""离线管线自测：不需要任何模型调用，验证指标 → 报告 → Bad Case → 回归集链路。

用途：
  * 改完评测代码后快速回归，不必等小模型跑完 190 题；
  * CI 里做冒烟检查。

    python tools/selftest_pipeline.py
"""
from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from slope_eval.badcase import mine, summarize_causes, update_regression_set  # noqa: E402
from slope_eval.metrics import SampleResult, by_group, summarize, tool_scores  # noqa: E402
from slope_eval.report import build_markdown  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILURES.append(name)


print("=" * 64)
print("1) 工具集合评分")
print("=" * 64)
check("完全匹配", tool_scores(["a"], ["a"])[:3] == (1.0, 1.0, 1.0))
check("空期望空预测算正确", tool_scores([], [])[3] is True)
check("漏调 → recall<1", tool_scores(["a", "b"], ["a"])[1] == 0.5)
check("多调 → precision<1", tool_scores(["a"], ["a", "b"])[0] == 0.5)
check("误调（期望空）→ precision=0", tool_scores([], ["a"])[0] == 0.0)
check("完全不匹配 → F1=0", tool_scores(["a"], ["b"])[2] == 0.0)

print()
print("=" * 64)
print("2) 指标汇总")
print("=" * 64)
results = [
    # 全对
    SampleResult(1, "距离多少", "realtime", "tool_direct", "tool_direct", True,
                 ["tool_get_distance"], ["tool_get_distance"], 1.0, 1.0, 1.0, True,
                 "ok", False, 1200.0, "1.25米，安全", "ctx",
                 faithfulness=1.0, context_recall=1.0, task_completion=True),
    # 路由错 + 工具错 + 完成度 fail
    SampleResult(2, "ESP8266 掉线", "hardware", "sensor_check", "react_agent", False,
                 ["tool_search_kb"], [], 0.0, 0.0, 0.0, False,
                 "ok", True, 30000.0, "不确定", "ctx",
                 faithfulness=0.3, context_recall=0.2, task_completion=False),
    # 缺标准答案 → context_recall 应为 None
    SampleResult(3, "你好", "smalltalk", "casual", "casual", True,
                 [], [], 1.0, 1.0, 1.0, True,
                 "degraded", False, 800.0, "你好！", "",
                 faithfulness=1.0, context_recall=None, task_completion=True),
]
s = summarize(results)
# summarize() 对外输出保留 4 位小数，断言容差取 5e-5
TOL = 5e-5
check("total=3", s["total"] == 3)
check("route_accuracy=2/3", abs(s["route_accuracy"] - 2 / 3) < TOL, str(s["route_accuracy"]))
check("tool_exact_match=2/3", abs(s["tool_exact_match"] - 2 / 3) < TOL, str(s["tool_exact_match"]))
check("degraded_rate=1/3", abs(s["degraded_rate"] - 1 / 3) < TOL, str(s["degraded_rate"]))
check("context_recall 只统计有标准答案的 2 条",
      s["evaluated_samples"]["context_recall"] == 2,
      str(s["evaluated_samples"]))
check("latency_p50 计算", s["latency_p50_ms"] == 1200.0, str(s["latency_p50_ms"]))

groups = by_group(results, lambda r: r.expected_route)
check("按路由分组", set(groups) == {"tool_direct", "sensor_check", "casual"}, str(sorted(groups)))

print()
print("=" * 64)
print("3) 报告生成")
print("=" * 64)
md = build_markdown(s, results, route_groups=groups,
                    task_groups=by_group(results, lambda r: r.task_type),
                    judge_stats={"calls": 10, "cache_hits": 2, "parse_failures": 0},
                    tag="selftest")
check("报告含五项指标", all(k in md for k in
      ["Route Accuracy", "Tool Accuracy", "Task Completion Rate",
       "Faithfulness", "Context Recall"]))
check("报告含裁判可靠性章节", "裁判可靠性" in md)
check("报告含口径说明", "口径说明" in md)
check("报告标注夹具条件", "夹具" in md)

print()
print("=" * 64)
print("4) Bad Case 挖掘与回归集")
print("=" * 64)
samples_by_id = {
    1: {"id": 1, "query": "距离多少", "expected_route": "tool_direct"},
    2: {"id": 2, "query": "ESP8266 掉线", "expected_route": "sensor_check",
        "success_criteria": "必须给出排查步骤"},
    3: {"id": 3, "query": "你好", "expected_route": "casual"},
}
cases = mine(results, samples_by_id)
causes = summarize_causes(cases)
check("挖到 bad case", len(cases) > 0, f"{len(cases)} 条")
check("#2 命中路由错误", any(c["cause"] == "route_misclassification" and c["question_id"] == 2
                          for c in cases))
check("#2 命中工具选择问题", any(c["cause"] == "tool_selection" and c["question_id"] == 2
                          for c in cases))
check("#1 不应进 bad case", not any(c["question_id"] == 1 for c in cases))
check("严重度排序（blocker 在前）", cases[0]["severity"] in ("blocker", "high"),
      cases[0]["severity"])

with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "regression.jsonl"
    path, added1, total1 = update_regression_set(cases, path=p)
    check("回归集首次写入", added1 == len(cases), f"新增 {added1}")
    # 再写一次应全部去重
    path2, added2, total2 = update_regression_set(cases, path=p)
    check("回归集去重生效", added2 == 0 and total2 == total1, f"新增 {added2}")

print()
print("=" * 64)
if FAILURES:
    print(f"自测未通过，失败项：{FAILURES}")
    sys.exit(1)
print("离线管线自测全部通过")
