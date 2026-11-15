# -*- coding: utf-8 -*-
"""核对：知识库新文档里写的阈值，和系统 `alert_rule` 表的实际配置是否一致。

知识库文档里的数字必须是**系统真正在用的值**，不能凭印象写 ——
否则 Agent 会照着文档说出一个和规则引擎对不上的阈值，
用户一问"到底哪个准"就露馅了。
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

from sqlalchemy import create_engine, text           # noqa: E402
from config import settings as CFG                   # noqa: E402

url = getattr(CFG, "database_url", None) or getattr(CFG, "db_url", None)
e = create_engine(url, pool_pre_ping=True)
with e.connect() as c:
    rows = c.execute(text(
        "select code, name, metric, operator, threshold, level, enabled "
        "from alert_rule order by code")).fetchall()

print("=" * 78)
print("alert_rule 表的实际配置")
print("=" * 78)
for r in rows:
    print(f"  {r[0]:<18} {str(r[1])[:22]:<24} metric={str(r[2]):<18} "
          f"{str(r[3]):<3} thr={r[4]:<10} level={str(r[5]):<6} enabled={r[6]}")
print(f"\n  共 {len(rows)} 条规则")
