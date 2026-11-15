# FMCW 边坡智能监测系统

**嵌入式感知下位机 + 本地 AI Agent 决策 + 管理平台** 的边坡安全监测系统，支持离线运行与滑坡风险预警。

仓库包含三部分：下位机固件工程、当前上位机（Agent + 平台 + Web）、早期单机版上位机。

## 一、目录说明

| 目录 | 内容 | 说明 |
| --- | --- | --- |
| [`下位机部分/`](下位机部分) | STM32 固件工程（Keil MDK） | 双 STM32 采集/通信架构、ESP8266、北斗/GPS、超声波 |
| [`new_rag/`](new_rag) | **当前主版本** | Python Agent（LangGraph）+ Spring 平台 + Web + 评测 |
| [`Code/`](Code) | 早期单机版上位机 | PyInstaller 打包工程与资源，**已不作为主线维护** |
| [项目 README](边坡智能监测Agent系统%20-%20项目README.md) | 软硬件整体介绍 | 架构、创新点、完整技术栈 |
| `readme.txt` | 简短入口说明 | — |

> 日常开发与部署请看 `new_rag/`；`Code/` 仅作历史版本留存。

## 二、系统架构

```text
感知层：超声波测距 / 北斗 GPS / 视觉识别（YOLOv8-n）
   ↓ WiFi TCP / 串口
传输层：ESP8266（AP 固定热点）+ 串口协议
   ↓
决策层：本地 Agent（LangGraph 控制流 + 工具调用 + RAG + 记忆）
   ↓
应用层：风险预警 / 数据可视化 / 规则告警 / 管理平台
```

设计要点：

- **零幻觉硬数据**：位移、坐标、设备状态等数值只由工具/硬件返回，模型不生成数值。
- **规则与模型分工**：是否告警由确定性规则引擎判定，模型只负责解释与建议。
- **离线可用**：Ollama 本地模型 + FAISS 本地索引，无云端依赖。

## 三、当前版本（new_rag）能力

- **显式控制流**：意图识别 → 按意图收窄工具 → ReAct 循环 → 统一收口（`slope_graph.py`）。
- **会话记忆**：Redis 保存最近 5 轮原话，滑动窗口挤出的对话滚动合并为摘要；长期画像存 MySQL。
- **多因素评估工具**：位移速率、降雨、孔压、邻点对比、裂缝、规则告警查询。
- **规则告警引擎**：阈值规则命中后写入 `alert_event`，去重后供 Agent 解释。
- **管理平台**：Spring Boot 提供点位、设备、规则、告警确认接口（`slope-platform/`）。
- **评测体系**：路由准确率、工具调用、检索召回、忠实度、任务完成率与延迟（`slope_eval/`、`eval/`）。

## 四、快速开始（new_rag）

### 环境要求

- Python 3.11+
- MySQL 8.0、Redis 6+
- Ollama（本地模型：`qwen3:4b`、`qwen3-embedding:4b`）
- JDK 17（仅管理平台需要）

### 步骤

```bash
cd new_rag

# 1. 依赖
pip install -r requirements.txt

# 2. 配置
copy .env.example .env      # 填写 MySQL / Redis / Ollama 地址

# 3. 本地模型
ollama pull qwen3:4b
ollama pull qwen3-embedding:4b

# 4. 启动 Agent（桌面版带界面）
python end_new.py

# 或：无界面 / 服务模式
python headless.py
```

管理平台与 Web：

```bash
# 平台（默认 8080，库表见 slope-platform/src/main/resources/schema.sql）
cd new_rag/slope-platform && mvn spring-boot:run

# Web（nginx 配置见 slope-web/deploy/nginx.conf）
```

## 五、测试与评测

```bash
cd new_rag

# 记忆与图结构单测
python -m unittest discover -s tests -v

# 一键自检
python tools/run_all_checks.py

# 评测
python -m slope_eval.cli
```

## 六、相关文档

- [软硬件整体介绍](边坡智能监测Agent系统%20-%20项目README.md)
- [new_rag 评测与部署说明](new_rag/README_评测与部署.md)
- [管理平台说明](new_rag/slope-platform/README.md)
- [Web 部署说明](new_rag/slope-web/README.md)

## 七、说明

- 仓库不包含 `.env`、数据库文件与模型权重之外的运行数据；`yolov8n.pt` 为历史版本保留。
- 代码用于项目展示与学习交流，第三方依赖遵循各自许可证。
