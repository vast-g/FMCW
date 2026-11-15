# -*- coding: utf-8 -*-
"""把 slope_agent 库里所有表与字段导出来，用于说明记忆模块的数据落点。

只读，不改任何数据。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 路径引导：本文件在 tools/ops/ 下，被测模块在项目根
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

sys.stdout.reconfigure(errors="replace")

from sqlalchemy import create_engine, inspect, text

from config import settings as CFG

url = getattr(CFG, "database_url", None) or getattr(CFG, "db_url", None)
e = create_engine(url, pool_pre_ping=True)
insp = inspect(e)

# 按用途分组，方便讲"记忆相关的表有哪些"
GROUPS = {
    "记忆直接相关": ["memory", "user_profile", "conversation_summary", "conversation", "message"],
    "监测数据": ["site", "sensor_reading", "alert_rule", "alert_event"],
    "可观测": ["agent_trace", "trace_run", "trace_step", "events", "feedback"],
    "其它": ["user"],
}

tables = sorted(insp.get_table_names())
print("=" * 78)
print(f"slope_agent 库共 {len(tables)} 张表")
print("=" * 78)

known = set()
for g, names in GROUPS.items():
    known.update(names)

for group, names in GROUPS.items():
    print(f"\n### {group}")
    for t in names:
        if t not in tables:
            print(f"  {t}  （不存在）")
            continue
        cols = insp.get_columns(t)
        try:
            with e.connect() as c:
                n = c.execute(text(f"select count(*) from `{t}`")).scalar()
        except Exception:
            n = "?"
        pk = insp.get_pk_constraint(t).get("constrained_columns") or []
        idx = insp.get_indexes(t)
        print(f"  ── {t}  ({n} 行)")
        for c in cols:
            flags = []
            if c["name"] in pk:
                flags.append("PK")
            if not c.get("nullable", True):
                flags.append("NOT NULL")
            print(f"       {c['name']:<22} {str(c['type']):<22} {' '.join(flags)}")
        if idx:
            for i in idx:
                u = "UNIQUE" if i.get("unique") else "INDEX"
                print(f"       [{u}] {i.get('name')} → {i.get('column_names')}")

rest = [t for t in tables if t not in known]
if rest:
    print("\n### 未归类的表")
    for t in rest:
        print(f"  {t}")
