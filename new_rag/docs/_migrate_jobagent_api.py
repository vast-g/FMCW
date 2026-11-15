# -*- coding: utf-8 -*-
"""把求职项目的 Agent 构建从已废弃的 create_react_agent 迁到 create_agent。

## 为什么改

跑代码时 LangGraph 直接报警：

    LangGraphDeprecatedSinceV10: create_react_agent has been moved to `langchain.agents`.
    Please update your import to `from langchain.agents import create_agent`.
    Deprecated in LangGraph V1.0 to be removed in V2.0.

## 实测差异（我用真实 model 构图对比过）

| | 旧 create_react_agent | 新 create_agent |
|---|---|---|
| 返回类型 | CompiledStateGraph | CompiledStateGraph（一样） |
| 节点 | `__start__ / agent / tools / __end__` | `__start__ / **model** / tools / __end__` |
| 边 | 4 条，结构一致 | 4 条，结构一致 |
| 位置参数 | `(model, tools)` | `(model, tools)`（兼容） |
| ainvoke 签名 | 一致 | 一致 |

**唯一实质差异：把"调模型那个节点"从 `agent` 改名成了 `model`。**
本仓库没有任何代码按节点名取用 LangGraph 节点（`supervisor.py` 里的
`task["agent"]` 是字典键，不是节点名），所以改名无影响。

## 顺手确认：recursion_limit 的换算不用改

每轮 ReAct 仍然是「调模型 + 执行工具」两次节点执行，
所以 `recursion_limit_for()` 里 `max_turns * 2 + 1` 的算法依然成立。
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(errors="replace")

ROOT = Path(r"C:\Users\Lenovo\Desktop\Find_job\job-agent")
stamp = time.strftime("%Y%m%d_%H%M%S")

FILES = [
    ROOT / "app" / "agents" / "react_agent.py",
    ROOT / "app" / "agents" / "sub_agents.py",
    ROOT / "app" / "core" / "pipeline.py",
    ROOT / "app" / "llm" / "factory.py",
]

for p in FILES:
    if p.exists():
        shutil.copy2(p, p.with_suffix(f".py.bak_createagent_{stamp}"))
print(f"已备份 {len([p for p in FILES if p.exists()])} 个文件")

# ── 真实改动：react_agent.py 的 import 与调用 ──
P = ROOT / "app" / "agents" / "react_agent.py"
t = P.read_text(encoding="utf-8")
before = t

t = t.replace(
    "from langgraph.prebuilt import create_react_agent",
    "# ⚠️ create_react_agent 在 LangGraph V1.0 已废弃（V2.0 移除），\n"
    "# 官方迁移路径是 langchain.agents.create_agent —— 返回的同样是\n"
    "# CompiledStateGraph，节点与边结构一致，只有「调模型那个节点」\n"
    "# 从 `agent` 改名成了 `model`（本仓库没有代码按节点名取值）。\n"
    "from langchain.agents import create_agent",
)
t = t.replace(
    "agent = create_react_agent(self.model, tools)",
    "agent = create_agent(self.model, tools)",
)
t = t.replace(
    '"""ReAct Agent：基于 LangGraph create_react_agent。',
    '"""ReAct Agent：基于 LangChain create_agent（内部是 LangGraph 状态图）。',
)
P.write_text(t, encoding="utf-8")
print(f"  react_agent.py: {'已改' if t != before else '未变'}")

# ── 其余文件只改文档字符串里的 API 名，避免留过时说法 ──
for p, old, new in [
    (ROOT / "app" / "agents" / "sub_agents.py",
     "基于 LangChain 消息 + create_react_agent。",
     "基于 LangChain 消息 + create_agent（LangGraph 状态图）。"),
    (ROOT / "app" / "core" / "pipeline.py",
     "意图识别 -> 编排 -> 执行(create_react_agent / Workflow) -> 记忆写入 -> 返回",
     "意图识别 -> 编排 -> 执行(create_agent / Workflow) -> 记忆写入 -> 返回"),
    (ROOT / "app" / "llm" / "factory.py",
     "需要 bind_tools / with_structured_output / create_react_agent 时",
     "需要 bind_tools / with_structured_output / create_agent 时"),
    (ROOT / "app" / "llm" / "factory.py",
     "主 Agent 走的是 `create_react_agent`",
     "主 Agent 走的是 `create_agent`"),
]:
    if not p.exists():
        continue
    s = p.read_text(encoding="utf-8")
    if old in s:
        p.write_text(s.replace(old, new), encoding="utf-8")
        print(f"  {p.name}: 文档已更新")
