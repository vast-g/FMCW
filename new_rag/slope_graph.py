# -*- coding: utf-8 -*-
"""边坡监测 Agent 的**显式 LangGraph 控制流**。

## 为什么要做这个改造

改造前，整个请求处理写在 ``end_new.py`` 的 ``run()`` 里 —— 一个约 370 行的方法，
带 **11 个提前 return** 和一大堆关键词判断。它能跑，但有几个结构性问题：

1. **控制流不可见**。想知道"一个问题会走哪条路"，只能顺着 `if/return` 读到底。
2. **工具永远全暴露**。``create_agent(model, tools)`` 把 11 个工具一次性给模型，
   不管用户是在问"距离多少"还是"超声波原理"。工具越多，模型选错的概率越大
   （这一点和"输出预算"那个坑是一脉相承的：**上下文越干净，模型表现越稳**）。
3. **循环预算散落**。ReAct 的轮数上限靠 `recursion_limit`、时间预算靠方法里的
   局部变量，两者不在同一个地方，也没法参与"该不该继续"的判断。
4. **无法干预**。想在高风险结论发出前插一道人工确认？黑盒 agent 没有地方插。

## 改造后的形状

把控制流从"代码里的 if 链"搬到"图里的节点与边"：

```
START
  │
  ▼
classify（意图识别：规则优先，LLM 兜底）
  │
  ├─ status    ──→ direct       确定性直答（不花 LLM）
  ├─ recall    ──→ recall       长期记忆/画像查询
  ├─ knowledge ──→ knowledge    知识库检索
  ├─ alert     ──→ rule_lookup  读规则引擎结论（大模型不判定）
  └─ analysis  ──→ agent ⇄ tools   ← ReAct 循环（条件边）
                        │
                        ▼
                     finalize     剥思考段 + 收口
                        │
                        ▼
                       END
```

## 设计要点

* **包装而非重写**：每个节点都调用 ``SimpleAgent`` 上已有的方法
  （``_execute_multi_intent`` / ``_handle_sensor_check`` / ``_check_trend`` …）。
  这样改造不会把已验证过的业务逻辑搞坏 —— 换掉的是**调度方式**，不是**业务实现**。
* **本模块自包含**：只依赖 langchain / langgraph，``agent`` 实例和其它依赖
  通过 ``build_slope_graph(...)`` 参数传入。这样 ``end_new.py`` 可以 import 它，
  而它不需要 import ``end_new``，不存在循环导入。
* **按意图收窄工具**：``INTENT_TOOLS`` 决定每个意图能看见哪些工具。
  模型看不见的工具，它就不可能调用 —— 这是比"在提示词里叮嘱别乱用"更硬的约束。
"""
from __future__ import annotations

import time
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

# ══════════════════════════════════════════════════════════════════════
# 意图定义 与 「意图 → 允许的工具」
# ══════════════════════════════════════════════════════════════════════
#
# 这一步是本次改造的第一个重点：**按意图收窄工具集**。
#
# 改造前 11 个工具全量暴露。实际用起来有两个坏处：
#   * 问"超声波原理"时，模型可能先去调 tool_get_displacement_rate（白花 20 秒）
#   * 可选项越多，小模型的选择越不稳定
#
# 现在按"用户到底想要什么"分组。分组依据来自实际问法：
#   status    要一个当前读数      → 距离/GPS/天气
#   analysis  要综合判断          → 多因素监测 + 告警（要看全才敢下结论）
#   knowledge 问原理/排查         → 知识库
#   alert     问告警/为什么报警    → 规则引擎结论 + 快照
#   recall    问"我叫什么"         → 不需要工具，查记忆
#   chat      闲聊                → 不需要工具
#
INTENT_TOOLS: dict[str, list[str]] = {
    "status": ["tool_get_distance", "tool_get_gps", "tool_get_weather"],
    "analysis": [
        "tool_get_site_snapshot",
        "tool_get_displacement_rate",
        "tool_get_rainfall",
        "tool_get_pore_pressure",
        "tool_get_neighbor_sites",
        "tool_get_crack_history",
        "tool_get_active_alerts",
        # ★ 综合评估也要能查规范/标准。
        #   只给读数工具、不给知识工具，等于让模型"只测不判" ——
        #   被问"这个速率算几级预警"时它只能凭记忆答，而答案在标准文档里。
        "tool_search_kb",
    ],
    "knowledge": ["tool_search_kb"],
    "alert": ["tool_get_active_alerts", "tool_get_site_snapshot"],
    "recall": [],
    "chat": [],
}

# 走 ReAct 的意图（其余意图由确定性节点处理，不花 LLM 推理时间）
REACT_INTENTS = {"analysis", "knowledge", "alert", "status"}


class SlopeState(TypedDict, total=False):
    """图的状态。

    LangGraph 的 state 是一个**贯穿全图、被各节点读写**的字典。
    节点返回的 dict 会被**合并**进全局 state（不是替换）——
    这也是为什么 ``messages`` 要标 ``add_messages``：它把新消息**追加**到列表，
    而不是覆盖整个列表。ReAct 的多轮工具调用就靠这个累积。
    """

    # ── 输入 ──
    session_id: str
    user_input: str

    # ── 分类结果 ──
    intent: str                     # status / analysis / knowledge / alert / recall / chat
    allowed_tools: list[str]        # 本意图允许的工具名
    route: str                      # 实际走过的路由（用于 Trace / Route Accuracy）

    # ── 记忆（长期画像 + 事实召回）──
    profile_text: str               # 画像渲染后的文本，注入系统提示
    summary_text: str               # 滚动摘要（更早对话的压缩版），注入系统提示
    recall_hits: list[str]          # 摘要原文列表（留作观测/排查用）

    # ── ReAct 循环 ──
    messages: Annotated[list, add_messages]
    retry: int                      # 已循环轮数
    deadline: float                 # 本请求的挂钟截止时间戳

    # ── 结果 ──
    sources: list[str]              # RAG 命中的来源
    answer: str
    degraded: list[str]             # 降级记录


def _deadline_exceeded(state: SlopeState) -> bool:
    dl = float(state.get("deadline") or 0)
    return bool(dl) and time.time() > dl


# ══════════════════════════════════════════════════════════════════════
# 意图识别
# ══════════════════════════════════════════════════════════════════════
#
# 刻意做成「**规则优先，LLM 兜底**」而不是一律问模型：
#   * 规则能判的（"距离多少""传感器正常吗"）直接判掉，**0 次 LLM 调用**
#   * 只有规则判不出来时才问模型，多花 2~5 秒是值得的
#
# 这个取舍的理由很实际：绝大多数问法是短口令，让它们都走一次意图识别，
# 等于给每个"距离多少"都加 3 秒延迟。而真正模糊的长问题很少。
#
def classify_intent_rules(text: str) -> str | None:
    """纯规则的意图判定。判不出来返回 None（交给 LLM 兜底）。"""
    t = (text or "").strip()
    if not t:
        return "chat"

    # 闲聊
    if any(k in t for k in ("你好", "谢谢", "再见", "你是谁", "你叫什么", "你能做什么")):
        return "chat"

    # 问"我是谁/我的信息" → 查记忆，不需要工具
    if any(k in t for k in ("我叫什么", "我是谁", "我的名字", "什么名字", "我叫啥",
                            "我是做什么", "我的职业", "我的身份")):
        return "recall"

    # 问告警：明确的告警措辞
    if any(k in t for k in ("告警", "报警", "预警", "触发", "为什么报")):
        return "alert"

    # 知识类：出现"为什么/原因/排查/原理"等解释性措辞。
    #
    # ★ 这份判据必须和 SimpleAgent._is_knowledge_question 保持**同源**。
    #   之前两处各写一份、其中一处补了词另一处没补，导致同一个问题
    #   在"要不要走知识库"上给出相反答案 —— 实测就是把
    #   「讲讲几个因素怎么影响滑坡」判成了 analysis，绕开了知识库，
    #   模型只能凭记忆作答。判据分叉是 bug 温床，所以这里对齐。
    _KB_HINTS = (
        "为什么", "什么原因", "原因", "怎么排查", "如何排查", "排查",
        "原理", "是什么", "有什么区别", "有啥区别", "区别",
        "怎么处理", "如何处理", "怎么办", "怎么回事", "解释",
        "介绍", "有哪些", "讲讲", "原理是", "说说",
        "因素", "成因", "机理", "怎么影响", "如何影响",
        "有何影响", "有什么影响", "什么关系", "有何关系",
        "作用是", "意义是", "介绍一下", "解释一下",
    )
    if any(k in t for k in _KB_HINTS):
        return "knowledge"

    # 综合评估类
    # 注意：知识类判定在上一步，所以这里出现"滑坡/位移"等词时
    # 说明不是在问"原理是什么"，而是在问"现在怎么样"，归 analysis 是对的。
    if any(k in t for k in ("综合", "评估", "分析", "判断", "风险", "滑坡", "会不会",
                            "趋势", "危不危险", "安全吗", "塌", "稳定吗", "稳不稳")):
        return "analysis"

    # 状态类：短口令
    if any(k in t for k in ("距离", "多远", "多少米", "传感器", "正常吗", "掉线",
                            "卡死", "健康", "定位", "坐标", "天气", "气温", "温度")):
        return "status"

    return None


# ══════════════════════════════════════════════════════════════════════
# 构图
# ══════════════════════════════════════════════════════════════════════
def build_slope_graph(
    *,
    agent: Any,
    tools: list,
    model: Any,
    system_prompt: str,
    max_rounds: int = 5,
    intent_llm: Any = None,
    strip_thinking=None,
):
    """构建边坡 Agent 的显式控制流图。

    参数
    ----
    agent      : ``SimpleAgent`` 实例。节点通过它调用已有的业务方法
                 （``_execute_multi_intent`` 等），**不复制任何业务逻辑**。
    tools      : 工具对象列表（``end_new.py`` 里那个 tools 数组）。
    model      : 已配置好的 chat model（会被 ``bind_tools``）。
    system_prompt : ReAct 的系统提示词。
    max_rounds : ReAct 最多循环几轮（图级的硬上限）。
    intent_llm : 可选。规则判不出意图时用它兜底；为 None 时兜底到 analysis。
    strip_thinking : 可选。剥掉模型思考段的函数（``end_new.strip_thinking``）。

        ★ 这个参数是**必须传的**，但做成可选是因为模块要自包含。
        不传的后果很严重 —— 实测过：模型会把推理写进正文，而推理里
        常常出现"我判断应该报警"这类话术，直接**破坏双轨制边界**
        （规则引擎判定，Agent 只解释）。收口节点必须剥掉它。

    返回
    ----
    编译好的图（CompiledStateGraph），调用方式：
        ``graph.invoke(initial_state, config={"recursion_limit": ...})``
    """
    # 工具名 → 工具对象。按意图收窄时要用。
    tool_by_name = {getattr(t, "name", ""): t for t in tools}

    # 剥思考段：没传就退化成恒等函数（并在收口时不做处理）。
    _strip = strip_thinking if callable(strip_thinking) else (lambda s: s)

    # ToolNode 注册**全部**工具：执行权交给图，但"能不能调"由 agent 节点
    # 的 bind_tools 决定 —— 模型看不见的工具它压根发不出 tool_call。
    # 这里保留全量是为了让 ToolNode 能执行任何被合法请求的工具。
    tool_node = ToolNode(tools)

    # ────────────────────── 节点 1：意图识别 ──────────────────────
    def classify_node(state: SlopeState) -> dict:
        text = state.get("user_input", "")
        intent = classify_intent_rules(text)
        how = "规则"

        if intent is None:
            # 规则判不出来 —— 这才是 LLM 兜底的场景（而不是每个请求都问一遍）
            if intent_llm is not None:
                try:
                    from langchain_core.prompts import ChatPromptTemplate
                    prompt = ChatPromptTemplate.from_messages([
                        ("system",
                         "你是意图分类器。把用户的话归入下面某一类，**只输出一个英文单词**：\n"
                         "status=问当前读数(距离/GPS/天气/传感器状态)\n"
                         "analysis=要综合风险评估/趋势判断\n"
                         "knowledge=问原理/标准/排障方法\n"
                         "alert=问告警/为什么报警\n"
                         "chat=闲聊或其它\n"
                         "只输出单词本身，不要解释。"),
                        ("user", "{q}"),
                    ])
                    raw = (prompt | intent_llm).invoke({"q": text})
                    cand = str(getattr(raw, "content", raw)).strip().lower()
                    for k in ("analysis", "knowledge", "alert", "status", "chat"):
                        if k in cand:
                            intent = k
                            how = "LLM兜底"
                            break
                except Exception as exc:      # noqa: BLE001
                    print(f"[意图] LLM 兜底失败: {type(exc).__name__}: {exc}")
            if intent is None:
                # 兜底到 analysis 是刻意的：宁可多查几个工具，也别把
                # 该分析的问题误判成闲聊而答非所问。
                intent = "analysis"
                how = "默认"

        allowed = list(INTENT_TOOLS.get(intent, []))
        print(f"[意图] {text[:28]}… → {intent}（{how}），可用工具 {len(allowed)} 个")
        return {"intent": intent, "allowed_tools": allowed}

    # ────────────── 节点 1.5：加载长期记忆（**所有路径共用**）──────────────
    def load_memory_node(state: SlopeState) -> dict:
        """把长期画像与最近摘要读进 state，供后续节点注入系统提示。

        ## 为什么必须单独成一个节点、且放在分流之前

        第一版接线是错的：我把画像加载写在了 ``recall_node`` 里，而
        ``recall`` 只在用户问"我叫什么"时才会被路由到。
        结果就是 —— **画像只在"问我身份"时注入提示词**，
        而风险评估、知识问答这些真正需要画像的场景反而拿不到。
        这属于"接线接对了符号、接错了位置"，测试不查这个就发现不了。

        现在它在 ``classify`` 之后无条件执行，所以**每条路径都带得上画像**。

        成本：一次单行查询 + 一次取最近 1 条摘要，毫秒级。相对于动辄
        几十秒的模型推理，这个开销可以忽略，换来的是"模型真的记得你"。

        失败静默降级：记忆是增强项，读不到就当没有，绝不能影响主流程。

        ★ 摘要是方案 B 的一部分：滑动窗口只有 5 轮，更早的对话会被挤出去，
        由滚动摘要压成一段话存在 conversation_summary 表里。这里把它取出来
        注入提示词 —— 否则第 6 轮之前的信息就**彻底失忆**了。
        （第一版只把摘要塞进 state 的 recall_hits，却没有任何节点读它，
          属于"实现了但没接线"，所以这里改成单独产出 summary_text 并真正注入。）
        """
        profile_text = ""
        summary_text = ""
        hits: list[str] = []
        try:
            mem = getattr(agent, "memory", None)
            if mem is not None:
                prof = mem.load_profile()
                profile_text = mem.render_for_prompt(prof) or ""
                # 只要最近 1 条：滚动摘要是**覆盖式**的，最新一条就是
                # "到目前为止的完整压缩版"。取多条反而是重复内容。
                hits = [str(x) for x in (mem.recent_summaries(limit=1) or [])]
                if hits:
                    summary_text = "\n".join(f"- {h}" for h in hits)
                if profile_text or summary_text:
                    print(f"[记忆] 画像 {len(prof)} 字段 + 摘要 {len(hits)} 条已载入")
        except Exception as exc:              # noqa: BLE001
            print(f"[记忆] 加载失败（不影响主流程）: {type(exc).__name__}: {exc}")
        return {"profile_text": profile_text, "summary_text": summary_text,
                "recall_hits": hits}

    # ────────────────────── 节点 2：确定性直答 ──────────────────────
    def direct_node(state: SlopeState) -> dict:
        """状态类问题走确定性直答 —— 看图、查口令，不花 LLM 推理时间。"""
        text = state.get("user_input", "")
        ans = ""
        route = "status_direct"
        try:
            ans = agent._execute_multi_intent(text) or ""
        except Exception as exc:              # noqa: BLE001
            print(f"[直答] 失败: {type(exc).__name__}: {exc}")
        if not ans:
            # 工具直答没命中 → 退到传感器自检 / 趋势
            try:
                ans = agent._handle_sensor_check() or ""
                route = "sensor_check"
            except Exception:                 # noqa: BLE001
                ans = ""
        if not ans:
            try:
                ans = agent._check_trend() or ""
                route = "trend_analysis"
            except Exception:                 # noqa: BLE001
                ans = ""
        return {"answer": ans, "route": route if ans else "status_miss"}

    # ────────────────────── 节点 3：长期记忆查询（事实）──────────────────
    def recall_node(state: SlopeState) -> dict:
        """回答"我是谁"这类**问自身信息**的问题。

        注意：画像的加载已经由 ``load_memory_node`` 在分流前做掉了，
        这里只负责**把身份信息组织成一句话回答**。
        两件事分开，是因为它们的触发条件不同 ——
        画像每条路径都要（注入提示），身份回答只有被问到才需要。
        """
        ans = ""
        try:
            # 先走已有的 FAISS 事实检索，再退回 facts 字典
            fact = agent._query_fact("用户信息")
            if fact and ":" in str(fact):
                ans = str(fact)
        except Exception:                     # noqa: BLE001
            ans = ""
        if not ans:
            facts = getattr(agent, "facts", {}) or {}
            name, occ = facts.get("name", ""), facts.get("occupation", "")
            if name and occ:
                ans = f"你是{name}，职业是{occ}"
            elif name:
                ans = f"你叫{name}"
            elif occ:
                ans = f"你的职业是{occ}"
            else:
                ans = "你还没告诉我你是谁，可以说'我叫XXX'让我记住哦"
        return {"answer": ans, "route": "recall"}

    # ────────────────────── 节点 4：知识库检索 ──────────────────────
    def knowledge_node(state: SlopeState) -> dict:
        """知识类问题：**检索一次 + 综合一次**。

        为什么不让模型自己调工具（像 analysis 那样走 ReAct）：
        知识问答只需要**一次检索**，"要不要再查一次"这个决策没有收益，
        反而多烧一轮推理（每轮约 26 秒）。

        为什么**必须综合**，不能直接返回检索片段：
        把原文直接甩给用户，他看到的是
        「以下是从知识库检索到的原文片段：[1] 来源:optimization_cases.txt …」
        —— 那是**素材不是答案**。
        （这个坑是回归测试抓出来的：检索链路完全正常，回答却是"甩片段"。
          根因是我画图时把"检索"和"作答"混成了一个节点，漏掉了作答那一步。）
        """
        text = state.get("user_input", "")
        ans, sources = "", []
        try:
            fn = tool_by_name.get("tool_search_kb")
            if fn is None:
                # 工具没注册进来 —— 是构图时传的 tools 列表不对，不是检索问题
                print("[知识] 工具 tool_search_kb 未注册到图中")
            else:
                passages = str(fn.invoke({"query": text}))
                # 检索为空是**常见且合法**的结果（语料没覆盖），
                # 但要能区分"真的没命中"和"链路坏了" —— 后者不该静默。
                if not passages:
                    print(f"[知识] 检索返回空（query={text[:40]!r}）")
                elif "未找到" in passages or "未加载" in passages:
                    print(f"[知识] 检索未命中：{passages[:60]!r}")
                else:
                    import re as _re
                    sources = _re.findall(r"来源:\s*([^\s\n]+)", passages)
                    # 有了素材，再让模型**基于素材作答** —— 这一步不能省
                    try:
                        from langchain_core.prompts import ChatPromptTemplate
                        # 画像也注入这里。取舍：
                        #   好处 —— 用户的沟通偏好（比如"要求给数字依据"）对作答方式确实有用；
                        #   风险 —— 画像与问题无关时会占上下文。但画像只有 6 个短字段，
                        #   相对于 4000 字的检索片段可以忽略，所以选择注入。
                        sys_text = (
                            "你是边坡监测领域的技术助手。**只根据下面的知识库片段**回答用户问题：\n"
                            "- 直接给结论，不要复述用户的问题，也不要复述片段原文\n"
                            "- 不要输出你的思考过程，也不要写「首先/我需要/根据片段」这类叙述\n"
                            "- 片段里没有的内容不要编造；确实没有就说「知识库未收录」\n"
                        )
                        if state.get("profile_text"):
                            sys_text += ("\n（以下是该用户的长期画像，仅用于调整回答方式与详略，"
                                         "不要把它当成事实依据写进答案）\n"
                                         + str(state["profile_text"]) + "\n")
                        # 滚动摘要同样只作背景，不当作知识依据 ——
                        # 它是"我们之前聊过什么"，不是"领域知识"。
                        if state.get("summary_text"):
                            sys_text += ("\n（以下是更早对话的摘要，仅用于理解对话背景，"
                                         "不要当成本次问题的答案依据）\n"
                                         + str(state["summary_text"]) + "\n")
                        sys_text += "\n知识库片段：\n{ctx}"
                        prompt = ChatPromptTemplate.from_messages([
                            ("system", sys_text),
                            ("user", "{q}"),
                        ])
                        # 给"思考 + 作答"都留出预算。
                        # 预算被思考吃光时 content 会是**空字符串**（不是半截答案）——
                        # 实测过：knowledge 综合这一步用默认预算时经常为空，
                        # 于是对外表现为"知识库里没找到"，而素材其实就摆在眼前。
                        #
                        # 注意用 model_copy 而不是 .bind(num_predict=...)：
                        # bind 会把参数当成 chat() 的**顶层**关键字传下去，
                        # 报 "got an unexpected keyword argument 'num_predict'"。
                        # ChatOllama 是 pydantic 模型，复制一份改字段才是对的。
                        synth = model
                        try:
                            synth = model.model_copy(update={"num_predict": 3072})
                        except Exception:                 # noqa: BLE001
                            pass                          # 测试用的假模型没有这个方法
                        out = (prompt | synth).invoke(
                            {"ctx": passages[:4000], "q": text})
                        ans = str(getattr(out, "content", out)).strip()
                        if not ans:
                            print("[知识] 综合产出为空（模型没给出内容）")
                    except Exception as exc:          # noqa: BLE001
                        print(f"[知识] 综合失败: {type(exc).__name__}: {exc}")

                    # ★ 综合失败时**不能谎称"没找到"** —— 素材明明检索到了。
                    #   退回检索原文：它是知识库的真实内容，比一句"没找到"有用得多，
                    #   更不会误导用户以为知识库没有这条。（这个坑是实测暴露的：
                    #   检索命中、却答"知识库里暂时没找到相关内容"，属于事实错误。）
                    #
                    #   注意**不要再加一层前缀** —— 检索原文自己已经带了
                    #   「以下是从知识库检索到的原文片段：」这个头，
                    #   再套一个「以下是知识库中相关的内容：」就成了两个头叠在一起
                    #   （用户实际截图里就是这样，看着像 bug）。
                    if not ans:
                        ans = str(passages)[:2600]
        except Exception as exc:              # noqa: BLE001
            print(f"[知识] 检索失败: {type(exc).__name__}: {exc}")
        if not ans:
            ans = "知识库里暂时没找到相关内容。"
        # 综合这一步同样要剥思考段 —— 和 finalize 一个道理
        ans = _strip(ans) or ans
        return {"answer": ans, "route": "knowledge_rag", "sources": sources}

    # ────────────────────── 节点 5：规则引擎结论 ──────────────────────
    def rule_lookup_node(state: SlopeState) -> dict:
        """先拿规则引擎的判定，再交给模型解释。

        ★ 这一层是**硬边界**：判定永远由规则引擎出，模型只负责解释。
        所以这里先把判定结果取出来放进 state，模型看到的"事实"就是这个，
        而不是它自己猜的。
        """
        verdict_text, verdict = "", {}
        try:
            fn = tool_by_name.get("tool_get_active_alerts")
            if fn is not None:
                verdict_text = str(fn.invoke({"hours": 24, "only_unacked": True}))
                verdict = {"raw": verdict_text[:2000]}
        except Exception as exc:              # noqa: BLE001
            print(f"[规则] 取告警失败: {type(exc).__name__}: {exc}")
        return {"rule_verdict": verdict, "route": "rule_lookup",
                "messages": [HumanMessage(
                    content=f"{state.get('user_input','')}\n\n"
                            f"【规则引擎的实际判定（请据此解释，不要自行判定）】\n"
                            f"{verdict_text or '当前无未确认告警'}")]}

    # ────────────────────── 节点 6：LLM 推理（ReAct 的"想"）──────────────────
    def agent_node(state: SlopeState) -> dict:
        """绑定**本意图允许的工具**后让模型推理。"""
        allowed = state.get("allowed_tools") or []
        bound = [tool_by_name[n] for n in allowed if n in tool_by_name]
        llm = model.bind_tools(bound) if bound else model

        sys_text = system_prompt
        # 注入长期画像
        if state.get("profile_text"):
            sys_text += "\n\n【用户长期画像】\n" + str(state["profile_text"])
        # 注入滚动摘要：窗口只有 5 轮，更早的对话靠这一段兜住。
        # 措辞上明确标"可能不完整"，避免模型把它当成逐字记录去引用。
        if state.get("summary_text"):
            sys_text += ("\n\n【之前对话的摘要（更早的内容已压缩，可能不完整，仅作背景参考）】\n"
                         + str(state["summary_text"]))

        msgs = [SystemMessage(content=sys_text)] + list(state.get("messages") or [])
        try:
            resp = llm.invoke(msgs)
        except Exception as exc:              # noqa: BLE001
            print(f"[Agent] 推理失败: {type(exc).__name__}: {exc}")
            resp = AIMessage(content="")
        retry = int(state.get("retry") or 0) + 1
        return {"messages": [resp], "retry": retry}

    # ────────────────────── 条件边 ①：classify 之后走哪条 ──────────────────
    def route_after_classify(state: SlopeState) -> str:
        """★ 条件边之一：按意图分流。

        这是整张图的分叉点 —— "一个问题走哪条路"从代码里的 if 链
        变成了图上一条看得见的边。
        """
        intent = state.get("intent") or "analysis"
        if intent == "status":
            return "direct"
        if intent == "recall":
            return "recall"
        if intent == "knowledge":
            return "knowledge"
        if intent == "alert":
            return "rule_lookup"
        if intent == "chat":
            return "finalize"          # 闲聊不花推理，直接收口
        return "agent"

    # ────────────────────── 条件边 ②：ReAct 循环的"该不该继续"──────────────
    def should_continue(state: SlopeState) -> str:
        """★ 条件边之二：ReAct 循环的核心判断。

        三种情况收口：超轮数、超挂钟预算、模型不再请求工具。
        这三个判断**集中在图里**，而不是散落在业务方法各处的局部变量 ——
        这是把控制流显式化最直接的收益。
        """
        if int(state.get("retry") or 0) >= max_rounds:
            return "finalize"
        if _deadline_exceeded(state):
            return "finalize"
        msgs = state.get("messages") or []
        if not msgs:
            return "finalize"
        last = msgs[-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return "finalize"

    # ────────────────────── 节点 7：收口 ──────────────────────
    def finalize_node(state: SlopeState) -> dict:
        """所有路径的出口：取最终答案、剥思考段。

        收口节点存在的意义是**保证每条路径都经过同一套后处理** ——
        改造前流式路径和 invoke 路径各写了一遍取答案的逻辑，
        结果其中一条漏了 ``not tool_calls`` 判断，同一个问题换条路径答案就不一样。
        收口到一处之后，这种"只改了一半"的不一致从结构上就不可能再发生。

        ★ 这里必须剥思考段。实测教训：不剥的话模型会把推理写进正文，
        而推理里常出现"我判断应该报警"这类话术 —— 直接破坏双轨制边界
        （判定归规则引擎，Agent 只解释）。这个坑是回归测试抓出来的。
        """
        ans = str(state.get("answer") or "").strip()
        if not ans:
            msgs = state.get("messages") or []
            # 只取"不再请求工具"的那条 AI 消息 —— 请求工具的那条内容是中间推理，
            # 拿它当答案，用户看到的是"念了一遍计划"。
            ai_msgs = [m for m in msgs
                       if getattr(m, "type", "") == "ai" and getattr(m, "content", "")]
            finals = [m for m in ai_msgs
                      if not (getattr(m, "tool_calls", None))]
            if finals:
                ans = str(finals[-1].content)
        ans = _strip(ans) or ""
        return {"answer": ans, "route": state.get("route") or "react_agent"}

    # ══════════════════════ 组图 ══════════════════════
    g = StateGraph(SlopeState)
    g.add_node("classify", classify_node)
    g.add_node("load_memory", load_memory_node)   # ★ 分流前无条件加载画像
    g.add_node("direct", direct_node)
    g.add_node("recall", recall_node)
    g.add_node("knowledge", knowledge_node)
    g.add_node("rule_lookup", rule_lookup_node)
    g.add_node("agent", agent_node)
    g.add_node("tools", tool_node)      # LangGraph 内置节点
    g.add_node("finalize", finalize_node)

    # 普通边：一定走这条
    g.add_edge(START, "classify")
    # classify 之后先加载画像，再分流 —— 这样**每条路径都带得上画像**。
    # （第一版把画像加载放在 recall 节点里，导致只有"问我身份"时才注入，是错的。）
    g.add_edge("classify", "load_memory")
    g.add_edge("tools", "agent")        # 工具执行完必然回到推理（形成循环）
    g.add_edge("direct", "finalize")
    g.add_edge("recall", "finalize")
    g.add_edge("knowledge", "finalize")
    g.add_edge("finalize", END)

    # 条件边 ①：按意图分流（入口从 classify 改到了 load_memory）
    g.add_conditional_edges("load_memory", route_after_classify, {
        "direct": "direct",
        "recall": "recall",
        "knowledge": "knowledge",
        "rule_lookup": "rule_lookup",
        "agent": "agent",
        "finalize": "finalize",
    })

    # 条件边 ②：ReAct 循环
    g.add_conditional_edges("agent", should_continue, {
        "tools": "tools",
        "finalize": "finalize",
    })

    # rule_lookup 拿到规则引擎结论后，交给模型解释
    g.add_edge("rule_lookup", "agent")

    return g.compile()
