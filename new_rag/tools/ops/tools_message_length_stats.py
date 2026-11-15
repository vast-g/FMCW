# -*- coding: utf-8 -*-
"""统计真实对话消息的长度，用来判断"5 轮 vs 10 轮"到底占多少 token。

只读。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)
sys.stdout.reconfigure(errors="replace")

from sqlalchemy import create_engine, text

from config import settings as CFG

e = create_engine(getattr(CFG, "database_url", None) or getattr(CFG, "db_url", None))

with e.connect() as c:
    rows = c.execute(text(
        "select role, char_length(content) n from message "
        "where role in ('user','ai') order by id desc limit 200")).fetchall()

if not rows:
    print("  message 表里没有对话数据")
    raise SystemExit(0)

lens = [r[1] for r in rows]
lens.sort()
n = len(lens)
avg = sum(lens) / n
med = lens[n // 2]
p90 = lens[int(n * 0.9)]
mx = lens[-1]

print(f"  样本 {n} 条消息（最近 200 条）")
print(f"  平均长度 : {avg:8.0f} 字")
print(f"  中位数   : {med:8} 字")
print(f"  P90      : {p90:8} 字")
print(f"  最大     : {mx:8} 字")
print()
print("  按轮（一轮 = 用户 + AI 两条）估算：")
for r in (3, 5, 8, 10, 15):
    chars = r * avg * 2
    # 中文粗估：1 汉字 ≈ 1 token（qwen 系大致如此），再加少量标记开销
    tok = chars * 1.0
    print(f"    {r:>2} 轮 ≈ {chars:7.0f} 字 ≈ {tok:7.0f} token"
          f"   （占 32k 窗口 {tok / 32768 * 100:4.1f}%）")
print()
print("  如果按 P90（长对话）估：")
for r in (5, 10):
    chars = r * p90 * 2
    print(f"    {r:>2} 轮 ≈ {chars:7.0f} 字 ≈ {chars:7.0f} token"
          f"   （占 32k 窗口 {chars / 32768 * 100:4.1f}%）")
