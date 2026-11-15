# -*- coding: utf-8 -*-
"""独立校验 eval/testset.jsonl 的合规性（不依赖 build_testset.py）。"""
import json
import os
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "testset.jsonl")
DOCS = os.path.join(os.path.dirname(BASE), "rag_docs")
QS = os.path.join(os.path.dirname(BASE), "test_200_questions.json")

ROUTES = {"react_agent", "event_query", "sensor_check", "trend_analysis",
          "slip_judgment", "self_info", "fact_write", "note_write",
          "casual", "compound"}
TOOLS = {"tool_get_distance", "tool_get_gps", "tool_get_weather", "tool_search_kb"}
FIELDS = ["id", "query", "task_type", "expected_route", "expected_tools",
          "reference_answer", "reference_sources", "grounded", "reviewed"]

errors = []
rows = []
with open(OUT, encoding="utf-8") as f:
    for lineno, line in enumerate(f, 1):
        if not line.strip():
            errors.append(f"line {lineno}: 空行")
            continue
        try:
            rows.append(json.loads(line))
        except Exception as e:
            errors.append(f"line {lineno}: JSON 非法 -> {e}")

print(f"[1] 总行数: {len(rows)}  (期望 190) -> {'OK' if len(rows) == 190 else 'FAIL'}")

docnames = set(os.listdir(DOCS))
qs = {it["id"]: it["query"] for it in json.load(open(QS, encoding="utf-8"))}

ids = [r.get("id") for r in rows]
if len(ids) != len(set(ids)):
    errors.append("id 存在重复")
if set(ids) != set(range(1, 191)):
    errors.append(f"id 不是 1..190 全覆盖, 缺失={sorted(set(range(1,191)) - set(ids))}")
print(f"[2] id 1..190 无重复无缺失 -> {'OK' if set(ids) == set(range(1, 191)) and len(ids) == len(set(ids)) else 'FAIL'}")

bad_fields = [r.get("id") for r in rows if list(r.keys()) != FIELDS]
if bad_fields:
    errors.append(f"字段名/顺序不符: {bad_fields}")
print(f"[3] 字段名严格匹配 {FIELDS} -> {'OK' if not bad_fields else 'FAIL'}")

bad_route = [(r["id"], r["expected_route"]) for r in rows if r["expected_route"] not in ROUTES]
if bad_route:
    errors.append(f"非法 expected_route: {bad_route}")
print(f"[4] expected_route 全部合法 -> {'OK' if not bad_route else 'FAIL'}")

bad_tools = [(r["id"], t) for r in rows for t in r["expected_tools"] if t not in TOOLS]
if bad_tools:
    errors.append(f"非法工具: {bad_tools}")
print(f"[5] expected_tools 元素全部合法 -> {'OK' if not bad_tools else 'FAIL'}")

bad_g = [r["id"] for r in rows
         if r["grounded"] and (not r["reference_answer"] or not r["reference_sources"])]
if bad_g:
    errors.append(f"grounded=true 但缺答案或来源: {bad_g}")
print(f"[6] grounded=true 均有非空 reference_answer 与 reference_sources -> {'OK' if not bad_g else 'FAIL'}")

bad_src = [(r["id"], s) for r in rows for s in r["reference_sources"] if s not in docnames]
if bad_src:
    errors.append(f"来源文件不存在: {bad_src}")
print(f"[7] reference_sources 文件均真实存在于 rag_docs/ -> {'OK' if not bad_src else 'FAIL'}")

bad_ng = [r["id"] for r in rows
          if (not r["grounded"]) and (r["reference_answer"] is not None or r["reference_sources"])]
if bad_ng:
    errors.append(f"grounded=false 但仍有答案或来源: {bad_ng}")
print(f"[8] grounded=false 的行 answer=null 且 sources=[] -> {'OK' if not bad_ng else 'FAIL'}")

bad_rev = [r["id"] for r in rows if r["reviewed"] is not False]
if bad_rev:
    errors.append(f"reviewed 非 false: {bad_rev}")
print(f"[9] reviewed 全为 false -> {'OK' if not bad_rev else 'FAIL'}")

bad_q = [r["id"] for r in rows if qs.get(r["id"]) != r["query"]]
if bad_q:
    errors.append(f"query 与原始问题集不一致: {bad_q}")
print(f"[10] query 与 test_200_questions.json 完全一致 -> {'OK' if not bad_q else 'FAIL'}")

bad_len = [(r["id"], len(r["reference_answer"])) for r in rows
           if r["reference_answer"] and not (80 <= len(r["reference_answer"]) <= 200)]
if bad_len:
    errors.append(f"参考答长度越界(80-200): {bad_len}")
print(f"[11] 参考答长度均在 80-200 字 -> {'OK' if not bad_len else 'FAIL'}")

# ===== 统计 =====
print("\n===== 统计 =====")
rc = Counter(r["expected_route"] for r in rows)
print("[expected_route 分布]")
for k, v in rc.most_common():
    print(f"  {k:18s} {v:4d}")
tc = Counter(r["task_type"] for r in rows)
print("[task_type 分布]")
for k, v in tc.most_common():
    print(f"  {k:24s} {v:4d}")
g = sum(1 for r in rows if r["grounded"])
print(f"[grounded] true={g}  false={len(rows)-g}  比例={g/len(rows)*100:.1f}%")
tuc = Counter(t for r in rows for t in r["expected_tools"])
print("[expected_tools 分布]", dict(tuc))
empty = sum(1 for r in rows if not r["expected_tools"])
print(f"[expected_tools 为空的行数] {empty}")

# 一致性规则：react_agent + 知识型 → 必须含 tool_search_kb
print("\n===== 一致性规则 =====")
nokb = [r["id"] for r in rows
        if r["grounded"] and r["expected_route"] == "react_agent"
        and "tool_search_kb" not in r["expected_tools"]]
if nokb:
    errors.append(f"grounded 的 react_agent 问题缺少 tool_search_kb: {nokb}")
print(f"[12] grounded 的 react_agent 均含 tool_search_kb -> {'OK' if not nokb else 'FAIL'}")

bad_tool_route = [r["id"] for r in rows
                  if r["expected_route"] in {"sensor_check", "trend_analysis",
                                             "slip_judgment", "self_info",
                                             "fact_write", "note_write", "casual"}
                  and r["expected_tools"]]
if bad_tool_route:
    errors.append(f"纯规则类路由却给了工具: {bad_tool_route}")
print(f"[13] 纯规则类路由的 expected_tools 均为空 -> {'OK' if not bad_tool_route else 'FAIL'}")

# 与 end_new.py run() 的真实关键词门控对齐（确定性可复核）
def has_kw(t, ks):
    return any(k in t for k in ks)

EQ = ["发生过", "什么时候", "记录"]
mis_eq = [r["id"] for r in rows if (r["expected_route"] == "event_query") != has_kw(r["query"], EQ)]
if mis_eq:
    errors.append(f"event_query 标签与 run() 关键词门控不一致: {mis_eq}")
print(f"[14] event_query 标签 == 含[发生过/什么时候/记录] -> {'OK' if not mis_eq else 'FAIL'}")

mis_re = [r["id"] for r in rows
          if r["expected_route"] == "react_agent" and (has_kw(r["query"], EQ)
             or has_kw(r["query"], ["传感器", "正常吗", "掉线", "卡死", "健康"])
             or has_kw(r["query"], ["你好", "谢谢", "再见", "你是谁", "你叫什么", "你能做什么"])
             or has_kw(r["query"], ["会滑坡吗", "会不会塌", "安全吗"]))]
if mis_re:
    print(f"[15] 注意：以下 id 虽标注为 react_agent，但其字面含更早规则分支的关键词，"
          f"运行时该分支会先返回：{mis_re}")
else:
    print("[15] react_agent 标签未被更早规则分支截获 -> OK")
print("[15b] 说明：这些是「关键词级联先命中」的边缘样本（如含“正常吗”“掉线”等泛化词），"
      "标签按其知识类语义意图给出，评测时需注意")

# 覆盖度声明
missing_routes = ROUTES - {r["expected_route"] for r in rows}
print(f"[16] 未被覆盖的 route 分支: {sorted(missing_routes) if missing_routes else '无'}")

print("\n===== 结论 =====")
if errors:
    print(f"FAILED，共 {len(errors)} 项问题：")
    for e in errors:
        print("  -", e)
else:
    print("全部通过（PASS）：190 行合法 JSONL，所有字段与枚举、来源、一致性规则均合规。")
