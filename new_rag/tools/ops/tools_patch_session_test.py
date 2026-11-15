# -*- coding: utf-8 -*-
"""更新会话隔离测试：窗口从 20 条改成 10 条之后，把写死的期望值改成读常量。

原断言是「每个会话恰好 20 条」—— 那个 20 恰好等于旧窗口大小，
所以它其实**看不出来窗口有没有生效**（写 20 条、窗口 20 条，凑巧对）。
现在写 40 条、断言 == MAX_ENTRIES，同时验证了"窗口生效"和"会话隔离"两件事。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(errors="replace")

P = Path(r"C:\Users\Lenovo\Desktop\new_rag\tools\verify\tools_verify_session_fix.py")
s = P.read_text(encoding="utf-8")
orig = s
n = 0

# ① 写 20 条 → 写 40 条（远超窗口，才能看出窗口有没有截）
OLD1 = 'ws = [threading.Thread(target=writer, args=(f"s{k}", 20)) for k in range(5)]'
NEW1 = ('# 每会话写 40 条，远超窗口 —— 这样"窗口生效"和"会话隔离"能同时被验证。\n'
        '# 原来是 20 条，恰好等于旧窗口大小，看不出窗口到底有没有起作用。\n'
        'ws = [threading.Thread(target=writer, args=(f"s{k}", 40)) for k in range(5)]')
if s.count(OLD1) == 1:
    s = s.replace(OLD1, NEW1)
    n += 1
else:
    print(f"  [!!] ① 命中 {s.count(OLD1)} 次")

# ② 断言 20 → 断言窗口常量
OLD2 = '    if len(rows) != 20 or any(r[0] != sid for r in rows):'
NEW2 = ('    # 期望值不写死：窗口大小就是 _PersistentHistory.MAX_ENTRIES（当前 10 条 = 5 轮）。\n'
        '    # 之前这里写死 20，把窗口改成 5 轮之后这条就假失败了。\n'
        '    if len(rows) != _WINDOW or any(r[0] != sid for r in rows):')
if s.count(OLD2) == 1:
    s = s.replace(OLD2, NEW2)
    n += 1
else:
    print(f"  [!!] ② 命中 {s.count(OLD2)} 次")

# ③ 断言文案
OLD3 = 'check("每个会话恰好 20 条且内容未串", not bad, f"异常={bad}")'
NEW3 = 'check(f"每个会话恰好 {_WINDOW} 条（窗口上限）且内容未串", not bad, f"异常={bad}")'
if s.count(OLD3) == 1:
    s = s.replace(OLD3, NEW3)
    n += 1
else:
    print(f"  [!!] ③ 命中 {s.count(OLD3)} 次")

# ④ 在使用前定义 _WINDOW
ANCHOR = "def writer(sid: str, n: int):"
WINDOW_DEF = ('import end_new as _E\n'
              '_WINDOW = _E._PersistentHistory.MAX_ENTRIES   # 窗口上限（条）\n\n\n'
              + ANCHOR)
if s.count(ANCHOR) == 1:
    s = s.replace(ANCHOR, WINDOW_DEF, 1)
    n += 1
else:
    print(f"  [!!] ④ 命中 {s.count(ANCHOR)} 次")

P.write_text(s, encoding="utf-8")
print(f"  应用 {n} 处，{len(orig)} → {len(s)} 字符")
