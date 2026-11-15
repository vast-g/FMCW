"""边坡监测 Agent 自动化评测框架。

模块划分：

* `judge`   —— LLM 客户端（Ollama / 百炼）与 LLM-as-Judge，含结果缓存与自一致性
* `metrics` —— 五项核心指标（Route / Tool / Task Completion / Faithfulness / Context Recall）
* `runner`  —— 数据集加载、无头驱动 Agent、收集 Trace、汇总与落盘
* `report`  —— 生成可读的 Markdown 评测报告
* `badcase` —— 从 Trace 自动挖掘 Bad Case 并生成回归集
* `cli`     —— 统一命令行入口

配套（项目根目录）：

* `tracing.py`    —— 结构化 Agent Trace（评测的数据源）
* `resilience.py` —— 错误分类、重试、请求级预算、降级记录
* `headless.py`   —— 无头模式支撑（容器/CI 中导入核心逻辑）
* `config.py`     —— 全部配置的单一事实来源
"""

__version__ = "1.0.0"

__all__ = ["judge", "metrics", "runner", "report", "badcase", "cli"]
