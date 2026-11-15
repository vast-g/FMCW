# -*- coding: utf-8 -*-
"""读取 visual-check 的结果，打印每个视口的容纳情况。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(errors="replace")

BASE = Path(r"C:\Users\Lenovo\.dsh\profiles\desktop\node_modules\@tt-a1i\archify-dsh\skills\archify")
HTML = r"C:\Users\Lenovo\Desktop\ReAct-架构图.html"

p = subprocess.run(
    ["node", "bin/archify.mjs", "visual-check", HTML, "--json"],
    cwd=str(BASE), capture_output=True, text=True, encoding="utf-8", errors="replace",
)
out = (p.stdout or "") + (p.stderr or "")
i = out.find("{")
data = json.loads(out[i:]) if i >= 0 else {}
c = data.get("containment") or {}
print("containment:", c.get("status"))
for v in (c.get("viewports") or []):
    print(f"  {v.get('width')}x{v.get('height')}  ok={v.get('ok')}"
          f"  scroll={v.get('scrollWidth')}x{v.get('scrollHeight')}"
          f"  inner={v.get('innerWidth')}x{v.get('innerHeight')}")
print()
shots = data.get("screenshots") or data.get("captures") or []
if shots:
    print("截图：")
    for s in shots[:4]:
        print("  ", s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)[:160])
print()
print("contactSheet:", data.get("contactSheet"))
