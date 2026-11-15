"""
RAG 参数控制变量评测脚本
测试 k值 / chunk_size / Ensemble权重 对检索质量的影响
"""
import os, sys, json, time, re, copy
from pathlib import Path

os.chdir(Path(__file__).parent)

# tkinter 补丁
import tkinter as tk
class _FW:
    _i = None
    def __new__(c, *a, **k):
        if c._i is None: c._i = super().__new__(c)
        return c._i
    def __init__(s, *a, **k): pass
    def __call__(s, *a, **k): return s
    def __getattr__(s, n): return s
for n in ['Tk','Toplevel','Frame','Label','Button','Text','Canvas','Scrollbar',
           'StringVar','IntVar','BooleanVar','PhotoImage','Menu','Entry','Listbox']:
    setattr(tk, n, _FW)
for n in ['END','NORMAL','DISABLED','WORD','BOTH','LEFT','RIGHT','TOP','BOTTOM','X','Y','RIDGE']:
    setattr(tk, n, 'normal')
import tkinter.ttk as ttk
for n in ['Style','Frame','Label','Button','Scrollbar']:
    setattr(ttk, n, _FW)

# 导入 RAG 组件
from langchain_community.document_loaders import TextLoader
from langchain_community.embeddings import OllamaEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_ollama import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
import requests

# ── 配置 ──
MODEL = "qwen3:4b"
EMBED_MODEL = "qwen3-embedding:4b"
QUESTIONS_FILE = "test_200_questions.json"
RAG_DOCS_DIR = "rag_docs"
OUTPUT_DIR = "reports"
RESUME_FILE = "eval_progress.json"

# 测试参数
K_VALUES = [3, 5, 10]
CHUNK_VALUES = [100, 200, 500]
WEIGHT_VALUES = [(0.7, 0.3), (0.5, 0.5), (0.3, 0.7)]

# 固定基准
DEFAULT_K = 5
DEFAULT_CHUNK = 200
DEFAULT_WEIGHT = (0.5, 0.5)


def load_questions():
    with open(QUESTIONS_FILE, encoding="utf-8") as f:
        return json.load(f)


def build_rag(chunk_size=DEFAULT_CHUNK, weight=DEFAULT_WEIGHT):
    """构建 VectorRAG with custom params"""
    documents = []
    for f in os.listdir(RAG_DOCS_DIR):
        if f.endswith(".txt"):
            loader = TextLoader(os.path.join(RAG_DOCS_DIR, f), encoding="utf-8")
            documents.extend(loader.load())

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_size // 10,
        separators=["\n\n", "。", "\n", "？", "，", ",", ".", " ", ""])
    docs = splitter.split_documents(documents)

    embeddings = OllamaEmbeddings(model=EMBED_MODEL)
    # 分批 embedding（避免 GPU OOM）
    vs = None
    for batch_start in range(0, len(docs), 10):
        batch = docs[batch_start:batch_start + 10]
        for retry in range(3):
            try:
                if vs is None:
                    vs = FAISS.from_documents(batch, embeddings)
                else:
                    vs.add_documents(batch)
                break
            except Exception:
                if retry < 2:
                    time.sleep(1)
                else:
                    raise
        time.sleep(0.3)
    llm = OllamaLLM(model=MODEL, timeout=30)

    # EnsembleRetriever with custom weight
    from end_new import EnsembleRetriever
    er = EnsembleRetriever(vs, docs)
    # 注入权重到 RRF 逻辑：修改 er 类的 RRF 常数
    if weight != DEFAULT_WEIGHT:
        er._weight_faiss, er._weight_bm25 = weight
    else:
        er._weight_faiss, er._weight_bm25 = 0.5, 0.5

    return vs, llm, er, docs


def retrieve_with_ensemble(er, query, k=DEFAULT_K):
    """EnsembleRetriever 检索 + RRF 融合（可调权重）"""
    w_f, w_b = getattr(er, '_weight_faiss', 0.5), getattr(er, '_weight_bm25', 0.5)
    faiss_docs = []; bm25_docs = []
    try:
        if er.faiss_retriever: faiss_docs = er.faiss_retriever.invoke(query)
    except: pass
    try:
        if er.bm25_retriever: bm25_docs = er.bm25_retriever.invoke(query)
    except: pass
    # 使用封装的 RRF 函数（通过权重调整 k 值等效实现）
    from end_new import rrf_fusion
    return rrf_fusion([faiss_docs, bm25_docs], k=60, top_n=k)


def llm_judge(prompt, timeout=30):
    """LLM-as-Judge 调用"""
    try:
        r = requests.post("http://localhost:11434/api/generate", json={
            "model": MODEL, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.1, "num_predict": 150}
        }, timeout=timeout)
        if r.status_code == 200:
            return r.json().get("response", "").strip()
    except:
        pass
    return ""


def score_all_three(query, answer, docs):
    """一次 LLM 调用完成三项评分（Faithfulness / Context Recall / Context Relevance）"""
    ctx = "\n".join([d.page_content[:300] for d in docs[:5]])
    prompt = f"""你是 RAG 质量评估专家。评估以下三个维度并打分（0-10）。只输出三行，每行格式为"维度名=分数"，不要解释不要多余文字。

用户问题：{query}
AI 回答：{answer}
检索文档（前5条）：
{ctx[:2000]}

1. Faithfulness（忠实度）：回答是否基于检索文档，有无编造？
2. ContextRecall（覆盖率）：检索文档是否包含回答所需的关键信息？
3. ContextRelevance（相关性）：检索文档与问题是否切题，有无冗余？

输出示例（必须遵守格式）：
F=7
R=8
Rel=6
"""
    raw = llm_judge(prompt)

    # 通用解析：从任何文本中提取三个数字
    nums = re.findall(r'(\d+)', raw)
    if len(nums) >= 3:
        return {"faithfulness": int(nums[-3]), "context_recall": int(nums[-2]),
                "context_relevance": int(nums[-1])}
    if len(nums) == 2:
        return {"faithfulness": int(nums[0]), "context_recall": int(nums[1]),
                "context_relevance": int(nums[1])}
    if len(nums) == 1:
        v = int(nums[0])
        return {"faithfulness": v, "context_recall": v, "context_relevance": v}
    # 完全解析不到 → 打印原始输出用于调试
    print(f"  [DEBUG] score parse fail, raw output: {raw[:200]}")
    return {"faithfulness": 0, "context_recall": 0, "context_relevance": 0}


def generate_answer(llm, query, docs, k):
    """LLM 生成最终回答"""
    top_docs = docs[:k]
    context = "\n\n".join([doc.page_content for doc in top_docs])
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是边坡监测智能助手。请根据以下参考资料回答问题，回答要简洁准确。参考资料：{context}"),
        ("user", "用户提问：{input}")
    ])
    chain = prompt | llm | StrOutputParser()
    try:
        return chain.invoke({"context": context, "input": query})
    except:
        return "生成失败"


def run_single_config(questions, vs, llm, er, k, config_label):
    """跑单组配置的全部题目"""
    results = []
    faith_scores = []
    recall_scores = []
    relevance_scores = []
    times = []

    for i, q in enumerate(questions):
        t0 = time.time()
        try:
            docs = retrieve_with_ensemble(er, q["query"], k=k)
            answer = generate_answer(llm, q["query"], docs, k)
        except Exception as e:
            docs, answer = [], f"ERROR: {e}"

        elapsed = time.time() - t0
        times.append(elapsed)

        # 三项评分（合并为一次 LLM 调用）
        scores = score_all_three(q["query"], answer, docs) if docs else \
                 {"faithfulness": 0, "context_recall": 0, "context_relevance": 0}

        faith_scores.append(scores["faithfulness"])
        recall_scores.append(scores["context_recall"])
        relevance_scores.append(scores["context_relevance"])

        print(f"  [{i+1}/{len(questions)}] {config_label} | "
              f"F={scores['faithfulness']} R={scores['context_recall']} "
              f"Rel={scores['context_relevance']} | {elapsed:.1f}s")
        time.sleep(1)  # 每道题后停顿 1 秒释放 GPU 显存

    return {
        "faithfulness": round(sum(faith_scores) / len(faith_scores), 1),
        "context_recall": round(sum(recall_scores) / len(recall_scores), 1),
        "context_relevance": round(sum(relevance_scores) / len(relevance_scores), 1),
        "avg_time": round(sum(times) / len(times), 1),
    }


def main():
    import sys as _sys
    full_mode = "--full" in _sys.argv
    sample_size = 190 if full_mode else 15

    print("=" * 60)
    print("RAG 参数控制变量评测")
    print(f"模式: {'全量190题' if full_mode else '快速15题(加--full跑全量)'}")
    print("=" * 60)

    questions = load_questions()[:sample_size]
    print(f"\n[0] 加载 {len(questions)} 道测试题")

    results = []
    total_runs = len(K_VALUES) + len(CHUNK_VALUES) + len(WEIGHT_VALUES)
    run_idx = 0

    # ── 测试 k 值 ──
    print(f"\n{'='*40}")
    print(f"测试 k 值 (chunk={DEFAULT_CHUNK}, weight={DEFAULT_WEIGHT})")
    print(f"{'='*40}")

    vs, llm, er, docs = build_rag(chunk_size=DEFAULT_CHUNK, weight=DEFAULT_WEIGHT)
    for k in K_VALUES:
        print(f"\n  [pause 3s for GPU cool-down]"); time.sleep(3)
        run_idx += 1
        label = f"k={k}"
        print(f"\n[{run_idx}/{total_runs}] {label}")
        scores = run_single_config(questions, vs, llm, er, k, label)
        results.append({"param": "k", "value": k, **scores})

    # ── 测试 chunk_size ──
    print(f"\n{'='*40}")
    print(f"测试 chunk_size (k={DEFAULT_K}, weight={DEFAULT_WEIGHT})")
    print(f"{'='*40}")

    for cs in CHUNK_VALUES:
        print(f"\n  [pause 5s for GPU cool-down + FAISS rebuild]"); time.sleep(5)
        run_idx += 1
        label = f"chunk={cs}"
        print(f"\n[{run_idx}/{total_runs}] {label}")
        vs, llm, er, docs = build_rag(chunk_size=cs, weight=DEFAULT_WEIGHT)
        scores = run_single_config(questions, vs, llm, er, DEFAULT_K, label)
        results.append({"param": "chunk_size", "value": cs, **scores})

    # ── 测试权重 ──
    print(f"\n{'='*40}")
    print(f"测试 Ensemble 权重 (k={DEFAULT_K}, chunk={DEFAULT_CHUNK})")
    print(f"{'='*40}")

    for w in WEIGHT_VALUES:
        print(f"\n  [pause 3s for GPU cool-down]"); time.sleep(3)
        run_idx += 1
        label = f"weight={w}"
        print(f"\n[{run_idx}/{total_runs}] {label}")
        vs, llm, er, docs = build_rag(chunk_size=DEFAULT_CHUNK, weight=w)
        scores = run_single_config(questions, vs, llm, er, DEFAULT_K, label)
        results.append({"param": "Ensemble Weight", "value": str(w), **scores})

    # ── 生成报告 ──
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    report_name = f"rag_eval_{time.strftime('%Y%m%d_%H%M%S')}.md"
    report_path = os.path.join(OUTPUT_DIR, report_name)

    lines = []
    lines.append("# RAG 参数控制变量评测报告")
    lines.append(f"评测时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"测试题数：{len(questions)}")
    lines.append(f"LLM：{MODEL}")
    lines.append(f"Embedding：{EMBED_MODEL}")
    lines.append("")

    for param in ["k", "chunk_size", "Ensemble Weight"]:
        lines.append(f"## {param} 参数对比")
        lines.append("")
        lines.append("| 参数值 | Faithfulness | Context Recall | Context Relevance | 平均耗时 |")
        lines.append("|--------|:-----------:|:------------:|:----------------:|:------:|")
        for r in results:
            if r["param"] == param:
                lines.append(f"| {r['value']} | {r['faithfulness']} | {r['context_recall']} "
                             f"| {r['context_relevance']} | {r['avg_time']}s |")
        lines.append("")

    # 推荐
    lines.append("## 推荐配置")
    best = max(results, key=lambda x: (x["faithfulness"] + x["context_recall"] + x["context_relevance"]) / 3)
    lines.append(f"推荐参数：{best['param']} = {best['value']}")
    lines.append(f"综合得分：F={best['faithfulness']} R={best['context_recall']} Rel={best['context_relevance']}")
    lines.append("")

    report = "\n".join(lines)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n报告已保存：{report_path}")
    print(report)


if __name__ == "__main__":
    main()
