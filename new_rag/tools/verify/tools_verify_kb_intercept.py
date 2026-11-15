# -*- coding: utf-8 -*-
"""回归：知识类问题被关键词拦截器吞掉（用户实际截图里的三个问题）。

## 现象

    问：给我讲讲几个因素是怎么影响边坡是否滑坡的？
    答：当前无距离数据,请检查传感器连接。      ← 固定兜底话术

## 根因

`_execute_multi_intent` 里有个裸的"滑坡"关键词，只要出现两字就调滑坡判断，
而它在 `run()` 的**第 0 步**执行（所有知识类守卫之前）→ 提前返回，
RAG 和 Agent 永远没机会跑。

之前修"传感器遮蔽"只给第 5/6 步加了守卫，**漏了第 0 步** ——
同一类缺陷只修了一半，所以这次把三个问题都固化进测试。

## 覆盖三层

  1. `_is_knowledge_question` 对这三个问题都应判为 True（纯函数）
  2. **路由不该是 tool_direct / slip_judgment**（那两条正是被吞掉的路由）
  3. 真实跑一次，回答里不能出现那句兜底话术
"""
from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(errors="replace")

# ── 路径引导 ──
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
os.environ.setdefault("SLOPE_HEADLESS", "1")
# ────────────────

PASS: list[str] = []
FAIL: list[str] = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


# 用户截图里的原话（一字不改）
USER_QUESTIONS = [
    "孔隙水压力和滑坡是什么关系？",
    "给我讲讲几个因素是怎么影响边坡是否滑坡的？",
    "影响边坡滑坡的因素？",
]

# 要状态的短口令 —— 这些**必须仍然秒回**，不能被新守卫误伤
STATUS_PHRASES = [
    "距离多少",
    "传感器正常吗",
    "现在安全吗",
    "会滑坡吗",
    "定位坐标",
]

CANNED = "当前无距离数据"

print("=" * 74)
print("回归：知识类问题被关键词拦截器吞掉")
print("=" * 74)

import end_new as E                      # noqa: E402

agent = E.agent

print("\n1) _is_knowledge_question 判据（纯函数）")
for q in USER_QUESTIONS:
    check(f"{q[:22]!r} 判为知识类", agent._is_knowledge_question(q) is True)

print("\n2) 要状态的短口令不能被误伤（仍是确定性命中）")
for q in STATUS_PHRASES:
    kb = agent._is_knowledge_question(q)
    check(f"{q[:16]!r} 不判为知识类", kb is False, f"_is_knowledge_question={kb}")

print("\n3) 静态检查：第 0 步确实加了守卫")
src = open("end_new.py", encoding="utf-8").read()
check("单意图路径有 `not _kb_question` 守卫", "if not comprehensive and not _kb_question:" in src)
check("复合拆分路径也加了守卫", "_kb_question = self._is_knowledge_question(user_input)" in src)
check("守卫变量在两条路径之前就绪",
      src.index("_kb_question = self._is_knowledge_question")
      < src.index("if not comprehensive and not _kb_question:"))

print("\n4) 真实跑一次（走完整链路，约 30~90 秒）")
q = USER_QUESTIONS[1]                    # 截图里那句

# ★ 必须显式加载 RAG。
#   本项目**无头模式下故意不自动加载知识库**（见 end_new.py 里那段：
#   `if not headless and not SLOPE_HEADLESS: Thread(target=load_rag).start()`
#   —— 目的是"避免导入即产生副作用"）。所以评测/测试要自己调 ensure_rag()。
#   这次修完路由之后问题从 analysis 改判成 knowledge、真的去查知识库了，
#   才把这个前提暴露出来（之前走 analysis 不碰知识库，看不见）。
_rag = E.ensure_rag()
check("知识库已就绪", _rag is not None and getattr(E, "rag_state", "") == "ready",
      f"rag_state={getattr(E, 'rag_state', '?')}")

try:
    ans = agent.run(q, session_id="kbguard-verify")
    ans = str(ans or "")
    check("回答不是那句兜底话术", CANNED not in ans, f"回答 {len(ans)} 字")
    check("回答里没有\"知识库未加载\"", "知识库未加载" not in ans)
    # 诚实地说"知识库未收录"是**合法结果**（检索确实没命中），不算失败 ——
    # 它和"被关键词拦截器吞掉、返回一条与问题无关的兜底话术"是两回事。
    # 前者说明链路通了、只是语料没覆盖；后者说明问题根本没走到检索。
    honest_miss = "未找到" in ans or "未收录" in ans
    check("回答有实质内容，或诚实说明未收录",
          len(ans) > 30 or honest_miss,
          ("（未收录：说明链路通了，是语料没覆盖）" if honest_miss else ans[:80]))
    check("不是被拦截器吞掉（意图确实走到了知识库）",
          agent.stats.get("last_intent") == "knowledge")
    intent = agent.stats.get("last_intent")
    check("意图判为知识类（会去查知识库）", intent == "knowledge", f"intent={intent}")

    # ── 输出格式：这两条都是用户实际截图里看到的毛病 ──
    # 1) 前缀叠了两层（兜底路径又加了个头，盖在检索原文自带的头上面）
    check("没有叠两层的标题",
          ans.count("以下是从知识库检索到的原文片段") <= 1
          and "以下是知识库中相关的内容" not in ans,
          f"检索头出现 {ans.count('以下是从知识库检索到的原文片段')} 次")
    # 2) 字符硬切把句子切成两半 —— 截断必须显式标注，不能静默丢内容。
    #    判据：若以"…（片段较长，此处已截断）"结尾则合规；否则不该出现半句结尾。
    truncated_ok = ans.rstrip().endswith("此处已截断）")
    looks_cut = (not truncated_ok) and len(ans) >= 2500 and not ans.rstrip().endswith(("。", "！", "？", "）", "：", "\"", "」"))
    check("没有静默的字符硬切（截断必有标注）", not looks_cut,
          f"结尾: {ans.rstrip()[-24:]!r}")
    print(f"      识别意图: {intent}")
    print(f"      回答节选: {ans[:180]}")
except Exception as exc:
    check("真实跑一次", False, f"{type(exc).__name__}: {exc}")

print()
print("=" * 74)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
for f in FAIL:
    print(f"  失败: {f}")
print("=" * 74)
