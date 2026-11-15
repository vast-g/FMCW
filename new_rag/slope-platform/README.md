# slope-platform · 边坡监测 Agent 的 Java 业务域后端

> 把「边坡监测 Agent」项目里的**业务数据部分**从 Python 单文件（`end_new.py`）里拆出来，
> 用 Spring Boot + JPA + Spring Security 单独实现的一套 REST 服务。
>
> **核心设计原则：业务数据（点位 / 设备 / 告警规则）的唯一写入口是 Java。**
> Python 侧只读这些表，继续负责 Agent 推理、RAG 检索和告警规则的实际判定。

---

## 1. 它在整个系统里的位置

```
                    ┌─────────────────────────────────────────┐
   现场/模拟器 ───▶  │  sensor_reading（读数，采集链路写入）      │
                    └─────────────────────────────────────────┘
                                     │ 读
                                     ▼
   ┌───────────────────────┐   ┌───────────────────────────────────┐
   │  Python 侧（Agent）    │   │  alert_engine.py 规则引擎（确定性） │
   │  end_new.py           │   │  读 alert_rule（阈值）             │
   │  · ReAct Agent 推理    │   │  写 alert_event（告警事件）        │
   │  · RAG 检索            │   └───────────────────────────────────┘
   │  · 工具调用/会话记忆     │                   │ 写
   └───────────────────────┘                   ▼
             │ 只读                       ┌───────────────┐
             │  site / alert_rule         │  alert_event  │
             ▼                            └───────────────┘
   ┌─────────────────────────────────────────────────────────────┐
   │  Java 侧（本服务 slope-platform）                             │
   │  · site / device / alert_rule 的增删改查 + 鉴权               │
   │  · 告警事件查询 + 确认留痕（acknowledged_by / _at）            │
   └─────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
                          MySQL: slope_agent（与 Python 共用同一个库）
```

**为什么值得这么拆**：`end_new.py` 有 14 万字符，Agent 逻辑和业务 CRUD 混在一起。
Agent 代码迭代快、需要频繁试错，而"谁能改告警阈值""谁确认了这条告警"属于必须稳定、必须留痕的
业务能力。两者的变更节奏和可靠性要求完全不同，放在一个文件里，任何一次 Agent 调试都可能
顺手影响业务数据。拆开之后，业务数据有了确定的写入口和权限边界。

---

## 2. 技术选型

| 组件 | 版本 | 选择原因 |
|---|---|---|
| **Spring Boot** | **3.5.16** | 见下方说明 |
| JDK | 25（本机 `jdk-25.0.4`） | 环境已就绪，`maven.compiler.release=25` |
| Spring Framework | 6.2.x（Boot 3.5.16 自带） | 官方支持 JDK 17~25 |
| Hibernate ORM | 6.6.53.Final | Boot 3.5.16 管理版本，实测能识别 MySQL 9.7.1 |
| MySQL 驱动 | `com.mysql:mysql-connector-j`（Boot 管理版本） | 与 MySQL 9.7.1 服务端兼容 |
| JWT | jjwt 0.12.6 | 无状态鉴权，服务端不存 session |
| 构建 | Maven 3.9.4 | 环境已就绪 |

### 为什么是 Spring Boot 3.5.16

1. **JDK 25 兼容是硬要求**，而 3.5.x 是 3.x 的最后一个维护分支，其内置的 Spring Framework
   6.2.x 官方支持到 JDK 25，实际构建（`release 25`）与启动都验证通过。
2. **比 Spring Boot 4.x 更适合本项目的现状**：Boot 4 做了模块拆分（`spring-boot-starter-web`
   在 4.0 中被 `spring-boot-starter-webmvc` 取代等），API 与 starter 名称都有变动。
   这个项目是给人看的求职项目，选一个资料最多、踩坑记录最全的版本，性价比更高。
3. **3.5.16 而不是 3.5.0**：分支末端的版本把该分支暴露出来的问题都修完了。
4. 官方支持结束时间：3.5.x 是最后一个 3.x 分支，之后升级 Boot 4 是一个独立的、可评估的动作，
   不应该和"拆分业务域"这件事绑在一起做。

> 实测结论：`mvn -q -DskipTests package` 与 `mvn -q test` 在 JDK 25.0.4 上均通过，
> 启动日志显示 `Hibernate ORM core version 6.6.53.Final`、`Database version: 9.7.1`。

### 为什么不用 Redis

需求里提到"引入 Java/Redis/MySQL 后端配件"，但**当前环境没有 Redis 服务**（也没有 Docker）。
把 `spring-boot-starter-data-redis` 加进来、只为了在配置文件里出现一行 Redis 配置，
会让服务多一个必然失败的依赖（连接池启动即报错、健康检查变红），属于典型的"为了简历好看而挖坑"。
JWT 是无状态的，鉴权本身不需要 Redis。因此这一版**刻意不引入 Redis**，见第 10 节。

---

## 3. 目录结构与模块划分

```
slope-platform/
├── pom.xml
├── README.md
└── src
    ├── main
    │   ├── java/com/slope/platform/
    │   │   ├── SlopePlatformApplication.java     启动类
    │   │   ├── common/                          跨层通用件
    │   │   │   ├── ApiResponse.java             统一响应 {code,message,data}
    │   │   │   ├── PageResult.java              统一分页体
    │   │   │   ├── BusinessException.java       可预期业务异常（带 HTTP 状态）
    │   │   │   └── GlobalExceptionHandler.java  全局异常 → 统一响应
    │   │   ├── config/
    │   │   │   └── SecurityConfig.java          安全过滤链 + 内存用户 + BCrypt
    │   │   ├── security/
    │   │   │   ├── JwtProperties.java           app.jwt.* 配置绑定
    │   │   │   ├── JwtService.java              签发 / 校验
    │   │   │   ├── JwtAuthFilter.java           从 Bearer token 还原身份
    │   │   │   └── RestAuthenticationEntryPoint.java  401 也返回统一结构
    │   │   ├── entity/                          JPA 实体（严格对齐既有表）
    │   │   │   ├── Site.java                    site
    │   │   │   ├── SensorReading.java           sensor_reading（只读，@Immutable）
    │   │   │   ├── AlertRule.java               alert_rule
    │   │   │   ├── AlertEvent.java              alert_event（业务列 updatable=false）
    │   │   │   ├── Device.java                  device（本项目新建）
    │   │   │   └── enums/{DeviceType,DeviceStatus}.java
    │   │   ├── repository/                      5 个 Spring Data 仓储
    │   │   ├── dto/                             请求/响应对象（record + 校验注解）
    │   │   ├── service/                         业务规则都在这层
    │   │   └── controller/                      REST 入口（只做参数→service→响应）
    │   └── resources/
    │       ├── application.yml
    │       └── schema.sql                       只建 device 表（幂等）
    └── test/java/com/slope/platform/
        ├── SlopePlatformApplicationTests.java   @SpringBootTest 集成测试
        └── security/JwtServiceTest.java         JWT 单元测试
```

分层约定：**Controller 不写业务判断，Service 不碰 HttpServletRequest，Repository 不写业务规则。**
业务规则（编号查重、外键前置校验、确认人来自登录身份）全部集中在 Service，方便将来
加定时任务或消息消费等其它入口时复用同一套规则。

---

## 4. 如何启动

### 前置条件

| 项目 | 要求 |
|---|---|
| JDK | 17+（本项目按 25 编译，`JAVA_HOME` 指向 `jdk-25.0.4`） |
| Maven | 3.6.3+（实测 3.9.4） |
| MySQL | 已存在库 `slope_agent`，账号 `root / Root@123456`，端口 3306 |
| 端口 | 8080 未被占用 |

### 构建

```bash
cd slope-platform
mvn -q -DskipTests package     # 产出 target/slope-platform-1.0.0.jar
mvn -q test                    # 跑测试（会真连本机 MySQL，只读、事务回滚）
```

### 启动 / 停止

```bash
java -jar target/slope-platform-1.0.0.jar
# 换端口： java -jar target/slope-platform-1.0.0.jar --server.port=18080
```

启动时 `schema.sql` 会幂等地建 `device` 表（`CREATE TABLE IF NOT EXISTS`），
**不会新建、修改或清空任何既有表**。

停止用 `Ctrl+C`，或按端口找进程：

```powershell
Stop-Process -Id (Get-NetTCPConnection -LocalPort 8080 -State Listen).OwningProcess
```

> 数据库连接串在 `application.yml` 里。生产部署应该把它改成环境变量注入
> （密码、JWT 密钥都不该进仓库）；本机为了开箱即跑写了默认值。

---

## 5. 接口清单

### 统一响应结构

```jsonc
{ "code": 0, "message": "ok", "data": { } }
```

* `code == 0` 表示成功，HTTP 状态码同时为 200；
* 失败时 `code` 与 HTTP 状态码一致：`400` 参数错误 / `401` 未认证 / `404` 不存在 /
  `409` 与现有数据冲突 / `500` 服务端异常，`data` 为 `null`。

### JSON 字段命名

全局 `spring.jackson.property-naming-strategy=SNAKE_CASE`，即请求体和响应体都是
`slope_group` / `site_id` / `updated_at` 这种写法。原因：库里的列名、Python 侧的
对象属性、以及需求里的查询参数（`site_id`、`slope_group`）本来就是这个风格，
三者统一之后前端和 Python 侧不需要来回换算字段名。

### 接口总表

| 方法 | 路径 | 说明 | 是否需要 token |
|---|---|---|---|
| POST | `/api/auth/login` | 用户名密码换 JWT | ❌ |
| GET | `/api/health` | 健康检查（真的查一次库） | ❌ |
| GET | `/api/sites` | 点位列表，`?slope_group=A` 可选过滤 | ✅ |
| GET | `/api/sites/{id}` | 点位详情 | ✅ |
| POST | `/api/sites` | 新建点位 | ✅ |
| PUT | `/api/sites/{id}` | 修改点位 | ✅ |
| DELETE | `/api/sites/{id}` | 删除点位（下面有设备时返回 409） | ✅ |
| GET | `/api/devices` | 设备列表，`?site_id=`、`?status=` 可选过滤 | ✅ |
| GET | `/api/devices/{id}` | 设备详情 | ✅ |
| POST | `/api/devices` | 新建设备 | ✅ |
| PUT | `/api/devices/{id}` | 修改设备 | ✅ |
| DELETE | `/api/devices/{id}` | 删除设备 | ✅ |
| GET | `/api/alert-rules` | 规则列表，`?metric=`、`?enabled=` 可选过滤 | ✅ |
| GET | `/api/alert-rules/{id}` | 规则详情 | ✅ |
| POST | `/api/alert-rules` | 新建规则 | ✅ |
| PUT | `/api/alert-rules/{id}` | **修改规则（含改阈值）** | ✅ |
| PUT | `/api/alert-rules/{id}/enabled` | **只切启停**，body `{"enabled": false}` | ✅ |
| DELETE | `/api/alert-rules/{id}` | 删除规则（历史告警事件保留） | ✅ |
| GET | `/api/alert-events` | 告警事件条件分页查询 | ✅ |
| GET | `/api/alert-events/{id}` | 告警事件详情 | ✅ |
| POST | `/api/alert-events/{id}/acknowledge` | **确认告警（留痕）** | ✅ |
| GET | `/api/readings` | 读数查询（附加，只读），`?site_id=&metric=&hours=&limit=` | ✅ |

`/api/readings` 不是需求点名的接口，是给前端趋势图补的只读查询，只有 GET、没有写入口。

### 各接口的参数

**`GET /api/alert-events` 查询参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `site_id` | int | 点位过滤 |
| `level` | string | `blue` / `yellow` / `orange` / `red` |
| `acknowledged` | bool | 是否已确认 |
| `from` / `to` | ISO 时间 | `2026-10-12T00:00:00`，闭区间 |
| `page` | int | 页码，**从 1 开始**，默认 1 |
| `size` | int | 每页条数，默认 20，服务端上限 200 |

响应 `data` 为 `{ "list": [...], "total": 21, "page": 1, "size": 20 }`。

**`GET /api/devices`**：`site_id`、`status`（`online` / `offline` / `fault`）。

**`POST /api/devices` 请求体示例**

```json
{
  "code": "D-RADAR-01", "name": "坡顶雷达", "site_id": 13,
  "type": "radar", "vendor": "某某科技", "model": "SL-100",
  "status": "online", "last_heartbeat": "2026-10-12T13:00:00",
  "installed_at": "2026-09-01T09:00:00"
}
```

`type` 可选值：`radar`(雷达) / `displacement_meter`(位移计) / `rain_gauge`(雨量计) /
`crack_meter`(裂缝计) / `pore_pressure_meter`(孔压计) / `camera`(摄像头) /
`gnss`(GNSS 接收机) / `other`(其它)。响应里会额外带 `type_label` / `status_label` 两个中文名，
前端不用自己维护映射表。

> 为什么库里存英文码：既有表就是这么做的——`alert_rule.level` 是 `yellow/orange/red`，
> `sensor_reading.quality` 是 `ok/suspect/bad`。同一个系统里两种风格混用，
> 后面做统计和告警联动会很难受。

**`POST /api/alert-events/{id}/acknowledge`**：无请求体。
`acknowledged_by` 取自 token 里的登录用户名，**不接受请求参数传入**——否则任何人都能
冒用别人的名字把告警"处理掉"，留痕就形同虚设。重复确认返回 409 并告知由谁、何时确认过。

### curl 示例

```bash
# 1. 登录
curl -s -X POST http://localhost:8080/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# 2. 带 token 访问（下面是 PowerShell 取 token 的方式）
$token = (Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/auth/login `
  -ContentType 'application/json' -Body '{"username":"admin","password":"admin123"}').data.token
$H = @{ Authorization = "Bearer $token" }

Invoke-RestMethod -Uri http://localhost:8080/api/sites -Headers $H
Invoke-RestMethod -Uri "http://localhost:8080/api/sites?slope_group=A" -Headers $H

# 3. 改告警阈值（业务侧最关键的写操作）
Invoke-RestMethod -Method Put -Uri http://localhost:8080/api/alert-rules/41 -Headers $H `
  -ContentType 'application/json' -Body '{"code":"R-RATE-01","name":"位移速率超限","metric":"displacement_rate","threshold":0.15,"level":"yellow","window_hours":12,"dedup_minutes":30,"enabled":true}'

# 4. 只停用，不碰阈值
Invoke-RestMethod -Method Put -Uri http://localhost:8080/api/alert-rules/41/enabled -Headers $H `
  -ContentType 'application/json' -Body '{"enabled":false}'

# 5. 告警事件：只看未确认的橙色告警
Invoke-RestMethod -Uri "http://localhost:8080/api/alert-events?acknowledged=false&level=orange&page=1&size=5" -Headers $H

# 6. 确认告警（留痕）
Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/alert-events/128/acknowledge -Headers $H
```

---

## 6. 鉴权方式

**JWT（HS256）+ Spring Security 无状态过滤链。**

1. `POST /api/auth/login` 用 `UserDetailsService` + `BCryptPasswordEncoder` 校验密码，
   成功则签发 JWT，`sub` 是用户名、`role` claim 是角色、`exp` 默认 120 分钟。
2. 客户端后续请求带 `Authorization: Bearer <token>`。
3. `JwtAuthFilter` 校验签名与有效期，通过则把身份放进 `SecurityContext`；
   校验失败**不设置认证信息**，继续走过滤链。
4. `SecurityConfig` 里只有两个白名单：`/api/auth/login`、`/api/health`
   （外加 `OPTIONS` 预检）。**其余一律 `authenticated()`**，没有偷懒的 `permitAll()`。
5. 未认证请求由 `RestAuthenticationEntryPoint` 返回和其它接口一致的
   `{"code":401,"message":"未认证或 token 无效，请先登录","data":null}`。

几个刻意的决定：

* **密钥长度在启动时校验**：HS256 要求 ≥32 字节，配置错了就让服务起不来，
  而不是等第一次登录才暴露。密钥可用环境变量 `SLOPE_JWT_SECRET` 覆盖。
* **登录失败不区分"用户不存在"和"密码错误"**，都返回同一句话，避免给出用户名探测接口。
* **当前用户是内存里硬编码的 `admin / admin123`**，密码仍走 BCrypt 编码。
  这是简化实现：Python 侧已经在同一个库里建了一张 `user` 表（给会话用），
  Java 侧再去动它会破坏"业务数据只有一个写入口"的约定。真实部署应该新建 Java 自己的
  用户表或对接 SSO —— 那时**只需要替换 `UserDetailsService` 这一个 Bean**，
  "取用户 → 校验密码 → 签发 token" 的链路一行都不用改。

---

## 7. 数据模型

### 复用既有表（不改任何列）

| 表 | 行数（本机） | Java 侧权限 |
|---|---|---|
| `site` | 4 | 增删改查 |
| `sensor_reading` | 1440 | **只读**（实体标 `@Immutable`） |
| `alert_rule` | 8 | 增删改查 + 启停 |
| `alert_event` | 21 | 查询 + 只能改确认三列 |

实体与表的对齐方式：**`ddl-auto: none`**，不依赖 Hibernate 的 `validate`（它的类型名比较
对 `float`/`datetime` 容易误报），而是由 `SlopePlatformApplicationTests` 真去查每一张表 ——
SQL 是 Hibernate 依据实体生成的，任何一处列名/类型/tinyint(1)→Boolean 映射对不上都会立刻报错。

`tinyint(1)` → Java `Boolean`，`datetime` → `LocalDateTime`，`text` → `String`（`columnDefinition = "text"`）。

### 新建表 `device`

唯一由本项目新建的表，DDL 在 `src/main/resources/schema.sql`：

```sql
CREATE TABLE IF NOT EXISTS device (
    id             INT         NOT NULL AUTO_INCREMENT,
    code           VARCHAR(48) NOT NULL,               -- 唯一
    name           VARCHAR(96) NOT NULL,
    site_id        INT         NOT NULL,               -- FK → site(id)
    type           VARCHAR(32) NOT NULL,               -- radar/位移计/雨量计/摄像头...
    vendor         VARCHAR(64) NULL,
    model          VARCHAR(64) NULL,
    status         VARCHAR(16) NOT NULL DEFAULT 'offline',  -- online/offline/fault
    last_heartbeat DATETIME    NULL,
    installed_at   DATETIME    NULL,
    created_at     DATETIME    NOT NULL,
    updated_at     DATETIME    NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_device_code (code),
    KEY idx_device_site (site_id),
    KEY idx_device_status (status),
    CONSTRAINT fk_device_site FOREIGN KEY (site_id) REFERENCES site (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;
```

设计说明：

* **为什么需要这张表**：原来"点位"和"读数"直接相连，传感器掉线时看数据的人分不清
  "坡体真没动"还是"设备已经死了"。设备台账把硬件状态单独管起来（`last_heartbeat`
  是在线判定的依据），这在安全监测里不是可选项。
* **为什么用 `schema.sql` 而不是 `ddl-auto: update`**：`ddl-auto` 会扫描库里所有表并尝试
  "对齐结构"，这个库同时被 Python 侧使用，自动改结构的风险不可控。`IF NOT EXISTS` 天然幂等，
  每次启动重复执行也安全。
* **外键为什么不加 `ON DELETE CASCADE`**：删点位时应该被拦下来并提示"还有设备在上面"，
  而不是把设备台账一起悄悄删掉——那会让资产账对不上。所以 `SiteService.delete` 里先查
  `countBySiteId`，有设备就返回 409 并带上数量。
* **`device` 只有 Java 写**，Python 侧目前不读也不写。

---

## 8. 与 Python 侧的边界（重要）

### 职责划分

| 能力 | 归属 | 说明 |
|---|---|---|
| 点位（`site`）增删改查 | **Java** | 有鉴权、有校验 |
| 设备台账（`device`）增删改查 | **Java** | 新建的表，只有 Java 用 |
| 告警规则（`alert_rule`）增删改查、改阈值、启停 | **Java** | `updated_at` 留痕 |
| 告警确认（`alert_event.acknowledged*`） | **Java** | 确认人来自登录身份 |
| 告警事件**查询** | **Java**（前端）/ Python（Agent 内部） | Python 用 SQLAlchemy 直接查 |
| 告警规则的**实际判定执行** | **Python** `alert_engine.py` | 读 `alert_rule` 的阈值做确定性判定 |
| `alert_event` **写入** | **Python** `alert_engine.py` | Java 不生产事件 |
| `sensor_reading` 写入 | 采集链路 / Python `sensor_monitor.py` 模拟器 | Java 只读 |
| Agent 推理、RAG 检索、会话记忆 | **Python** `end_new.py` | 与业务表无关 |

### 三条约定

1. **业务数据（`site` / `device` / `alert_rule`）的唯一写入口是 Java。**
   Python 侧不要 `INSERT`/`UPDATE`/`DELETE` 这三张表。目前 Python 侧的
   `slope_monitor.seed_if_empty()` 只在表为空时插入种子数据（不会覆盖已有数据），
   属于历史遗留的初始化逻辑；**正式部署时应改为由 Java 侧的接口初始化**。
2. **Python 只读 `site` / `alert_rule`。** 它读 `alert_rule` 的
   `threshold` / `window_hours` / `dedup_minutes` / `enabled` 做判定，
   所以 Java 改阈值后 Python 下一轮判定就会生效，不需要重启 Python 服务。
3. **`alert_event` 是"Python 写、Java 读 + 确认"。** 事件里的
   `threshold` 是**触发当时的快照**，即使规则后来被改了，历史依然可以还原
   "当时为什么报警"。因此 Java 侧的 `AlertEvent` 实体把 `value` / `threshold` /
   `message` / `evidence` 等业务列全部标成 `updatable = false` —— 生成 UPDATE 语句时
   根本不会带上这些列，从 ORM 层堵死"改历史"的可能。

### 共享同一个库的注意事项

* 两侧共用 `slope_agent`，**不要**用 `ddl-auto: update` / `create`（会互相改结构）。
* 时间统一按 `Asia/Shanghai` 处理：JDBC 串显式写了 `connectionTimeZone=Asia/Shanghai`，
  否则换一台机器部署可能整体偏移 8 小时 —— 告警时间错 8 小时是致命的。
* 库里还有 Python 自己的表（`user` / `conversation` / `message` / `memory` / `feedback` /
  `events` / `agent_trace` / `trace_run` / `trace_step`），Java 侧**完全不碰**。

---

## 9. 测试

```bash
mvn -q test
```

| 测试类 | 用例数 | 说明 |
|---|---|---|
| `security/JwtServiceTest` | 5 | 纯单元测试，不启动 Spring：签发/解析、篡改拒绝、异密钥拒绝、过期拒绝、短密钥启动即失败 |
| `SlopePlatformApplicationTests` | 4 | `@SpringBootTest` 真连 MySQL：上下文加载、五张表实体映射能真执行查询、既有数据完好、`device` 表可读写（事务回滚） |

**为什么测试直连真实 MySQL 而不是 H2**：这个服务的风险全在"Java 实体与其它语言共用的真实表"
之间——方言差异、`tinyint(1)`→`Boolean`、`datetime` 精度这些最容易出问题的地方，
换成 H2 恰好全被绕过去，测试就变成了自我安慰。
测试类是 `@Transactional` 的（默认回滚）且不修改任何既有业务数据，不会污染那 1440 行读数。

---

## 10. 验证记录（本机实测，2026-10-12）

环境：Windows / JDK 25.0.4 / Maven 3.9.4 / MySQL 9.7.1，库 `slope_agent`。

**构建**

```
> mvn -q clean package -DskipTests
=== CLEAN_PACKAGE_EXIT=0 ===
slope-platform-1.0.0.jar            60116499   ← 可执行 fat jar
slope-platform-1.0.0.jar.original      69489   ← repackage 前的原始 thin jar
```

**测试**：`mvn -q test` 退出码 0，9/9 通过。

```
Test set: com.slope.platform.security.JwtServiceTest
Tests run: 5, Failures: 0, Errors: 0, Skipped: 0

Test set: com.slope.platform.SlopePlatformApplicationTests
Tests run: 4, Failures: 0, Errors: 0, Skipped: 0
```

**启动**（`java -jar target/slope-platform-1.0.0.jar`，8080）

```
HHH000412: Hibernate ORM core version 6.6.53.Final
Database version: 9.7.1
Tomcat started on port 8080 (http) with context path '/'
Started SlopePlatformApplication in 3.968 seconds (process running for 4.323)
```

**接口冒烟测试**：26 个请求 + 23 条断言，全部通过（0 失败）。

| 用例 | 结果 | 用例 | 结果 |
|---|---|---|---|
| 健康检查（免鉴权） | 200 | 非法等级校验 | 400 |
| 无 token 访问 `/api/sites` | 401 | 告警事件分页 | 200 |
| 错误密码登录 | 401 | 告警事件多条件过滤 | 200 |
| 正确登录拿 token | 200 | 告警事件时间范围过滤 | 200 |
| 点位列表 / A 组过滤 | 200 | 确认告警（留痕） | 200 |
| 规则详情（改前/改后） | 200 | 重复确认 | 409 |
| 改阈值 0.1 → 0.15 | 200 | 新建设备 | 200 |
| 停用 / 启用规则 | 200 | 设备按 site_id / status 过滤 | 200 |
| 阈值还原为 0.1 | 200 | 非法设备类型 | 400 |
| 删有设备的点位（护栏） | 409 | 读数查询 | 200 |

关键返回片段：

```jsonc
// 改阈值：threshold 变了，updated_at 也刷新了（审计留痕）
{"code":0,"message":"ok","data":{"id":41,"code":"R-RATE-01","threshold":0.15,
 "enabled":true,"updated_at":"2026-10-12T13:36:33.1401833"}}

// 确认告警：acknowledged_by 来自 token 身份，不是请求参数
{"code":0,"message":"ok","data":{"id":131,"acknowledged":true,
 "acknowledged_by":"admin","acknowledged_at":"2026-10-12T13:37:54.6479932"}}

// 重复确认被拒，并把"谁确认的"告诉他
{"code":409,"message":"该告警已由 admin 于 2026-10-12T13:37:55 确认，不能重复确认","data":null}

// 点位下还有设备时不允许删除
{"code":409,"message":"点位 S-01 下还有 1 台设备，请先删除或转移这些设备","data":null}

// 设备：库里存小写码，同时给出中文名
{"code":0,"data":{"id":5,"code":"SMOKE-DEV-2235","type":"rain_gauge","status":"online",
 "type_label":"雨量计","status_label":"在线"}}
```

**数据安全核对**：测试前后逐表对比，既有数据零变化（`site` 4 / `sensor_reading` 1440 /
`alert_rule` 8 / `alert_event` 21，已确认数仍为基线 1 条；`R-RATE-01` 阈值已改回 0.1；
新建的 `device` 表为空）。冒烟测试中为验证"确认留痕"而写入的 2 条确认记录，
测完已回滚为未确认状态。

---

## 11. 当前未做的部分（诚实清单）

1. **没有引入 Redis**。环境里没有 Redis 服务（也没有 Docker），硬加依赖会让服务多一个
   必然连接失败的组件。JWT 是无状态的，鉴权不需要 Redis。将来如果要加，合适的落点是
   **告警抑制/去重缓存**和**刷新令牌黑名单**（目前去重窗口是 Python 侧 `dedup_minutes` 实现的）。
2. **用户是内存里硬编码的 `admin/admin123`，没有用户表、没有角色权限模型**。
   所有登录用户都是 `ROLE_ADMIN`，没有"值班员只能确认、管理员才能改阈值"这种区分。
   接真实用户体系时需要新建独立的用户表（或对接 SSO）并加 `@PreAuthorize`。
3. **没有刷新令牌 / 登出 / 令牌吊销**。token 有效期 120 分钟，过期只能重新登录；
   服务端不保存已签发 token，因此无法主动作废（无状态方案的固有取舍）。
4. **没有前端**，只有 REST 接口。
5. **没有告警事件的导出/统计接口**（按点位、按等级出报表这类需求没做）。
6. **`device` 与 `sensor_reading` 之间没有建立关联**。理想模型里读数应该能追溯到具体设备
   （`sensor_reading` 里加 `device_id`），但那需要改既有表结构——需求明确要求不动既有表，
   所以设备台账目前是独立的一张表，靠 `site_id` 与点位关联。
7. **没有分页/过滤的 `site`、`device` 列表**：这两个表数据量小（点位 4 条），
   直接返回全量；只有 `alert_event` 做了分页。
8. **没有接口文档（Swagger/OpenAPI）**，接口清单在本 README 里手工维护。
9. **没有 Dockerfile / CI**，环境里没有 Docker。
10. **没有做接口级的权限细分与限流**，登录接口没有失败次数限制。
11. **`alert_event` 的确认没有做"取消确认"**，重复确认返回 409 而不是覆盖——
    这是刻意的（保留第一确认人），但如果业务上需要撤销，得额外设计。
