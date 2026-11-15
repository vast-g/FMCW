# -*- coding: utf-8 -*-
"""方案 A：把 trace 从「run 一行 + step N 行」压成「一次请求一行」。

## 核心思路

不是"记录所有发生过的事"，而是"记录评测要用的东西"。

评测真正消费的只有 5 样：
    路由(route) · 工具(tools) · 上下文(context_text) · 答案(answer) · 降级(degraded)

其中 `context_text` 是新增的关键列 —— 它是**所有 observation 输出的拼接**，
也就是"模型当时看到了什么"。Faithfulness / Context Recall 靠它判定
"回答有没有胡编"，所以必须单独留一列，不能省、也不能塞进 answer。

## 丢掉了什么（有意的取舍）

  * 每一步的时间戳与单独耗时
  * 每个工具的入参、完整输出（只留工具名数组）

## 换来什么

  1 张表 · 1 次 INSERT · 零 join。想查细节还有控制台日志。

## 兼容性

`note_step()` 保留为**兼容垫片**：kind == "observation" 时转成上下文累积，
其余 kind 直接忽略 —— 这样 `run()` 和 `slope_graph.py` 里那 ~10 处调用
**一行都不用改**。
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(errors="replace")

TRACING = Path(r"C:\Users\Lenovo\Desktop\new_rag\tracing.py")
METRICS = Path(r"C:\Users\Lenovo\Desktop\new_rag\slope_eval\metrics.py")
ENDNEW = Path(r"C:\Users\Lenovo\Desktop\new_rag\end_new.py")

stamp = time.strftime("%Y%m%d_%H%M%S")
for p in (TRACING, METRICS, ENDNEW):
    shutil.copy2(p, p.with_suffix(f".py.bak_planA_{stamp}"))
print(f"已备份 3 个文件（后缀 .bak_planA_{stamp}）")


def patch(path: Path, pairs: list[tuple[str, str, str]]) -> None:
    text = path.read_text(encoding="utf-8")
    for old, new, label in pairs:
        n = text.count(old)
        if n != 1:
            print(f"  [!!] {label}: 命中 {n} 次（期望 1），跳过")
            continue
        text = text.replace(old, new, 1)
        print(f"  [OK] {label}")
    path.write_text(text, encoding="utf-8")


# ══════════════════════════════════════════════════════════════════
# 1) tracing.py：加 context_text 列
# ══════════════════════════════════════════════════════════════════
P1 = ("    # 结果\n"
      "    answer_digest = Column(Text, default=\"\")\n",
      "    # 结果\n"
      "    answer_digest = Column(Text, default=\"\")\n"
      "    # ⭐ 方案 A 的核心新增列：模型当时看到的上下文（所有 observation 拼接）。\n"
      "    # Faithfulness / Context Recall 需要它做蕴含判定 —— 判定\"回答有没有胡编\"。\n"
      "    # 单独成列而不是塞进 answer_digest：两者一个是\"看到什么\"、一个是\"说了什么\"，\n"
      "    # 混在一起评测就无从区分\"没看到却说了\"和\"看到了照着说\"。\n"
      "    context_text = Column(Text, default=\"\")\n")
patch(TRACING, [(*P1, "1) TraceRun 加 context_text 列")])

# ══════════════════════════════════════════════════════════════════
# 2) TraceRecorder：用 context 累积替代 _steps
# ══════════════════════════════════════════════════════════════════
P2 = (
    "    _steps: list[dict] = field(default_factory=list, repr=False)\n"
    "    _t0: float = field(default_factory=time.perf_counter, repr=False)\n"
    "    _saved: bool = field(default=False, repr=False)\n",

    "    # ⭐ 方案 A：不再保存逐步明细，只累积**模型看到的上下文**。\n"
    "    # 原来这里是 `_steps: list[dict]`（N 行明细），评测要 join 才能拼出上下文；\n"
    "    # 现在直接在一次请求里攒成一个字符串，落库就是一行。\n"
    "    _context_parts: list[str] = field(default_factory=list, repr=False)\n"
    "    _t0: float = field(default_factory=time.perf_counter, repr=False)\n"
    "    _saved: bool = field(default=False, repr=False)\n"
    "\n"
    "    # 上下文总长上限。observation 单条上限 2000 字符，多次调用会累积，\n"
    "    # 这里整体再截一次 —— 评测做蕴含判定不需要无限长的上下文，\n"
    "    # 而没有上限的话一次请求可能写进几十 KB。\n"
    "    _CONTEXT_LIMIT = 8000\n",
    "2) TraceRecorder 换掉 _steps，改用 _context_parts",
)
patch(TRACING, [P2])

# 3) set_route 不再 _append
P3 = (
    '    def set_route(self, route: str) -> None:\n'
    '        """标记本次请求实际走的路由分支（Route Accuracy 的唯一来源）。"""\n'
    '        self.route = route\n'
    '        self._append("route", route, ok=True)\n',
    '    def set_route(self, route: str) -> None:\n'
    '        """标记本次请求实际走的路由分支（Route Accuracy 的唯一来源）。\n'
    '\n'
    '        方案 A 之后不再往步骤表写一条 route 记录 —— 它就是 run 行上的一个字段。\n'
    '        """\n'
    '        self.route = route\n',
    "3) set_route 不再写步骤",
)
patch(TRACING, [P3])

# 4) add_tool 后新增 add_context
P4 = (
    '    def add_tool(self, name: str) -> None:\n'
    '        """登记一次真实工具调用（Tool Accuracy 的唯一来源）。"""\n'
    '        if name not in self.predicted_tools:\n'
    '            self.predicted_tools.append(name)\n',
    '    def add_tool(self, name: str) -> None:\n'
    '        """登记一次真实工具调用（Tool Accuracy 的唯一来源）。"""\n'
    '        if name not in self.predicted_tools:\n'
    '            self.predicted_tools.append(name)\n'
    '\n'
    '    def add_context(self, text: str) -> None:\n'
    '        """累积"模型看到的内容"（工具观测 / 检索片段）。\n'
    '\n'
    '        这就是原来 `note_step("observation", ..., output=...)` 存进步骤表的\n'
    '        那份数据，现在直接攒进一次请求的上下文里，落库时作为一个列。\n'
    '        """\n'
    '        s = str(text or "").strip()\n'
    '        if s:\n'
    '            self._context_parts.append(s)\n',
    "4) 新增 add_context 方法",
)
patch(TRACING, [P4])

# 5) note_step 改成兼容垫片
P5 = (
    '    def note_step(\n'
    '        self,\n'
    '        kind: str,\n'
    '        name: str,\n'
    '        args: dict | None = None,\n'
    '        output: str = "",\n'
    '        output_limit: int = 500,\n'
    '    ) -> None:\n'
    '        """记录一个瞬时步骤（如工具调用/工具返回这一事件本身）。\n'
    '\n'
    '        `output_limit` 用于工具返回内容：Faithfulness 与 Context Recall 需要\n'
    '        拿检索到的上下文做蕴含判定，所以观测结果要留够长度（默认 500 字符，\n'
    '        调用方可传入更大值）。\n'
    '        """\n'
    '        self._append(kind, name, args=args, output=output, output_limit=output_limit)\n',

    '    def note_step(\n'
    '        self,\n'
    '        kind: str,\n'
    '        name: str,\n'
    '        args: dict | None = None,\n'
    '        output: str = "",\n'
    '        output_limit: int = 500,\n'
    '    ) -> None:\n'
    '        """**兼容垫片**：方案 A 之后不再记录逐步明细。\n'
    '\n'
    '        只做一件事：`kind == "observation"` 时把 output 累积进上下文\n'
    '        （那是 Faithfulness 唯一需要的过程数据），其余 kind 直接忽略。\n'
    '\n'
    '        保留这个签名是为了让 `run()` 和 `slope_graph.py` 里那 ~10 处调用\n'
    '        一行都不用改 —— 换的是内部实现，不是调用方式。\n'
    '        """\n'
    '        if kind == "observation":\n'
    '            self.add_context(str(output or "")[:output_limit])\n',
    "5) note_step 改成兼容垫片",
)
patch(TRACING, [P5])

# 6) save 只写 run
P6 = (
    '    def save(self, run: dict, steps: list[dict]) -> None:\n'
    '        record = dict(run)\n'
    '        record["steps"] = steps\n'
    '        self._append_jsonl(record)\n'
    '\n'
    '        if not self._ensure_db():\n'
    '            return\n'
    '        try:\n'
    '            with self._Session() as session:  # type: ignore[misc]\n'
    '                session.execute(insert(TraceRun).values(**run))\n'
    '                if steps:\n'
    '                    session.execute(insert(TraceStep), steps)\n'
    '                session.commit()\n'
    '        except Exception as exc:  # noqa: BLE001\n'
    '            log.warning("Agent Trace 落库失败（JSONL 已保存）：%s: %s", type(exc).__name__, exc)\n',

    '    def save(self, run: dict) -> None:\n'
    '        """落库一次请求（方案 A：只有 run 这一行，没有步骤表）。\n'
    '\n'
    '        JSONL 先写（成败都不影响），MySQL 后写（失败只告警）。\n'
    '        这个顺序不变 —— JSONL 是事实来源，MySQL 是便于查询的副本。\n'
    '        """\n'
    '        self._append_jsonl(dict(run))\n'
    '\n'
    '        if not self._ensure_db():\n'
    '            return\n'
    '        try:\n'
    '            with self._Session() as session:  # type: ignore[misc]\n'
    '                session.execute(insert(TraceRun).values(**run))\n'
    '                session.commit()\n'
    '        except Exception as exc:  # noqa: BLE001\n'
    '            log.warning("Agent Trace 落库失败（JSONL 已保存）：%s: %s", type(exc).__name__, exc)\n',
    "6) save 只写 run 一行",
)
patch(TRACING, [P6])

# 7) finish 带上 context_text
P7 = (
    '            "answer_digest": _digest(self.answer, 2000),\n',
    '            "answer_digest": _digest(self.answer, 2000),\n'
    '            # 方案 A：模型看到的上下文，一次性拼好落在这一行里\n'
    '            "context_text": ("\\n\\n".join(self._context_parts))[: self._CONTEXT_LIMIT],\n',
    "7) finish 带上 context_text",
)
patch(TRACING, [P7])

# 8) finish 调用 save(run) 而不是 save(run, steps)
P8 = (
    "        get_store().save(run, self._steps)\n",
    "        get_store().save(run)\n",
    "8) finish 改调 save(run)",
)
patch(TRACING, [P8])

print()
print("tracing.py 改完")
