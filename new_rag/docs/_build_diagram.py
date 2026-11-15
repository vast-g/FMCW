# -*- coding: utf-8 -*-
"""validate / deliver / visual-check 一条龙，输出精简结果。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(errors="replace")

BASE = Path(r"C:\Users\Lenovo\.dsh\profiles\desktop\node_modules\@tt-a1i\archify-dsh\skills\archify")
SPEC = r"C:\Users\Lenovo\Desktop\new_rag\docs\react-agent.workflow.json"
HTML = r"C:\Users\Lenovo\Desktop\ReAct-架构图.html"


def run(*args):
    p = subprocess.run(["node", "bin/archify.mjs", *args],
                       cwd=str(BASE), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    i = out.find("{")
    return (json.loads(out[i:]) if i >= 0 else {}), p.returncode


d, rc = run("validate", "workflow", SPEC, "--quality", "showcase", "--json")
print("=== validate ===")
print("  ok =", d.get("ok"))
err = d.get("error") or ""
for line in [x.strip() for x in err.split("\n") if x.strip().startswith("-")][:12]:
    print("  -", line[:170])
if d.get("ok"):
    d2, rc2 = run("deliver", "workflow", SPEC, HTML, "--quality", "showcase", "--json")
    v = d2.get("validation") or {}
    print("=== deliver ===")
    print(f"  ok = {d2.get('ok')}  checks {v.get('checksPassed')}/{v.get('checkCount')}"
          f"  errors {v.get('errors')}  warnings {v.get('warnings')}")
    if d2.get("ok"):
        d3, _ = run("visual-check", HTML, "--json")
        c = d3.get("containment") or {}
        print("=== visual-check ===")
        print("  containment:", c.get("status"))
        for vw in (c.get("viewports") or []):
            print(f"    {vw.get('width')}x{vw.get('height')}  ok={vw.get('ok')}")
