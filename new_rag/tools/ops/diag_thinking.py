# -*- coding: utf-8 -*-
"""诊断：qwen3:4b 的思考到底走哪个字段，`think` 开关管不管用。

## 背景

边坡 Agent 会把模型的**整段推理**当成答案返回给用户（已经出现过两次）。
项目里 4 处创建模型都传了 `reasoning=False`，但泄漏依旧 ——
所以要直接问 Ollama：思考是落在 `content` 里，还是单独的 `thinking` 字段？
`think: false` 到底能不能关掉它？

这决定了修法：
  * 如果思考在独立的 `thinking` 字段 → 我们读错了字段，改读数方式即可
  * 如果思考混在 `content` 里且关不掉 → 只能靠"识别 + 剥除 + 兜底重试"
"""
from __future__ import annotations

import json
import sys

import requests

sys.stdout.reconfigure(errors="replace")
P = {"http": None, "https": None}
BASE = "http://127.0.0.1:11434"
MODEL = "qwen3:4b"

PROMPT = "位移速率0.56mm/h、累计雨量170mm、孔压1.65kPa，这几个参数怎么影响边坡监测？"


def show():
    print("=" * 76)
    print("1) 模型能力（capabilities 里有没有 thinking）")
    print("=" * 76)
    r = requests.post(BASE + "/api/show", json={"model": MODEL}, timeout=30, proxies=P)
    j = r.json()
    caps = j.get("capabilities")
    print(f"  capabilities : {caps}")
    det = j.get("details") or {}
    print(f"  family       : {det.get('family')}  params={det.get('parameter_size')}")
    tpl = j.get("template") or ""
    print(f"  模板里有 /no_think : {'/no_think' in tpl}")
    print(f"  模板长度          : {len(tpl)}")


def chat(label, payload_extra, prompt=PROMPT):
    print()
    print("=" * 76)
    print(f"{label}")
    print("=" * 76)
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"num_predict": 2048},
    }
    body.update(payload_extra)
    r = requests.post(BASE + "/api/chat", json=body, timeout=300, proxies=P)
    j = r.json()
    msg = j.get("message") or {}
    content = msg.get("content") or ""
    thinking = msg.get("thinking")

    print(f"  响应顶层字段 : {sorted(j.keys())}")
    print(f"  message 字段 : {sorted(msg.keys())}")
    print()
    print(f"  content  长度: {len(content)}")
    print(f"  thinking 长度: {len(thinking) if thinking is not None else '（无此字段）'}")
    print()
    has_think_tag = "<｜end▁of▁thinking｜>" in content or "<think>" in content or "</think>" in content
    print(f"  content 里含思考标签 : {has_think_tag}")
    print()
    print("  ── content 开头 300 字 ──")
    print("  " + content[:300].replace("\n", "\n  "))
    if thinking:
        print()
        print("  ── thinking 开头 200 字 ──")
        print("  " + str(thinking)[:200].replace("\n", "\n  "))

    # 判断 content 是不是"推理腔"（这次泄漏的特征）
    markers = ["我需要", "首先，", "用户的问题", "根据规则", "接下来", "不过，", "可能需"]
    hit = [m for m in markers if m in content[:2000]]
    print()
    print(f"  ⚠️ content 是否像推理腔: {'是' if len(hit) >= 2 else '否'}  命中 {hit}")
    return content, thinking


if __name__ == "__main__":
    show()
    chat("2) 默认调用（不传 think）", {})
    chat("3) 传 think: false", {"think": False})
    chat("4) 提示词里加 /no_think", {}, prompt=PROMPT + " /no_think")
