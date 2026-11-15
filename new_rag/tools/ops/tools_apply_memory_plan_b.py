# -*- coding: utf-8 -*-
"""方案 B 改造：5 轮滑动窗口 + 滚动摘要 + 删掉冗余的 FAISS 语义事实。

## 改完的目标形态

    Redis  最近 5 轮（10 条消息），满了挤掉最早一轮，TTL 7 天滑动续期
    MySQL  conversation_summary（滚动摘要，注入提示词）
           memory（事实，key-value）
           user_profile（画像，一行 JSON）
    删除   FAISS 语义事实（数据是 memory 的副本，只服务一个查询）
           旧 _summarize_conversation（写 facts、写 FAISS、截断 20 条）

## 为什么挤出的轮次不能直接丢掉

窗口只有 5 轮，第 6 轮进来时第 1 轮被挤出去。
如果直接丢，用户问"我第一次说的那个点位"就彻底失忆了。
所以挤出的内容要**累积起来**，够一批就异步滚动进摘要：
    新摘要 = LLM(旧摘要 + 这批被挤出的对话)
覆盖式更新 —— 摘要永远是"到目前为止的压缩版"，不会越滚越长。
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(errors="replace")

TARGET = Path(r"C:\Users\Lenovo\Desktop\new_rag\end_new.py")
BACKUP = TARGET.with_suffix(f".py.bak_memB_{time.strftime('%Y%m%d_%H%M%S')}")
shutil.copy2(TARGET, BACKUP)
print(f"已备份 → {BACKUP.name}")

text = TARGET.read_text(encoding="utf-8")
orig_len = len(text)
applied: list[str] = []


def sub(old: str, new: str, label: str) -> None:
    """按唯一锚点替换；命中次数不是 1 就报错跳过（不静默改错）。"""
    global text
    n = text.count(old)
    if n != 1:
        print(f"  [!!] {label}: 命中 {n} 次（期望 1），跳过")
        return
    text = text.replace(old, new, 1)
    applied.append(label)
    print(f"  [OK] {label}")


# ══════════════════════════════════════════════════════════════════
# P1. _PersistentHistory：加 5 轮滑动窗口 + 挤出回调
# ══════════════════════════════════════════════════════════════════
OLD_P1 = '''    __slots__ = ("_store", "_sid")

    def __init__(self, store, sid, items=None):
        super().__init__(items or [])
        self._store = store
        self._sid = sid

    def _flush(self):
        if self._store is not None:
            self._store.save(self._sid, list(self))

    # 只覆写会改变内容的入口，其余（迭代/切片/len）沿用 list
    def append(self, item):
        super().append(item)
        self._flush()

    def extend(self, items):
        super().extend(items)
        self._flush()
'''

NEW_P1 = '''    # ── 滑动窗口：只保留最近 5 轮 ──
    #
    # 一轮对话 = 用户一条 + AI 一条，所以 5 轮 = 10 条。
    #
    # 为什么在**存储层**做窗口，而不是像原来那样在 run() 里截断：
    #   1. 原来「存 10 轮、喂模型 5 轮」是两个数，白占一倍内存还容易说不清
    #   2. 窗口一旦由存储层保证，上层怎么 append 都不会超
    #   3. 挤出的内容可以在这里**捕获**，交给滚动摘要 —— 在 run() 里截断就抓不到
    #
    # 为什么是 5 轮：实测 message 表里单条消息 P90 是 500 字，
    # 5 轮最坏约 5000 token（占 32k 窗口 15%）；典型只有 4.7%。
    # 10 轮在最坏情况下会到 30%，对本地小模型来说太激进。
    MAX_ENTRIES = 10

    __slots__ = ("_store", "_sid", "_on_evict")

    def __init__(self, store, sid, items=None, on_evict=None):
        super().__init__(items or [])
        self._store = store
        self._sid = sid
        # 挤出回调：签名 (sid, evicted_items)。为 None 时挤出的内容直接丢。
        self._on_evict = on_evict

    def _flush(self):
        if self._store is not None:
            self._store.save(self._sid, list(self))

    def _enforce_window(self) -> None:
        """超出窗口就挤掉最早的若干条，并把它们交给回调。

        用 `del self[:n]` 而不是循环 `pop(0)`：
        `pop` 被覆写过、每次都会 _flush()，循环会让 Redis 写回 N 次。
        `del` 切片不经过被覆写的方法，只在末尾统一写回一次。
        """
        overflow = len(self) - self.MAX_ENTRIES
        if overflow <= 0:
            return
        evicted = list(self[:overflow])
        del self[:overflow]
        cb = self._on_evict
        if cb is not None and evicted:
            try:
                cb(self._sid, evicted)
            except Exception as exc:          # noqa: BLE001
                # 挤出回调失败绝不能影响对话本身 —— 摘要少一条而已
                print(f"[记忆] 挤出回调失败（不影响对话）: {type(exc).__name__}: {exc}")

    # 只覆写会改变内容的入口，其余（迭代/切片/len）沿用 list
    def append(self, item):
        super().append(item)
        self._enforce_window()
        self._flush()

    def extend(self, items):
        super().extend(items)
        self._enforce_window()
        self._flush()
'''
sub(OLD_P1, NEW_P1, "P1 _PersistentHistory 加滑动窗口 + 挤出回调")


# ══════════════════════════════════════════════════════════════════
# P2. history property：把挤出回调传进去
# ══════════════════════════════════════════════════════════════════
OLD_P2 = '''            store = getattr(self, "_session_store", None)
            items = store.load(sid) if store else []
            self._histories[sid] = _PersistentHistory(store, sid, items)
        return self._histories[sid]'''

NEW_P2 = '''            store = getattr(self, "_session_store", None)
            items = store.load(sid) if store else []
            # 加载时也套一次窗口：历史可能是按旧版本（20 条）存的，
            # 直接读回来会超过 5 轮。套一次窗口能把老数据也收敛到新规格。
            self._histories[sid] = _PersistentHistory(
                store, sid, items,
                on_evict=getattr(self, "_on_history_evict", None),
            )
            if len(self._histories[sid]) > _PersistentHistory.MAX_ENTRIES:
                # 老数据超窗口：直接裁掉，不触发摘要（这些是历史遗留，没有对话语境）
                del self._histories[sid][: len(self._histories[sid]) - _PersistentHistory.MAX_ENTRIES]
                self._histories[sid]._flush()
        return self._histories[sid]'''
sub(OLD_P2, NEW_P2, "P2 history property 传挤出回调 + 老数据收敛")


# ══════════════════════════════════════════════════════════════════
# P3. _save_to_conversation：删掉旧的 >20 触发
# ══════════════════════════════════════════════════════════════════
OLD_P3 = '''        except Exception as e:
            print(f"[消息] MySQL 写入失败: {e}")
        # 超过 10 条（5轮）触发摘要
        if len(self.history) > 20:  # 10 轮
            threading.Thread(target=self._summarize_conversation, daemon=True).start()
'''
NEW_P3 = '''        except Exception as e:
            print(f"[消息] MySQL 写入失败: {e}")
        # 注意：这里原来有一句「len(self.history) > 20 就触发摘要」。
        # 现在摘要由滑动窗口驱动 —— 历史被挤出窗口时，_on_history_evict 会把
        # 那几条攒起来，够一批就异步滚动一次。窗口只有 10 条，旧的 20 阈值
        # 永远不会成立，留着就是死代码。
'''
sub(OLD_P3, NEW_P3, "P3 _save_to_conversation 删掉旧摘要触发")


# ══════════════════════════════════════════════════════════════════
# P4. 用滚动摘要替换旧的 _summarize_conversation
# ══════════════════════════════════════════════════════════════════
OLD_P4 = '''    def _summarize_conversation(self):
        """对话摘要：提取前5轮生成简短摘要存入长期记忆"""
        try:
            old = self.history[:max(0, len(self.history) - 20)]  # 10 轮
            if not old:
                return
            text = "\\n".join([f"{'用户' if r == '用户' else 'AI'}:{c[:80]}" for r, c in old[-10:]])
            r = requests.post(f"{CFG.llm_base_url}/api/generate", json={
                "model": CFG.llm_model,
                "prompt": f"请用一句话（30字以内）总结以下对话的核心内容：\\n{text}",
                "stream": False, "options": {"num_predict": 60}
            }, timeout=CFG.timeout_llm_generate)
            if r.status_code == 200:
                summary = r.json().get("response", "").strip()
                if summary and len(summary) > 5:
                    ts = time.strftime("%m-%d %H:%M")
                    self.facts[f"对话摘要_{ts}"] = summary[:100]
                    self._save_facts()
                    self._store_fact_to_faiss(f"对话摘要_{ts}", summary[:100])
                    print(f"[摘要] {summary[:80]}")
                    # 移除已总结的历史（保留内存整洁）
                    self.history = self.history[-20:]  # 保留最近 10 轮
        except Exception as e:
            print(f"[摘要] 生成失败: {e}")
'''

NEW_P4 = '''    # ──────────────── 滚动摘要（方案 B）────────────────
    #
    # 机制：滑动窗口把最早一轮挤出去 → 这里把挤出的几条攒进缓冲区
    #       → 攒够 _EVICT_BATCH 条就异步跑一次"旧摘要 + 这批对话 → 新摘要"
    #
    # 为什么攒批而不是每挤一条就摘要：
    #   窗口 5 轮，第 6 轮起**每一轮**都会有内容被挤出。
    #   如果每次都调一次 LLM，第 6 轮之后相当于每轮多一次推理 —— 代价太高，
    #   而且每次只喂一轮，摘要几乎没新信息。
    #   攒 2 轮（4 条）跑一次，既控制调用频率，又保证每次都有实质增量。
    #
    # 为什么是"滚动覆盖"而不是"每次插一条新摘要"：
    #   覆盖式保证摘要永远是"到目前为止的压缩版"，长度稳定、不会越滚越长。
    #   插入式会积累一堆碎片，注入时还得拼接、去重。
    _EVICT_BATCH = 4          # 4 条 = 2 轮

    def _on_history_evict(self, sid, evicted):
        """滑动窗口挤出内容时的回调：攒批 + 异步滚动摘要。

        ★ 这个方法会被 `_PersistentHistory.append` 在**每轮对话**里调用，
        所以必须足够轻（只做 list 操作），LLM 调用一律扔到后台线程。
        """
        buf = getattr(self, "_evict_buffer", None)
        if buf is None:
            self._evict_buffer = buf = {}
        bucket = buf.setdefault(sid, [])
        bucket.extend(evicted)
        if len(bucket) < self._EVICT_BATCH:
            return
        batch = list(bucket)
        del bucket[:]
        print(f"[摘要] 窗口挤出 {len(batch)} 条，异步滚动摘要（session={sid[:12]}…）")
        threading.Thread(
            target=self._roll_summary, args=(sid, batch), daemon=True
        ).start()

    def _roll_summary(self, sid, batch):
        """把「旧摘要 + 这批被挤出的对话」合并成一段新摘要并覆盖写入。

        失败一律降级：摘要是增强项，生成不出来最多是"记不全"，不能影响对话。
        """
        try:
            mem = getattr(self, "memory", None)
            if mem is None:
                return          # 没启用结构化记忆 → 摘要也没地方存，直接跳过
            old_summaries = mem.recent_summaries(limit=1) or []
            old_text = old_summaries[0] if old_summaries else "（无）"

            convo = "\\n".join(
                f"{'用户' if r == '用户' else 'AI'}：{str(c)[:80]}" for r, c in batch
            )
            prompt = (
                "把「已有摘要」和「新增对话」合并成一段新的摘要，要求：\\n"
                "- 100 字以内，只输出摘要本身，不要写'好的'/'用户说'这类叙述\\n"
                "- 保留：讨论过哪些点位、发现了什么风险、做了或建议了什么处置\\n"
                "- 已有摘要里仍然重要的信息不要丢\\n\\n"
                f"已有摘要：{old_text}\\n\\n新增对话：\\n{convo}\\n\\n新摘要："
            )
            r = requests.post(f"{CFG.llm_base_url}/api/generate", json={
                "model": CFG.llm_model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 200},
            }, timeout=CFG.timeout_llm_generate)
            if r.status_code != 200:
                print(f"[摘要] LLM 返回 HTTP {r.status_code}，本轮跳过")
                return
            summary = str(r.json().get("response", "")).strip()
            # 剥掉可能混进来的思考段（qwen3 会写推理）
            if "</think>" in summary:
                summary = summary.rsplit("</think>", 1)[-1].strip()
            if not summary or len(summary) < 5:
                print("[摘要] 产出为空/过短，本轮跳过")
                return
            mem.add_summary(summary[:300])
            print(f"[摘要] 已更新滚动摘要：{summary[:80]}")
        except Exception as exc:                  # noqa: BLE001
            print(f"[摘要] 滚动摘要失败（不影响对话）: {type(exc).__name__}: {exc}")
'''
sub(OLD_P4, NEW_P4, "P4 滚动摘要替换旧 _summarize_conversation")


# ══════════════════════════════════════════════════════════════════
# P5. 删 _init_fact_store
# ══════════════════════════════════════════════════════════════════
i0 = text.find("    def _init_fact_store(self):")
i1 = text.find("    # 这个函数的目的在于把在新增的事实给向量化")
if i0 != -1 and i1 != -1 and i1 > i0:
    text = text[:i0] + text[i1:]
    applied.append("P5 删 _init_fact_store")
    print("  [OK] P5 删 _init_fact_store")
else:
    print(f"  [!!] P5 定位失败 (i0={i0}, i1={i1})")

# ══════════════════════════════════════════════════════════════════
# P6. 删 _store_fact_to_faiss
# ══════════════════════════════════════════════════════════════════
i0 = text.find("    # 这个函数的目的在于把在新增的事实给向量化")
i1 = text.find("    # ══ 自进化：新案例写回知识库 ══")
if i0 != -1 and i1 != -1 and i1 > i0:
    text = text[:i0] + text[i1:]
    applied.append("P6 删 _store_fact_to_faiss")
    print("  [OK] P6 删 _store_fact_to_faiss")
else:
    print(f"  [!!] P6 定位失败 (i0={i0}, i1={i1})")

# ══════════════════════════════════════════════════════════════════
# P7. 改写 _query_fact_from_faiss → _query_fact（只保留精确查字典那半）
# ══════════════════════════════════════════════════════════════════
OLD_P7 = '''    def _query_fact_from_faiss(self, query):
        """从向量中匹配"""
        # 1. 精确匹配优先：直接查 facts 字典
        if "名字" in query or "我是谁" in query or "我叫啥" in query:
            name = self.facts.get("name", "")
            return f"用户的名字: {name}" if name else None
        if "职业" in query or "做什么" in query or "身份" in query:
            occ = self.facts.get("occupation", "")
            return f"用户的职业: {occ}" if occ else None
        if "爱好" in query or "喜欢" in query:
            hobby = self.facts.get("hobby", "")
            return f"用户的爱好: {hobby}" if hobby else None

        # 2. 其他情况走 FAISS 语义检索（对话摘要、历史事件等）
        if getattr(self, 'fact_store', None) is None:
            return None
        try:
            docs = self.fact_store.similarity_search(query, k=1)
            if docs:
                content = docs[0].page_content
                if content.startswith("对话摘要"):     # 如果检索结果是对话摘要，丢弃（防止污染）
                    return None
                return content
        except:
            pass
        return None
'''

NEW_P7 = '''    def _query_fact(self, query):
        """按关键词精确查事实（原 `_query_fact_from_faiss`，已去掉向量分支）。

        ## 为什么不需要 FAISS 了

        改造前这里有一条"语义兜底"：先精确匹配，匹配不到就去 FAISS 里
        similarity_search 找最像的一条。问题是那条兜底有两个毛病：

          1. **数据是重复的** —— FAISS 里的内容就是 `self.facts` 的副本
             （`_init_fact_store` 拿 facts 建索引）。查 33 条事实用一个
             向量库，索引维护成本比收益大。
          2. **摘要混在里面** —— 旧代码把"对话摘要_xxx"也写进同一个库，
             于是检索到摘要时还得打个补丁 `startswith("对话摘要")` 丢弃。
             这正是"摘要存了却不用"的根源。

        现在事实只用字典查（33 条，O(1)），摘要走独立表、由 load_memory
        节点统一注入 —— 两条链各走各的，不需要兜底也不需要补丁。
        """
        if "名字" in query or "我是谁" in query or "我叫啥" in query:
            name = self.facts.get("name", "")
            return f"用户的名字: {name}" if name else None
        if "职业" in query or "做什么" in query or "身份" in query:
            occ = self.facts.get("occupation", "")
            return f"用户的职业: {occ}" if occ else None
        if "爱好" in query or "喜欢" in query:
            hobby = self.facts.get("hobby", "")
            return f"用户的爱好: {hobby}" if hobby else None
        return None
'''
sub(OLD_P7, NEW_P7, "P7 _query_fact_from_faiss → _query_fact（去掉向量分支）")

# ══════════════════════════════════════════════════════════════════
# P8. 删 __init__ 里的 _init_fact_store() 调用
# ══════════════════════════════════════════════════════════════════
OLD_P8 = "        self._init_fact_store()          # FAISS 长期记忆\n"
sub(OLD_P8, "        # （原 _init_fact_store 已删除：事实改用字典精确查，不再建 FAISS 索引）\n",
    "P8 删 __init__ 里的 _init_fact_store 调用")

# ══════════════════════════════════════════════════════════════════
# P9. 删 run() 里写 FAISS 的那句 + 改 _query_fact_from_faiss 调用
# ══════════════════════════════════════════════════════════════════
OLD_P9 = '''            # 同步写入 FAISS
            for k, v in new_facts.items():
                self._store_fact_to_faiss(k, v)
'''
sub(OLD_P9, '''            # 说明：这里原来还有一段"同步写入 FAISS"。
            # 事实的唯一权威存储是 memory 表（_save_facts 已写），
            # self.facts 字典也已经更新，FAISS 只是它的重复索引 —— 已删除。
''', "P9 删 run() 里写 FAISS")

OLD_P9B = '            fact = self._query_fact_from_faiss("用户信息")'
sub(OLD_P9B, '            fact = self._query_fact("用户信息")', "P9b run() 改调 _query_fact")

TARGET.write_text(text, encoding="utf-8")
print()
print(f"完成：应用 {len(applied)} 处，{orig_len} → {len(text)} 字符")
