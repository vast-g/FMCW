"""结构化长期画像记忆（三层记忆的"摘要层 + 画像层"）。

背景 —— 为什么要新建这个模块，而不是继续往 `end_new.py` 里加代码：

    旧的长期记忆是两套并存的：
      1) MySQL `memory` 表（纯 key-value，`_load_facts()` / `_save_facts()`）；
      2) FAISS 向量库（`_init_fact_store()` / `_store_fact_to_faiss()` /
         `_query_fact_from_faiss()`）。

    问题不在"存没存"，而在**存了却不敢用**：`_summarize_conversation()` 把
    `对话摘要_xxx` 同时写进 facts 字典和 FAISS，而 `_query_fact_from_faiss()`
    检索到 `content.startswith("对话摘要")` 时**主动丢弃**（理由写着"防止污染"）。
    也就是说摘要存了、也检索得到，却在最后一步被扔掉。

    根因不是那行 if，而是**元叙述（摘要）和事实混在同一个向量库里检索**：
    摘要天然是整段对话的概括，语义上必然和"用户问的任何问题"都相似，
    所以它一定会被召回；可它对"回答具体问题"又毫无用处 —— 那它就是噪声。
    在同一个索引里既想召回事实、又想召回摘要，只能靠这行 if 打补丁。

    所以本模块把职责拆开，各自用各自最合适的存储和检索方式：
      · 摘要 → `conversation_summary` 表，**只按时间倒序取最近 N 条**，
        用于压缩上下文；它永远不进入事实检索，也就无所谓"污染"。
      · 画像 → `user_profile` 表，**结构化字段**，整份注入系统提示；
        它跨会话有效，解决"每次对话都要重新自我介绍"的问题。
      · 事实 → 继续用 `end_new.py` 里已有的 `memory` 表，本模块不碰。

    这样每层只有一个用途，"要不要丢弃检索结果"这种判断就自然消失了。

对外契约（调用方按这个签名接线）：
    ProfileMemory(engine, llm=None, user_id=1, extract_every=5)
    load_profile / save_profile / maybe_update_from_turns
    add_summary / recent_summaries / render_for_prompt / stats

两条硬约束：
    · **LLM 可选**：llm=None 时所有涉及 LLM 的方法优雅跳过，
      本模块在没有任何 LLM、没有任何大模型依赖的环境下也必须能 import 和跑通。
    · **绝不抛异常**：记忆属于"锦上添花"，它失败不能把主对话流程带崩。
      因此所有 DB / LLM 操作都包 try/except，失败打 `[记忆]` 前缀日志并降级。
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

__all__ = [
    "PROFILE_FIELDS",
    "LIST_PROFILE_FIELDS",
    "FIELD_LABELS",
    "MemoryBase",
    "ProfileMemory",
    "build_default_llm",
]

# ══════════════════════════════════════════════════════════════════
# 画像字段定义
# ══════════════════════════════════════════════════════════════════
# 这套字段是**针对边坡监测场景**设计的，不是通用的"姓名/职业/爱好"：
# 那个通用三件套只能回答"我是谁"，而运维人员在边坡场景里真正需要的是
# "我关心哪个点位、我怕什么、我认不认可你的阈值"——这三件事决定了
# 同一个位移数值该不该被强调、要不要主动提示降雨耦合风险。
PROFILE_FIELDS: dict[str, str] = {
    "role": "用户角色，如 运维人员/管理员/工程师/访客",
    "focus_sites": "关注的监测点位编码列表，如 ['S-01','S-03']",
    "concerns": "长期关注的问题类型，如 ['位移速率','降雨预警','设备排障']",
    "threshold_prefs": "对阈值的偏好或质疑，如 '认为0.3m阈值过严'",
    "interaction_style": "沟通偏好，如 '要求给数字依据'/'喜欢简短回答'",
    "last_risk_topic": "上次讨论的风险话题",
}

# 这两类字段在 JSON 里是数组，其余是字符串。
# 之所以要显式区分：模型经常把 focus_sites 写成 "S-01,S-03" 这种字符串，
# 如果不归一化，画像的形态会随模型心情漂移，下游渲染/比较就没法稳定。
LIST_PROFILE_FIELDS: frozenset[str] = frozenset({"focus_sites", "concerns"})

# 渲染进系统提示时的中文标签（顺序即注入顺序，按重要性排）
FIELD_LABELS: dict[str, str] = {
    "role": "用户角色",
    "focus_sites": "关注点位",
    "concerns": "长期关注",
    "threshold_prefs": "阈值偏好",
    "interaction_style": "沟通偏好",
    "last_risk_topic": "上次风险话题",
}

# 这些"值"没有任何信息量，视同空 —— 模型很爱用它们来填不知道的字段。
_EMPTY_WORDS = {
    "", "null", "none", "nan", "n/a", "na", "无", "没有", "未知", "不知道",
    "暂无", "不清楚", "未提及", "未说明", "[]", "{}", "-",
}


class MemoryBase(DeclarativeBase):
    """本模块独立的 ORM 基类。

    **刻意不复用 `end_new.py` 的 `_Base`**：那个文件正被并行编辑，
    如果两边共用同一个 DeclarativeBase，任何一侧改字段都会让另一侧
    在 create_all 时炸掉。独立 Base 的代价只是两张表分属两个 metadata，
    好处是"我改我的、你改你的"互不干扰 —— 这正是本次改造要的隔离。
    """


def _now() -> datetime:
    """naive UTC 时间戳。

    不用 `datetime.utcnow()`：它从 Python 3.12 起带 DeprecationWarning，
    而这里只需要一个可排序的时间值，不需要时区语义（与项目既有表的写法保持一致的"存进去的也是 naive UTC"）。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


class _UserProfile(MemoryBase):
    """结构化画像表：一个用户一行，整份 JSON 覆盖写。

    为什么是一行 JSON 而不是"一字段一行"（像 memory 表那样）：
    画像总是**整份读写**（渲染提示、合并抽取都一次要全部字段），
    按字段拆行会让每次读画像都要 N 次查询 + 拼装，而收益（按字段更新）
    这里根本用不上。单行 JSON 让"整份覆盖"这个语义天然成立。
    """

    __tablename__ = "user_profile"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_profile_user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="画像ID")
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True, comment="所属用户ID")
    data: Mapped[str] = mapped_column(Text, nullable=False, default="{}", comment="画像 JSON 文本")
    # 累计"已喂进来的对话轮数"。放在同一张表而不是单独建表，
    # 是因为它就属于画像的一份元数据，且必须与画像同生共死（画像删了它也就没意义）。
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="累计轮数（用于判断是否到抽取周期）")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, comment="更新时间")


class _ConversationSummary(MemoryBase):
    """对话摘要表：**只用于压缩上下文，永不参与事实检索**。

    这是本次改造的核心分离点。旧实现把摘要塞进 FAISS，
    结果摘要必然被语义检索召回、又必须被丢弃；现在它有了自己的表和
    自己的读取方式（按时间取最近 N 条），"是不是噪声"这个问题不存在了。
    """

    __tablename__ = "conversation_summary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="摘要ID")
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True, comment="所属用户ID")
    summary: Mapped[str] = mapped_column(Text, nullable=False, comment="摘要正文")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, comment="创建时间")


class ProfileMemory:
    """结构化长期画像记忆。所有方法都保证不抛异常（失败即降级）。"""

    def __init__(self, engine, llm: Any = None, user_id: int = 1, extract_every: int = 5):
        """engine 是 SQLAlchemy Engine；llm 是可选的 LLM 客户端（可能为 None）。

        这里在 __init__ 里就建表（幂等 create_all），而不是等第一次写入：
        目的是让"表到底建没建成功"在进程启动日志里就能看见，
        而不是等用户聊了五轮才发现画像一直没落库。
        """
        self.engine = engine
        self.llm = llm
        self.user_id = self._coerce_uid(user_id)
        # 钳到 >=1：传 0 会导致 `total % 0` 除零，而"每轮都抽取"又是最贵的配置，
        # 所以宁可把它当成一次配置笔误修正到 1，也不要让主流程因为一个参数崩掉。
        try:
            self.extract_every = max(1, int(extract_every))
        except (TypeError, ValueError):
            self.extract_every = 5
        self._Session = None
        if engine is not None:
            try:
                self._Session = sessionmaker(bind=engine, expire_on_commit=False)
            except Exception as e:  # noqa: BLE001
                print(f"[记忆] 会话工厂创建失败（画像/摘要降级为不可用）：{e}")
        self._ensure_tables()

    # ══════════ 建表 ══════════
    def _ensure_tables(self) -> None:
        """幂等建表。create_all 只创建缺失的表，重复调用是安全的。"""
        if self.engine is None:
            print("[记忆] 未提供数据库引擎，画像/摘要功能降级为不可用")
            return
        try:
            MemoryBase.metadata.create_all(self.engine)
            print("[记忆] 结构化记忆表就绪：user_profile / conversation_summary")
        except Exception as e:  # noqa: BLE001
            print(f"[记忆] 建表失败（画像/摘要降级为不可用）：{e}")

    # ══════════ 内部工具 ══════════
    @staticmethod
    def _coerce_uid(user_id: Any) -> int:
        try:
            return int(user_id)
        except (TypeError, ValueError):
            return 1

    def _uid(self, user_id: int | None) -> int:
        """user_id 用 `is None` 判断而不是 `or`：

        user_id=0 虽然现实中不该出现，但用 `or` 会把 0 悄悄变成默认用户，
        这类"值被静默改写"的 bug 在这个项目里已经出现过（session_id 写死 default）。
        """
        return self.user_id if user_id is None else self._coerce_uid(user_id)

    @contextmanager
    def _session(self):
        if self._Session is None:
            raise RuntimeError("无可用数据库引擎")
        s = self._Session()
        try:
            yield s
            s.commit()
        except Exception:
            try:
                s.rollback()
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            s.close()

    # ══════════ 画像读写 ══════════
    def load_profile(self, user_id: int | None = None) -> dict:
        """返回画像 dict（字段见 PROFILE_FIELDS）。没有则返回 {}。"""
        uid = self._uid(user_id)
        try:
            with self._session() as s:
                row = s.query(_UserProfile).filter(_UserProfile.user_id == uid).first()
                if row is None or not row.data:
                    return {}
                return self._sanitize_profile(json.loads(row.data))
        except Exception as e:  # noqa: BLE001
            print(f"[记忆] 读取画像失败（降级为空画像）：{e}")
            return {}

    def save_profile(self, profile: dict, user_id: int | None = None) -> None:
        """整份覆盖写入（不会动 turn_count，避免"存一次画像就把抽取周期清零"）。"""
        uid = self._uid(user_id)
        clean = self._sanitize_profile(profile or {})
        try:
            payload = json.dumps(clean, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            print(f"[记忆] 画像序列化失败（本次不落库）：{e}")
            return
        try:
            with self._session() as s:
                row = s.query(_UserProfile).filter(_UserProfile.user_id == uid).first()
                if row is None:
                    s.add(_UserProfile(user_id=uid, data=payload,
                                       turn_count=0, updated_at=_now()))
                else:
                    row.data = payload
                    row.updated_at = _now()
        except Exception as e:  # noqa: BLE001
            print(f"[记忆] 写入画像失败（本次画像不落库）：{e}")

    # ══════════ 摘要读写 ══════════
    def add_summary(self, text: str, user_id: int | None = None) -> None:
        """存一条对话摘要到 conversation_summary 表（超长截断，防止单条撑爆行）。"""
        t = (text or "").strip()
        if not t:
            return
        uid = self._uid(user_id)
        try:
            with self._session() as s:
                s.add(_ConversationSummary(user_id=uid, summary=t[:2000], created_at=_now()))
        except Exception as e:  # noqa: BLE001
            print(f"[记忆] 摘要写入失败（该条摘要丢失，不影响主流程）：{e}")

    def recent_summaries(self, limit: int = 3, user_id: int | None = None) -> list[str]:
        """取最近几条摘要（用于压缩上下文）。

        返回时**按时间正序**：注入提示词时读起来是"事情按发生顺序展开"，
        比倒序更符合模型理解上下文的习惯（查询本身仍是按 id 倒序取最近 N 条）。
        """
        uid = self._uid(user_id)
        try:
            n = max(0, int(limit))
        except (TypeError, ValueError):
            n = 3
        if n == 0:
            return []
        try:
            with self._session() as s:
                rows = (s.query(_ConversationSummary)
                        .filter(_ConversationSummary.user_id == uid)
                        .order_by(_ConversationSummary.id.desc())
                        .limit(n)
                        .all())
            return [r.summary for r in reversed(rows)]
        except Exception as e:  # noqa: BLE001
            print(f"[记忆] 摘要读取失败（按无摘要处理）：{e}")
            return []

    # ══════════ 画像抽取（LLM） ══════════
    def maybe_update_from_turns(self, turns: list[tuple[str, str]],
                                user_id: int | None = None) -> dict | None:
        """累计轮数达到 extract_every 的倍数时，用 LLM 抽取画像并合并保存。

        返回更新后的画像；未触发或 LLM 不可用时返回 None。
        **绝不抛异常** —— 记忆失败不能影响主流程。
        """
        # 没有 LLM 就直接返回，连轮数都不累加：
        # 否则"没配 LLM 的环境"会一路把计数涨上去，等哪天接上 LLM 的第一秒
        # 就被判定为"到周期了"而立刻抽一次，行为不可预期。
        if self.llm is None:
            return None

        pairs = self._normalize_turns(turns)
        if not pairs:
            return None

        uid = self._uid(user_id)
        total = self._bump_turn_count(uid, len(pairs))
        if total is None or total % self.extract_every != 0:
            return None

        try:
            conversation = "\n".join(f"{role}: {content}" for role, content in pairs)
            raw = self._call_llm(self._build_extract_prompt(conversation))
            if not raw:
                print("[记忆] 画像抽取：LLM 无输出，跳过本轮")
                return None
            extracted = self._parse_json_object(raw)
            if not extracted:
                # 解析失败**不猜、不降级成自由文本**：把非结构化内容写进画像，
                # 会让画像从"可信字段"退化成"又一坨噪声"，正是本次要消灭的东西。
                print("[记忆] 画像抽取：LLM 输出不是合法 JSON，跳过（不写脏数据）")
                return None
            clean = self._sanitize_profile(extracted)
            current = self.load_profile(uid)
            merged = self._merge_profile(current, clean)
            self.save_profile(merged, uid)
            print(f"[记忆] 画像已更新（累计第 {total} 轮触发）：{list(merged.keys())}")
            # 只要"触发了且 LLM 正常返回 JSON"，即使没有新字段也返回当前画像，
            # 让调用方能区分"这次真的抽过了"和"没到周期 / LLM 不可用"。
            return merged
        except Exception as e:  # noqa: BLE001
            print(f"[记忆] 画像抽取失败（本轮跳过）：{e}")
            return None

    def _bump_turn_count(self, uid: int, delta: int) -> int | None:
        """累加轮数并返回累计值；失败返回 None。

        注意这不是原子自增（先读后写）：多条对话并发时可能少计几轮。
        可以接受 —— 少计一轮的后果只是"晚一轮抽取画像"，而为了它去引入
        行锁/乐观重试，代价远大于收益。**这里选择一致性换简单，是有意的。**
        """
        try:
            with self._session() as s:
                row = s.query(_UserProfile).filter(_UserProfile.user_id == uid).first()
                if row is None:
                    row = _UserProfile(user_id=uid, data="{}", turn_count=0, updated_at=_now())
                    s.add(row)
                    s.flush()
                row.turn_count = int(row.turn_count or 0) + delta
                row.updated_at = _now()
                s.flush()
                return int(row.turn_count)
        except Exception as e:  # noqa: BLE001
            print(f"[记忆] 轮数累加失败（本轮不抽取画像）：{e}")
            return None

    def _build_extract_prompt(self, conversation: str) -> str:
        """构造抽取提示词：强约束 JSON + 明确"不知道就省略"。

        必须显式写"不要编造"：小模型在结构化抽取任务上很爱把字段填满，
        而画像里一条编造的"关注点位"会直接影响后续回答的侧重，比空字段危险得多。
        """
        field_lines = "\n".join(f"- {k}: {v}" for k, v in PROFILE_FIELDS.items())
        return (
            "你是边坡监测系统的记忆整理模块。请从下面的对话片段中抽取【用户画像】字段。\n"
            "要求：\n"
            "1. 只输出一个 JSON 对象，不要任何解释文字，不要 Markdown 代码块；\n"
            "2. 不确定的字段请省略或置为 null，绝对不要编造；\n"
            "3. focus_sites 和 concerns 输出字符串数组，其余字段输出字符串。\n\n"
            f"字段定义：\n{field_lines}\n\n"
            f"对话片段：\n{conversation}\n\n"
            "JSON："
        )

    def _call_llm(self, prompt: str) -> str | None:
        """调用 LLM 并把各种返回形态统一成字符串；任何失败都返回 None。

        这里适配多种客户端（LangChain 的 invoke / generate / predict，
        以及最朴素的 callable）：调用方接线的自由度更大，
        而"到底传进来的是什么"不该成为记忆功能失效的原因。
        """
        llm = self.llm
        if llm is None:
            return None
        try:
            if hasattr(llm, "invoke"):
                out = llm.invoke(prompt)
            elif hasattr(llm, "predict"):
                out = llm.predict(prompt)
            elif hasattr(llm, "generate"):
                out = llm.generate([prompt])
            elif callable(llm):
                out = llm(prompt)
            else:
                print("[记忆] 传入的 llm 不支持 invoke/predict/generate，跳过抽取")
                return None
        except Exception as e:  # noqa: BLE001
            print(f"[记忆] LLM 调用失败（本轮不更新画像）：{e}")
            return None
        return self._coerce_text(out)

    @staticmethod
    def _coerce_text(out: Any) -> str | None:
        """把 LLM 返回值压成纯文本（兼容 str / .content / list[Generation]）。"""
        try:
            if out is None:
                return None
            if isinstance(out, str):
                return out.strip() or None
            if isinstance(out, (list, tuple)):
                parts = [ProfileMemory._coerce_text(x) for x in out]
                joined = "\n".join(p for p in parts if p)
                return joined or None
            if isinstance(out, dict):
                for key in ("response", "text", "content", "output"):
                    if out.get(key):
                        return ProfileMemory._coerce_text(out[key])
                return None
            for attr in ("content", "text", "response"):
                val = getattr(out, attr, None)
                if val:
                    return ProfileMemory._coerce_text(val)
            return None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _parse_json_object(raw: str) -> dict | None:
        """严格 JSON 解析：先直解，失败再剥离代码块/取第一个平衡的 {...}。

        顺序很关键 —— 先直解能保证"模型老老实实输出 JSON"这条主路径不被正则误伤；
        兜底路径只处理两种常见坏习惯（```json 包裹、JSON 前后带解释文字）。
        """
        if not raw:
            return None
        text = raw.strip()
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else None
        except Exception:  # noqa: BLE001
            pass

        # 去掉 Markdown 代码块围栏
        fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
        if fenced:
            text = fenced.group(1).strip()
            try:
                obj = json.loads(text)
                return obj if isinstance(obj, dict) else None
            except Exception:  # noqa: BLE001
                pass

        # 取第一个括号平衡的 { ... }（粗略处理字符串内的花括号：引号状态机）
        start = text.find("{")
        while start != -1:
            depth = 0
            in_str = False
            escape = False
            for i in range(start, len(text)):
                ch = text[i]
                if in_str:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        cand = text[start:i + 1]
                        try:
                            obj = json.loads(cand)
                            if isinstance(obj, dict):
                                return obj
                        except Exception:  # noqa: BLE001
                            pass
                        break
            start = text.find("{", start + 1)
        return None

    @staticmethod
    def _normalize_turns(turns: Any) -> list[tuple[str, str]]:
        """把 turns 规整成 [(role, content)]，容忍 dict 形态与脏数据。

        坏数据就跳过而不是报错 —— 记忆模块不该因为调用方多传了一个 None
        而让整轮对话失败。
        """
        out: list[tuple[str, str]] = []
        if not turns:
            return out
        try:
            iterator = list(turns)
        except TypeError:
            return out
        for item in iterator:
            try:
                if isinstance(item, dict):
                    role = str(item.get("role") or "用户")
                    content = str(item.get("content") or "")
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    role = str(item[0])
                    content = str(item[1])
                else:
                    continue
                content = content.strip()
                if content:
                    out.append((role or "用户", content))
            except Exception:  # noqa: BLE001
                continue
        return out

    # ══════════ 归一化 / 合并 ══════════
    @classmethod
    def _sanitize_profile(cls, profile: Any) -> dict:
        """只保留已知字段、剔除空值、并做类型归一化。"""
        if not isinstance(profile, dict):
            return {}
        clean: dict[str, Any] = {}
        for field in PROFILE_FIELDS:
            if field not in profile:
                continue
            value = cls._clean_value(field, profile[field])
            if value:
                clean[field] = value
        return clean

    @classmethod
    def _clean_value(cls, field: str, value: Any) -> Any:
        if value is None:
            return None
        if field in LIST_PROFILE_FIELDS:
            items = cls._as_list(value)
            return items or None
        if isinstance(value, (list, tuple, set)):
            # 非列表字段却给了数组：拼成顿号文本比整条丢掉更有信息量
            text = "、".join(str(x).strip() for x in value if str(x).strip())
        else:
            text = str(value).strip()
        text = text.strip("'\"").strip()
        if text.lower() in _EMPTY_WORDS:
            return None
        return text

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        """把 ['S-01','S-03'] / "S-01、S-03" / "['S-01','S-03']" 统一成 list。"""
        if isinstance(value, (list, tuple, set)):
            raw_items = [str(x) for x in value]
        else:
            s = str(value).strip().strip("[]")
            raw_items = re.split(r"[、,，;；/\s]+", s)
        out: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            t = str(item).strip().strip("'\"").strip()
            if not t or t.lower() in _EMPTY_WORDS:
                continue
            if t in seen:
                continue
            seen.add(t)
            out.append(t)
        return out

    @classmethod
    def _merge_profile(cls, current: dict, new: dict) -> dict:
        """合并新旧画像。

        合并策略是有取舍的，不是随手写的：
        · 列表字段（点位、关注点）取**并集**。因为每次抽取只看最近若干轮，
          若直接覆盖，用户上个月说过的点位会因为"最近没提"而被抹掉，
          而画像的价值恰恰在于跨会话长期有效 —— 宁可留一点陈旧，
          也不要丢已经确认过的偏好。
        · 字符串字段（角色、偏好）**非空覆盖**。这类字段会随用户认知变化
          （比如"认为 0.3m 阈值过严"后来变成"接受 0.35m"），
          保留旧值反而是错的。
        """
        merged = dict(current or {})
        for field, value in (new or {}).items():
            if field not in PROFILE_FIELDS or not value:
                continue
            if field in LIST_PROFILE_FIELDS:
                old = merged.get(field) or []
                old_list = old if isinstance(old, list) else cls._as_list(old)
                merged[field] = cls._as_list(old_list + list(value))
            else:
                merged[field] = value
        return cls._sanitize_profile(merged)

    # ══════════ 渲染 ══════════
    def render_for_prompt(self, profile: dict | None = None) -> str:
        """把画像渲染成可注入系统提示的中文文本。画像为空时返回空字符串。

        传 None 表示"按当前用户现查"，传 {} 表示"显式按空画像渲染"（返回 ""）。
        这两种语义必须分开，否则调用方想确认"空画像到底渲染出什么"都没法测。
        """
        data = self.load_profile() if profile is None else self._sanitize_profile(profile)
        if not data:
            return ""
        lines = ["【用户画像（长期记忆）】"]
        for field, label in FIELD_LABELS.items():
            if field not in data:
                continue
            value = data[field]
            if isinstance(value, (list, tuple)):
                text = "、".join(str(x) for x in value)
            else:
                text = str(value)
            lines.append(f"- {label}：{text}")
        # 只有标题没有内容时也算空画像（理论上不会发生，但别渲染出半截提示词）
        return "\n".join(lines) if len(lines) > 1 else ""

    # ══════════ 健康检查 ══════════
    def stats(self) -> dict:
        """返回 {'profile_fields': n, 'summaries': n} 之类的统计，供健康检查用。"""
        out = {
            "user_id": self.user_id,
            "profile_fields": 0,
            "summaries": 0,
            "db_ok": False,
        }
        try:
            out["profile_fields"] = len(self.load_profile())
        except Exception as e:  # noqa: BLE001
            print(f"[记忆] 统计画像字段失败：{e}")
        try:
            with self._session() as s:
                out["summaries"] = int(
                    s.query(func.count(_ConversationSummary.id))
                    .filter(_ConversationSummary.user_id == self.user_id)
                    .scalar() or 0
                )
            out["db_ok"] = True
        except Exception as e:  # noqa: BLE001
            print(f"[记忆] 统计摘要条数失败：{e}")
        return out


def build_default_llm():
    """按项目 `config.py` 的配置构造 Ollama LLM；任何失败都返回 None。

    刻意**不在模块顶层 import config / langchain**：
      1) config 会读 .env 并实例化 pydantic settings，属于有副作用的 import；
      2) 本模块要求"没有任何 LLM 的环境也能 import 和使用"，
         顶层 import 会让只想用 SQLite 跑个单测的场景也背上这份依赖。
    所以做成显式的懒加载工厂：需要默认 LLM 的调用方主动调它，不调就完全无依赖。
    """
    try:
        from config import settings as CFG  # 字段名已核对：llm_model / llm_base_url / timeout_llm_generate
    except Exception as e:  # noqa: BLE001
        print(f"[记忆] 未启用默认 LLM（无法导入 config：{e}）")
        return None

    ollama_cls = None
    try:
        # end_new.py 用的是 langchain_ollama，老环境里则是 langchain_community，
        # 两个都试一遍，避免因为依赖位置差异让画像功能静默失效。
        from langchain_ollama import OllamaLLM as _OllamaLLM  # type: ignore
        ollama_cls = _OllamaLLM
    except Exception:  # noqa: BLE001
        try:
            from langchain_community.llms import OllamaLLM as _OllamaLLM  # type: ignore
            ollama_cls = _OllamaLLM
        except Exception as e:  # noqa: BLE001
            print(f"[记忆] 未启用默认 LLM（缺少 OllamaLLM 依赖：{e}）")
            return None

    kwargs = dict(
        model=getattr(CFG, "llm_model", "qwen3:4b"),
        base_url=getattr(CFG, "llm_base_url", "http://localhost:11434"),
        timeout=getattr(CFG, "timeout_llm_generate", 30),
    )
    try:
        # reasoning=False 关闭 qwen3 的 thinking：抽取画像只需要一小段 JSON，
        # 开着 thinking 会让 4b 模型先生成上千 token 推理，单次从 1-2 秒劣化到几十秒。
        return ollama_cls(reasoning=False, num_predict=256, **kwargs)
    except TypeError:
        try:
            return ollama_cls(**kwargs)
        except Exception as e:  # noqa: BLE001
            print(f"[记忆] 默认 LLM 构造失败：{e}")
            return None
    except Exception as e:  # noqa: BLE001
        print(f"[记忆] 默认 LLM 构造失败：{e}")
        return None
