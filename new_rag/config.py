"""配置中心（重构版）

设计目标：
1. **单一事实来源**：所有可调参数集中在此，业务代码不得再出现硬编码的模型名/超时/路径。
2. **环境优先**：优先级 环境变量 > .env 文件 > 代码默认值。
3. **向后兼容**：保留旧版模块级名字（DB_HOST / OLLAMA_MODEL / MYSQL_KWARGS ...），
   使 end_new.py 等既有调用点无需同步修改。
4. **可追溯**：`config_version` 是"影响评测结果的关键参数"的短哈希，
   写入 Agent Trace，保证不同配置跑出的指标不会被错误地横向比较。

用法：
    from config import settings          # 推荐：结构化访问
    settings.llm_model
    from config import DB_HOST           # 兼容旧代码
"""
from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录
BASE_DIR = Path(__file__).parent.resolve()


def _default_data_dir() -> Path:
    return BASE_DIR / "data"


class Settings(BaseSettings):
    """全部运行时配置。字段名即环境变量名（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── 应用 ──
    app_name: str = "slope-monitor-agent"
    app_env: str = Field(default="dev", description="dev / prod")
    log_level: str = "info"
    # 无头模式：不创建 tkinter 窗口、不启动摄像头/串口线程，供容器与 CI 使用
    headless: bool = False

    # ── LLM（Ollama）──
    # 兼容旧变量名 OLLAMA_MODEL / OLLAMA_EMBED_MODEL / OLLAMA_BASE_URL / OLLAMA_TIMEOUT
    llm_model: str = Field(
        default="qwen3:4b",
        validation_alias=AliasChoices("llm_model", "OLLAMA_MODEL"),
    )
    llm_embed_model: str = Field(
        default="qwen3-embedding:4b",
        validation_alias=AliasChoices("llm_embed_model", "OLLAMA_EMBED_MODEL"),
    )
    llm_base_url: str = Field(
        default="http://localhost:11434",
        validation_alias=AliasChoices("llm_base_url", "OLLAMA_BASE_URL"),
    )
    # qwen3 系列默认开启 thinking，会显著拖慢生成；评测/生产默认关闭
    llm_think: bool = False
    llm_temperature: float = 0.0
    llm_num_predict: int = 512
    # 裁判模型（LLM-as-Judge）。留空表示复用 llm_model
    judge_model: str = ""
    judge_num_predict: int = 256
    judge_temperature: float = 0.0
    # 裁判提供方：ollama（本地小模型）| openai_compat（任意 OpenAI 兼容端点）
    # 推荐 openai_compat：用小模型当裁判会因分辨力不足产生"全是同一个分"的退化结果，
    # 而"被评对象用本地小模型、裁判用强模型"也更符合评测学上的做法。
    # 兼容旧值 "dashscope"（历史上只接百炼，现已泛化为任意 OpenAI 兼容端点）。
    judge_provider: str = Field(
        default="ollama",
        validation_alias=AliasChoices("judge_provider", "JUDGE_PROVIDER"),
    )
    judge_max_tokens: int = 1024  # 推理型裁判需给思考过程留足额度
    # OpenAI 兼容裁判端点（原仅用于百炼 DashScope，现可指向任意兼容服务，如 DeepSeek）。
    # 新增 JUDGE_API_KEY / JUDGE_BASE_URL 两个更直观的别名，
    # 避免"变量名叫 dashscope、实际连的是 DeepSeek"这种名不符实的配置
    # —— 本项目历史上已多次因"配置名与实际不符"踩坑。
    dashscope_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "judge_api_key", "JUDGE_API_KEY", "dashscope_api_key", "DASHSCOPE_API_KEY"
        ),
    )
    dashscope_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias=AliasChoices(
            "judge_base_url", "JUDGE_BASE_URL", "dashscope_base_url", "DASHSCOPE_BASE_URL"
        ),
    )

    # ── 超时（秒）──
    timeout_llm_generate: int = Field(
        default=30,
        validation_alias=AliasChoices("timeout_llm_generate", "OLLAMA_TIMEOUT"),
    )
    timeout_llm_rewrite: int = 15
    timeout_llm_rerank: int = 15
    timeout_llm_judge: int = 60
    timeout_embedding: int = 30
    timeout_http_tool: int = 5
    timeout_mysql_connect: int = 3
    # 单次请求的总预算；超出即走降级，不再继续等待
    timeout_request_budget: int = 90
    timeout_react_budget: int = 60

    # ── 重试 ──
    retry_max_attempts: int = 2
    retry_backoff_base: float = 0.5

    # ── 会话存储 ──
    # memory: 进程内；redis: 多实例共享，是否持久化仍取决于 Redis 配置。
    # 显式选择 redis 后，故障不会自动切换到进程内存。
    # memory 留给离线开发/评测环境，当前 .env 已配置 redis。
    session_lock_ttl: int = Field(default=30, ge=1)
    session_backend: str = Field(
        default="memory",
        validation_alias=AliasChoices("session_backend", "SESSION_BACKEND"),
    )
    redis_url: str = Field(
        default="redis://127.0.0.1:6379/0",
        validation_alias=AliasChoices("redis_url", "REDIS_URL"),
    )
    # 会话历史保留时长（秒）。给 TTL 是为了避免废弃会话无限占内存。
    session_ttl: int = Field(
        default=7 * 24 * 3600,
        validation_alias=AliasChoices("session_ttl", "SESSION_TTL"),
    )

    # ── ReAct ──
    react_max_iterations: int = 5

    # ── RAG ──
    chunk_size: int = 200
    chunk_overlap: int = 20
    rag_top_k: int = 5
    rerank_top_k: int = 5
    rag_docs_dir: str = str(BASE_DIR / "rag_docs")
    fact_store_dir: str = str(BASE_DIR / "faiss_facts")

    # ── 业务阈值 ──
    alert_cooldown: int = 10
    dist_threshold_safe: float = 0.5
    dist_threshold_warn: float = 0.3
    alert_distance: float = 0.3
    conf_thresh: float = 0.45
    yolo_model_path: str = str(BASE_DIR / "yolov8n.pt")

    # ── MySQL ──
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = "Root@123456"
    db_name: str = "slope_agent"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_recycle: int = 3600

    # ── 服务 ──
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # ── 评测 / 可观测性 ──
    eval_dir: str = str(BASE_DIR / "eval")
    reports_dir: str = str(BASE_DIR / "reports")
    badcases_dir: str = str(BASE_DIR / "bad_cases")
    feedback_dir: str = Field(
        default=str(BASE_DIR / "feedback"),
        validation_alias=AliasChoices("feedback_dir", "FEEDBACK_DIR"),
    )
    # 结构化 trace 另存一份 JSONL，便于离线分析与回归挖掘
    trace_jsonl: str = str(BASE_DIR / "reports" / "agent_trace.jsonl")
    trace_enabled: bool = True

    # ── 派生属性 ──
    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.db_user}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def uses_remote_judge(self) -> bool:
        """裁判是否走远程 OpenAI 兼容端点（而非本地 Ollama）。

        **必须集中判断**：原先三处各自硬编码 `== "dashscope"`，
        导致把 JUDGE_PROVIDER 改成 "deepseek" 之类的同义值后，
        只有 make_client 认它、而 base_url / ready 判断仍回落到本地 Ollama，
        表现为"模型名是 deepseek-flash、却连到 11434"→ 404。
        这类"改一处漏三处"的配置漂移，本项目已踩过多次。
        """
        return (self.judge_provider or "").lower() in (
            "dashscope", "openai", "bailian", "deepseek", "openai_compat",
        )

    @property
    def effective_judge_model(self) -> str:
        """裁判模型：显式配置优先，否则按提供方给默认值。"""
        if self.judge_model:
            return self.judge_model
        return "glm-5.3" if self.uses_remote_judge else self.llm_model

    @property
    def effective_judge_base_url(self) -> str:
        if self.uses_remote_judge:
            return self.dashscope_base_url
        return self.llm_base_url

    def judge_ready(self) -> tuple[bool, str]:
        """裁判是否可用；返回 (可用, 原因)。用于评测启动前的快速自检。"""
        if self.uses_remote_judge:
            if not self.dashscope_api_key:
                return False, (f"JUDGE_PROVIDER={self.judge_provider} "
                               "但未配置 JUDGE_API_KEY / DASHSCOPE_API_KEY")
            return True, f"{self.judge_provider} / {self.effective_judge_model}"
        return True, f"ollama / {self.effective_judge_model}"

    @property
    def mysql_kwargs(self) -> dict:
        return {
            "host": self.db_host,
            "port": self.db_port,
            "user": self.db_user,
            "password": self.db_password,
            "database": self.db_name,
            "charset": "utf8mb4",
            "connect_timeout": self.timeout_mysql_connect,
        }

    def config_version(self) -> str:
        """影响评测结论的关键参数指纹。

        只纳入会改变模型输出或检索结果的字段；端口、日志级别等运维参数不参与，
        避免"改了日志级别导致历史指标不可比"这类无意义抖动。
        """
        key_fields = [
            "llm_model",
            "llm_embed_model",
            "llm_think",
            "llm_temperature",
            "llm_num_predict",
            "chunk_size",
            "chunk_overlap",
            "rag_top_k",
            "rerank_top_k",
            "react_max_iterations",
            "timeout_request_budget",
        ]
        payload = json.dumps({k: getattr(self, k) for k in key_fields}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

    def ensure_dirs(self) -> None:
        for d in (self.eval_dir, self.reports_dir, self.badcases_dir, self.feedback_dir):
            Path(d).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例。测试中可用 get_settings.cache_clear() 重置。"""
    return Settings()


settings = get_settings()

# ══════════════════════════════════════════════════════════════════
# 向后兼容层：旧代码（end_new.py 等）直接引用模块级常量。
# 新代码请使用 settings.*，这些名字仅为平滑迁移保留。
# ══════════════════════════════════════════════════════════════════
DB_HOST = settings.db_host
DB_PORT = settings.db_port
DB_USER = settings.db_user
DB_PASSWORD = settings.db_password
DB_NAME = settings.db_name
DATABASE_URL = settings.database_url
MYSQL_KWARGS = settings.mysql_kwargs

OLLAMA_MODEL = settings.llm_model
OLLAMA_EMBED_MODEL = settings.llm_embed_model
OLLAMA_BASE_URL = settings.llm_base_url
OLLAMA_TIMEOUT = settings.timeout_llm_generate

SERVER_HOST = settings.server_host
SERVER_PORT = settings.server_port
LOG_LEVEL = settings.log_level
HEADLESS = settings.headless or os.getenv("SLOPE_HEADLESS") == "1"

ALERT_COOLDOWN = settings.alert_cooldown
CHUNK_SIZE = settings.chunk_size
CHUNK_OVERLAP = settings.chunk_overlap
RAG_TOP_K = settings.rag_top_k
CONF_THRESH = settings.conf_thresh
ALERT_DISTANCE = settings.alert_distance

RAG_DOCS_DIR = settings.rag_docs_dir
YOLO_MODEL_PATH = settings.yolo_model_path
FEEDBACK_DIR = settings.feedback_dir
