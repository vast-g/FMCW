# -*- coding: utf-8 -*-
"""验证 `_format_passages` 的截断行为（用户实际看到的"像是没输出完"）。

要求：
  * 内容没超预算时**不能**出现截断标记（别没事就吓人）
  * 超预算时必须在**行边界**断开，并**显式标注**已截断
    （原来是 `[:1400]` 字符硬切，正好把句子切一半，看起来像输出中断）
"""
from __future__ import annotations

import os
import sys
import types

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

import end_new as E                          # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def mk(txt, src):
    d = types.SimpleNamespace()
    d.page_content = txt
    d.metadata = {"source": src}
    return d


fmt = E.VectorRAG._format_passages

print("=" * 74)
print("_format_passages 截断行为")
print("=" * 74)

print("\n1) 内容没超预算 → 不该有截断标记")
out = fmt([mk("短片段一，内容完整。", "a.txt"), mk("短片段二。", "b.txt")])
check("没有截断标记", "已截断" not in out, f"长度 {len(out)}")
check("完整保留两条", "[1]" in out and "[2]" in out)
check("结尾是完整句子", out.rstrip().endswith("。"), repr(out.rstrip()[-12:]))

print("\n2) 内容超预算 → 必须在行边界断开 + 显式标注")
# 必须**确实超过预算**才能触发截断分支。第一版写了 39 行，
# 算下来 2578 字刚好卡在 2581 的预算内 —— 没触发截断，于是断言"有截断标记"失败。
# 这是测试用例的问题（输入不够长），不是实现的问题。
long_txt = "\n".join(f"第{i}行：" + "内容" * 40 for i in range(1, 90))
out2 = fmt([mk(long_txt, "big.txt")])
check("有截断标记", "已截断" in out2, f"长度 {len(out2)}")
check("长度受控（不超过预算太多）", len(out2) < 2900, f"{len(out2)} 字")
body = out2.rstrip()
check("标记出现在末尾", body.endswith("此处已截断）"), repr(body[-16:]))
# 截断前那一行应当是完整的（以"内容"结尾，而不是"内"）
head = body.split("…（片段较长")[0].rstrip()
check("断开处是整行，不是半句", head.endswith("内容") and not head.endswith("内 "),
      repr(head[-14:]))

print("\n3) 边界情况")
check("空列表有兜底", fmt([]) == "未找到相关内容")
check("全空内容有兜底", fmt([mk("", "x.txt")]) == "未找到相关内容")

print()
print("=" * 74)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
for f in FAIL:
    print(f"  失败: {f}")
print("=" * 74)
