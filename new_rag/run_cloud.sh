#!/bin/bash
# AutoDL 一键脚本：安装环境 → 拉模型 → 跑评测 → 生成报告
set -e

echo "[1/5] apt 依赖"
apt update -qq && apt install -y -qq pkg-config cmake >/dev/null 2>&1

echo "[2/5] pip 依赖"
pip install ollama langchain langchain-community langchain-ollama langchain-text-splitters \
    rank_bm25 pymysql sqlalchemy python-docx requests python-dotenv -q

echo "[3/5] 启动 Ollama"
ollama serve &
sleep 3

echo "[4/5] 拉模型"
ollama pull qwen3:4b
ollama pull qwen3-embedding:4b

echo "[5/5] 跑评测"
python eval_rag_params.py --full

echo "=== DONE ==="
ls -lh reports/
