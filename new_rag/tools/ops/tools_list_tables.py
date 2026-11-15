# -*- coding: utf-8 -*-
"""列出库里所有表 + 行数（只读）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)
sys.stdout.reconfigure(errors="replace")

from sqlalchemy import create_engine, inspect, text

from config import settings as CFG

e = create_engine(getattr(CFG, "database_url", None) or getattr(CFG, "db_url", None))
insp = inspect(e)
rows = []
for t in sorted(insp.get_table_names()):
    with e.connect() as c:
        n = c.execute(text("select count(*) from `" + t + "`")).scalar()
    rows.append((t, n))

print(f"共 {len(rows)} 张表")
for t, n in rows:
    print(f"  {t:<24} {n:>7} 行")
