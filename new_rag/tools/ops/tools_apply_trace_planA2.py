# -*- coding: utf-8 -*-
"""方案 A 第二步：删掉随之变成死代码的三块。

  * `_StepCtx` 类 —— 只服务于 `with rec.step(...)`
  * `step()` 方法 —— **全工程从未被调用过**（grep 确认）
  * `_append()` 方法 —— 只被 mark_error/mark_timeout/step 调用，
    而它写的 `_steps` 已经在第一步被替换成 `_context_parts`

删完之后 `mark_error` / `mark_timeout` 只改状态字段，不再往步骤表塞行。
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(errors="replace")

P = Path(r"C:\Users\Lenovo\Desktop\new_rag\tracing.py")
shutil.copy2(P, P.with_suffix(f".py.bak_planA2_{time.strftime('%Y%m%d_%H%M%S')}"))

text = P.read_text(encoding="utf-8")
orig = len(text)
n = 0


def cut(start_marker: str, end_marker: str, label: str) -> None:
    """删掉 [start_marker, end_marker) 区间。"""
    global text, n
    i0 = text.find(start_marker)
    i1 = text.find(end_marker, i0 + 1) if i0 != -1 else -1
    if i0 == -1 or i1 == -1:
        print(f"  [!!] {label}: 定位失败 (i0={i0}, i1={i1})")
        return
    text = text[:i0] + text[i1:]
    n += 1
    print(f"  [OK] {label}: 删除 {i1 - i0} 字符")


# 1) 删 _StepCtx 类（从 @dataclass 到 TraceRecorder 的 @dataclass）
cut("@dataclass\nclass _StepCtx:", "@dataclass\nclass TraceRecorder:",
    "1) 删 _StepCtx 类")

# 2) 删 _append 方法（从 "# -- 步骤埋点 --" 到 note_step 定义）
cut("    # -- 步骤埋点 --\n    def _append(", "    def note_step(",
    "2) 删 _append 方法")

# 3) 删 step() 上下文管理器（从 @contextmanager 到 "# -- 收尾 --"）
cut("    @contextmanager\n    def step(", "    # -- 收尾 --",
    "3) 删 step() 上下文管理器")

# 4) mark_error / mark_timeout 不再调 _append
OLD4 = '''    def mark_error(self, error_type: str, detail: str = "") -> None:
        self.status = "error"
        self.error_type = error_type
        self._append("error", detail or error_type, ok=False, error_type=error_type)

    def mark_timeout(self, detail: str = "") -> None:
        self.status = "timeout"
        self.error_type = "TimeoutError"
        self._append("error", detail or "timeout", ok=False, error_type="TimeoutError")
'''
NEW4 = '''    def mark_error(self, error_type: str, detail: str = "") -> None:
        """标记本次请求出错。方案 A 之后只改状态字段，不写步骤行。"""
        self.status = "error"
        self.error_type = error_type
        if detail:
            self.add_context(f"[error] {detail}")

    def mark_timeout(self, detail: str = "") -> None:
        """标记本次请求超时。"""
        self.status = "timeout"
        self.error_type = "TimeoutError"
        if detail:
            self.add_context(f"[timeout] {detail}")
'''
if text.count(OLD4) == 1:
    text = text.replace(OLD4, NEW4)
    n += 1
    print("  [OK] 4) mark_error / mark_timeout 不再调 _append")
else:
    print(f"  [!!] 4) 命中 {text.count(OLD4)} 次")

P.write_text(text, encoding="utf-8")
print()
print(f"完成：应用 {n} 处，{orig} → {len(text)} 字符（-{orig - len(text)}）")
