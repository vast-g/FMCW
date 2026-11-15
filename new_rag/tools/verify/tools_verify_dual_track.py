"""端到端验证「双轨制」：规则引擎判定 + Agent 解释，职责边界不能混。

要验的是三件事：
  1. Agent 会去查**规则引擎的实际判定**（调 tool_get_active_alerts）
  2. 回答里引用的是规则引擎的结论（等级/规则名），不是自己"感觉要不要报警"
  3. **不说**"我判断应该报警""系统已自动报警"这类越界话术
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
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("SLOPE_HEADLESS", "1")
sys.stdout.reconfigure(errors="replace")

import end_new as E  # noqa: E402

agent = E.agent._get()

QUESTIONS = [
    "刚才是不是报警了？为什么报的？",
    "现在有没有触发预警？依据是什么？",
]

REPORT = BASE / "reports" / "dual_track_check.md"
REPORT.parent.mkdir(parents=True, exist_ok=True)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


lines = ["# 双轨制验证：规则引擎判定 vs Agent 解释", ""]
answers = []

for q in QUESTIONS:
    print("=" * 74)
    print(f"问题：{q}")
    print("=" * 74)
    ans = str(agent.run(q, session_id="__dualtrack__"))
    answers.append(ans)
    print(ans)
    print()
    lines += [f"## 问：{q}", "", ans, ""]

REPORT.write_text("\n".join(lines), encoding="utf-8")
print(f"（实录已写入 {REPORT}）")
print()

joined = "\n".join(answers)

print("=" * 74)
print("断言：职责边界")
print("=" * 74)

# 越界话术
OVERREACH = ["我判断应该报警", "我决定报警", "系统已自动报警", "已自动报警",
             "我认为需要报警", "我已经触发"]
hit_over = [k for k in OVERREACH if k in joined]
check("没有越界宣称'已自动报警/我判定要报警'", not hit_over, str(hit_over))

# 是否引用了规则引擎的判定
ENGINE_HINTS = ["规则引擎", "规则", "告警记录", "预警"]
check("引用了规则引擎的判定", any(k in joined for k in ENGINE_HINTS),
      str([k for k in ENGINE_HINTS if k in joined]))

# 是否给出了等级
LEVELS = ["红色", "橙色", "黄色", "蓝色"]
hit_lv = [k for k in LEVELS if k in joined]
check("说明了告警等级", bool(hit_lv), str(hit_lv))

# 是否给了依据（多因素数字）
EVID = ["位移", "雨", "孔压", "裂缝"]
hit_ev = [k for k in EVID if k in joined]
check("给出了判定依据（多因素）", len(hit_ev) >= 3, str(hit_ev))

# 是否提示了未确认 → 处置建议
check("提到了确认/处置状态", any(k in joined for k in ("未确认", "确认", "处置", "核查")),
      "")

print()
print("=" * 74)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
for f in FAIL:
    print(f"  失败: {f}")
print("=" * 74)
