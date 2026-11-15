"""改造完整性自检：文件编码、路由埋点、trace 埋点、配置外置程度。"""
import io
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

F = "end_new.py"
s = io.open(F, encoding="utf-8").read()
lines = s.splitlines()

print("=" * 62)
print("1) 文件完整性")
print("=" * 62)
print(f"  总行数            : {len(lines)}")
print(f"  三引号出现次数    : {s.count(chr(34) * 3)}  (应为偶数)")
print(f"  替换字符 U+FFFD   : {s.count(chr(0xFFFD))}  (应为 0)")
print(f"  BOM U+FEFF        : {s.count(chr(0xFEFF))}  (应为 0)")

print()
print("=" * 62)
print("2) 路由埋点（Route Accuracy 的数据来源）")
print("=" * 62)
routes = re.findall(r'_done\((?:ans|response),\s*"([a-z_]+)"\)', s)
from collections import Counter

for r, c in sorted(Counter(routes).items()):
    print(f"  {r:18s} {c} 处")
print(f"  合计 {len(routes)} 处；另含动态 final_route 分支 1 处")

print()
print("=" * 62)
print("3) Trace 埋点（Tool Accuracy 的数据来源）")
print("=" * 62)
print(f"  rec.add_tool(...)  : {s.count('rec.add_tool')} 处")
print(f"  rec.note_step(...) : {s.count('rec.note_step')} 处")
print(f"  TraceRecorder 构造 : {s.count('TraceRecorder(')} 处")
print(f"  DegradationLog     : {s.count('DegradationLog()')} 处")
print(f"  degrade.record     : {s.count('degrade.record')} 处")

print()
print("=" * 62)
print("4) 硬编码残留检查（应尽量为 0）")
print("=" * 62)
leftovers = {
    'qwen3:4b 字面量': s.count('"qwen3:4b"'),
    'qwen3-embedding 字面量': s.count('"qwen3-embedding:4b"'),
    'localhost:11434 字面量': s.count('localhost:11434'),
    'timeout=5) 硬编码': s.count("timeout=5)"),
    'timeout=15) 硬编码': s.count("timeout=15)"),
    'timeout=20) 硬编码': s.count("timeout=20)"),
    'timeout=30) 硬编码': s.count("timeout=30)"),
}
for k, v in leftovers.items():
    flag = "OK" if v == 0 else "残留"
    print(f"  [{flag:4s}] {k:28s} {v}")

print()
print("=" * 62)
print("5) 异常处理质量")
print("=" * 62)
bare = len(re.findall(r'^\s*except\s*:', s, re.M))
broad = len(re.findall(r'except Exception', s))
silent = len(re.findall(r'except[^\n]*:\s*\n\s*pass\s*$', s, re.M))
print(f"  裸 except:            {bare}")
print(f"  except Exception:     {broad}")
print(f"  紧跟 pass 的静默吞没  {silent}")
