# ═══════════════════════════════════════════════════════════════
# 边坡监测 Agent —— 无头镜像（API + 评测）
#
# 说明：
#   * 只安装 requirements.txt（核心），不装 torch / opencv / tkinter，
#     镜像体积从数 GB 降到数百 MB；GUI 在容器里本来也无法运行。
#   * 以非 root 用户运行。
#   * 默认以 API 模式启动，并通过 /health 暴露就绪状态。
# ═══════════════════════════════════════════════════════════════
FROM python:3.13-slim

# 基础系统依赖：
#   curl    —— 容器健康检查
#   build-essential 用于编译个别没有 wheel 的包（装完即删，减小体积）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SLOPE_HEADLESS=1 \
    TZ=Asia/Shanghai

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl build-essential \
 && rm -rf /var/lib/apt/lists/*

# 依赖单独一层，代码改动不会导致重装依赖
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt \
 && apt-get purge -y --auto-remove build-essential

# 应用代码
COPY . .

# 非 root 运行
RUN useradd -m -u 10001 slope \
 && mkdir -p /app/reports /app/bad_cases /app/feedback \
 && chown -R slope:slope /app
USER slope

EXPOSE 8000

# 就绪探测：RAG 未就绪时 /health 返回 200 但 status=degraded，
# 这里只判断 HTTP 可达（进程存活），避免在慢速 CPU 上被反复重启。
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# 默认跑 API；评测用 `docker compose run --rm eval python -m slope_eval ...`
CMD ["python", "end_new.py", "--api"]
