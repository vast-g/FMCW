"""清理验证脚本写入的测试会话数据，避免污染用户库。"""
from __future__ import annotations


# ── 路径引导（本脚本已整理进 tools/ 子目录）──────────────────────────
# 项目根**不能靠"上跳固定层数"来算** —— 脚本可能在 tools/（一层）
# 也可能在 tools/verify/ 或 tools/ops/（两层），层数一变就算错。
# 所以改成「向上找标志文件」：一路往上，第一个含 config.py 的目录就是项目根。
#
# 需要它做三件事：
#   1) 项目根加进 sys.path → import config / end_new 才能成功
#   2) 本目录与同级 tools/* 也加进去 → verify/ 与 ops/ 之间可以互相 import
#   3) chdir 到项目根 → rag_docs / reports/ 这类相对路径仍指向同一个地方
import os as _os
import sys as _sys

_ME = _os.path.dirname(_os.path.abspath(__file__))
_ROOT = _ME
while _ROOT != _os.path.dirname(_ROOT):
    if _os.path.exists(_os.path.join(_ROOT, "config.py")):
        break
    _ROOT = _os.path.dirname(_ROOT)

for _p in (_ROOT, _ME):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
for _sib in ("verify", "ops"):
    _p = _os.path.join(_ROOT, "tools", _sib)
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.append(_p)
_os.chdir(_ROOT)
# ────────────────────────────────────────────────────────────────
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("SLOPE_HEADLESS", "1")
sys.stdout.reconfigure(errors="replace")

from urllib.parse import quote_plus  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

import config as _cfg  # noqa: E402

SIDS = ["__api_verify_A__", "__api_verify_B__", "__verify_A__", "__verify_B__",
        "__tooltest__", "__probe__", "__dump__", "__dualtrack__", "__nginx_check__",
        "kb-check-1", "persist-check",
        "eval-testset-1", "eval-testset-2", "eval-testset-3", "eval-testset-4",
        "eval-testset-5", "eval-multifactor-1", "eval-multifactor-2",
        "eval-multifactor-3", "eval-multifactor-4", "eval-multifactor-5",
        "eval-dualtrack-1", "eval-dualtrack-2", "eval-dualtrack-3",
        "eval-dualtrack-4", "eval-dualtrack-5",
        "eval-online-1", "eval-online-2", "eval-online-3", "eval-online-4",
        "eval-online-5"]

url = (f"mysql+pymysql://{_cfg.DB_USER}:{quote_plus(_cfg.DB_PASSWORD)}"
       f"@{_cfg.DB_HOST}:{_cfg.DB_PORT}/{_cfg.DB_NAME}")
eng = create_engine(url, echo=False)

removed = 0
with eng.begin() as c:
    # 除了显式列表，也按前缀兜底清理我自己造的验证/评测会话，
    # 避免遗漏（这些都不是用户的真实对话）。
    rows = list(c.execute(text(
        "SELECT id, session_id FROM conversation "
        "WHERE session_id LIKE '\\_\\_%' OR session_id LIKE 'eval-%' "
        "   OR session_id LIKE 'kb-check-%' OR session_id LIKE 'persist-check%'")))
    for cid, sid in rows:
        if sid in ("default", "test_session", "imported_events"):
            continue          # 这三个是项目原有的，不能动
        n = c.execute(text("DELETE FROM message WHERE conversation_id=:c"),
                      {"c": cid}).rowcount
        c.execute(text("DELETE FROM conversation WHERE id=:c"), {"c": cid})
        removed += 1
        print(f"  已删 session={sid!r} (conv id={cid}, {n} 条消息)")
if not removed:
    print("  没有需要清理的测试会话")

with eng.connect() as c:
    print()
    print("=== 清理后 conversation 表 ===")
    for r in c.execute(text("SELECT id, session_id FROM conversation ORDER BY id")):
        print(f"  id={r[0]} session={r[1]!r}")
