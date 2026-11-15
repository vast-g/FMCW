package com.slope.platform;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

/**
 * 边坡监测 Agent · Java 业务域服务入口。
 *
 * <p>这个服务只负责"业务数据"这一层：点位、设备、告警规则的增删改查，以及告警确认留痕。
 * Agent 推理、RAG 检索、告警规则的实际判定仍然留在 Python 侧（见 README「与 Python 侧的边界」）。
 *
 * <p>拆分的动机很简单：业务数据需要有确定的写入口和权限控制，而 Agent 代码迭代快、
 * 不适合承担这类职责。两者共用同一个 MySQL 库 {@code slope_agent}。
 */
@SpringBootApplication
// 让 @ConfigurationProperties 的 record 能被自动绑定（JwtProperties），不需要额外 @EnableConfigurationProperties
@ConfigurationPropertiesScan
public class SlopePlatformApplication {

    public static void main(String[] args) {
        SpringApplication.run(SlopePlatformApplication.class, args);
    }
}
