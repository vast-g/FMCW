# -*- coding: utf-8 -*-
"""单独测：同一个 prompt 在 qwen3 上会产出多长的 thinking。

用途：判断 tools_verify_thinking_leak.py 里"正常预算下有产出"这条
是不是**测试本身不稳定**（模型思考长度随环境波动），而不是代码回归。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)
sys.stdout.reconfigure(errors="replace")

import requests

from config import settings as CFG

prompt = "位移速率0.56mm/h、雨量170mm、孔压1.65kPa，一句话说明这几个参数对边坡的影响。"
P = {"http": None, "https": None}

for budget in (2048, 4096):
    print(f"\n=== num_predict = {budget} ===")
    r = requests.post(f"{CFG.llm_base_url}/api/chat", json={
        "model": CFG.llm_model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": True,
        "options": {"num_predict": budget},
    }, timeout=900, proxies=P)
    if r.status_code != 200:
        print(f"  HTTP {r.status_code}")
        continue
    msg = (r.json() or {}).get("message") or {}
    content = (msg.get("content") or "").strip()
    thinking = msg.get("thinking") or ""
    print(f"  thinking : {len(thinking)} 字")
    print(f"  content  : {len(content)} 字")
    ok = len(content) > 20
    print(f"  断言'有产出' -> {'PASS' if ok else 'FAIL'}")
    if content:
        print("  content 节选：" + content[:100].replace("\n", " "))
