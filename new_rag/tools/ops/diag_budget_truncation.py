# -*- coding: utf-8 -*-
"""决定性实验：思考把输出预算吃光时，两种模式的 content 分别是什么。

这是选修法的依据：
  * 若「不传 think」时 content 为空 → 可以**明确检测到"没产出答案"**，
    交给兜底路径即可，不会把推理当答案 —— 那就不需要任何启发式剥离
  * 若「不传 think」时 content 仍是一段推理 → 才需要写"推理腔检测"这类
    容易误伤真答案的启发式规则

做法：把 num_predict 压到很小（思考一定写不完），看 content 是什么。
"""
from __future__ import annotations

import sys

import requests

sys.stdout.reconfigure(errors="replace")
P = {"http": None, "https": None}

# 故意用"需要长思考"的复杂问题，保证思考一定会撑爆预算
PROMPT = ("位移速率0.56mm/h、累计雨量170mm、孔压1.65kPa、裂缝4.0mm，"
          "请结合边坡失稳机理，说明这几个参数分别如何影响稳定性，并给出处置建议。")

for npred in (128, 400):
    print("=" * 76)
    print(f"num_predict = {npred}（远小于思考所需）")
    print("=" * 76)
    for label, extra in (("不传 think（思考独立）", {}),
                         ("think:false（思考混入 content）", {"think": False})):
        try:
            r = requests.post("http://127.0.0.1:11434/api/chat", json={
                "model": "qwen3:4b",
                "messages": [{"role": "user", "content": PROMPT}],
                "stream": False,
                "options": {"num_predict": npred},
                **extra,
            }, timeout=600, proxies=P)
            j = r.json()
            msg = j.get("message") or {}
            content = (msg.get("content") or "").strip()
            thinking = msg.get("thinking")
            done = j.get("done_reason")

            print(f"\n  【{label}】")
            print(f"    done_reason : {done}")
            print(f"    content 长度: {len(content)}")
            print(f"    thinking    : {len(thinking) if thinking else 0} 字")
            print(f"    含 </think>  : {'</think>' in content}")
            if content:
                print(f"    content 开头: {content[:120]!r}")
                # 判断这段是"答案"还是"推理"
                head = content[:300]
                markers = ["好的，", "首先，", "用户", "我需要", "接下来", "让我",
                           "根据规则", "总结：", "分析"]
                hit = [m for m in markers if m in head]
                print(f"    推理腔标记  : {hit}")
            else:
                print("    ✅ content 为空 —— 可明确判定「没有产出答案」")
        except Exception as exc:
            print(f"\n  【{label}】 调用失败: {type(exc).__name__}: {exc}")
    print()
