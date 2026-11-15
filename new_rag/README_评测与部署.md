# 评测体系与部署优化 · 改造说明

> 项目：边坡监测 AI Agent（本机 Ollama 小模型驱动）
> 改造目标：让简历里"LLM-as-Judge + Agent Trace 自动化评测 / Bad Case 回归 / 配置外置 / 异常与超时控制 / Docker Compose 部署"这几点**真实成立**。

---

## 一、改造前后对照

| 简历能力 | 改造前 | 改造后 |
|---|---|---|
| Agent Trace | `agent_trace` 表存在，但**工具调用不入库**、`tool_name` 恒为 None、写库失败被 `except: pass` 静默吞掉 | `trace_run` + `trace_step` 双表，route / 工具 / 状态 / 降级 / 延迟 / 配置指纹全结构化，另有 JSONL 兜底 |
| 路由可测 | 路由是关键词级联，但**不产生任何路由标签**，无法计算准确率 | 12 个分支全部登记 route，Route Accuracy 可算 |
| 工具可测 | 工具调用只推给 GUI 队列，不入库 | 两条路径（ReAct + 直接工具）都登记工具名，Tool Accuracy 可算 |
| LLM-as-Judge | 用"回答里最后三个数字"当评分；六组配置全部 5.0/5.0/5.0，**结论不可信** | 强模型裁判（百炼 glm-5.3）+ JSON Schema 约束 + 自一致性投票 + 退化为 `unknown` 而非猜分 |
| Faithfulness | 0-10 打分 | 回答拆原子陈述，逐条做**蕴含判定**（supported / not_supported / unknown） |
| Context Recall | 无标准答案也硬算 | 必须有 `reference_answer`，逐句判断能否被检索上下文支撑 |
| Tool Accuracy | 无 | 期望工具集 vs 实调工具集的 P/R/F1 + 完全匹配率 |
| Route Accuracy | 无 | 确定性断言 |
| Task Completion Rate | 无 | 按任务类型验收标准判定（规则 + 裁判混合） |
| Bad Case 回归 | 无 | 自动挖掘 + 7 类归因 + 回归集去重累积 + 门禁命令 |
| 配置外置 | `config.py` 只用于数据库连接；模型名/超时/路径**硬编码 7 处**；默认路径还是错的 | pydantic-settings 统一收口，旧变量名兼容，含 `config_version` 指纹 |
| 异常处理 | 12 处裸 `except:`、45 处宽泛捕获、6 处静默 `pass` | 错误三分类（可重试/可降级/致命）+ 指数退避重试 + 降级必须记录原因 |
| 超时控制 | 只有单次 LLM 调用的硬编码 15/20/30s，无请求级预算，ReAct **会卡死** | 请求级 Deadline 逐层传递 + ReAct 轮次上限 + 挂钟预算 |
| Docker Compose | 无 Dockerfile、无 compose、无 requirements.txt | 三服务编排（api / ollama / mysql）+ 模型预拉取任务 + 健康检查 |

---

## 二、交付文件

### 新增模块（项目根）

| 文件 | 作用 |
|---|---|
| `tracing.py` | 结构化 Agent Trace（评测的数据源） |
| `resilience.py` | 错误分类、重试、请求级预算、降级记录 |
| `headless.py` | 无头模式支撑（容器/CI 可导入核心逻辑） |
| `config.py` | **重写**：全部配置的单一事实来源 |
| `requirements.txt` / `requirements-full.txt` | 核心依赖 / 含 GUI 与硬件的完整依赖 |
| `Dockerfile` / `docker-compose.yml` / `.dockerignore` | 容器化 |
| `.gitignore` | 防止 `.env` 等密钥文件被提交 |

### 评测框架 `slope_eval/`

| 文件 | 作用 |
|---|---|
| `judge.py` | LLM 客户端（Ollama / 百炼 OpenAI 兼容）+ LLM-as-Judge + 缓存 |
| `metrics.py` | 五项指标实现与汇总 |
| `runner.py` | 数据集加载、无头驱动 Agent、Trace 收集、传感器夹具 |
| `report.py` | Markdown 报告生成 |
| `badcase.py` | Bad Case 挖掘、归因、回归集维护 |
| `cli.py` | 统一命令行入口 |

### 数据集 `eval/`

| 文件 | 内容 |
|---|---|
| `testset.jsonl` | 190 条带标签样本（`expected_route` / `expected_tools` / `reference_answer` / `success_criteria`） |
| `testset_extra.jsonl` | 22 条补充样本，覆盖原题集**完全没触及的 8 个路由分支** |
| `regression.jsonl` | 运行 `mine` 后自动累积的回归集 |

### 工具 `tools/`

| 文件 | 作用 |
|---|---|
| `verify_instrumentation.py` | 检查路由/工具埋点数量、硬编码残留、异常处理质量 |
| `selftest_pipeline.py` | **离线**管线自测（不调用模型，秒级完成） |

---

## 三、怎么跑（按顺序）

### 0. 环境预检（几秒）

```powershell
python -m slope_eval check
```

会检查：配置、裁判 key、知识库、模型是否已拉取、MySQL 连通性。**有红色项先解决**。

### 1. 裁判自检（几秒，2 次 API 调用）

```powershell
python -m slope_eval judge-test
```

### 2. 小样本试跑（建议先跑 5 条）

```powershell
python -m slope_eval run --limit 5 --no-judge --reset-trace
```

`--no-judge` 只用本地小模型、不算裁判指标，用于先确认 Agent 链路通了。

### 3. 正式评测

```powershell
# 先跑 20 条带裁判，确认指标正常
python -m slope_eval run --limit 20

# 全量（212 条：190 主题 + 22 补充）
python -m slope_eval run --full
```

**关于耗时**：本地 4B 模型在 CPU 上单题约 20-40 秒，全量 212 条约 **1.5-2.5 小时**。
如果嫌慢，可以分几次跑（每次指定 `--routes` 或 `--limit`），
**Trace 是追加写的，`report` 与 `mine` 能基于已落盘数据工作**。

### 4. 生成报告 / 挖掘 Bad Case

```powershell
python -m slope_eval report      # 从最近结果重新生成报告
python -m slope_eval mine        # 挖掘 Bad Case 并累积进回归集
```

产物：
- `reports/eval_report_*.md` —— 人看的报告
- `reports/eval_results_*.json` —— 机器读的结果
- `reports/agent_trace.jsonl` —— 全量 Trace
- `bad_cases/badcases_*.jsonl` —— 归因后的 bad case
- `eval/regression.jsonl` —— 回归集

### 5. 建立基线与门禁

```powershell
python -m slope_eval baseline    # 把当前结果设为基线
# 之后每次改动 prompt / 路由 / 换模型后：
python -m slope_eval gate        # 指标回退超过容差即失败（退出码 1）
```

`gate` 可直接挂到 CI：**任何一次改动都不允许让历史成功样本重新变错**。

---

## 四、指标口径（面试会被问，务必理解）

| 指标 | 怎么算 | 为什么这么算 |
|---|---|---|
| Route Accuracy | 预测路由 == 期望路由 | 路由是规则级联的**确定输出**，有精确解，用 LLM 打分只会引入噪声 |
| Tool Accuracy | 期望工具集与实调工具集的 P/R/F1 + 完全匹配率 | 集合比较，同样有精确解 |
| Task Completion Rate | 按任务类型的验收标准判 pass/fail | 不同任务类型标准不同（闲聊不要求引用知识库） |
| Faithfulness | 回答切原子陈述 → 逐条判断能否被检索上下文**蕴含** | 用蕴含而非打分：4B 模型给 0-10 分近乎随机 |
| Context Recall | 标准答案切句 → 逐句判断能否被上下文支撑 | **没有标准答案的样本不参与**，不编分数 |
| Context Relevance | 召回片段与问题的相关比例 | 反映检索精度 |

### 三个必须强调的设计

1. **裁判与被测模型分离**：被测是本地 `qwen3:4b`，裁判是百炼 `glm-5.3`。
   原来用 4B 模型当裁判，结果退化成"所有配置同分"，这个结论毫无价值。
2. **`unknown` 是一等公民**：裁判判不了就记 `unknown` 并单独统计，
   绝不折算成默认分。报告里专设"裁判可靠性"一节。
3. **`config_version` 指纹**：模型 / chunk / top_k 等一变，历史指标就不可比，
   报告里会显式标注。

---

## 五、评测条件的诚实说明

跑出来的数字有两个前提，写简历或汇报时必须说清楚：

1. **无头模式 + 传感器夹具**：评测环境没有真实硬件（`wifi_thread` 不启动），
   `current_distance` 恒为 0，距离类路由会失效。因此 runner 会注入固定夹具
   （`distance=1.25m`、西安坐标）。**夹具条件下的指标 ≠ 真实硬件在线条件下的指标。**
2. **标签是 AI 生成的草稿**：`testset.jsonl` 全部 `reviewed:false`。
   `reference_answer` 基于 `rag_docs/` 真实内容撰写，但**尚未人工复核**。
   正式对外引用前，建议至少抽检 30 条。

---

## 六、容器化部署

```bash
# 首次：启动依赖并拉取模型（约 5GB，只需一次）
docker compose up -d mysql ollama
docker compose run --rm model-pull

# 启动 API
docker compose up -d api
curl http://localhost:8000/health
```

API 端点：
- `POST /chat` —— 问答（返回 `trace_id`）
- `POST /feedback` —— 反馈
- `GET /health` —— 健康检查（区分存活 / RAG 就绪）

在容器里跑评测：

```bash
docker compose run --rm eval python -m slope_eval check
docker compose run --rm eval python -m slope_eval run --full
```

> ⚠️ **本机未安装 Docker**，所以 compose 与 Dockerfile **只做了静态编写，未实机验证**。
> 首次使用请先 `docker compose config` 校验语法，再 `docker compose build`。

---

## 七、改造后的简历措辞建议

跑完全量评测、拿到真实数字后，可以这样写（**方括号处填真实数字**）：

> 构建基于 **Agent Trace + LLM-as-Judge** 的自动化评测体系：通过结构化 Trace 采集路由分支、
> 工具调用、降级与延迟，量化 **Route Accuracy [X]%、Tool F1 [X]、Task Completion Rate [X]%、
> Faithfulness [X]%、Context Recall [X]%**；裁判采用百炼 glm-5.3 与被测本地小模型（qwen3:4b）
> **相互独立**，并以 JSON Schema 约束 + 自一致性投票抑制裁判噪声；基于评测结果自动挖掘
> Bad Case 并按 7 类归因，沉淀 [N] 条回归集接入 CI 门禁。
>
> 部署侧完成**配置全量外置**（pydantic-settings，含配置指纹）、**异常三分类与指数退避重试**、
> **请求级超时预算传递**（杜绝 ReAct 无限循环），并提供 **Docker Compose 三服务编排**
> （应用 / Ollama / MySQL）与健康检查。

### 面试可能追问，提前准备

| 问题 | 你的答案要点 |
|---|---|
| 裁判怎么保证可靠？ | 与被测模型分离；JSON Schema 约束；自一致性投票；退化为 `unknown` 而非猜分；报告里公开一致率与 unknown 数 |
| 为什么不用 RAGAS？ | 可以用，但本项目要测的是**Agent 路由与工具调用**，RAGAS 覆盖不到；且 RAGAS 的裁判也依赖强模型 |
| 小模型带来了什么约束？ | 0-10 打分不可用（改离散判定）、意图分类不可靠（改规则路由 + ReAct 兜底）、thinking 模式拖慢且污染输出（关闭）、JSON 输出不稳（Schema + 解析降级） |
| 降级率怎么用？ | 它是运维指标：降级率高说明 ReAct 在该类问题上不产出，指向前端路由或 prompt 问题 |
| 评测集怎么来的？ | 190 条真实用户问法（原有）+ 22 条补充覆盖规则分支；标签 AI 起草 + 人工抽检 |

---

## 八、已知限制

1. 原 190 题只覆盖 `react_agent` / `event_query` 两个分支 —— 已用 `testset_extra.jsonl` 补 8 类，
   但补充样本量小（每类 1-3 条），扩展到每类 10 条以上会更稳。
2. `testset_extra.jsonl` 中 id 1003（天气）依赖外网；离线时该项会失败，属预期。
3. id 1021（历史事件）依赖 MySQL 中已有事件记录，结果可能随库内容变化。
4. `eval_rag_params.py`（旧评测脚本）**保留未删**，它仍有参考价值（k / chunk / 权重的控制变量实验），
   但**其裁判实现不可信**，若要复用请改用 `slope_eval.judge.Judge`。
5. Docker 未实机验证（本机无 Docker）。
