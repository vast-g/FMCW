package com.slope.platform.controller;

import com.slope.platform.common.ApiResponse;
import com.slope.platform.common.PageResult;
import com.slope.platform.entity.AlertEvent;
import com.slope.platform.service.AlertEventService;
import java.time.LocalDateTime;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 告警事件接口：条件分页查询 + 确认。
 */
@RestController
@RequestMapping("/api/alert-events")
public class AlertEventController {

    private final AlertEventService alertEventService;

    public AlertEventController(AlertEventService alertEventService) {
        this.alertEventService = alertEventService;
    }

    /**
     * 条件分页查询。
     *
     * @param acknowledged 是否已确认；不传表示不限
     * @param from         起始时间，ISO 格式（2026-10-01T00:00:00）
     * @param page         页码，从 1 开始
     * @param size         每页条数，上限 200（防止前端一次拖走整张表）
     */
    @GetMapping
    public ApiResponse<PageResult<AlertEvent>> list(
            @RequestParam(name = "site_id", required = false) Integer siteId,
            @RequestParam(name = "level", required = false) String level,
            @RequestParam(name = "acknowledged", required = false) Boolean acknowledged,
            @RequestParam(name = "from", required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime from,
            @RequestParam(name = "to", required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime to,
            @RequestParam(name = "page", defaultValue = "1") int page,
            @RequestParam(name = "size", defaultValue = "20") int size) {
        return ApiResponse.ok(alertEventService.search(siteId, level, acknowledged, from, to, page, size));
    }

    @GetMapping("/{id}")
    public ApiResponse<AlertEvent> get(@PathVariable Integer id) {
        return ApiResponse.ok(alertEventService.get(id));
    }

    /**
     * 确认告警（留痕）。
     *
     * <p><b>确认人取自 {@link Authentication}，也就是 JWT 里的登录身份，而不是请求参数。</b>
     * 这是这一组接口里最重要的一行：如果 acknowledged_by 由前端传，
     * 任何人都能冒用别人的名字把告警"处理掉"，留痕就失去了意义。
     */
    @PostMapping("/{id}/acknowledge")
    public ApiResponse<AlertEvent> acknowledge(@PathVariable Integer id, Authentication authentication) {
        String operator = authentication == null ? "unknown" : authentication.getName();
        return ApiResponse.ok(alertEventService.acknowledge(id, operator));
    }
}
