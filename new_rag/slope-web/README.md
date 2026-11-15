# 边坡监测智能平台 · Web 前端

系统统一入口。**零外部依赖**（不引任何 CDN），离线可用。

## 这一层解决什么

在加它之前，系统是"两个后端各自能跑，但没有一个地方能看到全貌"：

```
Java 业务域 (8080)  点位 / 设备 / 规则 / 告警
Python Agent (8000) 对话 / 推理 / 评测
```

现在统一由 nginx 收口，浏览器只跟一个来源说话：

```
浏览器 ──> nginx :80 ─┬─ 静态资源（本目录）由 nginx 直接吐
                      ├─ /api/   → Java  127.0.0.1:8080
                      └─ /agent/ → Python 127.0.0.1:8000
```

**为什么用 nginx 同源反代，而不是前端直连两个端口：**

1. **没有跨域**：只跟一个来源说话，Java 侧不需要配 CORS，也没有预检请求
2. **端口不外露**：8080/8000 可以只监听回环，外部只能从 80 进
3. **策略集中**：超时、压缩、限流统一在这一层调，不用改两端代码

## 目录

```
slope-web/
├─ index.html              页面结构（四块业务区域）
├─ css/app.css             深色大屏样式
├─ js/api.js               API 客户端（JWT 携带、信封解包、错误统一处理）
├─ js/charts.js            手写 SVG 图表（折线 + 点位分布）
├─ js/app.js               主逻辑（渲染 + 操作转发）
└─ deploy/
   ├─ slope.ps1            ★ 一键启停控制台（启动/停止/重启/状态/菜单）
   ├─ slope.bat            双击入口（纯 ASCII，见下方说明）
   ├─ nginx.conf           站点配置（含关键的超时设置）
   └─ mime.types           复制自 nginx 安装目录，让配置自包含
```

## 启动

**最简单：双击 `deploy\slope.bat`**，会出交互菜单。

或者用命令行：

```powershell
cd C:\Users\Lenovo\Desktop\new_rag\slope-web\deploy

.\slope.ps1            # 交互菜单
.\slope.ps1 start      # 一键启动
.\slope.ps1 stop       # 一键停止
.\slope.ps1 restart    # 重启
.\slope.ps1 status     # 只看状态
```

然后打开 <http://127.0.0.1/>，用 `admin / admin123` 登录。

### 启动顺序是有依赖的

脚本严格按 **Redis → Java → Agent → nginx** 启动，而且**每一步都等到真正就绪**才继续：

```
✅ Redis 已启动 redis_version:3.2.100
✅ Java 已就绪 (8080)
→ Agent 预热中…（starting / rag_state=loading）
✅ Agent 已就绪，RAG 已加载（耗时 28s）
✅ nginx 已启动 (80)
```

为什么必须等 Agent：它的就绪分两段 —— 进程起来是 `starting`，**RAG（嵌入模型 + FAISS）加载完才是 `ok`**。不等就开页面，问知识类问题会得到"知识库未加载"。

停止顺序相反（nginx 先停），避免出现"页面还能访问但后端已经没了"的空窗。

### 两个文件为什么一个 .ps1 一个 .bat

| 文件 | 编码 | 原因 |
|---|---|---|
| `slope.ps1` | **UTF-8 with BOM** | 本机是 **Windows PowerShell 5.1**，它读无 BOM 的 UTF-8 会按 GBK 解释 —— 中文变乱码后**连字符串结尾的引号都会被吃掉**，直接语法报错 |
| `slope.bat` | **纯 ASCII** | `.bat` 按控制台代码页读取，非 ASCII 字节会被破坏；所以中文全部放在 `.ps1` 里，`.bat` 只负责 `chcp 65001` + 转发 |

> 改脚本时注意：用编辑器保存 `slope.ps1` 要选 **UTF-8 with BOM**（"UTF-8" 不带 BOM 会出问题）。

### 路径是自动探测的

`slope.ps1` 会自己找 nginx 和 Redis（先查 PATH，再在常见目录搜）。找不到时**不会报错中断**，而是明确告诉你后果：

```
⚠️  找不到 redis-server.exe，跳过
→ 会话将回落进程内内存：重启会丢对话上下文（功能仍可用）
```

换机器/换版本时可以显式指定：

```powershell
$env:SLOPE_NGINX_HOME = 'D:\tools\nginx-1.24.0'
$env:SLOPE_REDIS_HOME = 'D:\tools\Redis-x64-5.0.14'
.\slope.ps1 start
```

## 页面上的四块

| 区域 | 数据来源 | 说明 |
|---|---|---|
| **告警事件** | Java `/api/alert-events` | 等级色标、实测/阈值对照、一键确认（确认人由后端从 JWT 取，前端传不了） |
| **监测点位** | Java `/api/sites` | 点位卡片；有告警的会标红 |
| **点位分布与告警态势** | 前端 SVG | 按经纬度投影，圈的**大小＝告警条数**，颜色＝最高等级 |
| **多因素趋势** | Java `/api/readings` | 位移/雨强/孔压/裂缝四条曲线可切换，近 72 小时 |
| **Agent 智能助手** | Python `/agent/chat` | 多因素综合评估；返回内容按 `**粗体**` 渲染 |
| **告警规则** | Java `/api/alert-rules` | **可直接改阈值和启停**，规则引擎下一轮即按新值判定 |

## 两个刻意的设计约束

### 1. 前端不做任何告警判定

页面**只呈现规则引擎算出来的结果**，自己不算等级、不判断"要不要报警"。

原因：安全场景里判定必须是确定性的、可复现的、可审计的（详见 `../alert_engine.py` 的说明）。前端一旦自己也判一次，就会出现"页面说橙色、后端说红色"这种最糟的情况。

### 2. Agent 长耗时在 nginx 层显式放宽

Agent 做一次多因素综合评估要连续调 5 个工具、跑 3 轮推理，实测 **40~95 秒**（瓶颈是 qwen3 每轮要写约 2000 token 的思考）。

nginx 默认 `proxy_read_timeout` 是 **60 秒**，不改会直接 504，而且现象是**"页面报超时，但后端其实还在跑"**，非常难排查。所以：

```nginx
location /agent/ {
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
    proxy_buffering    off;   # 不缓冲，长回答尽早开始回传
}
```

前端也配套给了"深度评估约需 1~2 分钟"的进度提示，避免用户以为卡死。

## 验证

两个脚本（放在 `new_rag/` 根目录）：

```powershell
python tools_verify_fullstack.py     # 只经 nginx 访问，验证统一入口真的通了
python tools_smoke_java.py 8080      # 直接验证 Java 业务域（含绕过 HTTP 查库复核）
```

覆盖：静态资源、`/api/` 反代、`/agent/` 反代（含路径重写）、鉴权未被绕过、端到端对话不超时。

## 已知限制

- 登录用户是内存硬编码的 `admin`，没有用户表、没有角色细分。
- 图表是手写 SVG，够用但没有缩放/悬停提示等交互。
- 点位分布是**按经纬度线性投影的示意图**，不是真实地图瓦片（部署环境没有外网，
  引不了 Leaflet 的 CDN）。
- `slope.ps1` 里的服务清单是**固定四个**（Redis/Java/Agent/nginx）。
  如果再加服务，需要同时改 `$Ports`、`Show-Status` 的 `$rows` 和启停函数。
