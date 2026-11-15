package com.slope.platform.controller;

import com.slope.platform.common.ApiResponse;
import com.slope.platform.repository.SiteRepository;
import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 健康检查。
 *
 * <p>刻意"真的碰一下数据库"而不是直接 return up：
 * 进程活着但数据库连接池被打满 / 库被重启，是这类服务最常见的故障形态，
 * 一个不查库的健康检查在这种时候依然报 up，等于没有。
 *
 * <p>同样刻意不返回堆栈或连接串——健康检查常常是公开的。
 */
@RestController
public class HealthController {

    private final SiteRepository siteRepository;

    public HealthController(SiteRepository siteRepository) {
        this.siteRepository = siteRepository;
    }

    @GetMapping("/api/health")
    public ApiResponse<Map<String, Object>> health() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("application", "slope-platform");
        body.put("time", LocalDateTime.now().toString());
        try {
            body.put("database", "up");
            body.put("site_count", siteRepository.count());
            body.put("status", "UP");
        } catch (RuntimeException ex) {
            // 这里不抛异常：健康检查本身必须永远能返回结果，否则调用方分不清
            // "服务挂了"和"服务活着但依赖挂了"
            body.put("database", "down");
            body.put("status", "DEGRADED");
        }
        return ApiResponse.ok(body);
    }
}
