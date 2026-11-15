"""统一跑全部验证套件，输出汇总表。

单条命令看全貌，避免"改了 A 忘了 B 回归"。
"""
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
import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent          # tools/
ROOT = BASE.parent                               # 项目根
VERIFY = BASE / "verify"
sys.stdout.reconfigure(errors="replace")

# 套件清单。
# 整理进 tools/verify/ 之后，路径要带上子目录；子进程用 cwd=项目根 启动
# （每个套件自己也会 chdir 到项目根 —— 见它们头部的「路径引导」）。
SUITES = [
    ("会话隔离（第0批）", "tools/verify/tools_verify_session_fix.py"),
    ("多因素数据层", "tools/verify/tools_verify_monitor.py"),
    ("6 个新工具", "tools/verify/tools_verify_new_tools.py"),
    ("Redis 会话存储（第2A批）", "tools/verify/tools_verify_redis_session.py"),
    ("告警规则引擎（第2B批）", "tools/verify/tools_verify_alert_engine.py"),
    ("双轨制边界（第2B批）", "tools/verify/tools_verify_dual_track.py"),
    # ── 以下为「显式控制流图 + 长期画像 + 读链路」改造后新增的套件 ──
    ("控制流图结构与路由", "tools/verify/tools_verify_slope_graph.py"),
    ("长期画像记忆", "tools/verify/tools_verify_profile_memory.py"),
    ("读链路 Java/Python", "tools/verify/tools_verify_data_readpath.py"),
    ("思考泄漏", "tools/verify/tools_verify_thinking_leak.py"),
    ("知识类问题被拦截", "tools/verify/tools_verify_kb_intercept.py"),
    ("检索片段截断", "tools/verify/tools_verify_passage_truncation.py"),
    # ── 记忆方案 B：5 轮滑动窗口 + 滚动摘要 + 去掉 FAISS 语义事实 ──
    ("记忆滑动窗口", "tools/verify/tools_verify_memory_window.py"),
    # ── trace 双写清理：下线 agent_trace，统一走 TraceRecorder ──
    ("trace 链路清理", "tools/verify/tools_verify_trace_cleanup.py"),
    # ── trace 方案 A：run 一行 + context_text，去掉步骤表 ──
    ("trace 单行化", "tools/verify/tools_verify_trace_planA.py"),
]

print("=" * 72)
print("全部验证套件")
print("=" * 72)
print(f"  {'套件':<26} {'通过':>10}  状态")
print("  " + "-" * 56)

total_ok = total_all = 0
failed = []
for name, script in SUITES:
    if not (ROOT / script).exists():
        print(f"  {name:<26} {'—':>10}  缺失")
        continue
    try:
        # cwd 用项目根而不是脚本所在目录：套件里的相对路径（rag_docs/、reports/）
        # 都是相对项目根的。脚本自己的路径引导也会 chdir 到根，两边一致。
        p = subprocess.run([sys.executable, script], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=1800,
                           encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        print(f"  {name:<26} {'—':>10}  超时")
        failed.append(name)
        continue

    m = re.search(r"通过\s+(\d+)\s*/\s*(\d+)", out)
    if m:
        ok, all_ = int(m.group(1)), int(m.group(2))
        total_ok += ok
        total_all += all_
        status = "OK" if ok == all_ else "**有失败**"
        if ok != all_:
            failed.append(name)
        print(f"  {name:<26} {f'{ok}/{all_}':>10}  {status}")
    else:
        # 有的套件用 [PASS]/[FAIL] 计数
        ok = len(re.findall(r"\[PASS\]", out))
        bad = len(re.findall(r"\[FAIL\]", out))
        total_ok += ok
        total_all += ok + bad
        status = "OK" if bad == 0 else f"**{bad} 项失败**"
        if bad:
            failed.append(name)
        print(f"  {name:<26} {f'{ok}/{ok + bad}':>10}  {status}")

print("  " + "-" * 56)
print(f"  {'合计':<26} {f'{total_ok}/{total_all}':>10}")
if failed:
    print()
    print("  有失败的套件：" + "、".join(failed))
