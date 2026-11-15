# -*- coding: utf-8 -*-
"""测：知识综合这一步到底稳不稳，以及 num_predict 有没有真的生效。

## 为什么单独测这个

用户实际拿到的回答是**兜底路径**（直接甩检索片段），而不是综合后的答案。
说明"综合产出为空"这件事在真实环境里经常发生，不能只靠一次测试碰运气。

要区分两种可能：
  A. 预算是够的，但模型偶尔就是不吐 content（纯波动）
  B. 预算根本不够，思考每次都把预算吃光 —— 那就是 `model_copy` 没生效

判断依据：打印每次的 thinking 长度和 num_predict 实际值。
"""
from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(errors="replace")

_ME = os.path.dirname(os.path.abspath(__file__))
_ROOT = _ME
while _ROOT != os.path.dirname(_ROOT):
    if os.path.exists(os.path.join(_ROOT, "config.py")):
        break
    _ROOT = os.path.dirname(_ROOT)
for _p in (_ROOT, _ME):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.chdir(_ROOT)

import requests                                  # noqa: E402
from config import settings as CFG               # noqa: E402

BASE = getattr(CFG, "llm_base_url", "http://127.0.0.1:11434").rstrip("/")
MODEL = getattr(CFG, "llm_model", "qwen3:4b")
CFG_NUM = getattr(CFG, "llm_num_predict", "?")

print("=" * 76)
print("知识综合步骤稳定性测试")
print("=" * 76)
print(f"  模型            : {MODEL}")
print(f"  配置 num_predict: {CFG_NUM}")

# 模拟 knowledge 节点的输入：一段检索片段 + 一个要求"综合作答"的指令
PASSAGES = """以下是从知识库检索到的原文片段：

[1] 来源:slope_instability_factors.txt
【边坡失稳的影响因素与作用机理】
本系统监测的每个参数，都对应边坡失稳链条上的一个环节。
内因：岩土体性质、地质结构、坡度坡高、地下水条件。
外因：降雨（最主要触发）、孔隙水压力、地震、人类工程活动、冻融。
因素之间是耦合的：位移要看速率与加速度而非绝对值；降雨加位移加速是危险组合；
多点同步同向加速比单点加速危险得多。

[2] 来源:displacement_criteria.txt
全坡面 95% 以上监测点距离变化在正常范围内，平均位移在 10 秒内变化 < 2mm，判定为稳定。"""

SYS = ("你是边坡监测领域的技术助手。**只根据下面的知识库片段**回答用户问题：\n"
       "- 直接给结论，不要复述用户的问题，也不要复述片段原文\n"
       "- 不要输出你的思考过程\n\n"
       "知识库片段：\n" + PASSAGES)
Q = "给我讲讲几个因素是怎么影响边坡是否滑坡的？"

for npred in (3072, 2048):
    print()
    print("=" * 76)
    print(f"num_predict = {npred}")
    print("=" * 76)
    empty = 0
    for run in range(1, 4):
        r = requests.post(BASE + "/api/chat", json={
            "model": MODEL,
            "messages": [{"role": "system", "content": SYS},
                         {"role": "user", "content": Q}],
            "stream": False,
            "think": True,
            "options": {"num_predict": npred},
        }, timeout=900, proxies={"http": None, "https": None})
        j = r.json()
        msg = j.get("message") or {}
        content = (msg.get("content") or "").strip()
        thinking = (msg.get("thinking") or "").strip()
        done = j.get("done_reason")
        ev = j.get("eval_count", "?")
        if not content:
            empty += 1
        flag = "空" if not content else f"{len(content)}字"
        print(f"  第{run}次: content={flag:<8} thinking={len(thinking):<5} "
              f"done={done:<8} eval_count={ev}")
        if content:
            print(f"          {content[:90]}")
    print(f"  → 空产出 {empty}/3")

print()
print("=" * 76)
print("结论提示：")
print("  * 若 thinking 长度接近 num_predict（token 数），说明预算被思考吃光 → 要加预算")
print("  * 若 thinking 明显小于 num_predict 却仍为空 → 是模型行为问题，需要重试")
print("=" * 76)
