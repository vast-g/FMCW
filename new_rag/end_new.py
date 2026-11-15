
"""
边坡监测一体化上位机 - 完整版
单板WiFi方案：C8T6通过ESP8266 AP回传距离+GPS，支持远程控制LED/蜂鸣器
AI Agent：Ollama Qwen + 向量RAG + 短期/长期记忆 + 趋势预警
"""

import cv2
import queue
import threading
import numpy as np
import socket
import folium
import webbrowser
import os
import time
import sys
import json
import re
from PIL import Image, ImageTk, ImageDraw, ImageFont
from ultralytics import YOLO
# 无头模式：容器 / CI 中无窗口系统也能导入核心推理链路（实现见 headless.py）
from headless import tk, ttk, messagebox
import matplotlib.pyplot as plt
# 无头模式下 matplotlib 的 Tk 画布也必须替换（matplotlib 内部用的是真实 tkinter）
from headless import FigureCanvasTkAgg
from matplotlib.figure import Figure
import serial
import serial.tools.list_ports



import requests
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import message_to_dict, messages_from_dict, BaseMessage
from typing import Sequence
from langchain_community.embeddings import OllamaEmbeddings
import os
from langchain_community.document_loaders import TextLoader
from langchain_community.vectorstores import FAISS
from langchain_ollama import OllamaLLM, ChatOllama
from langchain_core.tools import tool
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain_core.messages import AIMessage

import sqlite3 

# ── 配置 / 可观测性 / 容错基础设施 ──
# 所有可调参数集中在 config.py：本文件不得再硬编码模型名、超时与路径
from config import settings as CFG
from tracing import TraceRecorder, get_store
from resilience import DegradationLog, new_deadline, retry_call
from session_store import SessionBusyError, SessionLeaseLostError, SessionStoreUnavailable

# ── 显式控制流（LangGraph）──
# 把"意图识别 → 按意图收窄工具 → ReAct 循环 → 收口"从 run() 里的 if 链
# 搬到图里。见 slope_graph.py 顶部的设计说明。
from slope_graph import build_slope_graph, classify_intent_rules, INTENT_TOOLS

# ============================= # RAG 参数设置（统一来自 config.py） #=====================================#
CHUNK_SIZE = CFG.chunk_size          # 文档分块最大字符数
CHUNK_OVERLAP = CFG.chunk_overlap    # 分块之间重叠字符数
RAG_TOP_K = CFG.rag_top_k            # 检索返回文档数（k）
# ==================================================================================#


# ============================================================================================ 告警通知============================================================================================#


class FakeStream:
    def write(self, *a): pass
    def flush(self): pass
    def fileno(self): return 1


if sys.stdout is None: sys.stdout = FakeStream()
if sys.stderr is None: sys.stderr = FakeStream()
os.environ["PYTHONUNBUFFERED"] = "1"

# 微信/邮件告警已移除，仅保留 MySQL 事件存储
last_alert_time = 0
ALERT_COOLDOWN = 10


def alert(typ, content):
    """告警：写入 MySQL 事件库"""
    global last_alert_time
    now = time.time()
    if now - last_alert_time < ALERT_COOLDOWN: return
    last_alert_time = now
    if 'agent' in globals() and agent is not None:
        try:
            agent._store_event(typ, content, current_distance if "边坡异常" in typ else None)
            agent._save_case_to_kb(f"{typ}: {content}")
        except Exception as e: print(f"[告警] 存储失败:{e}")




# ============================================================================================向量RAG ============================================================================================#



# ==================== 新增组件1: Query Rewriter (查询改写) ====================
class QueryRewriter:
    """用本地 Qwen 把用户问题改写成 2~3 条检索友好的短句"""
    def __init__(self):
        try:
            self.llm = OllamaLLM(model=CFG.llm_model, base_url=CFG.llm_base_url, timeout=CFG.timeout_llm_rewrite,
                          reasoning=True, num_predict=128)
        except Exception:
            self.llm = None

    def rewrite(self, query: str) -> list[str]:
        if self.llm is None:
            return [query]
        try:
            prompt = f"""你是检索查询优化专家。请把以下用户问题改写成2~3条更适合检索的短句。每条用中文，去掉冗余词，保留核心关键词。只输出改写后的句子，每行一条，不要编号，不要解释。原问题：{query}改写："""
            result = self.llm.invoke(prompt).strip()
            cleaned = self._clean(result, query)
            if cleaned:
                print(f"[Rewrite] {query[:30]}... -> {len(cleaned)} 条")
                return cleaned[:3]
            # 一条合格改写都没有 → **必须回落到原问题**，绝不能拿脏数据去检索
            print(f"[Rewrite] {query[:30]}... -> 无有效改写，回落到原问题")
        except Exception as e:
            print(f"[Rewrite] 失败: {e}")
        return [query]

    @staticmethod
    def _clean(raw: str, original: str) -> list[str]:
        """清洗 Query Rewrite 的输出。

        ## 为什么必须清洗（这是实测挖出来的**最致命**的一个缺陷）

        qwen3 在这条链路上会把**提示词本身**、带"原问题："前缀的模板行、
        以及整段推理一起吐出来，而且**不带 </think> 标记**。原实现只做
        `len(line) > 3` 的过滤，于是 `lines[0]` 恰好是那句
        "首先，用户要求我作为检索查询优化专家…"。

        而 `ask()` 里取的是 `queries[0]` ——
        **结果每个问题都在用同一串提示词去检索，召回结果完全一样**，
        表现出来就是"知识库答不出任何问题、看起来是空的"。
        这个缺陷不会报错、日志看起来也正常，非常隐蔽。

        清洗规则：
          * 去掉编号、"原问题：/改写："等前缀
          * 丢掉提示词回声与思考叙述（含"你是""用户要求我""我需要""只输出"等）
          * 丢掉过长/过短的行
          * 与原问题重复的行也丢掉（那不算"改写"）
        一条都不剩时返回空列表，由调用方回落到原问题。
        """
        out: list[str] = []
        for line in str(raw).split("\n"):
            s = line.strip()
            if not s:
                continue
            # 去编号前缀（"1. " "1、" "- " 等）
            s = re.sub(r'^[\-\*•\d]+\s*[\.\)、:：]?\s*', '', s).strip()
            # 去模板前缀
            s = re.sub(r'^(原问题|问题|改写|改写后|查询|答案)\s*[:：]\s*', '', s).strip()

            # 提示词回声与思考叙述 —— 这些是"关于任务的话"，不是检索词
            noise = ("你是", "用户要求我", "作为检索", "查询优化专家", "只输出",
                     "不要编号", "不要解释", "每行一条", "我需要", "首先，", "首先,",
                     "改写为", "更适合检索", "让我", "我可以", "以下是")
            if any(k in s for k in noise):
                continue
            if s == original or len(s) < 3 or len(s) > 60:
                continue
            # 去掉末尾的语气/标点残留
            s = s.strip('。.，,；;：:')
            if len(s) < 3:
                continue
            if s not in out:
                out.append(s)
        return out


# ==================== RRF 融合工具函数 ====================
def rrf_fusion(doc_lists: list[list], k: int = 60, top_n: int = 20) -> list:
    """Reciprocal Rank Fusion：合并多个检索器的结果

    Args:
        doc_lists: 多个检索器返回的文档列表，按排名升序排列
        k: RRF 平滑常数（默认 60）
        top_n: 返回文档数
    Returns:
        融合后的文档列表（保持原文档对象引用）
    """
    scores = {}
    all_docs = []
    for doc_list in doc_lists:
        for rank, doc in enumerate(doc_list):
            key = doc.page_content if hasattr(doc, 'page_content') else str(doc)
            scores[key] = scores.get(key, 0) + 1.0 / (k + rank)
        all_docs.extend(doc_list)

    # 按 RRF 分数排序
    ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_n]

    # 从原始文档列表中找回文档对象
    result = []
    for key, _ in ranked:
        for d in all_docs:
            dk = d.page_content if hasattr(d, 'page_content') else str(d)
            if dk == key:
                result.append(d)
                break
        else:
            result.append(type('Doc', (), {'page_content': key, 'metadata': {}})())
    return result


# ==================== 新增组件2: Ensemble Retriever (混合检索) ====================
class EnsembleRetriever:
    """FAISS 向量检索 + LangChain BM25Retriever，RRF 融合"""
    def __init__(self, vectorstore, documents: list):
        from langchain_community.retrievers import BM25Retriever
        try:
            self.faiss_retriever = vectorstore.as_retriever(search_kwargs={"k": 20})
        except Exception:
            self.faiss_retriever = None
        try:
            self.bm25_retriever = BM25Retriever.from_documents(documents, k=20)
        except Exception:
            self.bm25_retriever = None

    def invoke(self, query: str) -> list:
        """混合检索：FAISS + BM25 -> RRF 融合"""
        faiss_docs = []
        bm25_docs = []

        if self.faiss_retriever:
            try:
                faiss_docs = self.faiss_retriever.invoke(query)
            except Exception as e:
                print(f"[Ensemble] FAISS 失败: {e}")

        if self.bm25_retriever:
            try:
                bm25_docs = self.bm25_retriever.invoke(query)
            except Exception as e:
                print(f"[Ensemble] BM25 失败: {e}")

        results = rrf_fusion([faiss_docs, bm25_docs], k=60, top_n=20)
        if not results:
            results = faiss_docs[:20]
        print(f"[Ensemble] FAISS={len(faiss_docs)} + BM25={len(bm25_docs)} -> RRF={len(results)}")
        return results


# ==================== 新增组件3: Reranker (重排序) ====================
class Reranker:
    """重排序：用**嵌入相似度**对召回结果重新排序。

    ## 为什么不再用小模型"打分"

    原来是让 qwen3 输出"序号:分数"来打分。实测 **0/20 条命中格式** ——
    qwen3 会把整段思考写满输出，正则根本匹配不到分数，于是静默回落到召回顺序，
    **"重排"这一步等于没做**，从日志上也看不出来（现已改为明确告警）。

    换成嵌入相似度后：
      * 确定性：同样的输入永远同样的排序，可复现（评测需要这一点）
      * 快：毫秒级，而 LLM 打分一轮要几十秒
      * 不依赖生成模型 → 不会再有"思考泄漏"污染排序
    重排只需要判断"哪条更贴近问题"，嵌入余弦相似度足够胜任。
    """

    def __init__(self):
        try:
            self.emb = OllamaEmbeddings(model=CFG.llm_embed_model,
                                        base_url=CFG.llm_base_url)
        except Exception:
            self.emb = None
        # 保留 llm 字段以兼容可能存在的旧引用
        self.llm = None

    def rerank(self, query: str, docs: list, top_k: int = 5) -> list:
        """按与 query 的嵌入相似度排序，返回 top_k"""
        if not docs or self.emb is None:
            return docs[:top_k]
        if len(docs) <= top_k:
            return docs
        try:
            import numpy as np
            qv = np.asarray(self.emb.embed_query(query), dtype=float)
            dv = np.asarray(self.emb.embed_documents(
                [str(getattr(d, "page_content", "")) for d in docs]), dtype=float)
            qn = qv / (np.linalg.norm(qv) + 1e-9)
            dn = dv / (np.linalg.norm(dv, axis=1, keepdims=True) + 1e-9)
            sims = dn @ qn
            order = sims.argsort()[::-1][:top_k]
            print(f"[Rerank] 嵌入相似度重排 {len(docs)} -> top_{top_k}"
                  f"（top1={sims[order[0]]:.3f}）")
            return [docs[i] for i in order]
        except Exception as e:
            # 失败要说话：静默回落会让"重排没生效"看起来像"检索就这水平"
            print(f"[Rerank] 嵌入重排失败: {type(e).__name__}: {e}，回落召回顺序")
            return docs[:top_k]


# ==================== 升级版 VectorRAG ====================
class VectorRAG:
    """向量知识库（Query Rewrite + Hybrid Search + Rerank）"""
    def __init__(self, doc_path="rag_docs"):
        self.vectorstore = None
        self.llm = None
        self.rewriter = None
        self.ensemble_retriever = None
        self.reranker = None
        self._all_documents = []

        if not os.path.exists(doc_path):
            print("知识库文件夹不存在"); return

        try:
            documents = []
            for f in os.listdir(doc_path):
                if f.endswith(".txt"):
                    loader = TextLoader(os.path.join(doc_path, f), encoding="utf-8")
                    documents.extend(loader.load())
            if not documents: print("没有找到有效文档"); return

            splitter = RecursiveCharacterTextSplitter(
                chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP,
                separators=["\n\n", "。", "\n", "？", "，", ",", ".", " ", ""],
                length_function=len)
            docs = splitter.split_documents(documents)
            self._all_documents = docs

            # 分批 embedding（避免 GPU OOM: 171 chunks 一次全请求会爆显存）
            embeddings = OllamaEmbeddings(model=CFG.llm_embed_model, base_url=CFG.llm_base_url)
            batch_size = 10
            self.vectorstore = None
            for batch_start in range(0, len(docs), batch_size):
                batch = docs[batch_start:batch_start + batch_size]
                for retry in range(3):
                    try:
                        if self.vectorstore is None:
                            self.vectorstore = FAISS.from_documents(batch, embeddings)
                        else:
                            self.vectorstore.add_documents(batch)
                        break
                    except Exception as e:
                        if retry < 2:
                            print(f"[RAG] 分批 {batch_start//batch_size+1}/{(len(docs)+batch_size-1)//batch_size} 重试 {retry+1}...")
                            time.sleep(2)
                        else:
                            raise e
                time.sleep(0.5)  # 批次间隔，给 GPU 喘息
            self.llm = OllamaLLM(model=CFG.llm_model, base_url=CFG.llm_base_url, timeout=CFG.timeout_llm_generate,
                          reasoning=True, num_predict=2048)

            # 新增三大组件（任一失败不影响主流程）
            try:
                self.rewriter = QueryRewriter()
            except Exception as e:
                print(f"[RAG] QueryRewriter 初始化失败: {e}")
            try:
                self.ensemble_retriever = EnsembleRetriever(self.vectorstore, docs)
            except Exception as e:
                print(f"[RAG] EnsembleRetriever 初始化失败: {e}")
            try:
                self.reranker = Reranker()
            except Exception as e:
                print(f"[RAG] Reranker 初始化失败: {e}")

            print(f"向量知识库构建完成，共 {len(docs)} 个片段")
        except Exception as e:
            print(f"向量索引构建失败:{e},降级为关键词搜索")
            self.vectorstore = None

    def ask(self, query, k=RAG_TOP_K, synthesize=False):
        """完整检索链路：Rewrite → Hybrid → Rerank →（可选）Generate。

        synthesize=False（默认）返回**检索原文片段**，见下方第 4 步的说明。
        """
        if self.vectorstore is None:
            return self._keyword_fallback(query)

        try:
            # 1. Query Rewrite
            queries = self.rewriter.rewrite(query) if self.rewriter else [query]

            # 2. Ensemble Search（用第一条改写 query）
            search_query = queries[0] if queries else query
            docs = self.ensemble_retriever.invoke(search_query) if self.ensemble_retriever \
                else self.vectorstore.similarity_search(search_query, k=20)

            if not docs:
                return self._keyword_fallback(query)

            # 3. Rerank
            top_docs = self.reranker.rerank(query, docs, top_k=k) if self.reranker else docs[:k]

            # 4. 默认**直接返回检索到的原文片段**，不再让这一层的小模型先总结一遍。
            #
            # 为什么改（这是实测定下来的）：
            #   原来的做法是 chain.invoke() 让 qwen3:4b 根据检索结果写一段回答。
            #   但 qwen3 会把**推理过程**写进正文，而且在这条链路上**不带 </think> 标记**：
            #       实测返回 3147 字，全是"首先，用户提问是…我需要根据参考资料来回答…"
            #   这段"思考"被 tool_search_kb 当成**知识库内容**交给上层 Agent，
            #   Agent 读完发现确实没有答案，只能回"知识库未收录" ——
            #   现象是"知识库是空的"，实质是这一层没产出答案。
            #
            # 现在直接返回原文片段，三个好处：
            #   1. 下游 Agent 本身就是大模型，它要的是**事实**；中间加一层总结只会丢信息
            #   2. 不再有"小模型思考泄漏"，输出确定、可复现
            #   3. 少一次 LLM 调用，检索从几十秒降到毫秒级
            #
            # synthesize=True 时保留旧的"LLM 总结"行为，供确实需要一句话答案的场景。
            if not synthesize:
                return self._format_passages(top_docs)

            context = "\n\n".join([doc.page_content for doc in top_docs])
            prompt = ChatPromptTemplate.from_messages([
                ("system",
                 "你是边坡监测智能助手。**只输出答案本身**：\n"
                 "- 不要复述问题，不要写“首先/我需要/根据参考资料/用户问的是”这类叙述\n"
                 "- 不要输出你的思考过程\n"
                 "- 如果参考资料确实不包含相关信息，只回一句：未找到相关内容\n"
                 "参考资料：\n{context}"),
                ("user", "用户提问：{input}")
            ])
            chain = prompt | self.llm | StrOutputParser()
            raw = chain.invoke({"context": context, "input": query})
            # 双保险：万一推理段带 </think> 标记，剥掉它再返回。
            return strip_thinking(raw)
        except Exception as e:
            print(f"[RAG] 检索链路异常: {e}，降级基础检索")
            return self._basic_ask(query, k)

    @staticmethod
    def _format_passages(docs, max_docs=5, max_chars=2600):
        """把检索到的原文片段拼成人可读、模型也可用的形式。

        保留来源文件名：Agent 在回答里就能说明"依据是哪个文档"，
        评测的 Faithfulness / Context Recall 也才有据可查。

        ## 两个曾经的缺陷（都是用户实际看到的）

        1. **字符硬切把句子切成两半**。原来是 `(...)[:max_chars]`，
           1400 字一到就从中间断开，用户看到的是半句话结尾，像是"没输出完"。
           现在改成**在行边界截断**并显式标注"已截断"——
           要么给完整的段落，要么明确告诉读者这里被截了，别让人猜。

        2. **预算太小**（原来 1400）。这些片段是给下游 Agent 读的素材，
           5 条片段每条三百来字就撑爆了。抬到 2600 让常见情况能完整放下；
           真的超了也有明确截断标记，不会静默丢内容。
        """
        if not docs:
            return "未找到相关内容"
        out = []
        for i, d in enumerate(docs[:max_docs], 1):
            src = (getattr(d, "metadata", {}) or {}).get("source", "")
            src = os.path.basename(str(src)) if src else ""
            txt = str(getattr(d, "page_content", "")).strip()
            if not txt:
                continue
            out.append(f"[{i}]{(' 来源:' + src) if src else ''}\n{txt}")
        if not out:
            return "未找到相关内容"

        head = "以下是从知识库检索到的原文片段：\n\n"
        body = "\n\n".join(out)
        budget = max(max_chars - len(head), 200)
        if len(body) > budget:
            # 优先在换行处截断；找不到合适换行（比如一大段没换行）才退回硬切
            cut = body.rfind("\n", 0, budget)
            if cut < budget * 0.6:
                cut = budget
            body = body[:cut].rstrip() + "\n\n…（片段较长，此处已截断）"
        return head + body

    def _basic_ask(self, query, k=RAG_TOP_K):
        """降级：基础 FAISS 检索"""
        docs = self.vectorstore.similarity_search(query, k=k)
        if not docs:
            return "未找到相关内容"
        context = "\n\n".join([doc.page_content for doc in docs])
        prompt = ChatPromptTemplate.from_messages([
            ("system", "你是边坡监测智能助手。请根据以下参考资料回答问题，回答要简洁准确。参考资料：{context}"),
            ("user", "用户提问：{input}")
        ])
        chain = prompt | self.llm | StrOutputParser()
        return chain.invoke({"context": context, "input": query})

    def _keyword_fallback(self, query):
        """降级：关键词匹配（无 FAISS 时）"""
        kb_dir = "rag_docs"
        if not os.path.exists(kb_dir):
            return "知识库目录不存在"
        best_text = ""
        best_score = 0
        query_words = set(re.findall(r'[\u4e00-\u9fa5]{2,}|[a-zA-Z]{2,}', query))
        for fname in sorted(glob.glob(os.path.join(kb_dir, "*.txt"))):
            try:
                with open(fname, encoding="utf-8") as f:
                    text = f.read()
                score = sum(1 for w in query_words if w in text)
                if score > best_score:
                    best_score = score
                    best_text = text[:400]
            except Exception:
                continue
        return best_text if best_text else "未找到相关内容"

    def search(self, query, k=RAG_TOP_K):
        """纯检索（返回文档列表），用于 API 的 rag_info"""
        try:
            if self.ensemble_retriever:
                docs = self.ensemble_retriever.invoke(query)
                docs = self.reranker.rerank(query, docs, top_k=k) if self.reranker else docs[:k]
            else:
                docs = self.vectorstore.similarity_search(query, k=k)
            return [(doc.page_content[:500], doc.metadata.get("source", "")) for doc in docs]
        except Exception:
            return [(query, "")]




#============================================================================================   ReAct Agent 工具函数 ============================================================================================#

# 阈值常量（模块级）。
# 原实现里 tool_get_distance 写的是 `self.DIST_THRESHOLD_SAFE`，但它是**模块级函数**，
# 根本没有 self —— 一旦 current_distance > 0（即传感器真在工作）就抛 NameError。
# 无头模式下距离恒为 0、函数提前返回，所以这个崩溃长期没被发现。
DIST_THRESHOLD_SAFE = 0.5   # 安全阈值（米）
DIST_THRESHOLD_WARN = 0.3   # 警戒阈值（米）


def strip_thinking(text):
    """剥掉模型输出里的思考段，只留真正的回答。

    ## 为什么必须做

    qwen3 系列会把**内部推理写进 content**，并以 `</think>` 收尾：

        "好的，用户问…我需要调用 tool_get_rainfall…</think> 当前边坡风险较高…"
         └──────────── 思考（不该给用户看）────────┘ └── 真正的回答 ──┘

    实测这条链路里思考能占到 1500+ 字，把输出预算（num_predict）吃光，
    结果**工具数据拿到了、结论却没生成出来** —— 用户看到一整段"念计划"。

    流式/ invoke 两条路径都要过这一关，否则同一个问题换个路径答案就不一样。

    ## 三种情况（针对真实泄漏排查出来的）

      1. **有 `</think>`** —— 思考在前、答案在后，取标记之后即可
      2. **只有 `<think>` 未闭合** —— 预算被吃光，整段都是思考 → 返回空串，
         让调用方走兜底，而不是把"念计划"当答案
      3. **一个标记都没有** —— 又分两种，从文本上无法百分百区分：
         a. 字段已经分好了（`reasoning=True` 的正常情况）→ content 就是干净答案
         b. 思考被截断、还没写出标记 → content 是一整段推理
         用**元叙述检测**区分：推理在"谈论用户和任务"（"用户问…""我需要…"
         "根据规则…"），而真正的答案在"谈论业务内容"。命中多个元叙述词
         才判为推理 —— 宁可漏判，也不要误伤真答案。

    ## 一道重要前置修复（这才是泄漏的真正元凶）

    项目原来在 4 处创建模型时传了 `reasoning=False`，本意是"关掉思考"。
    实测**它反而是泄漏的元凶**：该参数让思考不再走独立字段，而是整段塞进
    content，**且不带 `</think>` 标记**（正是上面 3b 那种情况，无标记可剥）。

    实测对比（同一个问题，num_predict 压到 128 逼它截断）：

        reasoning=False → content = 188 字【推理】，无 </think>  ← 必然泄漏
        不传 / True     → content = 0 字                        ← 能明确判定"没产出"

    官方文档也写明：`reasoning=True` 时推理进 `reasoning_content` 字段，
    主内容不含推理标签。所以那 4 处已改为 `True`。
    """
    if not text:
        return text
    s = str(text)

    if "</think>" in s:
        s = s.rsplit("</think>", 1)[-1]
    if "<think>" in s:                      # 只有开标签（被截断）→ 整段都是思考
        s = s.split("<think>", 1)[0]

    s = s.strip()
    if not s:
        return s

    # 情况 3b：无任何标记 + 元叙述腔 → 判为"没写完的思考段"
    if _looks_like_unclosed_reasoning(s):
        return ""
    return s


# 元叙述词：推理段在"谈论用户和任务"，真正的答案在"谈论业务"。
# 这里取的都是**真答案里几乎不会出现**的说法，避免误伤。
_REASONING_META = (
    "用户问", "用户提到", "用户的问题", "用户说", "用户需要", "用户想",
    "我需要", "我得先", "我应该", "让我先", "让我来",
    "根据规则", "按照规则", "规则4", "规则 4",
    "接下来我", "首先，用户",
)


def _looks_like_unclosed_reasoning(s: str) -> bool:
    """判断一段**没有思考标记**的文本，是不是"没写完的思考段"。

    只在文本较长、且命中**多个**元叙述词时才判为推理：
    宁可漏判（用户看到一段推理，还能自己分辨），也不要误判
    （把一条好答案整个丢掉 —— 那个代价更大）。
    """
    if len(s) < 300:
        return False
    head = s[:600]
    hits = sum(1 for k in _REASONING_META if k in head)
    return hits >= 2


def _self_module():
    """取回「当前这个模块对象」。

    ## 为什么不能用 `import end_new`

    当本文件以 `python end_new.py --api` 方式运行时，Python 把它注册成
    **`__main__`**，而不是 `end_new`。此时工具函数里的 `import end_new`
    会把**整个文件再执行一遍**，得到的是一个**全新的模块对象**：

      * 那个实例的 `agent` 是另一个 `_LazyAgent`，从没被预热过；
      * 它的 `rag` 恒为 `None`（`load_rag` 只把 rag 设到了第一个实例上）。

    后果：`tool_search_kb` 永远返回"知识库未加载"，而主进程的 `/health`
    查的是第一个实例的全局 `rag`，报告"一切正常"。

    迷惑之处在于**它是选择性的**：
      * 多因素工具照常工作（`monitor` 在 `__init__` 里就建好了）
      * 只有 `rag` 这类"后设的"属性会丢
      * 用 `import end_new as E` 写的测试**完全测不出来**（那种情况下模块名就是
        `end_new`，`import end_new` 拿到的是同一个对象）
    只有"以脚本方式启动 + 走完整 HTTP 链路"才会暴露。

    所以统一改成从 `sys.modules` 里按真实名字取。
    """
    import sys as _sys
    return _sys.modules.get("end_new") or _sys.modules.get("__main__")


@tool
def tool_get_distance() -> str:
    """获取当前边坡超声波距离传感器读数，返回单位为米的距离值和安全状态"""
    global current_distance
    d = current_distance
    if d <= 0: return "传感器暂无数据，请稍后再试"
    if d < DIST_THRESHOLD_WARN:
        alert = "⚠️ 高风险，已进入警戒区间！"
    elif d < DIST_THRESHOLD_SAFE:
        alert = "⚠️ 距离过近！"
    else:
        alert = "✅ 安全距离"
    return f"当前边坡距离：{d:.2f}米。{alert}"


@tool
def tool_get_gps() -> str:
    """获取当前北斗GPS定位坐标，返回经度和纬度"""
    global current_lat, current_lng
    if abs(current_lat) < 0.01: return "GPS尚未定位，请确认天线已连接"
    return f"当前位置：纬度{current_lat:.6f}，经度{current_lng:.6f}"


@tool
def tool_get_weather(city: str = "北京") -> str:
    """查询指定城市的实时天气，参数city为城市名如北京、上海"""
    try:
        import requests
        geo = requests.get(
    f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=zh",
     timeout=CFG.timeout_http_tool)
        if geo.status_code != 200 or not geo.json().get("results"): return f"未找到城市{city}"
        loc = geo.json()[
                       "results"][0]; lat, lon = loc["latitude"], loc["longitude"]
        r = requests.get(
    f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,weather_code",
     timeout=CFG.timeout_http_tool)
        if r.status_code == 200:
            d = r.json()["current"]
            wcode = {
    0: "晴",
    1: "晴",
    2: "多云",
    3: "阴",
    45: "雾",
    51: "小雨",
    61: "中雨",
    71: "小雪",
    80: "阵雨",
    95: "雷阵雨"}.get(
        d.get(
            "weather_code",
            0),
             "未知")
            return f"{city}天气：{wcode}，温度{d['temperature_2m']}°C，湿度{d['relative_humidity_2m']}%"
    except: pass
    return f"{city}天气查询失败"


@tool
def tool_search_kb(query: str) -> str:
    """搜索边坡监测知识库，查询案例、原理、故障、规则等。参数query为搜索关键词或问题"""
    # 必须用 _self_module()：以脚本启动时 `import end_new` 会**重新执行整个文件**，
    # 拿到的是另一个没预热 RAG 的实例，导致这个工具恒返回"知识库未加载"。
    # 详见 _self_module() 的说明。
    _m = _self_module()
    a = getattr(_m, "agent", None)
    if a is not None and getattr(a, "rag", None):
        return a.search_kb(query)
    return "知识库未加载"


# ══════════════════════════════════════════════════════════════════════
#  多因素监测工具（新增）
#
#  为什么加：原来的 Agent 只能看「超声波距离 + 外部天气」两个量，但真实边坡
#  失稳的判断依据远不止这些 —— 降雨入渗导致孔隙水压力升高、抗滑力下降，
#  而最典型的前兆是**位移加速**（不是位移绝对值）。所以补齐下面五类。
#
#  注意：**这些工具只提供事实，不做告警判定**。判定归规则引擎（确定性、可复现），
#  Agent 负责综合与解释 —— 安全场景不能让大模型决定"要不要报警"。
# ══════════════════════════════════════════════════════════════════════

def _monitor_ctx():
    """取监测数据模块与会话工厂；未就绪时返回 (None, None)。

    用 _self_module() 而不是 `import end_new` —— 后者在脚本启动时会重新执行整个
    文件、拿到另一个没预热的实例（见 _self_module 说明）。
    """
    a = getattr(_self_module(), "agent", None)
    if a is None:
        return None, None
    try:
        return getattr(a, "monitor", None), getattr(a, "monitor_session", None)
    except Exception:  # noqa: BLE001
        return None, None


def _resolve_site(mod, sess, site: str) -> str:
    """把空字符串/中文名解析成点位编号，默认取主监测点。"""
    site = (site or "").strip()
    if site:
        return site
    try:
        return getattr(_self_module().agent, "primary_site", "S-01")
    except Exception:  # noqa: BLE001
        return "S-01"


@tool
def tool_get_displacement_rate(site: str = "", hours: int = 12) -> str:
    """查询监测点位的位移速率与趋势（mm/h），判断是否在**加速**。

    参数 site 为点位编号（如 S-01，留空则查主监测点），hours 为回看小时数。
    注意：判断滑坡风险要看**速率的变化**，不是位移的绝对值。
    """
    mod, sess = _monitor_ctx()
    if mod is None:
        return "监测数据模块未就绪"
    site = _resolve_site(mod, sess, site)
    r = mod.displacement_rate(sess, site, hours)
    if not r.get("ok"):
        return f"位移数据不可用：{r.get('error')}"
    return (f"点位 {site} 近 {hours} 小时：累计位移 {r['cumulative_mm']}mm，"
            f"当前速率 {r['rate_mm_per_h']}mm/h（前段 {r['rate_prev_mm_per_h']}mm/h），"
            f"加速度 {r['acceleration_mm_per_h2']}mm/h²，趋势：**{r['trend']}**")


@tool
def tool_get_rainfall(hours: int = 24) -> str:
    """查询降雨情况：累计雨量、最大小时雨强、降雨持续小时数。参数 hours 为回看小时数。

    降雨是边坡失稳**最主要的触发因素**，"久雨+暴雨"组合最危险。
    """
    mod, sess = _monitor_ctx()
    if mod is None:
        return "监测数据模块未就绪"
    r = mod.rainfall_summary(sess, hours)
    if not r.get("ok"):
        return f"降雨数据不可用：{r.get('error')}"
    return (f"近 {hours} 小时降雨：累计 {r['cumulative_mm']}mm，"
            f"最大小时雨强 {r['max_hourly_mm']}mm/h，"
            f"有效降雨时长 {r['wet_hours']} 小时")


@tool
def tool_get_pore_pressure(site: str = "", hours: int = 12) -> str:
    """查询孔隙水压力（kPa）及其抬升幅度。

    这是**力学上的直接原因**：降雨入渗 → 孔压升高 → 有效应力下降 → 抗滑力下降。
    参数 site 为点位编号（留空查主监测点），hours 为回看小时数。
    """
    mod, sess = _monitor_ctx()
    if mod is None:
        return "监测数据模块未就绪"
    site = _resolve_site(mod, sess, site)
    r = mod.pore_pressure(sess, site, hours)
    if not r.get("ok"):
        return f"孔压数据不可用：{r.get('error')}"
    return (f"点位 {site} 孔隙水压力：当前 {r['current_kpa']}kPa，"
            f"近 {hours} 小时抬升 {r['rise_kpa']}kPa")


@tool
def tool_get_neighbor_sites(site: str = "", hours: int = 6) -> str:
    """对比**同一边坡上其它监测点**的位移速率。

    **这是判断「整体滑动」还是「局部变形」的关键** ——
    多个点同步同向加速，比单点加速危险得多。留空则对比主监测点所在边坡。
    """
    mod, sess = _monitor_ctx()
    if mod is None:
        return "监测数据模块未就绪"
    site = _resolve_site(mod, sess, site)
    r = mod.neighbor_compare(sess, site, hours)
    if not r.get("ok"):
        return f"邻点数据不可用：{r.get('error')}"
    lines = [f"边坡 {r['slope_group']} 各组近 {hours} 小时位移速率："]
    for p in r["peers"]:
        lines.append(f"  - {p['site']} {p['name']}：{p['rate_mm_per_h']}mm/h"
                     f"（累计 {p['cumulative_mm']}mm）")
    if r["synchronized"]:
        lines.append(f"⚠️ 存在**多点同步加速**：{r['accelerating_sites']}"
                     f" —— 提示可能为整体滑动，而非局部变形")
    else:
        lines.append("未观察到多点同步加速")
    return "\n".join(lines)


@tool
def tool_get_crack_history(site: str = "", days: int = 7) -> str:
    """查询地表裂缝宽度及其变化（mm）。地表拉裂是滑坡的**直接前兆**。

    参数 days 为回看天数。
    """
    mod, sess = _monitor_ctx()
    if mod is None:
        return "监测数据模块未就绪"
    site = _resolve_site(mod, sess, site)
    r = mod.crack_history(sess, site, days)
    if not r.get("ok"):
        return f"裂缝数据不可用：{r.get('error')}"
    return (f"点位 {site} 裂缝宽度：当前 {r['current_mm']}mm，"
            f"近 {days} 天张开了 {r['open_delta_mm']}mm")


@tool
def tool_get_site_snapshot(site: str = "") -> str:
    """一次性获取某点位的当前完整读数快照（位移/降雨/孔压/裂缝/温度）。

    适合做"综合评估"时先拿全貌，再决定深入查哪一项。
    """
    mod, sess = _monitor_ctx()
    if mod is None:
        return "监测数据模块未就绪"
    site = _resolve_site(mod, sess, site)
    r = mod.current_snapshot(sess, site)
    if not r.get("ok"):
        return f"快照不可用：{r.get('error')}"
    parts = [f"点位 {site}（{r.get('name', '')}）当前读数："]
    for k, label, unit in (
        ("displacement_mm", "累计位移", "mm"),
        ("rainfall_mm", "当前雨强", "mm/h"),
        ("pore_pressure_kpa", "孔隙水压力", "kPa"),
        ("crack_width_mm", "裂缝宽度", "mm"),
        ("temperature_c", "温度", "℃"),
    ):
        if r.get(k) is not None:
            parts.append(f"  - {label}：{r[k]}{unit}")
    return "\n".join(parts)


@tool
def tool_get_active_alerts(hours: int = 24, only_unacked: bool = True) -> str:
    """查询**规则引擎**已产生的告警（等级、触发规则、判定依据）。

    用途：用户问"刚才是不是报警了""为什么报警"时，先拿到规则引擎的判定结果，
    再**解释**它 —— 而不是自己去判断该不该报警。

    ⚠️ 注意职责边界：**告警由确定性规则引擎产生，Agent 只负责解读**。
    回答里不要说"我判断应该报警"或"系统已自动报警"，而应说明"规则引擎
    在什么时间因哪条规则触发了什么等级的告警，依据是哪些数据"。
    """
    a = getattr(_self_module(), "agent", None)
    if a is None:
        return "告警模块未就绪"
    try:
        ae = getattr(a, "alert_engine", None)
        sess = getattr(a, "monitor_session", None)
    except Exception:  # noqa: BLE001
        return "告警模块未就绪"
    if ae is None or sess is None:
        return "告警模块未就绪（规则引擎未初始化）"

    try:
        with sess() as s:
            alerts = ae.active_alerts(s, hours=hours, only_unacked=only_unacked)
    except Exception as e:  # noqa: BLE001
        return f"告警查询失败：{type(e).__name__}: {e}"

    if not alerts:
        return (f"近 {hours} 小时没有"
                f"{'未确认的' if only_unacked else ''}告警记录。")

    lines = [f"近 {hours} 小时规则引擎共产生 {len(alerts)} 条告警"
             f"（按时间倒序，最多列 8 条）："]
    for x in alerts[:8]:
        lines.append(
            f"  · [{x['level_text']}] {x['at']} {x['site']} —— {x['message'].split('：', 1)[-1]}"
        )
        ev = x.get("evidence") or {}
        keys = ("displacement_rate", "displacement_accel", "rainfall_cum",
                "pore_pressure_rise", "crack_open")
        brief = "，".join(f"{k}={ev[k]}" for k in keys if k in ev)
        if brief:
            lines.append(f"      依据：{brief}")
        if x.get("acknowledged"):
            lines.append("      状态：已确认")
        else:
            lines.append("      状态：**未确认**")
    top = max(alerts, key=lambda x: {"blue": 1, "yellow": 2, "orange": 3, "red": 4}
              .get(x["level"], 0))
    lines.append(f"\n当前最高等级：**{top['level_text']}**（{top['site']}）")
    return "\n".join(lines)







# ============================================================================================ SQLAlchemy ORM 模型 ============================================================================================#
from sqlalchemy import create_engine, String, Text, Float, Boolean, DateTime, ForeignKey, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, scoped_session, relationship
from datetime import datetime

class _Base(DeclarativeBase):
    """ORM 基类"""
    pass

class _User(_Base):
    """用户表"""
    __tablename__ = "user"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="用户ID")
    name: Mapped[str] = mapped_column(String(64), nullable=False, default="default_user", comment="用户名")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="注册时间")
    memories = relationship("_Memory", back_populates="user")
    conversations = relationship("_Conversation", back_populates="user")

class _Conversation(_Base):
    """会话表"""
    __tablename__ = "conversation"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="会话ID")
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False, default=1, comment="所属用户ID")
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", comment="会话标识（手机、电脑）")
    status: Mapped[str] = mapped_column(String(16), default="active", comment="状态: active/closed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="创建时间")
    user = relationship("_User", back_populates="conversations")
    messages = relationship("_Message", back_populates="conversation", order_by="_Message.id")

class _Message(_Base):
    """对话消息表"""
    __tablename__ = "message"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="消息ID")
    conversation_id: Mapped[int] = mapped_column(Integer, ForeignKey("conversation.id"), nullable=False, comment="所属会话ID")
    role: Mapped[str] = mapped_column(String(16), nullable=False, comment="角色: user/ai/tool")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="消息正文")
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="消息时间")
    conversation = relationship("_Conversation", back_populates="messages")

class _Memory(_Base):
    """长期记忆表"""
    __tablename__ = "memory"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="记忆ID")
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False, default=1, comment="所属用户ID")
    key: Mapped[str] = mapped_column(String(128), nullable=False, comment="记忆键")
    value: Mapped[str] = mapped_column(Text, nullable=False, comment="记忆值")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="创建时间")
    user = relationship("_User", back_populates="memories")

class _Feedback(_Base):
    """用户反馈表"""
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="反馈ID")
    message_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("message.id"), nullable=True, comment="关联消息ID")
    type: Mapped[str] = mapped_column(String(8), nullable=False, comment="反馈类型: good/bad")
    content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="附加反馈文字")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="创建时间")

class _Event(_Base):
    """告警事件表"""
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="事件ID")
    timestamp: Mapped[str] = mapped_column(String(30), nullable=False, comment="事件时间")
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, comment="事件类型")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="事件描述")
    distance: Mapped[float | None] = mapped_column(Float, nullable=True, comment="触发距离(m)")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已处理")

class _PersistentHistory(list):
    """带持久化的会话历史。

    做成 `list` 的子类，是为了让原有 20 处 `self.history.append(...)`、
    `self.history[-10:]`、`self.history.clear()` **一行都不用改** ——
    结构变了、调用方式没变，这才叫增量改造而不是重写。

    每次改动同步写回后端（Redis 或内存）。会话历史只有几十条，
    整段序列化写回的成本可以忽略，换来的是"重启不丢上下文"。
    """

    # ── 滑动窗口：只保留最近 5 轮 ──
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

    def insert(self, i, item):
        super().insert(i, item)
        self._flush()

    def clear(self):
        super().clear()
        self._flush()

    def pop(self, *a):
        v = super().pop(*a)
        self._flush()
        return v

    def remove(self, item):
        super().remove(item)
        self._flush()

    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        self._flush()

    def __delitem__(self, k):
        super().__delitem__(k)
        self._flush()


class SimpleAgent:

    DIST_THRESHOLD_SAFE = 0.5  # 安全阈值
    DIST_THRESHOLD_WARN = 0.3  # 警戒阈值

##===================================这个属于初始化模块下属部分===================================##
    def __init__(self):
        self.rag = None

        # ── 会话隔离（原实现是 self.history = []，实例级共享）────────────────
        # 这是个**数据隔离漏洞**：配合 API 里硬编码的 session_id="default"，
        # 意味着用户 A 问完，用户 B 的上下文里会出现 A 的内容。
        # 现在按 session_id 分开存；`history` 变成属性（见下方），
        # 于是原有 20 处 `self.history` 的用法一行都不用改。
        self._histories = {}               # session_id -> _PersistentHistory
        self._current_session = "default"  # 当前请求所属会话
        # 会话存储后端：Redis 优先，不可用则回落内存（构造函数里初始化）
        self._session_store = None

        # ── Agent 执行锁 ─────────────────────────────────────────────────
        # 为什么是"串行"而不是"并发"：本机部署时推理在后端 4B 模型上跑，
        # **GPU 推理本身就是串行的**，并发只会让请求互相排队、还引入竞态
        # （Agent 有大量可变实例状态：当前 Trace、历史、统计）。
        # 所以这里用锁串行化 Agent 执行 —— 但**必须把阻塞移出事件循环**
        # （见 API 层的 `def _chat`），否则整个服务连同 /health 一起假死。
        self._agent_lock = threading.Lock()

        self.stats = {"tool_calls": 0, "rag_hits": 0, "total_queries": 0}
        self.obs_queue = queue.Queue()  # 可观测性：流式输出队列
        self.db_lock = threading.Lock()

        # SQLAlchemy ORM 初始化
        import config as _cfg
        from urllib.parse import quote_plus
        db_url = f"mysql+pymysql://{_cfg.DB_USER}:{quote_plus(_cfg.DB_PASSWORD)}@{_cfg.DB_HOST}:{_cfg.DB_PORT}/{_cfg.DB_NAME}"
        self._db_engine = create_engine(db_url, pool_size=5, max_overflow=10, pool_recycle=3600, echo=False)
        _Base.metadata.create_all(self._db_engine)
        self._Session = scoped_session(sessionmaker(bind=self._db_engine))
        self._init_events_db()  # MySQL 事件表创建（幂等）
        self._ensure_user()     # 确保默认用户存在，设置 self._user_id

        self._init_session_store()       # 会话存储后端（Redis 优先，回落内存）
        self.facts = self._load_facts()
        self._init_conversation_chain()  # LCEL 对话链（短期记忆+持久化）
        # （原 _init_fact_store 已删除：事实改用字典精确查，不再建 FAISS 索引）
        self._init_slope_monitor()       # 多因素监测数据（点位 + 多指标读数）
        self._init_react_agent()         # ReAct Agent（自主工具调用）
        self._init_long_term_memory()    # 结构化长期画像（摘要与事实分开存）

    def _init_long_term_memory(self):
        """初始化**结构化长期画像**记忆（slope_memory.ProfileMemory）。

        ## 为什么要在已有 `memory` 表 + FAISS 事实库之外再加一层

        原有长期记忆只有一个 key-value 事实库，有两个结构性问题：

        1. **画像没有结构**。"这个用户是运维、关注 S-01/S-03、要求给数字依据"
           这种信息必须以 key-value 逐条塞，既拼不出完整画像，也没法注入系统提示。
        2. **摘要和事实混在一个向量库里**。摘要是"关于对话的元叙述"，
           放进事实向量库检索必然是噪声 —— 所以原实现只能在检索到摘要时
           `startswith("对话摘要")` 直接丢弃（存了却不用）。

        新实现把两者分开：
          * `conversation_summary` 表 —— 只按时间取最近 N 条，用于压缩上下文，**永不进事实检索**
          * `user_profile` 表 —— 结构化画像（单行 JSON），注入系统提示

        失败一律降级：记忆是增强项，坏了不能影响主流程。
        """
        self.memory = None
        try:
            from slope_memory import ProfileMemory, build_default_llm
            self.memory = ProfileMemory(
                self._db_engine,
                llm=build_default_llm(),
                user_id=getattr(self, "_user_id", 1),
                extract_every=int(getattr(CFG, "memory_extract_every", 5)),
            )
            st = self.memory.stats()
            print(f"[记忆] 结构化长期画像就绪（已有画像字段 {st.get('profile_fields', 0)} 个，"
                  f"摘要 {st.get('summaries', 0)} 条）")
        except Exception as exc:                      # noqa: BLE001
            # 记忆不可用是**可接受的降级**：只是少了跨会话画像，主流程照跑。
            print(f"[记忆] 初始化失败（{type(exc).__name__}: {exc}），本次运行不启用长期画像")
            self.memory = None

    # ══ 会话隔离：history 按 session 分开 ═══════════════════════════════
    # 做成属性而不是改 20 处调用点 —— 结构变了、逻辑没变，
    # 这样"增量优化"不会把项目改成一个新东西。
    @property
    def history(self):
        """当前会话的历史记录（按 session_id 隔离，并持久化到会话存储后端）。

        首次访问某会话时从后端加载；之后由 _PersistentHistory 在每次改动时写回。
        于是"服务重启后上下文还在"这件事对上层完全透明。
        """
        sid = self._current_session
        if sid not in self._histories:
            # 用 getattr 而不是直接取属性：这个类在测试里会以 __new__ 方式构造
            # （绕开 __init__ 避免重复加载 RAG），直接访问会 AttributeError。
            store = getattr(self, "_session_store", None)
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
        return self._histories[sid]

    @history.setter
    def history(self, value):
        # 原代码里有 `self.history = self.history[-20:]` 这样的整体赋值，
        # 所以必须提供 setter，否则那处会直接报 AttributeError。
        #
        # ⚠️ 必须显式 _flush()：setter 只是换了一个新的 list 对象，
        # 不会经过 append/clear 那些会触发写回的入口。漏了这一步的话，
        # "截断历史"只发生在内存里，重启后旧数据又回来了（实测抓到过）。
        h = _PersistentHistory(getattr(self, "_session_store", None),
                               self._current_session, value)
        h._flush()
        self._histories[self._current_session] = h

    def enter_session(self, session_id):
        """进入某个会话。必须在每次请求开始时调用。"""
        self._current_session = (session_id or "default").strip() or "default"
        return self._current_session

    # ══ 基础模型链：历史统一由受保护的会话存储注入 ══
    def _init_conversation_chain(self):
        prompt = ChatPromptTemplate.from_messages([
            ("system", "你是边坡监测智能助手。禁止编造数字/坐标/天气数据。不知道就直说不知道。回答简短自然。"),
            MessagesPlaceholder("chat_history"),
            ("user", "{input}")
        ])
        # 这里同样必须限长 + 关思考：不限长时 qwen3:4b 会先生成数千 token 的
        # 内部推理，单次调用从 1-2 秒劣化到 30-50 秒（与另三处 OllamaLLM 同一问题）。
        llm = OllamaLLM(model=CFG.llm_model, base_url=CFG.llm_base_url,
                        timeout=CFG.timeout_llm_generate,
                        reasoning=True, num_predict=2048)
        self.conversation_chain = prompt | llm | StrOutputParser()
        print("[记忆] 对话链已就绪 (InMemoryChatMessageHistory)")

    # ══ 长期记忆：LangChain FAISS 向量存储的初始化 ══
    # ══ 自进化：新案例写回知识库 ══
    def _save_case_to_kb(self, case_text):
        """自进化：新案例写回知识库"""
        try:
            ts=time.strftime("%Y-%m-%d %H:%M:%S")
            entry=f"[案例] {ts} {case_text}\n"
            os.makedirs("rag_docs",exist_ok=True)
            with open("rag_docs/cases.txt","a",encoding="utf-8") as f:
                f.write(entry)
            print(f"[进化] 新案例已写入知识库")
        except Exception as e:
            print(f"[进化] 写入失败:{e}")

    # ══ 可观测性：推理步骤日志 ══
    def _log_step(self, step_type, content):
        """打印一条推理步骤到控制台。

        ## 改造说明：这里原来还往 MySQL `agent_trace` 表写一份

        那套写入和评测用的 `trace_step` **内容高度重叠**（都是"一步一行、
        带工具名和输出"），属于"新机制上线时旧机制没下线"造成的双写。
        更关键的是：读它的 `/trace` 接口**前端一次都没调用过** ——
        整条链是自循环，写了没人看。

        现在持久化统一走 `TraceRecorder`（trace_run + trace_step + JSONL），
        这里只保留控制台输出，方便本地调试时看实时步骤流。
        """
        print(f"[LOG][{time.strftime('%H:%M:%S')}][{step_type}] {str(content)[:80]}")

    def _query_fact(self, query):
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

        ## ⚠️ 顺手修掉的一个死分支

        改造前两个调用点传的都是**字面量 ``"用户信息"``**，而原分支只匹配
        "名字 / 职业 / 爱好" 这些词 —— ``"用户信息"`` 一个都不命中，
        之所以还能拿到结果，**完全是因为后面有 FAISS 兜底**。
        换句话说：那三个关键词分支从来没被执行过，是一条"看起来在用、
        实际全靠兜底"的死代码。现在 FAISS 拿掉了，必须把 "用户信息"
        这个**真正的入参**显式处理掉，否则查身份会直接返回 None。
        """
        q = str(query or "")

        # 综合身份查询：调用方传的就是 "用户信息"，把已知字段拼成一段。
        # 拼成 "键: 值" 是因为上游用 `":" in fact` 来判断"有没有查到"。
        if "用户信息" in q or "我是谁" in q or "身份" in q or "我叫什么" in q:
            parts = []
            for k, label in (("name", "用户的名字"),
                             ("occupation", "用户的职业"),
                             ("hobby", "用户的爱好")):
                v = self.facts.get(k, "")
                if v:
                    parts.append(f"{label}: {v}")
            return "；".join(parts) if parts else None

        if "名字" in q or "我叫啥" in q:
            name = self.facts.get("name", "")
            return f"用户的名字: {name}" if name else None
        if "职业" in q or "做什么" in q:
            occ = self.facts.get("occupation", "")
            return f"用户的职业: {occ}" if occ else None
        if "爱好" in q or "喜欢" in q:
            hobby = self.facts.get("hobby", "")
            return f"用户的爱好: {hobby}" if hobby else None
        return None


    # ══ 会话存储后端（Redis / 内存）══
    def _init_session_store(self):
        """按配置初始化；Redis 不可用时明确失败，不伪装成没有历史。"""
        import session_store as _ss
        import config as _cfg
        s = _cfg.settings if hasattr(_cfg, "settings") else _cfg.get_settings()
        store, note = _ss.build_session_store(
            prefer=getattr(s, "session_backend", "memory"),
            url=getattr(s, "redis_url", "redis://127.0.0.1:6379/0"),
            ttl=int(getattr(s, "session_ttl", 7 * 24 * 3600)),
            lock_ttl=int(getattr(s, "session_lock_ttl", 30)),
        )
        self._session_store = store
        print(f"[会话] 存储后端：{store.name} —— {note}")

    # ══ 多因素监测数据（点位 + 位移/降雨/孔压/裂缝/温度读数）══
    def _init_slope_monitor(self):
        """初始化多因素监测数据层。

        这是**新增数据源**，不改动任何既有链路：
        原来的超声波距离、天气、知识库检索照旧工作，
        这里只是把"判断边坡风险还需要看什么"补齐。

        失败不致命 —— 拿不到数据时对应的工具会如实返回"不可用"，
        而不是编一个数字出来（安全场景里，宁可说不知道）。
        """
        self.monitor = None
        self.monitor_session = None
        self.primary_site = "S-01"
        try:
            import slope_monitor as _sm
            from urllib.parse import quote_plus as _qp
            import config as _cfg
            _url = (f"mysql+pymysql://{_cfg.DB_USER}:{_qp(_cfg.DB_PASSWORD)}"
                    f"@{_cfg.DB_HOST}:{_cfg.DB_PORT}/{_cfg.DB_NAME}")
            _eng = _sm.build_engine(_url)
            _sm.Base.metadata.create_all(_eng)
            _sess = sessionmaker(bind=_eng)
            n_site, n_read = _sm.seed_if_empty(_sess)
            self.monitor = _sm
            self.monitor_session = _sess

            # ── 规则引擎（确定性告警）──
            # 与 Agent 是**两条独立链路**：这里负责"该不该报警"，
            # Agent 只负责"为什么报警、意味着什么"。安全场景下这个边界不能混。
            try:
                import alert_engine as _ae
                _ae.Base.metadata.create_all(_eng)
                with _sess() as _s:
                    n_rule = _ae.seed_rules(_s)
                    _r = _ae.run_once(_s)
                self.alert_engine = _ae
                print(f"[告警] 规则引擎就绪：{n_rule} 条规则，"
                      f"本轮触发 {_r['fired']} 条，最高等级 {_r['top_level'] or '无'}")
            except Exception as _e:  # noqa: BLE001
                self.alert_engine = None
                print(f"[告警] 规则引擎初始化失败（相关工具将返回不可用）：{_e}")
            # 主监测点：优先标记 is_primary 的那个
            try:
                with _sess() as _s:
                    row = _s.query(_sm.Site).filter(_sm.Site.is_primary == True).first()  # noqa: E712
                    if row:
                        self.primary_site = row.code
            except Exception:  # noqa: BLE001
                pass
            print(f"[监测] 多因素数据就绪：点位 {n_site} 个，读数 {n_read} 条，"
                  f"主监测点 {self.primary_site}")
        except Exception as e:  # noqa: BLE001
            print(f"[监测] 多因素数据初始化失败（相关工具将返回不可用）：{e}")

    # ══ ReAct Agent：自主工具调用 ══
    def _init_react_agent(self):
        try:
            # 性能与可控性的三个关键项（改造前都缺失，是"卡死"的根因）：
            #   reasoning=False 关闭 qwen3 的 thinking 长输出（默认开启，极慢）
            #   client_kwargs   给底层 HTTP 客户端设超时，避免无限等待
            #   num_predict     限制单次生成长度，防止小模型啰嗦到超时
            model = ChatOllama(
                model=CFG.llm_model,
                base_url=CFG.llm_base_url,
                temperature=CFG.llm_temperature,
                reasoning=True,
                num_predict=CFG.llm_num_predict,
                client_kwargs={"timeout": CFG.timeout_llm_generate},
            )
            tools = [
            tool_get_distance,
            tool_get_gps,
            tool_get_weather,
            tool_search_kb,
            # ── 多因素监测（新增）──
            # 补上真实边坡失稳判断所需的关键量：位移速率、降雨、孔压、
            # 邻点对比、裂缝。原有四个工具**原样保留**，只是"能看的量变多了"。
            tool_get_site_snapshot,
            tool_get_displacement_rate,
            tool_get_rainfall,
            tool_get_pore_pressure,
            tool_get_neighbor_sites,
            tool_get_crack_history,
            tool_get_active_alerts,
            ]
            system = (
            "你是边坡监测智能助手，也是技术问答专家。你可以使用以下工具：\n"
            "- tool_get_distance: 获取当前超声波距离传感器读数\n"
            "- tool_get_gps: 获取北斗GPS定位坐标\n"
            "- tool_get_weather: 查询城市天气\n"
            "- tool_search_kb: 搜索知识库（涵盖硬件排障、预警标准、算法原理、系统配置等）\n"
            "- tool_get_site_snapshot: 一次取某点位的完整读数快照（位移/雨强/孔压/裂缝/温度）\n"
            "- tool_get_displacement_rate: 位移速率与趋势，判断是否在**加速**\n"
            "- tool_get_rainfall: 累计雨量与小时雨强\n"
            "- tool_get_pore_pressure: 孔隙水压力及其抬升\n"
            "- tool_get_neighbor_sites: 对比同一边坡其它点位（判断整体滑动还是局部变形）\n"
            "- tool_get_crack_history: 地表裂缝宽度变化\n"
            "- tool_get_active_alerts: 查询**规则引擎**已产生的告警（等级/规则/依据）\n"
            "\n"
            "规则：\n"
            "1. 禁止编造数字/坐标/天气，实时数据必须通过工具获取\n"
            "2. 遇到知识类问题（原理/标准/配置/排障），必须先用 tool_search_kb 检索再回答\n"
            "3. 知识库有答案就直接引用，没有就诚实告知\n"
            "4. **评估边坡风险时要综合多因素，不要只看位移那一个数**：\n"
            "   - 位移要看**速率与是否加速**，不是绝对值（匀速和加速的当前位移可能一样，但后者是失稳前兆）\n"
            "   - 降雨是**最主要触发因素**，'久雨+暴雨'最危险\n"
            "   - 孔压升高是力学上的直接原因（有效应力下降→抗滑力下降）\n"
            "   - **多点同步同向加速比单点加速危险得多** → 用 tool_get_neighbor_sites 对比\n"
            "   - 地表裂缝张开是直接前兆\n"
            "4.1 **做风险综合评估时（用户问'有没有风险/会不会滑坡/综合判断'），\n"
            "     必须至少调用以下几项再下结论，缺哪项就说明缺哪项，不要凭部分数据下结论**：\n"
            "     tool_get_rainfall、tool_get_displacement_rate、tool_get_pore_pressure、\n"
            "     tool_get_neighbor_sites、tool_get_crack_history\n"
            "     其中最容易被忽略、也最关键的是 **tool_get_neighbor_sites** ——\n"
            "     它决定'整体滑动'还是'局部变形'，请优先调用。\n"
            "5. **你只做综合评估与解释，不做告警判定**。是否触发预警由确定性规则引擎判，\n"
            "   你给的是分析和建议。用户问'是不是报警了/为什么报警'时，\n"
            "   **先调 tool_get_active_alerts 拿规则引擎的实际判定**，再解释它 ——\n"
            "   绝不要说'我判断应该报警'或'系统已自动报警'。\n"
            "6. 数据不足以判断时，**主动说明缺哪项数据并建议补充**，不要臆测"
        )
            self.react_agent = create_agent(model, tools, system_prompt=system)
            print("[ReAct] agent ready")

            # ── 显式 LangGraph 控制流 ──
            #
            # 为什么不直接删掉 react_agent：
            #   图是新主路径，但构图本身可能因为 langgraph 版本差异失败。
            #   保留 create_agent 作为**兜底实现**，构图失败时 run() 自动退回，
            #   这样"改造控制流"不会变成"把服务搞挂"。
            #
            # intent_llm 传同一个 model：只在**规则判不出意图**时才会用到它
            #   （见 slope_graph.classify_intent_rules），不是每个请求都问一遍。
            try:
                self.slope_graph = build_slope_graph(
                    agent=self,
                    tools=tools,
                    model=model,
                    system_prompt=system,
                    max_rounds=max(1, int(CFG.react_max_iterations)),
                    intent_llm=model,
                    # 必须传：收口节点要剥掉模型思考段。
                    # 不传的后果实测过 —— 推理里的"我判断应该报警"会被当成答案返回，
                    # 破坏"规则引擎判定、Agent 只解释"的双轨边界。
                    strip_thinking=strip_thinking,
                )
                print("[Graph] 显式控制流图就绪（意图识别 + 按意图收窄工具 + 显式 ReAct 循环）")
            except Exception as ge:
                print(f"[Graph] 构图失败: {type(ge).__name__}: {ge}，回退 create_agent")
                self.slope_graph = None
        except Exception as e:
            print(f"[ReAct] init error:{e}, fallback to rule-based")
            self.react_agent = None
            self.slope_graph = None

    def _get_mysql_conn(self):
        """获取 SQLAlchemy ORM session"""
        return self._Session()

    def _ensure_user(self):
        """确保默认用户存在，设置 self._user_id"""
        try:
            session = self._get_mysql_conn()
            user = session.query(_User).filter(_User.name == "default_user").first()
            if not user:
                user = _User(name="default_user", created_at=datetime.utcnow())
                session.add(user)
                session.commit()
                session.refresh(user)
            self._user_id = user.id
            session.close()
            print(f"[用户] user_id={self._user_id}")
        except Exception as e:
            print(f"[用户] 初始化失败: {e}")
            self._user_id = 1

    def _init_events_db(self):
        """ORM: 事件表已由 create_all 创建（幂等）"""
        print("[事件库] ORM 就绪 (_Event)")

    def _load_facts(self):
        """ORM: 加载长期记忆"""
        try:
            session = self._get_mysql_conn()
            rows = session.query(_Memory).filter(_Memory.user_id == 1).all()
            session.close()
            if rows:
                facts = {r.key: r.value for r in rows}
                print(f"[记忆] MySQL 加载 {len(facts)} 条")
                return facts
        except Exception as e:
            print(f"[记忆] MySQL 失败: {e}")
        return {}


    def _save_facts(self):
        """ORM: 写入长期记忆"""
        try:
            session = self._get_mysql_conn()
            for k, v in self.facts.items():
                existing = session.query(_Memory).filter(
                    _Memory.user_id == 1, _Memory.key == k
                ).first()
                if existing:
                    existing.value = str(v)
                else:
                    session.add(_Memory(user_id=1, key=k, value=str(v)))
            session.commit()
            session.close()
        except Exception as e:
            print(f"[记忆] MySQL 写入失败: {e}")

    def _store_event(self, event_type, description, distance=None):
        """ORM: 写入事件"""
        try:
            session = self._get_mysql_conn()
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            session.add(_Event(timestamp=ts, event_type=event_type,
                              description=(description or "")[:200], distance=distance))
            session.commit()
            session.close()
            print(f"[事件库] 已存储: {event_type}")
        except Exception as e:
            print(f"[事件库] 存储失败: {e}")

    def _recall_events(self, query_text, limit=10):
        """ORM: 检索事件"""
        try:
            session = self._get_mysql_conn()
            results = []
            # 1. 日期查询
            m = re.search(r'(\d+)月(\d+)[日号]', query_text)
            if m:
                mon, day = m.group(1), m.group(2)
                pat = f"{mon.zfill(2)}-{day.zfill(2)}%"
                rows = session.query(_Event).filter(
                    _Event.timestamp.like(f"%{pat}%") |
                    _Event.description.like(f"%{mon}%月%{day}%")
                ).order_by(_Event.timestamp.desc()).all()
                for r in rows:
                    st = r.timestamp[-14:-3] if len(r.timestamp) > 14 else r.timestamp
                    ds = f"({r.distance:.2f}m)" if r.distance else ""
                    sd = (r.description or "")[:50] + ("..." if r.description and len(r.description) > 50 else "")
                    results.append(f"{st} {ds} {sd}")
            # 2. 关键词搜索
            if not results:
                words = re.findall(r'[\u4e00-\u9fa5]{2,}|[a-zA-Z]{2,}', query_text)
                kw_list = [w for w in words if w not in ('什么','怎么','哪个','哪些','是','的','了','吗','呢','吧','啊')]
                rowset = []
                seen = set()
                for kw in kw_list[:8]:
                    rows = session.query(_Event).filter(
                        _Event.event_type.like(f"%{kw}%") |
                        _Event.description.like(f"%{kw}%") |
                        _Event.timestamp.like(f"%{kw}%")
                    ).order_by(_Event.timestamp.desc()).limit(limit).all()
                    for r in rows:
                        key = (r.timestamp, r.event_type)
                        if key not in seen:
                            seen.add(key)
                            rowset.append(r)
                    if len(rowset) >= limit:
                        break
                for r in sorted(rowset, key=lambda x: x.timestamp, reverse=True):
                    st = r.timestamp[-14:-3] if len(r.timestamp) > 14 else r.timestamp
                    ds = f"({r.distance:.2f}m)" if r.distance else ""
                    sd = (r.description or "")[:50] + ("..." if r.description and len(r.description) > 50 else "")
                    results.append(f"{st} {ds} {sd}")
            # 3. 兜底
            if not results:
                rows = session.query(_Event).order_by(_Event.timestamp.desc()).limit(limit).all()
                for r in rows:
                    st = r.timestamp[-14:-3] if len(r.timestamp) > 14 else r.timestamp
                    ds = f"({r.distance:.2f}m)" if r.distance else ""
                    sd = (r.description or "")[:50] + ("..." if r.description and len(r.description) > 50 else "")
                    results.append(f"{st} {ds} {sd}")
            session.close()
            return results
        except Exception as e:
            print(f"[事件库] 检索失败: {e}")
            return []

    # ── 趋势与自检 ──
    def _check_trend(self):
        """
    分析最近30秒的距离变化趋势，判断是否触发主动预警。

    数据来源：全局变量 dist_log，每个元素为 (timestamp, distance)
    分析逻辑：
        1. 取最近30秒内的数据点（最多30个）
        2. 计算首尾差值 delta 和经过时间 elapsed
        3. 计算变化速率 rate = delta / elapsed（单位：米/秒）
        4. 根据 delta 和当前距离判断预警级别：

    判断规则（三个级别）：
        - 级别1（明显下降但未触发阈值）：
          delta > 0.05m 且 当前距离 > 0.3m
          → 提示"下降趋势明显，建议加强监测"
        - 级别2（小幅下降，需持续观察）：
          delta > 0.02m 且 当前距离 > 0.3m
          → 提示"趋势平稳，但需持续观察"
        - 级别3（已降至警戒线且下降速度快）：
          delta > 0.2m 且 当前距离 <= 0.3m
          → 提示"已降至警戒线，立即关注"

    返回值：
        - str: 包含预警级别、变化量、速率的中文提示
        - ""（空字符串）: 数据不足或趋势正常，不触发预警
    """

        global dist_log, dist_log_lock
        if len(dist_log) < 5: 
            return ""
        with dist_log_lock: 
            recent = dist_log[-30:][:]
        first = recent[0] 
        last  = recent[-1]
        delta = first[1] - last[1]
        elapsed = last[0] - first[0]

        if elapsed <= 0: 
            return ""
        rate = delta / elapsed
        if delta > 0.05 and last[1] > self.DIST_THRESHOLD_WARN:
            return (f"趋势预警：过去{elapsed:.0f}秒内距离从{first[1]:.2f}m降至{last[1]:.2f}m,"
                   f"降幅{delta:.2f}m(速率{rate:.4f}m/s),虽未触发0.3m阈值但下降趋势明显,建议加强监测。")
        elif delta > 0.02 and last[1] > self.DIST_THRESHOLD_WARN:
            return f"当前距离{last[1]:.2f}m,近{elapsed:.0f}秒下降{delta:.2f}m,趋势平稳,但需持续观察。"
        elif delta > 0.2 and last[1] <= self.DIST_THRESHOLD_WARN:
            return f"⚠️ 距离已降至{last[1]:.2f}m且下降速度较快({delta:.2f}m/{elapsed:.0f}s),请立即关注！"
        return ""

    def _check_sensor_health(self):
        global dist_log, dist_log_lock
        if not dist_log: return ""
        now = time.time()
        with dist_log_lock:
            last_vals = dist_log[-10:][:]
            last = dist_log[-1]
        gap = now - last[0]
        if gap > 300: return f"传感器可能掉线：最后一次更新于{gap:.0f}秒前({gap / 60:.1f}分钟),请检查连接。"
        if len(last_vals) >= 10:
            vals = [v for _, v in last_vals]
            if len(set(vals)) == 1:
                ft = last_vals[0][0]; lt = last_vals[-1][0]
                if lt - ft > 300: return f"传感器可能卡死：最近5分钟内10次读数均为{vals[0]:.2f}m未变化,请检查传感器。"
        return ""

    # ── 工具 ──
    def get_distance(self):
        global current_distance
        d = current_distance
        if d <= 0: return "传感器暂无数据,请稍后再试"
        alert = "⚠️ 距离过近,边坡有坍塌风险！" if d < self.DIST_THRESHOLD_SAFE else "✅ 安全距离"
        return f"当前边坡距离：{d:.2f} 米。{alert}"

    def get_gps(self):
        global current_lat, current_lng
        if abs(current_lat) < 0.01: return "GPS尚未定位,请确认天线已连接"
        return f"当前位置：纬度 {current_lat:.6f},经度 {current_lng:.6f}"

    def get_weather(self, city):
        try:
            import requests
            # 1. 地理编码：城市名 → 坐标
            geo = requests.get(
                f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=zh",
                timeout=CFG.timeout_http_tool)
            if geo.status_code != 200 or not geo.json().get("results"):
                return f"未找到城市'{city}'，请检查城市名"
            loc = geo.json()["results"][0]
            lat, lon = loc["latitude"], loc["longitude"]
            display_name = loc.get("name", city)
            # 2. 天气查询
            r = requests.get(
                f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,weather_code",
                timeout=CFG.timeout_http_tool)
            if r.status_code == 200:
                d = r.json()["current"]
                wcode = {0: "晴",1: "晴",2: "多云",3: "阴",45: "雾",51: "小雨",53: "小雨",61: "中雨",63: "中雨",71: "小雪",73: "小雪",80: "阵雨",81: "阵雨",95: "雷阵雨"}.get(d.get("weather_code",0),"未知")
                return f"{display_name} 当前天气：{wcode}，温度 {d['temperature_2m']}°C，湿度 {d['relative_humidity_2m']}%"
        except: 
            pass
        return f"{city} 天气查询失败，请稍后重试"


    def search_kb(self, query):
        """向量RAG检索（降级逻辑由 VectorRAG 内部处理）"""
        if self.rag:
            try:
                result = self.rag.ask(query)
                if result and "未找到" not in result:
                    self.stats["rag_hits"] += 1
                return result
            except Exception as e:
                print(f"[RAG] 检索失败: {e}")
        return "知识库未加载"


    def _extract_facts(self, text):
        """从用户输入中提取事实（姓名、职业、爱好等），存入字典返回。"""

        facts = {}
        # 1. 提取姓名：我叫XXX
        m = re.search(r"我叫\s*([\u4e00-\u9fa5a-zA-Z]+)", text)
        if m:
            nm_candidate = m.group(1)
            exclude = {"谁", "什么", "啥", "叫", "名字", "什么名字"}
            if nm_candidate and not any(w in nm_candidate for w in exclude):
                facts["name"] = nm_candidate

        # 2. 提取职业：我是XXX
        m = re.search(r"我是\s*(?:一个|名)?\s*([\u4e00-\u9fa5a-zA-Z]+)", text)
        if m:
            occ_candidate = m.group(1).rstrip("的")
            exclude = {"谁", "什么", "啥", "做什么", "什么人"}
            if occ_candidate and not any(w in occ_candidate for w in exclude):
                facts["occupation"] = occ_candidate

        # 3. 提取爱好：我喜欢XXX
        m = re.search(r"我喜欢\s*(.+?)(?:[。，、！？\n]|$)", text)
        if m:
            hobby = m.group(1).strip()
            if hobby not in ("什么", "啥"):
                facts["hobby"] = hobby

        # 4. 提取自定义事实：我的X是Y
        m = re.search(r"我的\s*(.{2,10}?)\s*是\s*(.{2,20}?)(?:[。，、！？]|$)", text)
        if m:
            key = m.group(1).strip()
            value = m.group(2).strip()
            facts[key] = value

        return facts



    def _save_to_conversation(self, user_msg, ai_msg):
        """ORM: 写入对话消息 + 超过10轮自动摘要

        **按当前会话落库** —— 原实现把 session_id 写死成 "default"，
        于是 `/history?session_id=别的值` 永远返回 0 条：
        用户明明聊过，记录却在 default 这个会话下。属于同一类"看起来对、
        实际没接上"的静默缺陷。
        """
        try:
            sid = self._current_session
            session = self._get_mysql_conn()
            conv = session.query(_Conversation).filter(
                _Conversation.session_id == sid
            ).first()
            if not conv:
                conv = _Conversation(user_id=1, session_id=sid, status="active")
                session.add(conv)
                session.flush()
            session.add(_Message(conversation_id=conv.id, role="user", content=str(user_msg)[:500]))
            session.add(_Message(conversation_id=conv.id, role="ai", content=str(ai_msg)[:500]))
            session.commit()
            session.close()
        except Exception as e:
            print(f"[消息] MySQL 写入失败: {e}")
        # 注意：这里原来有一句「len(self.history) > 20 就触发摘要」。
        # 现在摘要由滑动窗口驱动 —— 历史被挤出窗口时，_on_history_evict 会把
        # 那几条攒起来，够一批就异步滚动一次。窗口只有 10 条，旧的 20 阈值
        # 永远不会成立，留着就是死代码。

    # ──────────────── 滚动摘要（方案 B）────────────────
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
        成本：纯 list 操作，真正的 LLM 调用在 _roll_summary 的后台线程里。
        """
        # 没有结构化记忆后端（memory 为 None，比如没连上 MySQL 的测试环境）时
        # 直接退出：摘要生成出来也没地方存，起线程只会白调 LLM、刷屏日志。
        if getattr(self, "memory", None) is None:
            return
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

            convo = "\n".join(
                f"{'用户' if r == '用户' else 'AI'}：{str(c)[:80]}" for r, c in batch
            )
            prompt = (
                "把「已有摘要」和「新增对话」合并成一段新的摘要，要求：\n"
                "- 100 字以内，只输出摘要本身，不要写'好的'/'用户说'这类叙述\n"
                "- 保留：讨论过哪些点位、发现了什么风险、做了或建议了什么处置\n"
                "- 已有摘要里仍然重要的信息不要丢\n\n"
                f"已有摘要：{old_text}\n\n新增对话：\n{convo}\n\n新摘要："
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



    # ══ 复合问题拆分 ══
    def _split_complex_query(self, text):
        """检测并拆分多意图问题"""
        # 按标点拆分
        parts = re.split(r'[？?！!。；;]', text)
        parts = [p.strip() for p in parts if len(p.strip()) > 2]
        if len(parts) > 1: return parts
        # 无标点：按关键词分组检测
        kw_groups = [
            ["距离", "多远", "多少米", "位移"],
            ["GPS", "经纬度", "定位", "坐标"],
            ["天气", "气温", "温度", "下雨"],
            ["告警", "事件", "历史", "发生过"],
            ["传感器", "正常吗", "健康", "掉线"],
            ["滑坡", "危险", "安全", "塌"],
        ]
        found = [i for i,grp in enumerate(kw_groups) if any(k in text for k in grp)]
        if len(found) > 1:
            # 至少两个不同意图 → 直接并行调用工具
            return ["__COMPOUND__"]  # 标记为复合，特殊处理
        return [text]



    def _needs_comprehensive(self, text) -> bool:
        """判断这是不是"需要综合多因素"的问法。

        这类问题**不能**被关键词直答吞掉：直答只看超声波距离一个数，
        而综合评估要看位移速率、降雨、孔压、邻点、裂缝。交回 ReAct 才有意义。

        判据刻意保守 —— 只有出现明确的"综合/评估/分析/趋势"等词才触发，
        普通问法（"距离多少""现在安全吗"）仍走确定性直答，保持快且可复现。
        """
        hints = ("综合", "评估", "分析", "判断", "结合", "怎么看", "怎么判断",
                 "有没有风险", "风险有多大", "有多大风险", "趋势", "为什么",
                 "原因", "正常吗", "危不危险")
        return any(k in text for k in hints)

    def _is_knowledge_question(self, text) -> bool:
        """判断是不是"知识/解释类"问题 —— 这类问题必须交给 RAG / ReAct。

        ## 为什么需要这个判定

        `run()` 里有一长串**关键词直答拦截器**（传感器自检、趋势分析、GPS…），
        它们的本意是让"传感器正常吗""距离多少"这类**简短运维口令**秒回，
        但那套关键词太宽：只要问题里出现"传感器"三个字就会命中传感器自检，
        于是"超声波传感器读数跳变是什么原因？怎么排查？"
        —— 一个典型的**知识库问题** —— 被它拦下，返回"当前无距离数据"。
        工具和知识库明明都在，却永远没机会被用到。

        判据：出现"为什么/原因/排查/原理/怎么处理"这类措辞，说明用户在**问解释**，
        不是在**要状态**。状态类问法（"正常吗""多远"）不含这些词，仍然秒回。
        """
        hints = ("为什么", "什么原因", "原因", "怎么排查", "如何排查", "排查",
                 "原理", "是什么", "有什么区别", "有啥区别", "区别",
                 "怎么处理", "如何处理", "怎么办", "怎么回事", "解释",
                 "介绍", "有哪些", "讲讲", "原理是", "说说",
                 # ── 以下为「讲原理/讲因素」类说法补充（实测漏判过）──
                 # 例："影响边坡滑坡的因素？" —— 原判据一个词都匹配不上，
                 # 于是被第 0 步的"滑坡"关键词拦成"当前无距离数据"。
                 # 这些词都**强烈指向"要解释"**，而不会出现在
                 # "距离多少""传感器正常吗"这类要状态的短口令里。
                 "因素", "成因", "机理", "怎么影响", "如何影响",
                 "有何影响", "有什么影响", "什么关系", "有何关系",
                 "作用是", "意义是", "介绍一下", "解释一下")
        return any(k in text for k in hints)

    # ══ 共用：城市名提取 ══
    def _extract_city(self, text):
        """从文本中提取城市名，'这边/当地'等回退到GPS或默认西安"""
        clean = re.sub(r'^(现在|今天|明天|昨天|后天|大后天|中午|晚上|早上|上午|下午|当前)\s*', "", text)
        m = re.search(r'([\u4e00-\u9fa5]{2,4})(?:的)?(?:天气|气温|温度|下雨)', clean)
        city = m.group(1) if m else "西安"
        if city in ("这边", "那边", "这里", "那里", "当地", "现场", "本地", "今天", "最近", "几天"):
            global current_lat, current_lng
            if abs(current_lat) > 0.01: city = "西安"
            else: city = "西安"
        return city



    # ══ 统一处理方法（消除重复）══
    def _handle_slip_judgment(self, user_input=""):
        """滑坡判断：阈值判断 + 历史案例检索"""
        global current_distance
        d = current_distance
        if d <= 0: return "当前无距离数据,请检查传感器连接。"
        if d > self.DIST_THRESHOLD_SAFE:
            ans = f"距离{d:.2f}m，暂时安全"
        elif d > self.DIST_THRESHOLD_WARN:
            ans = f"距离{d:.2f}m，警戒区间({self.DIST_THRESHOLD_WARN}-{self.DIST_THRESHOLD_SAFE}m)"
        else:
            ans = f"距离{d:.2f}m，高风险(>{self.DIST_THRESHOLD_WARN}m)"
        case = self.search_kb("滑坡案例 位移")
        if case and "未找到" not in case and len(case) > 20:
            ans += f"\n参考案例：{case[:200]}"
        self.stats["tool_calls"] += 1
        return ans



    def _handle_sensor_check(self):
        """传感器自检：健康 + 趋势"""
        h = self._check_sensor_health()
        t = self._check_trend()
        ans = "传感器状态报告：\n"
        ans += h or "工作正常。\n"
        ans += t if t else f"当前距离：{current_distance:.2f}m。"
        return ans



    def _handle_event_query(self, q, limit=5):
        """历史事件查询"""
        evts = self._recall_events(q, limit=limit)
        if evts:
            NL = chr(10)
            return NL.join(["相关事件："] + evts[:8])
        return "暂无历史记录。"


    def _mark_tool(self, name):
        """登记一次工具调用到当前请求的 Trace。

        直接工具路径（`_execute_multi_intent`）原先完全不产生工具记录，
        导致 Tool Accuracy 只能覆盖 ReAct 路径。加上这层埋点后，
        两条路径的工具调用都可测。
        """
        rec = getattr(self, "_active_rec", None)
        if rec is not None:
            try:
                rec.add_tool(name)
                rec.note_step("tool", name)
            except Exception:
                pass

    def _execute_multi_intent(self, text):
        """检测 text 中的关键词，并行调用对应工具，返回合并后的结果。"""
        results = []
        if any(k in text for k in ["距离", "多远", "位移"]):
            self.stats["tool_calls"] += 1
            self._mark_tool("tool_get_distance")
            ans = self.get_distance()
            if "暂无" not in ans: results.append(ans)
        if any(k in text for k in ["GPS", "经纬度", "坐标", "定位"]):
            self.stats["tool_calls"] += 1
            self._mark_tool("tool_get_gps")
            ans = self.get_gps()
            if "暂无" not in ans: results.append(ans)
        if any(k in text for k in ["天气怎么样", "查天气", "气温多少", "温度多少", "下雨"]):
            self.stats["tool_calls"] += 1
            self._mark_tool("tool_get_weather")
            results.append(self.get_weather(self._extract_city(text)))
        # 移除历史事件自动触发，交给 run() 中的独立拦截或 ReAct 处理
        if any(k in text for k in ["传感器"]):
            results.append(self._handle_sensor_check())
        if any(k in text for k in ["滑坡", "塌方"]):
            results.append(self._handle_slip_judgment(text))
        if not results: return None
        if len(results) == 1: return results[0]
        return chr(10).join(results)

    def _run_single(self, q):
        """处理单条子问题（避免递归死循环）"""
        if any(k in q for k in ["天气", "气温", "温度", "下雨"]):
            city = self._extract_city(q)
            try: return self.get_weather(city)
            except: return f"{city}天气查询失败"
        if any(k in q for k in ["距离", "多远", "多少米", "位移"]):
            self.stats["tool_calls"] += 1
            ans = self.get_distance()
            return ans if "暂无" not in ans else "数据暂不可用"
        if any(k in q for k in ["GPS", "经纬度", "定位", "坐标"]):
            self.stats["tool_calls"] += 1
            ans = self.get_gps()
            return ans if "暂无" not in ans else "数据暂不可用"
        if any(k in q for k in ["告警", "事件", "历史", "发生"]):
            evts = self._recall_events(q, limit=3)
            return "\n".join(evts[:3]) if evts else "暂无记录"
        if any(k in q for k in ["传感器", "正常"]):
            return self._check_sensor_health() or "传感器正常"
        if any(k in q for k in ["滑坡", "危险", "安全", "塌"]):
            global current_distance
            d = current_distance
            if d <= 0: return "无距离数据"
            if d>self.DIST_THRESHOLD_SAFE: return f"距离{d:.2f}m，安全"
            if d>self.DIST_THRESHOLD_WARN: return f"距离{d:.2f}m，警戒"
            return f"距离{d:.2f}m，⚠️高风险"
        return None
    
 #============================================这个run函数是启动的重中之重============================================#
    def run(self, user_input, session_id=None):
        """会话级互斥、重新加载共享历史，成功后一次提交完整问答。"""
        # API 还有外层 _agent_lock；这里保护桌面/评测等直接调用入口。
        lock = getattr(self, "_run_lock", None)
        if lock is None:
            # __new__ 测试实例也适用；setdefault 在 GIL 下仅保留一把实例锁。
            lock = self.__dict__.setdefault("_run_lock", threading.RLock())
        with lock:
            if getattr(self, "_turn_active", False):
                raise RuntimeError("不允许在同一 Agent 内嵌套执行一轮会话")
            sid = self.enter_session(session_id if session_id is not None
                                     else getattr(self, "_current_session", "default"))
            store = getattr(self, "_session_store", None)
            if store is None:
                # 无存储的轻量测试/显式本地调用使用内存；Redis 异常不会走到这里。
                from session_store import MemorySessionStore
                store = self._session_store = MemorySessionStore()
            with store.session_guard(sid):
                # 每轮都重新加载，不能让实例本地副本盖掉其他实例的新回答。
                loaded = store.load(sid)
                previous = self._complete_history_pairs(loaded)
                working = _PersistentHistory(None, sid, previous)
                self._histories[sid] = working
                self._turn_previous = previous
                self._turn_active = True
                try:
                    answer = self._run_once(user_input, session_id=sid)
                    merged = previous + [("用户", str(user_input)), ("AI", str(answer or ""))]
                    overflow = max(0, len(merged) - _PersistentHistory.MAX_ENTRIES)
                    retained = merged[overflow:]
                    # 唯一提交点；存储端原子校验锁持有者、写窗口并更新计数/TTL。
                    store.commit_turn(sid, retained)
                    self._histories[sid] = _PersistentHistory(
                        store, sid, retained,
                        on_evict=getattr(self, "_on_history_evict", None))
                    if overflow:
                        try:
                            self._on_history_evict(sid, merged[:overflow])
                        except Exception as exc:
                            print(f"[记忆] 已提交问答，挤出摘要处理失败: {type(exc).__name__}")
                    return answer
                except BaseException:
                    # 工作副本未提交时丢弃；下次请求仍从共享后端重读。
                    self._histories.pop(sid, None)
                    raise
                finally:
                    self._turn_active = False
                    self.__dict__.pop("_turn_previous", None)
                    self._histories.pop(sid, None)

    def _prompt_history(self):
        """模型使用同一份已提交历史，不把助手回答误作用户消息。"""
        previous = getattr(self, "_turn_previous", None)
        if previous is None:
            previous = self._complete_history_pairs(self.history)
        return [HumanMessage(content=content) if role == "用户" else AIMessage(content=content)
                for role, content in previous]

    @staticmethod
    def _complete_history_pairs(items):
        """兼容旧版夹杂 Tool 或半轮的 JSON，只保留完整 user/AI 配对。"""
        pairs = []
        pending = None
        for role, content in items:
            if role in ("用户", "user", "human"):
                pending = str(content)
            elif role in ("AI", "ai", "assistant") and pending is not None:
                pairs.extend([("用户", pending), ("AI", str(content))])
                pending = None
        return pairs[-_PersistentHistory.MAX_ENTRIES:]

    def _run_once(self, user_input, session_id=None):
        """处理一次用户请求。

        改造点（相对原实现）：
        * 每个路由分支都显式登记 route，使 Route Accuracy 可计算；
        * ReAct 循环里真实发生的工具调用会写入 Trace，使 Tool Accuracy 可计算；
        * 引入请求级 Deadline，预算耗尽即降级，避免串行超时叠加；
        * 降级路径（关键词兜底 / 基础模型兜底）显式记录，降级率成为可观测指标；
        * **新增 session_id**：会话上下文按 session 隔离，修复跨用户串上下文。

        `session_id is None` 时**沿用当前会话**而不是重置为 default ——
        因为内部存在 `agent.run(q)` 这类嵌套调用，若默认重置会把外层设好的
        会话冲掉（表现为"多用户改造后反而串得更厉害"）。
        """
        global current_distance
        if session_id is not None:
            self.enter_session(session_id)
        self.history.append(("用户", user_input))
        self.stats["total_queries"] += 1
        start_time = time.time()  # 请求耗时记录

        # ── 可观测性：本次请求的 Trace 记录器 ──
        rec = TraceRecorder(
            query=user_input,
            question_id=getattr(self, "_eval_question_id", None),
            suite=getattr(self, "_eval_suite", "online"),
            request_id=getattr(self, "_current_trace_id", ""),
        )
        deadline = new_deadline()
        degrade = DegradationLog()
        # 当前请求的 Trace 记录器：供 _mark_tool() 等深层方法登记工具调用
        self._active_rec = rec

        def _done(ans, route):
            """登记路由并落 Trace，然后返回答案。"""
            rec.set_route(route)
            self._active_rec = None
            rec.finish(answer=str(ans or ""))
            return ans

        # 0. 复合问题拆分 + 强制工具调用（合并）
        #
        # ⚠️ 分流边界（这一条很重要）：
        # 关键词直答只认「超声波距离」一个数，够用来回答"现在安全吗"这类阈值问题；
        # 但**综合评估类**问法（"结合降雨综合判断""评估风险""趋势怎么样"）需要看
        # 位移速率、降雨、孔压、邻点、裂缝 —— 只有 ReAct 能拿到这些。
        # 如果让直答把这类问题吞掉，就出现"能力加了却永远用不上"的怪象
        # （实测就是这样：问"最近下雨有没有滑坡风险"返回的是"无距离数据"）。
        # 所以：**阈值类走确定性直答，综合类交回 Agent**。
        comprehensive = self._needs_comprehensive(user_input)

        # 知识类问题不做复合拆分、也不走关键词直答 ——
        # 它要的是"解释"，而 `_execute_multi_intent` 给的是一串工具读数。
        _kb_question = self._is_knowledge_question(user_input)
        parts = [] if (comprehensive or _kb_question) else self._split_complex_query(user_input)
        if parts == ["__COMPOUND__"] or len(parts) > 1:
            combined = user_input if parts == ["__COMPOUND__"] else " ".join(parts)
            ans = self._execute_multi_intent(combined)
            if ans:
                self.history.append(("AI", ans)); self._save_to_conversation(user_input, ans)
                return _done(ans, "compound")
        # ★ 这里是**最先执行**的拦截点，之前只给第 5/6 步加了守卫，漏了它。
        #   后果：任何含"滑坡""传感器"的知识类问题都在这里被吞掉，
        #   返回一条固定兜底话术，RAG 和 Agent 完全没机会跑。
        if not comprehensive and not _kb_question:
            ans = self._execute_multi_intent(user_input)
            if ans:
                self.history.append(("AI", ans)); self._save_to_conversation(user_input, ans)
                # 单意图直接工具调用（距离 / GPS / 天气 / 传感器 / 滑坡关键词命中），
                # 与真正的多意图拆分区分开，否则 Route Accuracy 会把两者混为一谈
                return _done(ans, "tool_direct")

        # 0.5 历史事件查询：仅"发生过""什么时候"等明确问历史的词触发
        if any(k in user_input for k in ["发生过", "什么时候", "记录"]):
            ans = self._handle_event_query(user_input)
            if ans and "暂无" not in ans and "相关事件" in ans:
                self.history.append(("AI", ans))
                self._save_to_conversation(user_input, ans)
                return _done(ans, "event_query")

        # 1. 事实提取 + 保存（JSON + FAISS 双写）  #这个是来保存用户画像的
        new_facts = self._extract_facts(user_input)  #新事实new_facts，是用户输入的信息
        if new_facts:
            self.facts.update(new_facts)        #老事实是之前存入到json文件中的信息
            self._save_facts()
            # 说明：这里原来还有一段"同步写入 FAISS"。
            # 事实的唯一权威存储是 memory 表（_save_facts 已写），
            # self.facts 字典也已经更新，FAISS 只是它的重复索引 —— 已删除。

        if "name" in new_facts:
            ans = f"记住啦,你叫{new_facts['name']}"
            self.history.append(("AI", ans))
            self._save_to_conversation(user_input, ans)
            return _done(ans, "fact_write")

        if "occupation" in new_facts:
            ans = f"记住啦,你是{new_facts['occupation']}"
            self.history.append(("AI", ans))
            self._save_to_conversation(user_input, ans)
            return _done(ans, "fact_write")

        if "hobby" in new_facts:
            ans = f"记住啦,你喜欢{new_facts['hobby']}"
            self.history.append(("AI", ans))
            self._save_to_conversation(user_input, ans)
            return _done(ans, "fact_write")

        # 2. 用户笔记
        if "记住：" in user_input or "请记住" in user_input:
            note = user_input.replace("记住：", "").replace("请记住", "").strip()
            self._store_event("user_note", note)
            fm = re.search(r'(.{1,10}?)(?:的(.{1,10}?))?是(.{1,20}?)(?:[。，、！？]|$)', note)
            if fm:
                k = fm.group(1).strip() + \
                             (f"_{fm.group(2).strip()}" if fm.group(2) else "")
                self.facts[k] = fm.group(3).strip()
                self._save_facts()
            ans = "已记录！"
            self.history.append(("AI", ans))
            self._save_to_conversation(user_input, ans)
            return _done(ans, "note_write")

        # 3. 查询自身信息（优先 FAISS 语义检索，回退 dict）
        if any(k in user_input for k in ["我叫什么","我是谁","我的名字","什么名字","我叫啥","我是做什么","我的职业","我的身份"]):
            fact = self._query_fact("用户信息")
            if fact and ":" in fact:
                ans = fact  # 直接返回 "用户的名字: xxx" 或 "用户的职业: xxx"
            else:
              # 回退查 dict
                name = self.facts.get("name", "")
                occ = self.facts.get("occupation", "")
                if name and occ:
                    ans = f"你是{name}，职业是{occ}"
                elif name:
                    ans = f"你叫{name}"
                elif occ:
                    ans = f"你的职业是{occ}"
                else:
                    ans = "你还没告诉我你是谁，可以说'我叫XXX'让我记住哦"
            self.history.append(("AI", ans))
            self._save_to_conversation(user_input, ans)
            return _done(ans, "self_info")

        # 4. 事实查询（仅查 facts 字典，穿透到 ReAct，不查 SQLite 事件库）
        fm2=re.search(r'(.{1,16}?)(?:是什么|加固过吗|有没有加固)',user_input)
        if fm2:
            key=fm2.group(1).strip()
            if key in self.facts:
                ans=f"{key}是{self.facts[key]}"
                self.history.append(("AI",ans))
                self._save_to_conversation(user_input,ans)
                return _done(ans, "fact_query")
            # key 不在 facts 中 → 不查 SQLite，穿透到 ReAct

        # 5. 传感器自检
        #    只在"要状态"时短路；"问原因/怎么排查"属于知识问题，交给 RAG/ReAct，
        #    否则一个含"传感器"三字的知识问题会被这里拦成"当前无距离数据"。
        if any(k in user_input for k in ["传感器", "正常吗", "掉线", "卡死", "健康"]) \
           and not self._is_knowledge_question(user_input):
            if current_distance<=0:
                ans="当前无距离数据,请检查传感器连接。"
            else:
                ans=self._handle_sensor_check()
            self.history.append(("AI",ans))
            self._save_to_conversation(user_input,ans)
            return _done(ans, "sensor_check")

        # 6. 趋势分析
        #    同样要放过知识类问题："位移速率上升的原因是什么"不该被当成"看趋势"。
        if any(k in user_input for k in ["趋势","下降","上升","怎么样","最近怎么样","当前状态","概况","总结"])\
           and not any(k in user_input for k in ["月","号","日","年","历史","记录","告警","天气","温度","气温","下雨"])\
           and not self._is_knowledge_question(user_input):
            trend=self._check_trend()
            ans=trend if trend else f"当前距离{current_distance:.2f}m,趋势平稳。"
            self.history.append(("AI",ans))
            self._save_to_conversation(user_input,ans)
            return _done(ans, "trend_analysis")

        # 7. 上下文继承
        context_hints=["随便","来一个","举个例子","具体","详细","展开","继续","接着说",
                       "比","对比","和","变化","涨了","跌了","原来","刚才","上午","下午","昨天","今天"]
        if any(w in user_input for w in context_hints):
            for role,content in reversed(self.history):
                if role=="用户":
                    user_input=f"{content} {user_input}"
                    break

        # 8. 反幻觉
        trend_kw=["比","对比","和","变化","涨了","跌了","趋势","原来","刚才","上午","下午","昨天","今天"]
        if any(k in user_input for k in trend_kw):
            if current_distance<=0:
                pass  # 不拦截，穿透到 ReAct，由 Agent 决定如何处理

        # 9. 闲聊    #就是判断，如果用户是在跟模型闲聊，那么就不走react agent，以免浪费资源
        casual=["你好","谢谢","再见","你是谁","你叫什么","你能做什么"]
        is_casual=any(w in user_input for w in casual)

        # 10. 滑坡判断（仅对直接询问"会不会塌/滑坡吗"做实时判断，知识类问题穿透到 ReAct）
        slip_kw=["会滑坡吗","会不会塌","安全吗"]
        if any(k in user_input for k in slip_kw):
            ans=self._handle_slip_judgment(user_input)
            self.stats["slip_judgments"]=self.stats.get("slip_judgments",0)+1
            self.history.append(("Tool",f"slip: dist={current_distance:.2f}"))
            self.history.append(("AI",ans[:300]))
            self._save_to_conversation(user_input,ans[:300])
            return _done(ans, "slip_judgment")

        # 11. 显式控制流（LangGraph）：意图识别 → 按意图收窄工具 → ReAct 循环 → 收口
        #
        # ── 这一段原来是什么 ──
        # 原来是约 135 行手写的 create_agent 流式循环，同时干三件事：
        # 决定用哪些工具、跑 ReAct 循环、从消息序列里抠最终答案。
        # 三件事混在一个方法里带来两个后果：
        #   * "循环上限""时间预算"散落在局部变量里，没法参与"该不该继续"的判断
        #   * 11 个工具对模型**全量暴露** —— 问"超声波原理"时它可能先去调位移速率
        #
        # ── 现在是什么 ──
        # 控制流搬到 slope_graph.py 的图里，这段只负责：准备 state、跑图、
        # 把过程喂给观测队列和 Trace。图的结构：
        #   classify（意图）→ 按意图 bind 工具 → agent ⇄ tools（条件边循环）→ finalize
        #
        # ── 为什么保留上面步骤 0~10 的确定性快速路径 ──
        # 它们不花 LLM、路由粒度更细（compound / sensor_check / trend_analysis …），
        # 而评测的 Route Accuracy 正是按这些路由名逐一断言的。
        # 图负责的是**agent 段**；把确定性的东西也塞进 LLM 只会更慢更不稳。
        response = None
        graph_intent = None
        graph_route = None
        if not is_casual and getattr(self, "slope_graph", None) is not None:
            # 预算保护：剩余预算不足时不再进图，直接走降级
            if deadline.remaining < 1.0:
                degrade.record("请求预算耗尽，跳过控制流图",
                               from_stage="react", to_stage="llm_fallback")
                rec.mark_degraded(degrade.summary())
            else:
                self._log_step("thought", "进入显式控制流图（意图识别 → ReAct）")
                self.obs_queue.put(("separator", "━" * 50))
                self.obs_queue.put(("phase", "[LangGraph] 启动推理..."))
                budget = min(float(CFG.timeout_react_budget), max(1.0, deadline.remaining))
                try:
                    recent_msgs = self._prompt_history()
                    init_state = {
                        "session_id": self._current_session,
                        "user_input": user_input,
                        "messages": recent_msgs + [HumanMessage(content=user_input)],
                        "retry": 0,
                        "deadline": time.time() + budget,
                        "answer": "",
                    }
                    graph_t0 = time.time()
                    final_state: dict = {}
                    # recursion_limit 给足：图里 agent ⇄ tools 是一对节点，
                    # 一轮 ReAct 至少消耗 2 步，再加 classify/finalize 各 1 步。
                    graph_limits = {"recursion_limit": max(6, int(CFG.react_max_iterations) * 2 + 4)}
                    for update in self.slope_graph.stream(
                        init_state, stream_mode="updates", config=graph_limits
                    ):
                        # 图内已有 deadline 判断；这里再加一道挂钟保护，
                        # 防止单个慢节点（比如一次卡住的工具调用）把请求拖死。
                        if time.time() - graph_t0 > budget:
                            degrade.record(f"控制流图超出预算 {budget:.0f}s",
                                           from_stage="react", to_stage="llm_fallback",
                                           error_type="GraphBudgetExceeded")
                            break
                        if not isinstance(update, dict):
                            continue
                        for node_name, node_out in update.items():
                            if not isinstance(node_out, dict):
                                continue
                            # 累积各节点的输出；finalize 是最后一个节点，
                            # 所以它的 answer / route 会覆盖前面节点的。
                            final_state.update(node_out)
                            if node_name == "classify":
                                it = node_out.get("intent")
                                nt = len(node_out.get("allowed_tools") or [])
                                self.obs_queue.put(
                                    ("thought", f"[意图] {it}｜本意图可用工具 {nt} 个"))
                            # 把节点里的工具调用与观察喂给前端队列 + Trace，
                            # 这样换成图之后"过程可见"这一点没有退化。
                            for msg in (node_out.get("messages") or []):
                                tcs = getattr(msg, "tool_calls", None)
                                if tcs:
                                    for tc in tcs:
                                        tn = tc.get("name", "?") if isinstance(tc, dict) \
                                            else getattr(tc, "name", "?")
                                        ta = tc.get("args", {}) if isinstance(tc, dict) \
                                            else getattr(tc, "args", {})
                                        rec.add_tool(tn)
                                        rec.note_step("tool", tn,
                                                      args=ta if isinstance(ta, dict)
                                                      else {"args": str(ta)})
                                        self.obs_queue.put(("tool", f"[调用工具] {tn} | 参数: {ta}"))
                                if getattr(msg, "type", "") == "tool":
                                    rec.note_step(
                                        "observation",
                                        getattr(msg, "name", None) or "tool_result",
                                        output=str(getattr(msg, "content", "")),
                                        output_limit=2000)
                                    self.obs_queue.put(
                                        ("observation",
                                         f"[工具返回] {str(getattr(msg, 'content', ''))[:200]}"))
                    graph_intent = final_state.get("intent")
                    graph_route = final_state.get("route")
                    response = str(final_state.get("answer") or "").strip() or None
                    if response:
                        self.obs_queue.put(("answer", f"{response}"))
                    self.obs_queue.put(("separator", "━" * 50))
                    if response:
                        self._log_step("observation", response[:100])
                except Exception as ge:
                    # 图执行失败不再静默：记录分类与降级原因，然后让第 12 步兜底
                    print(f"[Graph] 执行失败:{type(ge).__name__}: {ge}")
                    degrade.record(f"控制流图执行失败：{type(ge).__name__}",
                                   from_stage="react", to_stage="llm_fallback",
                                   error_type=type(ge).__name__)
                    self._log_step("error", "控制流图失败，进入回退")
                    self.obs_queue.put(("error", f"[Graph] 调用失败: {ge}"))
                    self.obs_queue.put(("separator", "━" * 50))
                    response = None

        # 11b. 兜底：图不可用时退回 create_agent（保留改造前的行为，保证可用性）
        if (response is None and not is_casual
                and getattr(self, "slope_graph", None) is None
                and self.react_agent is not None):
            self.obs_queue.put(("thought", "[兜底] 控制流图不可用，退回 create_agent"))
            try:
                recent_msgs = self._prompt_history()
                input_msgs = {"messages": recent_msgs + [HumanMessage(content=user_input)]}
                react_limits = {"recursion_limit": max(2, CFG.react_max_iterations * 2 + 1)}
                result = self.react_agent.invoke(input_msgs, config=react_limits)
                msgs_out = result.get("messages", [])
                for msg in msgs_out:
                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                        for tc in msg.tool_calls:
                            tn = tc.get("name", "?") if isinstance(tc, dict) \
                                else getattr(tc, "name", "?")
                            rec.add_tool(tn)
                            rec.note_step("tool", tn)
                    if getattr(msg, "type", "") == "tool" and hasattr(msg, "content"):
                        rec.note_step("observation",
                                      getattr(msg, "name", None) or "tool_result",
                                      output=str(msg.content), output_limit=2000)
                ai_msgs = [m for m in msgs_out
                           if getattr(m, "type", "") == "ai"
                           and hasattr(m, "content") and m.content]
                final_msgs = [m for m in ai_msgs
                              if not (hasattr(m, "tool_calls") and m.tool_calls)]
                if final_msgs:
                    response = strip_thinking(final_msgs[-1].content) or None
            except Exception as e:
                print(f"[兜底 ReAct] 错误:{e}")
                degrade.record(f"兜底 ReAct 失败：{type(e).__name__}",
                               from_stage="react", to_stage="llm_fallback",
                               error_type=type(e).__name__)
                response = None

        # 12. 回退：直接回答（短期记忆 LLM 调用）
        used_fallback = response is None
        if response is None:
            self.obs_queue.put(("phase", "[LLM 回退] 使用基础模型直接回答"))
            # 闲聊走基础模型是**设计内行为**，不是降级；只有 ReAct 该跑而没跑成
            # 才算降级。区分开来，降级率才是有意义的运维指标。
            if not is_casual:
                degrade.record("ReAct 无产出，回退基础模型", from_stage="react", to_stage="llm_fallback")
            # 会话隔离：用当前请求的 session_id，而不是写死的 "default"，
            # 否则所有用户共享同一条 LCEL 对话链历史。
            response=strip_thinking(self.conversation_chain.invoke({
                "input": user_input, "chat_history": self._prompt_history()}))
            self.obs_queue.put(("answer", f"{response[:300]}"))
            self.obs_queue.put(("separator", "━" * 50))
        self.history.append(("AI",response[:300]))
        # 落库：**主路径原先漏了这一句**。
        # 上面 11 个提前返回的分支都调了 _save_to_conversation，唯独走完
        # ReAct 的正常请求没有 —— 后果是 MySQL 里永远查不到真实对话，
        # `/history` 恒为空（用户明明聊过）。属于"只在一半分支上做对了"的静默缺陷。
        self._save_to_conversation(user_input, response)

        # ── 长期记忆：按周期抽取结构化画像 ──
        # 放在**回答已经生成之后**，而且是独立 try —— 记忆是增强项，
        # 抽取失败（LLM 超时 / JSON 不合规）绝不能影响这次回答的返回。
        try:
            mem = getattr(self, "memory", None)
            if mem is not None:
                turns = [("用户", user_input), ("AI", response)]
                updated = mem.maybe_update_from_turns(turns)
                if updated:
                    self._log_step("thought", f"长期画像已更新：{list(updated.keys())}")
        except Exception as me:
            print(f"[记忆] 画像抽取失败（不影响本次回答）: {type(me).__name__}: {me}")

        # 意图观测：路由仍是 react_agent（评测按这个断言），
        # 但把图识别出的**真实意图**单独记下来，便于定位"工具给错了"这类问题。
        if graph_intent:
            self.stats["last_intent"] = graph_intent
            rec.note_step("route", "intent", output=graph_intent)
            if graph_route:
                rec.note_step("route", "graph_route", output=str(graph_route))

        # 请求耗时
        elapsed = time.time() - start_time
        self.stats["last_latency"] = elapsed
        self.stats["avg_latency"] = (self.stats.get("avg_latency", 0) * (self.stats["total_queries"] - 1) + elapsed) / self.stats["total_queries"]
        print(f"[性能] 请求耗时: {elapsed:.2f}秒 | 平均: {self.stats['avg_latency']:.2f}秒")

        if degrade.happened:
            rec.mark_degraded(degrade.summary())
        # 最终路由：区分"真走了 ReAct"与"降级到基础模型"，避免把降级算成成功路由
        if is_casual:
            final_route = "casual"
        elif used_fallback:
            final_route = "llm_fallback"
        else:
            final_route = "react_agent"
        return _done(response, final_route)

#============================================================================================ SimpleAgent初始化============================================================================================#
rag = None


class _LazyAgent:
    """SimpleAgent 的延迟代理。

    改造前这里是 `agent=SimpleAgent()`，在**模块导入时**就执行：立刻连接
    MySQL、建表、构建 FAISS 长期记忆。后果是——
      * 导入即有副作用，评测脚本只能靠 20 行 tkinter 猴子补丁绕过；
      * 容器 / CI 无法做轻量导入与静态检查；
      * 数据库不可用时连"导入模块"都会失败。

    现在改为首次访问属性时才真正实例化，导入成本降到接近零。
    对外接口与直接持有 SimpleAgent 完全一致（含属性赋值）。
    """

    __slots__ = ("_real", "_init_lock")

    def __init__(self):
        object.__setattr__(self, "_real", None)
        # 首次实例化必须互斥：否则两个并发请求会各自 new 一个 SimpleAgent，
        # 于是 `agent._agent_lock` 拿到的是**两把不同的锁** —— 串行化形同虚设，
        # 而且会话历史也会分裂成两份。这是"以为加了锁其实没加"的典型。
        object.__setattr__(self, "_init_lock", threading.Lock())

    def _get(self):
        real = object.__getattribute__(self, "_real")
        if real is None:
            with object.__getattribute__(self, "_init_lock"):
                real = object.__getattribute__(self, "_real")  # 双检
                if real is None:
                    real = SimpleAgent()
                    object.__setattr__(self, "_real", real)
        return real

    @property
    def ready(self) -> bool:
        """是否已经真正实例化（不触发创建）。"""
        return object.__getattribute__(self, "_real") is not None

    def __getattr__(self, name):
        return getattr(self._get(), name)

    def __setattr__(self, name, value):
        setattr(self._get(), name, value)


agent = _LazyAgent()

# ============================================================
# 可观测性窗口：实时展示推理过程 + 工具调用 + 流式输出
# ============================================================
class ObservabilityWindow:
    def __init__(self, parent, obs_queue):
        self.queue = obs_queue
        self.win = tk.Toplevel(parent)
        self.win.title("Agent 推理过程")
        self.win.geometry("650x500")
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        self._open = True

        # 工具栏
        bar = tk.Frame(self.win)
        bar.pack(fill=tk.X, padx=5, pady=(5, 0))
        tk.Label(bar, text="🧠 实时推理追踪", font=("微软雅黑", 11, "bold")).pack(side=tk.LEFT)
        ttk.Button(bar, text="清空", command=self._clear).pack(side=tk.RIGHT)

        # 文本区
        self.text = tk.Text(self.win, wrap=tk.WORD, font=("Consolas", 10),
                            bg="#1e1e1e", fg="#d4d4d4", insertbackground="white")
        scroll = ttk.Scrollbar(self.win, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0), pady=5)
        scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 5), pady=5)

        # 颜色标签
        self.text.tag_config("thought", foreground="#569CD6")      # 蓝色-思考
        self.text.tag_config("tool", foreground="#CE9178")         # 橙色-工具调用
        self.text.tag_config("observation", foreground="#6A9955")  # 绿色-工具返回
        self.text.tag_config("error", foreground="#F44747")        # 红色-错误
        self.text.tag_config("answer", foreground="#DCDCAA")       # 黄色-最终回答
        self.text.tag_config("separator", foreground="#555555")    # 灰色-分隔线
        self.text.tag_config("phase", foreground="#C586C0")        # 紫色-阶段提示

        self.text.config(state=tk.DISABLED)
        # 初始提示
        self._append("phase", "等待 Agent 推理...")
        self._append("separator", "━" * 50)
        self._append("thought", "发送提问后，这里将实时显示：")
        self._append("thought", "  蓝色 = 推理思考")
        self._append("tool", "  橙色 = 工具调用")
        self._append("observation", "  绿色 = 工具返回")
        self._append("error", "  红色 = 错误信息")
        self._append("answer", "  黄色 = AI 最终回答")
        self._append("separator", "━" * 50)
        self._poll()

    def _poll(self):
        """每100ms轮询队列，更新界面"""
        if not self._open:
            return
        try:
            while True:
                event_type, content = self.queue.get_nowait()
                self._append(event_type, content)
        except queue.Empty:
            pass
        self.win.after(100, self._poll)

    def _append(self, event_type, content):
        tag_map = {
            "thought": "thought", "tool": "tool",
            "observation": "observation", "error": "error",
            "answer": "answer", "separator": "separator", "phase": "phase"
        }
        tag = tag_map.get(event_type, "answer")
        ts = time.strftime("%H:%M:%S")
        self.text.config(state=tk.NORMAL)
        self.text.insert(tk.END, f"[{ts}] {content}\n", tag)
        self.text.see(tk.END)
        self.text.config(state=tk.DISABLED)

    def _clear(self):
        self.text.config(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.config(state=tk.DISABLED)

    def _on_close(self):
        self._open = False
        self.win.destroy()

# RAG 加载状态。健康检查要靠它如实汇报，而不是只看 `rag is not None`。
# 起因：无头/容器模式下没有任何地方去加载 RAG，于是 rag 恒为 None，
# 健康检查**永远**返回 degraded —— Docker healthcheck 永不通过。
rag_state = "pending"      # pending / loading / ready / failed


def load_rag():
    global rag, rag_state
    rag_state = "loading"
    os.environ['HF_ENDPOINT']='https://hf-mirror.com'
    for attempt in range(3):
        try:
            rag=VectorRAG()
            agent.rag=rag
            rag_state = "ready"
            print(f"[RAG] loaded (attempt {attempt+1})")
            try: ai_status_val.config(text="AI运行中(RAG已就绪)",foreground=COLORS["success"])
            except: pass
            return
        except Exception as e:
            print(f"[RAG] attempt {attempt+1}/3 failed:{e}")
            if attempt<2: time.sleep(2)
    rag_state = "failed"
    print("[RAG] 3次重试均失败,Agent将使用关键词降级搜索")
def ensure_rag():
    """幂等地构建 RAG。GUI 模式下由后台线程自动调用；
    无头模式（容器 / 评测）由调用方显式触发，避免导入即产生副作用。"""
    global rag
    if rag is not None:
        return rag
    load_rag()
    return rag


# GUI 模式：保持原有的导入后自动预热行为
# 无头模式：不自动起线程，交由 ensure_rag() 显式控制
if not getattr(CFG, "headless", False) and os.getenv("SLOPE_HEADLESS") != "1":
    threading.Thread(target=load_rag, daemon=True).start()

# ============================================================================================全局配置 ============================================================================================#
WIDTH,HEIGHT=640,480
CONF_THRESH=0.45
target_cls=[0,15,16]
mon_x1,mon_y1,mon_x2,mon_y2=160,100,480,380
COLORS={"primary":"#165DFF","success":"#00B42A","warning":"#FF7D00","danger":"#F53F3F","bg":"#F5F7FA","card_bg":"#FFFFFF"}
frame_queue=queue.Queue(maxsize=1)
current_lat,current_lng,current_distance,current_beijing_time=0.0,0.0,0.0,"--:--:--"
ai_warn_flag,cap_status=False,True
dist_history,time_history,count=[],[],0
dist_log=[]; dist_log_lock=threading.Lock()
tcp_cmd_queue=queue.Queue()

# =================================== 主窗口 ===================================#
root=tk.Tk()
root.title("边坡监测一体化上位机｜单板WiFi方案")
root.geometry("1920x1080"); root.minsize(1600,900); root.configure(bg=COLORS["bg"])
style=ttk.Style(root); style.theme_use("clam")
style.configure("Card.TFrame",background=COLORS["card_bg"])
style.configure("Title.TLabel",font=("微软雅黑",16,"bold"),foreground=COLORS["primary"])
style.configure("Ctrl.TButton",font=("微软雅黑",11),padding=6)
main_container=ttk.Frame(root,style="Card.TFrame",padding=20)
main_container.pack(fill=tk.BOTH,expand=True,padx=10,pady=10)

# =================================== 左侧===================================#
left_panel=ttk.Frame(main_container,style="Card.TFrame",width=360)
left_panel.pack(side=tk.LEFT,fill=tk.Y,padx=10,pady=10); left_panel.pack_propagate(False)
ttk.Label(left_panel,text="📡 实时监测数据",style="Title.TLabel").pack(pady=(20,5))
def card(parent,icon,title,default):
    f=ttk.Frame(parent,style="Card.TFrame",relief=tk.RIDGE,borderwidth=1)
    f.pack(fill=tk.X,padx=10,pady=6)
    ttk.Label(f,text=icon,font=("微软雅黑",16)).pack(side=tk.LEFT,padx=10,pady=12)
    ttk.Label(f,text=title,font=("微软雅黑",12)).pack(anchor="w",pady=(8,0))
    val=ttk.Label(f,text=default,font=("微软雅黑",14,"bold"),foreground=COLORS["primary"])
    val.pack(anchor="w",pady=(0,8)); return val
distance_val=card(left_panel,"📏","边坡距离","0.00 m")
lat_val=card(left_panel,"🌍","纬度","0.000000")
lng_val=card(left_panel,"🌍","经度","0.000000")
time_val=card(left_panel,"🕒","北斗时间","--:--:--")
wifi_status_label=card(left_panel,"🔌","WiFi连接状态","等待连接")
ai_status_val=card(left_panel,"👀","AI状态","未启动")
ttk.Label(left_panel,text="📈 边坡距离变化",style="Title.TLabel").pack(pady=(20,10))
fig=Figure(figsize=(3.3,3),dpi=100,facecolor=COLORS["card_bg"])
ax=fig.add_subplot(111); ax.set_facecolor("#F9FAFB")
ax.set_title("距离实时曲线",fontsize=10); ax.set_xlabel("时间点",fontsize=9); ax.set_ylabel("m",fontsize=9)
ax.grid(True,alpha=0.3); line,=ax.plot([],[],color=COLORS["primary"],linewidth=2,label="距离")
ax.legend(loc="upper right",fontsize=8)
canvas=FigureCanvasTkAgg(fig,left_panel); canvas.get_tk_widget().pack(fill=tk.BOTH,padx=10,pady=5)

# =================================== 中间视频 ===================================#
video_panel=ttk.Frame(main_container,style="Card.TFrame")
video_panel.pack(side=tk.LEFT,fill=tk.BOTH,expand=True,padx=10,pady=10)
ttk.Label(video_panel,text="👁️ AI智能入侵监测",style="Title.TLabel").pack(pady=(20,10))
video_canvas=tk.Canvas(video_panel,width=WIDTH,height=HEIGHT,bg="#222",bd=0,highlightthickness=0)
video_canvas.pack(pady=10)

# ── 系统监测日志 ──
ttk.Label(video_panel,text="📋 系统监测日志",style="Title.TLabel").pack(pady=(10,5))
log_text=tk.Text(video_panel,height=12,state=tk.DISABLED,wrap=tk.WORD)
log_text.pack(fill=tk.BOTH,padx=10,pady=5)
def log_to_gui(msg):
    log_text.config(state=tk.NORMAL)
    tm=time.strftime("%H:%M:%S")
    log_text.insert(tk.END,f"[{tm}] {msg}\n")
    log_text.see(tk.END)
    log_text.config(state=tk.DISABLED)

# =================================== 右侧 ===================================#
right_panel=ttk.Frame(main_container,style="Card.TFrame",width=380)
right_panel.pack(side=tk.RIGHT,fill=tk.Y,padx=10,pady=10); right_panel.pack_propagate(False)
ttk.Label(right_panel,text="🗺️ 北斗定位地图",style="Title.TLabel").pack(pady=(20,10))
def open_map():
    if os.path.exists("slope_map.html"): webbrowser.open(f"file:///{os.path.realpath('slope_map.html')}")
    else: messagebox.showinfo("提示","暂无定位数据")
ttk.Button(right_panel,text="打开地图",command=open_map).pack(pady=5)

# =================================== 远程控制 ===================================#
ttk.Label(right_panel,text="🕹️ 远程控制",style="Title.TLabel").pack(pady=(20,10))
ctrl_frame=ttk.Frame(right_panel,style="Card.TFrame",relief=tk.RIDGE,borderwidth=1)
ctrl_frame.pack(fill=tk.X,padx=10,pady=5)
led_frame=ttk.Frame(ctrl_frame,style="Card.TFrame"); led_frame.pack(fill=tk.X,padx=5,pady=5)
ttk.Label(led_frame,text="💡 LED 控制",font=("微软雅黑",12,"bold")).pack(anchor="w")
led_state={"led1":False,"led2":False,"led3":False}; led_btn={}
def make_led_cmd(n,on): return f"CMD_LED_{n}_{'1' if on else '0'}_ENDLED_END"
def toggle_led(n,btn):
    led_state[f"led{n}"]=not led_state[f"led{n}"]; s=led_state[f"led{n}"]
    tcp_cmd_queue.put(make_led_cmd(n,s))
    btn.config(text=f"LED{n} ●" if s else f"LED{n} ○",foreground=COLORS["success"] if s else COLORS["danger"])
for i in[1,2,3]:
    row=ttk.Frame(led_frame,style="Card.TFrame"); row.pack(fill=tk.X,padx=10,pady=2)
    ttk.Label(row,text=f"LED{i}",font=("微软雅黑",10),width=6).pack(side=tk.LEFT)
    btn=ttk.Button(row,text=f"LED{i} ○",style="Ctrl.TButton"); btn.pack(side=tk.LEFT,padx=5)
    led_btn[f"led{i}"]=btn
led_btn["led1"].config(command=lambda: toggle_led(1,led_btn["led1"]))
led_btn["led2"].config(command=lambda: toggle_led(2,led_btn["led2"]))
led_btn["led3"].config(command=lambda: toggle_led(3,led_btn["led3"]))
buzz_frame=ttk.Frame(ctrl_frame,style="Card.TFrame"); buzz_frame.pack(fill=tk.X,padx=5,pady=5)
ttk.Label(buzz_frame,text="🔊 蜂鸣器控制",font=("微软雅黑",12,"bold")).pack(anchor="w")
buzz_state={"on":False}; buzz_btn=None
def toggle_buzzer(btn):
    buzz_state["on"]=not buzz_state["on"]
    tcp_cmd_queue.put(f"CMD_BUZZER_{'1' if buzz_state['on'] else '0'}_END")
    btn.config(text="🔔 蜂鸣器 ON" if buzz_state["on"] else "🔇 蜂鸣器 OFF",foreground=COLORS["danger"] if buzz_state["on"] else COLORS["success"])
buzz_row=ttk.Frame(buzz_frame,style="Card.TFrame"); buzz_row.pack(fill=tk.X,padx=10,pady=2)
buzz_btn=ttk.Button(buzz_row,text="🔇 蜂鸣器 OFF",style="Ctrl.TButton",command=lambda: toggle_buzzer(buzz_btn))
buzz_btn.pack(side=tk.LEFT,padx=5)
def all_off():
    for i in range(1,4):
        led_state[f"led{i}"]=False; tcp_cmd_queue.put(make_led_cmd(i,False))
        led_btn[f"led{i}"].config(text=f"LED{i} ○",foreground=COLORS["danger"])
    buzz_state["on"]=False; tcp_cmd_queue.put("CMD_BUZZER_0_END")
    buzz_btn.config(text="🔇 蜂鸣器 OFF",foreground=COLORS["success"])
ttk.Button(ctrl_frame,text="一键关闭全部",command=all_off).pack(pady=8)

# =================================== AI助手 ===================================#
ttk.Label(right_panel,text="🤖 AI Agent 智能助手",style="Title.TLabel").pack(pady=(10,10))
chat_display=tk.Text(right_panel,height=16,state=tk.DISABLED,wrap=tk.WORD)
chat_display.pack(padx=10,pady=5,fill=tk.BOTH)
chat_display.tag_config("user",foreground=COLORS["primary"]); chat_display.tag_config("ai",foreground=COLORS["success"])
chat_input=tk.Text(right_panel,height=4); chat_input.pack(padx=10,pady=5)
last_qa={"q":"","ans":""}
def send_chat():
    q=chat_input.get("1.0",tk.END).strip()
    if not q: return
    last_qa["q"]=q
    chat_input.delete("1.0",tk.END)
    chat_display.config(state=tk.NORMAL)
    chat_display.insert(tk.END,f"你：{q}\n","user")
    chat_display.insert(tk.END,"AI：思考中...\n\n","ai")
    chat_display.config(state=tk.DISABLED); chat_display.see(tk.END)
    def do_agent():
        import uuid as _uuid
        agent._current_trace_id = _uuid.uuid4().hex[:16]
        # 桌面端固定用 desktop 会话，与 API 的会话体系分开
        ans=agent.run(q, session_id="desktop")
        try: root.after(0,lambda:_show_answer(ans))
        except RuntimeError: pass
    threading.Thread(target=do_agent,daemon=True).start()
def _save_feedback(q, ans, feedback_type):
    """保存用户反馈（MySQL _Feedback 表）"""
    try:
        session = agent._get_mysql_conn()
        session.add(_Feedback(
            type=feedback_type,
            content=f"query: {q[:200]}, answer: {ans[:200]}",
            created_at=datetime.utcnow(),
        ))
        session.commit()
        session.close()
    except Exception as e:
        print(f"[反馈] ORM 写入失败: {e}")

def _show_answer(ans):
    chat_display.config(state=tk.NORMAL)
    # 只删最后一行"思考中..."
    last_start=chat_display.index("end-2l linestart")
    last_line=chat_display.get(last_start,"end-1c")
    if "思考中" in last_line: chat_display.delete(last_start,"end-1c")
    chat_display.insert(tk.END,f"AI：{ans}\n\n","ai")
    # 反馈按钮（好评/差评）
    fb_frame = tk.Frame(chat_display)
    btn_good = ttk.Button(fb_frame, text="👍 好用", width=6)
    btn_bad = ttk.Button(fb_frame, text="👎 不好用", width=6)
    btn_good.pack(side=tk.LEFT, padx=2)
    btn_bad.pack(side=tk.LEFT, padx=2)
    def on_fb(fb_type):
        _save_feedback(last_qa.get("q",""), ans, fb_type)
        btn_good.config(state=tk.DISABLED)
        btn_bad.config(state=tk.DISABLED)
    btn_good.config(command=lambda: on_fb("good"))
    btn_bad.config(command=lambda: on_fb("bad"))
    chat_display.window_create(tk.END, window=fb_frame)
    chat_display.insert(tk.END, "\n")
    chat_display.config(state=tk.DISABLED); chat_display.see(tk.END)
ttk.Button(right_panel,text="发送提问",command=send_chat).pack(pady=5)
def show_stats():
    s=getattr(agent,'stats',{})
    avg_lat = s.get('avg_latency', 0)
    msg=f"总查询：{s.get('total_queries',0)}\n工具调用：{s.get('tool_calls',0)}\nRAG命中：{s.get('rag_hits',0)}\n平均耗时：{avg_lat:.2f}秒"
    messagebox.showinfo("运行统计",msg)
ttk.Button(right_panel,text="📊 查看统计",command=show_stats).pack(pady=5)
obs_win = None
def open_obs():
    global obs_win
    if obs_win is None or not obs_win._open:
        obs_win = ObservabilityWindow(root, agent.obs_queue)
    else:
        obs_win.win.lift()
ttk.Button(right_panel, text="🧠 推理过程", command=open_obs).pack(pady=5)

# === 工具函数 ===
def weather_enhance(img): return cv2.convertScaleAbs(img,alpha=1.4,beta=20)
def put_chinese(img,text,pos,size=22,color=(0,255,0)):
    try:
        img_pil=Image.fromarray(cv2.cvtColor(img,cv2.COLOR_BGR2RGB))
        draw=ImageDraw.Draw(img_pil)
        try: font=ImageFont.truetype("C:/Windows/Fonts/simhei.ttf",size)
        except: font=ImageFont.truetype("simhei.ttf",size)
        draw.text(pos,text,font=font,fill=color[::-1])
        return cv2.cvtColor(np.array(img_pil),cv2.COLOR_RGB2BGR)
    except:
        cv2.putText(img,text,pos,cv2.FONT_HERSHEY_SIMPLEX,0.8,color,2); return img
def create_map(lat,lng):
    try:
        if abs(lat)<0.001 or abs(lng)<0.001: return
        m=folium.Map(location=[lat,lng],zoom_start=18)
        folium.Marker([lat,lng],icon=folium.Icon(color="red")).add_to(m)
        m.save("slope_map.html")
    except: pass

def update_display_data(distance):
    global current_distance,count,dist_log,dist_log_lock
    if distance==-1.0:
        try: distance_val.config(text="无信号/超出范围",foreground=COLORS["warning"])
        except Exception: pass
        return
    # 核心数据更新（GUI/API 通用）
    current_distance=round(float(distance),2)
    with dist_log_lock:
        dist_log.append((time.time(),current_distance))
        if len(dist_log)>100: dist_log.pop(0)
    dist_history.append(current_distance)
    time_history.append(count)
    count+=1
    if len(dist_history)>50:
        dist_history.pop(0)
        time_history.pop(0)
    # GUI 刷新（API 模式静默跳过）
    try:
        distance_val.config(text=f"{current_distance:.2f} m",foreground=COLORS["primary"])
        line.set_data(time_history,dist_history)
        ax.relim(); ax.autoscale_view(True,True,True)
        canvas.draw()
    except Exception: pass
    if current_distance<0.3:
        alert("边坡异常",f"距离过近：{current_distance:.2f}m")

def update_gps_data(lat,lng,alt,sats,tim):
    global current_lat,current_lng,current_beijing_time
    if abs(lat)>0.001 and abs(lng)>0.001:
        current_lat=lat
        current_lng=lng
        try:
            lat_val.config(text=f"{lat:.1f}")
            lng_val.config(text=f"{lng:.1f}")
            create_map(lat,lng)
        except Exception: pass
    if tim and tim!="--:--:--":
        current_beijing_time=tim
        try: time_val.config(text=tim)
        except Exception: pass

# === WiFi TCP ===
def wifi_thread():
    server_ip="127.0.0.1"; server_port=8080; poll_tick=0
    # 真实C8T6用192.168.0.48，模拟器用127.0.0.1
    while True:
        try:
            conn=socket.socket(socket.AF_INET,socket.SOCK_STREAM); conn.settimeout(3)
            conn.connect((server_ip,server_port))
            try: root.after(0,lambda: wifi_status_label.config(text="✅ WiFi已连接",foreground=COLORS["success"]))
            except RuntimeError: print("[WiFi] connected (API mode)")
            print("TCP -> C8T6 已连接")
            while True:
                try:
                    cmd=tcp_cmd_queue.get_nowait(); conn.sendall((cmd+"\r\n").encode("ascii"))
                    print(f"发送控制: {cmd}")
                except queue.Empty: pass
                if poll_tick%2==0: conn.sendall(b"GET_DIST\r\n")
                else: conn.sendall(b"GET_GPS\r\n")
                poll_tick+=1
                try: resp=conn.recv(512).decode("gbk",errors="ignore")
                except socket.timeout: time.sleep(0.3); continue
                if not resp: break
                for line in resp.split("\n"):
                    line=line.strip()
                    if line.startswith("DIST:"):
                        try:
                            dist=float(line.split(":", 1)[1])
                            try: root.after(0,update_display_data,dist)
                            except RuntimeError: update_display_data(dist)  # API 模式直接更新
                        except:
                            pass
                    elif line.startswith("GPS:"):
                        try:
                            body=line.split(":", 1)[1]
                            parts=body.split(",")
                            lat=float(parts[0]) if len(parts)>0 else 0.0
                            lng=float(parts[1]) if len(parts)>1 else 0.0
                            alt=float(parts[2]) if len(parts)>2 else 0.0
                            sats=parts[3] if len(parts)>3 else "0/0"
                            tim=parts[4].strip() if len(parts)>4 else "--:--:--"
                            try: root.after(0,update_gps_data,lat,lng,alt,sats,tim)
                            except RuntimeError: update_gps_data(lat,lng,alt,sats,tim)  # API 模式直接更新
                        except Exception as e: print(f"GPS解析错误:{e}")
                time.sleep(0.2)
        except Exception as e:
            print(f"WiFi异常:{e}")
            try: root.after(0,lambda: wifi_status_label.config(text="❌ WiFi断开 重连中...",foreground=COLORS["danger"]))
            except RuntimeError: print("[WIFI] disconnected (API mode)")
            time.sleep(5)

# === AI监测 ===
def ai_monitor_thread():
    global ai_warn_flag
    cap=cv2.VideoCapture(0); cap.set(cv2.CAP_PROP_FRAME_WIDTH,WIDTH); cap.set(cv2.CAP_PROP_FRAME_HEIGHT,HEIGHT)
    model=YOLO("yolov8n.pt",verbose=False)
    try: root.after(0,lambda: ai_status_val.config(text="运行中",foreground=COLORS["success"]))
    except RuntimeError: pass
    while True:
        if not cap_status: time.sleep(0.1); continue
        ret,frame=cap.read()
        if not ret:
            try: root.after(0,lambda: ai_status_val.config(text="❌ 摄像头异常",foreground=COLORS["danger"]))
            except RuntimeError: pass
            time.sleep(1); continue
        frame=weather_enhance(frame)
        cv2.rectangle(frame,(mon_x1,mon_y1),(mon_x2,mon_y2),(0,255,255),2)
        frame=put_chinese(frame,"监测区域",(mon_x1,mon_y1-30),22,(0,255,255))
        results=model(frame,conf=CONF_THRESH,verbose=False); ai_warn_flag=False
        for box in results[0].boxes:
            cls=int(box.cls[0])
            if cls in target_cls:
                x1,y1,x2,y2=map(int,box.xyxy[0])
                cv2.rectangle(frame,(x1,y1),(x2,y2),(0,0,255),2)
                if not(x2<mon_x1 or x1>mon_x2 or y2<mon_y1 or y1>mon_y2): ai_warn_flag=True
        if ai_warn_flag:
            frame=put_chinese(frame,"⚠️ 入侵告警！",(20,40),26,(0,0,255))
            try: root.after(0,lambda: ai_status_val.config(text="⚠️ 入侵告警",foreground=COLORS["danger"]))
            except RuntimeError: pass
            alert("入侵检测","监测区域内有人/动物！")
        else:
            frame=put_chinese(frame,"🟢 正常监测",(20,40),22,(0,255,0))
            try: root.after(0,lambda: ai_status_val.config(text="运行中",foreground=COLORS["success"]))
            except RuntimeError: pass
        try:
            if not frame_queue.full(): frame_queue.put(frame.copy(),block=False)
        except: pass
    cap.release()

def update_video():
    try:
        if not frame_queue.empty():
            f=frame_queue.get(); f=cv2.cvtColor(f,cv2.COLOR_BGR2RGB)
            img=ImageTk.PhotoImage(Image.fromarray(f)); video_canvas.img=img
            video_canvas.create_image(0,0,anchor=tk.NW,image=img)
    except: pass
    try: root.after(20,update_video)
    except RuntimeError: pass




# === 自主监控线程 ===
def autonomous_monitor():
    """自主监控边坡距离，发现异常写入 MySQL 事件库和知识库"""
    last_alert_time=0; last_alert_content=""
    time.sleep(10)
    while True:
        try:
            if current_distance<=0: pass
            else:
                trend=agent._check_trend()
                if trend and ("预警" in trend or "⚠️" in trend or "风险" in trend):
                    now=time.time()
                    if now-last_alert_time>300 or trend!=last_alert_content:
                        case=agent.search_kb("距离下降 边坡 案例")
                        if case and "未找到" not in case: 
                            trend+=f"\n参考案例：{case[:150]}"
                        agent._store_event("autonomous_alert",trend,current_distance)
                        agent._save_case_to_kb(trend)
                        last_alert_time=now; last_alert_content=trend
                        try: 
                            log_to_gui(f"[监控] ⚠️ {trend[:120]}")
                        except: 
                            pass
            time.sleep(30)
        except Exception as e:
            time.sleep(30)

# === 启动 ===
if __name__=="__main__":
    import sys as _sys
    if "--api" in _sys.argv:
        # ══ API 模式 ══
        print("[API] 启动 FastAPI 服务 (GUI 最小化后台)")
        root.withdraw()  # 隐藏 GUI 窗口

        # 启动后台线程（WiFi/传感器/监控）
        # 容器/无头环境下没有串口与摄像头，启动这些线程只会不断抛异常刷日志，
        # 因此按需启动：HEADLESS 时跳过，改为纯 API 服务。
        if not (CFG.headless or os.getenv("SLOPE_HEADLESS") == "1"):
            threading.Thread(target=wifi_thread, daemon=True).start()
            threading.Thread(target=ai_monitor_thread, daemon=True).start()
            threading.Thread(target=autonomous_monitor, daemon=True).start()
        else:
            print("[API] 无头模式：跳过 WiFi / 摄像头 / 自主监控线程")

        # ★ 无头/容器模式必须**显式**预热 RAG ★
        #
        # 原实现这里是空的：`ensure_rag()` 定义了却全文没有任何调用点，
        # 而上面那段"GUI 模式才自动预热"的逻辑在无头下又被跳过 ——
        # 后果有两个，都是部署形态下才会暴露的：
        #   1. `self.rag` 恒为 None，`tool_search_kb` 永远返回"知识库未加载"，
        #      知识库检索能力形同虚设（但工具照样被"调用"，所以 Tool F1 指标看不出来）
        #   2. `/health` 恒返回 degraded，**Docker healthcheck 永不通过**，
        #      compose 里 depends_on: condition: service_healthy 会一直挂住
        # 放后台线程是因为加载嵌入模型 + FAISS 要十几秒，不能拖慢服务启动。
        threading.Thread(target=ensure_rag, daemon=True).start()
        print("[API] 已在后台预热 RAG 知识库（无头模式不会自动触发，必须显式启动）")

        # FastAPI 应用（内嵌）
        from fastapi import FastAPI, Request, HTTPException
        from fastapi.responses import JSONResponse
        from fastapi.middleware.cors import CORSMiddleware
        from pydantic import BaseModel, Field
        import uuid as _uuid, time as _time

        _api_app = FastAPI(title="边坡监测 Agent API",
                          description="单个 end_new.py 文件，GUI + API 一体",
                          version="3.0.0")
        _api_app.add_middleware(CORSMiddleware, allow_origins=["*"],
                               allow_methods=["*"], allow_headers=["*"])

        # -- 中间件 --
        @_api_app.middleware("http")
        async def _mw_log(request: Request, call_next):
            rid = _uuid.uuid4().hex[:16]
            t0 = _time.time()
            resp = await call_next(request)
            resp.headers["X-Request-ID"] = rid
            print(f"[API] {request.method} {request.url.path} → {resp.status_code} ({(_time.time()-t0)*1000:.0f}ms)")
            return resp

        @_api_app.exception_handler(Exception)
        async def _exc_handler(request, exc):
            return JSONResponse(status_code=500, content={"code":500, "message":str(exc)})

        # -- Pydantic 模型 --
        class _ChatReq(BaseModel):
            query: str = Field(..., min_length=1, max_length=2000)
            session_id: str = "default"

        class _ChatResp(BaseModel):
            code: int = 200
            answer: str
            session_id: str
            trace_id: str = ""
            time: float = 0.0

        class _FbReq(BaseModel):
            query: str = Field(..., min_length=1)
            answer: str = Field(..., min_length=1)
            feedback_type: str = Field(..., pattern="^(good|bad)$")
            session_id: str = "default"
            trace_id: str = ""

        # -- 健康检查（容器编排用）--
        @_api_app.get("/health", tags=["Ops"])
        async def _health():
            """存活与就绪探测。

            三个状态，**必须区分开**：
              * starting —— RAG 还在后台加载。不算不健康，否则容器刚起来
                就会被编排系统判死，或者永远等不到 healthy。
              * ok       —— Agent 与知识库都可用，可以接流量。
              * degraded —— RAG 加载失败。服务能跑，但检索类问题会退化，
                必须显式暴露，而不是"看起来一切正常"。

            原实现只有 ok / degraded 两态，判定依据是 `rag is not None` ——
            而无头模式下没有任何地方去加载 RAG，于是**恒为 degraded**：
            Docker healthcheck 永不通过，compose 里
            depends_on: condition: service_healthy 会一直挂住。
            """
            rag_ready = rag is not None
            agent_ok = bool(getattr(agent, "ready", False))
            # 诊断用：分别看"模块全局 rag"与"真正实例上的 agent.rag"。
            # 曾经出现过两者不一致——/health 说就绪，但 search_kb 里 self.rag 是 None，
            # 于是工具返回"知识库未加载"而健康检查一切正常。这种"指标说好、功能是坏"
            # 的情况必须能一眼看出来，所以两个都暴露。
            try:
                rag_on_agent = getattr(agent, "rag", None) is not None
            except Exception:  # noqa: BLE001
                rag_on_agent = False
            if rag_ready:
                status = "ok"
            elif rag_state in ("pending", "loading"):
                status = "starting"
            else:
                status = "degraded"
            return {
                "status": status,
                "rag_ready": rag_ready,
                "rag_on_agent": rag_on_agent,
                "rag_state": rag_state,
                "agent_ready": agent_ok,
                "model": CFG.llm_model,
                "embed_model": CFG.llm_embed_model,
                "config_version": CFG.config_version(),
                "headless": bool(CFG.headless or os.getenv("SLOPE_HEADLESS") == "1"),
            }

        # -- 路由 --
        @_api_app.post("/chat", response_model=_ChatResp, tags=["Agent"])
        def _chat(req: _ChatReq):
            """对话接口。

            **这里刻意用 `def` 而不是 `async def`** —— FastAPI 对同步路由会自动
            丢进线程池执行，于是：

              * Agent 执行（本机 4B 推理，P50 约 28 秒）不再阻塞事件循环；
              * 期间 /health、/history、/stats 仍能正常响应。
                原先 `async def` 里直接调同步函数时，整个服务会连同健康检查
                一起假死（一个请求在跑，其他全部排队）。

            同时用 `_agent_lock` 串行化 Agent —— Agent 有大量可变实例状态
            （当前 Trace、会话历史、统计），并发会互相污染；而且本机 GPU 推理
            本身就是串行的，并发并不会真的更快。
            """
            t0 = _time.time()
            tid = _uuid.uuid4().hex[:16]
            try:
                with agent._agent_lock:
                    # trace_id 与 session_id 都必须在锁内设置：
                    # 它们在 Agent 里是实例级状态，锁外设置会被并发请求覆盖。
                    agent._current_trace_id = tid
                    ans = agent.run(req.query, session_id=req.session_id)
            except SessionBusyError:
                raise HTTPException(409, "上一条回复正在生成，请稍后再发送") from None
            except (SessionLeaseLostError, SessionStoreUnavailable):
                raise HTTPException(503, "会话记忆暂时不可用，请稍后重试") from None
            except Exception as e:
                raise HTTPException(500, f"Agent 执行失败: {e}")
            return _ChatResp(code=200, answer=str(ans or ""),
                            session_id=req.session_id, trace_id=tid,
                            time=round((_time.time()-t0)*1000, 1))

        @_api_app.post("/feedback", tags=["Agent"])
        async def _feedback(req: _FbReq):
            try:
                _save_feedback(req.query, req.answer, req.feedback_type)
                return {"code": 200, "message": "反馈已记录"}
            except Exception as e:
                raise HTTPException(500, f"保存失败: {e}")

        @_api_app.get("/history", tags=["Agent"])
        async def _history(session_id: str = "default", limit: int = 20):
            try:
                session = agent._get_mysql_conn()
                conv = session.query(_Conversation).filter(_Conversation.session_id == session_id).first()
                if not conv:
                    session.close()
                    return {"code": 200, "session_id": session_id, "total": 0, "messages": []}
                msgs = session.query(_Message).filter(_Message.conversation_id == conv.id).order_by(_Message.id.desc()).limit(limit).all()
                session.close()
                return {"code": 200, "session_id": session_id, "total": len(msgs),"messages": [{"role": m.role, "content": m.content[:500]} for m in reversed(msgs)]}
            except Exception as e:
                raise HTTPException(500, f"读取失败: {e}")

        @_api_app.get("/trace", tags=["Agent"])
        async def _trace(limit: int = 20):
            """最近的请求 trace —— 方案 A 之后**一次请求就是一行**。

            ## 两次改造的叠加结果

            第一次：从 MySQL `agent_trace` 改成读 JSONL（那张表只写没人读）。
            第二次（方案 A）：从"run 一行 + step N 行"压成"只有 run 一行"，
            所以这里不再返回 steps，而是把那一行的关键字段摊平返回。

            ## 为什么可以没有步骤

            评测真正消费的只有：路由、工具、**模型看到的上下文**、答案、降级。
            其中"上下文"就是 `context_text` 这一列 —— 它替代了原来的 N 条
            observation 步骤。丢掉的是"每一步单独耗时"这类归因信息，
            真要查那个还有控制台日志。
            """
            try:
                runs = get_store().fetch_runs()
                if not runs:
                    return {"code": 200, "total": 0, "runs": [],
                            "message": "暂无 trace 记录"}
                recent = list(runs)[-max(1, limit):]
                out = []
                for r in reversed(recent):          # 最新的在前
                    out.append({
                        "run_id": r.get("run_id"),
                        "query": (r.get("query") or "")[:200],
                        "route": r.get("route"),
                        "tools": r.get("predicted_tools"),
                        "tool_call_count": r.get("tool_call_count"),
                        "status": r.get("status"),
                        "degraded": r.get("degraded"),
                        "degrade_reason": r.get("degrade_reason"),
                        "elapsed_ms": r.get("elapsed_ms"),
                        "answer": (r.get("answer_digest") or "")[:300],
                        "context_len": len(str(r.get("context_text") or "")),
                        "model": r.get("model"),
                    })
                return {"code": 200, "total": len(out), "runs": out}
            except Exception as e:
                return {"code": 500, "message": f"读取 trace 失败: {e}", "runs": []}

        @_api_app.get("/stats", tags=["Agent"])
        async def _stats():
            s = getattr(agent, "stats", {})
            return {"code": 200, "data": {
                "total_queries": s.get("total_queries", 0),
                "tool_calls": s.get("tool_calls", 0),
                "rag_hits": s.get("rag_hits", 0),
                "avg_latency": round(s.get("avg_latency", 0), 2),
            }}

        @_api_app.get("/health", tags=["系统"])
        async def _health():
            return {
                "status": "healthy",
                "agent_ready": agent is not None,
                "rag_loaded": agent.rag is not None and agent.rag.vectorstore is not None,
                "react_ready": agent.react_agent is not None,
            }

        import uvicorn
        print("[API] 端点就绪 → http://127.0.0.1:8000/docs")
        uvicorn.run(_api_app, host="0.0.0.0", port=8000, log_level="warning")
    else:
        # ══ GUI 模式（默认） ══
        threading.Thread(target=wifi_thread,daemon=True).start()
        threading.Thread(target=ai_monitor_thread,daemon=True).start()
        threading.Thread(target=autonomous_monitor,daemon=True).start()
        update_video(); root.mainloop()
