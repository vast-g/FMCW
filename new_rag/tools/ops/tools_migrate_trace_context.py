# -*- coding: utf-8 -*-
"""方案 A 收尾：数据库加列 + 评测改取数 + /trace 改成读一行。

SQLAlchemy 的 create_all 不会给已存在的表加列，所以 `context_text` 必须
显式 ALTER。这里做成幂等（先查 information_schema，有了就跳过）。
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

url = getattr(CFG, "database_url", None) or getattr(CFG, "db_url", None)
e = create_engine(url, pool_pre_ping=True)

print("=" * 70)
print("1) 给 trace_run 加 context_text 列（幂等）")
print("=" * 70)
with e.begin() as c:
    n = c.execute(text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name='trace_run' "
        "AND column_name='context_text'")).scalar()
    if n:
        print("  已存在，跳过")
    else:
        c.execute(text("ALTER TABLE trace_run ADD COLUMN context_text TEXT NULL"))
        print("  ✓ 已添加 context_text TEXT")

with e.connect() as c:
    cols = [r[0] for r in c.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name='trace_run' "
        "ORDER BY ordinal_position")).fetchall()]
print(f"  trace_run 现有 {len(cols)} 列：{', '.join(cols)}")
