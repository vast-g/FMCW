"""统一命令行入口。

    python -m slope_eval check                 # 环境与配置预检
    python -m slope_eval judge-test            # 裁判链路自检
    python -m slope_eval run --limit 20        # 跑评测（先小样本验证）
    python -m slope_eval run --full            # 跑全部样本
    python -m slope_eval report                # 从最近结果重新生成报告
    python -m slope_eval mine                  # 挖掘 Bad Case 并更新回归集
    python -m slope_eval gate                  # 回归门禁（与基线比较）
    python -m slope_eval baseline              # 把最近一次结果设为基线

设计上把长任务拆成可独立执行的子命令：小模型跑 190 题要很久，
你可以 `run --limit 20` 先验证链路，再 `run --full` 慢慢跑，
中断后 `report`/`mine` 仍能基于已落盘的 Trace 工作。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from config import settings


def _setup(headless: bool = True, log_level: str = "INFO") -> None:
    if headless:
        os.environ["SLOPE_HEADLESS"] = "1"
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _print_summary(summary: dict, judge_stats: dict | None = None) -> None:
    def pct(v):
        return "—" if v is None else f"{v * 100:.1f}%"

    print()
    print("=" * 64)
    print("五项核心指标")
    print("=" * 64)
    print(f"  Route Accuracy        : {pct(summary.get('route_accuracy'))}")
    print(f"  Tool Accuracy (F1)    : {summary.get('tool_f1') if summary.get('tool_f1') is not None else '—'}")
    print(f"  Tool Exact Match      : {pct(summary.get('tool_exact_match'))}")
    print(f"  Task Completion Rate  : {pct(summary.get('task_completion_rate'))}")
    print(f"  Faithfulness          : {pct(summary.get('faithfulness'))}")
    print(f"  Context Recall        : {pct(summary.get('context_recall'))}")
    print(f"  Context Relevance     : {pct(summary.get('context_relevance'))}")
    print()
    print("运行健康度")
    print(f"  样本数 {summary.get('total')} | 降级率 {pct(summary.get('degraded_rate'))} | "
          f"错误率 {pct(summary.get('error_rate'))} | 超时率 {pct(summary.get('timeout_rate'))}")
    print(f"  延迟 P50 {summary.get('latency_p50_ms')}ms | P95 {summary.get('latency_p95_ms')}ms")
    print()
    print("裁判可靠性")
    print(f"  一致率 {summary.get('judge_agreement_mean')} | unknown {summary.get('judge_unknown_total')}")
    if judge_stats:
        print(f"  调用 {judge_stats.get('calls')} | 缓存命中 {judge_stats.get('cache_hits')} "
              f"| 解析失败 {judge_stats.get('parse_failures')}")
    print("=" * 64)


# ══════════════════════════════════════════════════════════════════
# 子命令
# ══════════════════════════════════════════════════════════════════
def cmd_check(args) -> int:
    _setup(headless=True)
    print("=" * 64)
    print("环境预检")
    print("=" * 64)
    ok = True

    # 1) 配置
    print(f"  配置指纹      : {settings.config_version()}")
    print(f"  被测模型      : {settings.llm_model}")
    print(f"  向量模型      : {settings.llm_embed_model}")
    ready, desc = settings.judge_ready()
    print(f"  裁判          : {desc}  {'OK' if ready else '缺失'}")
    ok &= ready

    # 2) 文件
    for label, p in [
        ("知识库目录", settings.rag_docs_dir),
        ("YOLO 权重", settings.yolo_model_path),
        ("测试集", str(Path(settings.eval_dir) / "testset.jsonl")),
    ]:
        exists = Path(p).exists()
        print(f"  {label:14s}: {'OK' if exists else '缺失'}  {p}")
    print(f"  补充测试集    : "
          f"{'OK' if (Path(settings.eval_dir) / 'testset_extra.jsonl').exists() else '缺失'}")

    # 3) 依赖
    print()
    print("  依赖：")
    for mod in ("requests", "sqlalchemy", "pymysql", "langchain_ollama", "langchain"):
        try:
            __import__(mod)
            print(f"    OK   {mod}")
        except Exception as e:  # noqa: BLE001
            print(f"    FAIL {mod}: {type(e).__name__}")
            ok = False

    # 4) Ollama
    print()
    try:
        from slope_eval.judge import OllamaClient

        oc = OllamaClient()
        avail = oc.is_available()
        print(f"  Ollama 可达   : {avail}")
        if avail:
            models = oc.list_models()
            for m in (settings.llm_model, settings.llm_embed_model):
                hit = any(m in x for x in models)
                print(f"    模型 {m:24s} {'已拉取' if hit else '未拉取 ✗'}")
                ok &= hit
        else:
            ok = False
    except Exception as e:  # noqa: BLE001
        print("  Ollama 检查失败:", e)
        ok = False

    # 5) MySQL
    print()
    try:
        import pymysql

        conn = pymysql.connect(**settings.mysql_kwargs)
        conn.close()
        print("  MySQL 连接    : OK")
    except Exception as e:  # noqa: BLE001
        print(f"  MySQL 连接    : 失败（{type(e).__name__}）—— Trace 将降级为仅写 JSONL")

    print()
    print("预检结果:", "通过" if ok else "存在问题（见上）")
    return 0 if ok else 1


def cmd_judge_test(args) -> int:
    _setup(headless=True)
    from slope_eval.judge import self_test

    return self_test()


def cmd_run(args) -> int:
    _setup(headless=True, log_level="WARNING" if not args.verbose else "INFO")
    from slope_eval.badcase import mine, save_badcases, summarize_causes, update_regression_set
    from slope_eval.metrics import by_group
    from slope_eval.report import load_latest_results, write_report
    from slope_eval.runner import (
        AgentDriver, evaluate_suite, load_testset, run_suite, save_results,
    )

    samples = load_testset(
        args.suite,
        limit=None if args.full else args.limit,
        only_routes=args.routes.split(",") if args.routes else None,
    )
    if not samples:
        print("测试集为空，请检查 eval/testset.jsonl 是否存在")
        return 1

    print(f"评测集：{len(samples)} 条（suite={args.suite}）")
    routes: dict[str, int] = {}
    for s in samples:
        routes[s.get("expected_route", "?")] = routes.get(s.get("expected_route", "?"), 0) + 1
    print("路由分布：", ", ".join(f"{k}={v}" for k, v in sorted(routes.items())))

    # 清空上一轮同 suite 的 trace，避免新旧结果混在一起
    if args.reset_trace:
        tp = Path(settings.trace_jsonl)
        if tp.exists():
            tp.unlink()
            print(f"已清空 Trace：{tp}")

    print("\n预热 Agent（加载 RAG + 模型）...")
    driver = AgentDriver(warmup=True)

    t0 = time.perf_counter()

    def progress(i, total, s):
        print(f"  [{i}/{total}] #{s.get('id')} {str(s.get('query'))[:36]}", flush=True)

    runs = run_suite(samples, suite=args.suite, driver=driver, progress=progress)
    print(f"\n执行完成，用时 {time.perf_counter() - t0:.1f}s；收集到 {len(runs)} 条 Trace")

    missing = [s.get("id") for s in samples if s.get("id") not in runs]
    if missing:
        print(f"⚠️ 有 {len(missing)} 条样本没有 Trace 记录：{missing[:10]}")

    use_judge = not args.no_judge
    print(f"\n开始评测（裁判={'关闭' if not use_judge else settings.effective_judge_model}）...")
    results, summary, judge_stats = evaluate_suite(
        samples, runs,
        use_judge=use_judge,
        judge_samples=args.judge_samples,
        judge_model=args.judge_model,
    )

    baseline_payload = load_latest_results()
    baseline = baseline_payload.get("summary") if baseline_payload else None

    route_groups = by_group(results, lambda r: r.expected_route)
    task_groups = by_group(results, lambda r: r.task_type)

    results_path = save_results(results, summary, tag=args.tag)
    report_path = write_report(
        summary, results,
        baseline=baseline, judge_stats=judge_stats,
        route_groups=route_groups, task_groups=task_groups,
        tag=args.tag,
    )

    _print_summary(summary, judge_stats)

    # Bad Case
    samples_by_id = {s.get("id"): s for s in samples}
    cases = mine(results, samples_by_id)
    if cases:
        bc_path = save_badcases(cases, tag=args.tag)
        reg_path, added, total = update_regression_set(cases)
        print(f"\nBad Case：{len(cases)} 条 → {bc_path}")
        print(f"  归因分布：{summarize_causes(cases)}")
        print(f"  回归集：新增 {added} 条，累计 {total} 条 → {reg_path}")

    print(f"\n结果 JSON：{results_path}")
    print(f"报告     ：{report_path}")
    return 0


def cmd_report(args) -> int:
    _setup(headless=True)
    from dataclasses import asdict

    from slope_eval.badcase import mine, summarize_causes
    from slope_eval.metrics import SampleResult, by_group
    from slope_eval.report import load_latest_results, write_report

    payload = load_latest_results()
    if not payload:
        print("找不到历史结果 JSON，请先运行 run")
        return 1

    results = [SampleResult(**s) for s in payload.get("samples", [])]
    summary = payload.get("summary", {})
    path = write_report(
        summary, results,
        route_groups=by_group(results, lambda r: r.expected_route),
        task_groups=by_group(results, lambda r: r.task_type),
        tag=args.tag or "regen",
    )
    _print_summary(summary)
    print(f"\n报告已重新生成：{path}")
    return 0


def cmd_mine(args) -> int:
    _setup(headless=True)
    from slope_eval.badcase import mine, save_badcases, summarize_causes, update_regression_set
    from slope_eval.metrics import SampleResult
    from slope_eval.report import load_latest_results
    from slope_eval.runner import load_testset

    payload = load_latest_results()
    if not payload:
        print("找不到历史结果 JSON，请先运行 run")
        return 1
    results = [SampleResult(**s) for s in payload.get("samples", [])]
    samples_by_id = {s.get("id"): s for s in load_testset("testset")}

    cases = mine(results, samples_by_id)
    if not cases:
        print("没有挖掘到 Bad Case")
        return 0
    bc = save_badcases(cases, tag=args.tag or "mine")
    reg, added, total = update_regression_set(cases)
    print(f"Bad Case {len(cases)} 条 → {bc}")
    print("归因分布：", summarize_causes(cases))
    print(f"回归集：新增 {added}，累计 {total} → {reg}")
    return 0


def cmd_gate(args) -> int:
    """回归门禁：本次指标不得低于基线（允许阈值内波动）。"""
    _setup(headless=True)
    from slope_eval.report import load_latest_results

    payload = load_latest_results()
    if not payload:
        print("找不到历史结果 JSON")
        return 1
    baseline_path = Path(settings.reports_dir) / "baseline.json"
    if not baseline_path.exists():
        print(f"未设置基线：请先 `python -m slope_eval baseline`")
        return 1

    base = json.loads(baseline_path.read_text(encoding="utf-8"))
    bs, cs = base.get("summary", {}), payload.get("summary", {})
    tol = args.tolerance
    checks = [
        ("route_accuracy", True),
        ("tool_f1", True),
        ("task_completion_rate", True),
        ("faithfulness", True),
        ("context_recall", True),
    ]
    print("=" * 64)
    print(f"回归门禁（容差 {tol * 100:.1f}pp；基线配置 {base.get('config_version')}）")
    print("=" * 64)
    failed = []
    for key, higher_better in checks:
        b, c = bs.get(key), cs.get(key)
        if b is None or c is None:
            print(f"  {key:24s} 基线={b} 本次={c}  → 跳过（缺数据）")
            continue
        delta = c - b
        ok = (delta >= -tol) if higher_better else (delta <= tol)
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {key:24s} 基线={b * 100:.1f}% 本次={c * 100:.1f}% "
              f"Δ={delta * 100:+.1f}pp")
        if not ok:
            failed.append(key)
    print("=" * 64)
    if failed:
        print("门禁未通过：", ", ".join(failed))
        return 1
    print("门禁通过")
    return 0


def cmd_baseline(args) -> int:
    _setup(headless=True)
    from slope_eval.report import load_latest_results

    payload = load_latest_results()
    if not payload:
        print("找不到历史结果 JSON")
        return 1
    out = Path(settings.reports_dir) / "baseline.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"基线已设置：{out}")
    print(f"  配置指纹 {payload.get('config_version')} | "
          f"样本 {payload.get('summary', {}).get('total')}")
    return 0


# ══════════════════════════════════════════════════════════════════
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m slope_eval",
        description="边坡监测 Agent 自动化评测（LLM-as-Judge + Agent Trace）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("check", help="环境与配置预检")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("judge-test", help="裁判链路自检")
    sp.set_defaults(func=cmd_judge_test)

    sp = sub.add_parser("run", help="执行评测")
    sp.add_argument("--suite", default="testset", help="数据集名（默认 testset）")
    sp.add_argument("--full", action="store_true", help="跑全部样本（默认只跑 20 条）")
    sp.add_argument("--limit", type=int, default=20, help="样本上限（未指定 --full 时生效）")
    sp.add_argument("--routes", default="", help="只跑指定路由，逗号分隔")
    sp.add_argument("--no-judge", action="store_true", help="跳过裁判（只算 Route/Tool）")
    sp.add_argument("--judge-samples", type=int, default=1, help="裁判自一致性采样次数")
    sp.add_argument("--judge-model", default="", help="覆盖裁判模型")
    sp.add_argument("--tag", default="", help="批次名，用于报告命名")
    sp.add_argument("--reset-trace", action="store_true", help="运行前清空 Trace")
    sp.add_argument("--verbose", action="store_true")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("report", help="从最近结果重新生成报告")
    sp.add_argument("--tag", default="")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("mine", help="挖掘 Bad Case 并更新回归集")
    sp.add_argument("--tag", default="")
    sp.set_defaults(func=cmd_mine)

    sp = sub.add_parser("gate", help="回归门禁")
    sp.add_argument("--tolerance", type=float, default=0.02, help="允许的指标回退幅度")
    sp.set_defaults(func=cmd_gate)

    sp = sub.add_parser("baseline", help="把最近一次结果设为基线")
    sp.set_defaults(func=cmd_baseline)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
