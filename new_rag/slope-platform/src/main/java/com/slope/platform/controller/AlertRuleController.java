package com.slope.platform.controller;

import com.slope.platform.common.ApiResponse;
import com.slope.platform.dto.AlertRuleRequest;
import com.slope.platform.dto.EnabledRequest;
import com.slope.platform.entity.AlertRule;
import com.slope.platform.service.AlertRuleService;
import jakarta.validation.Valid;
import java.util.List;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 告警规则接口。
 *
 * <p>两个写入口是刻意分开的：
 * <ul>
 *   <li>{@code PUT /api/alert-rules/{id}} —— 全量修改（改阈值、窗口、等级）；</li>
 *   <li>{@code PUT /api/alert-rules/{id}/enabled} —— 只切启停，不可能误改阈值。</li>
 * </ul>
 * 现场"临时停用一条规则"是高频操作，值得给它一个改不动别的字段的入口。
 */
@RestController
@RequestMapping("/api/alert-rules")
public class AlertRuleController {

    private final AlertRuleService alertRuleService;

    public AlertRuleController(AlertRuleService alertRuleService) {
        this.alertRuleService = alertRuleService;
    }

    @GetMapping
    public ApiResponse<List<AlertRule>> list(
            @RequestParam(name = "metric", required = false) String metric,
            @RequestParam(name = "enabled", required = false) Boolean enabled) {
        return ApiResponse.ok(alertRuleService.list(metric, enabled));
    }

    @GetMapping("/{id}")
    public ApiResponse<AlertRule> get(@PathVariable Integer id) {
        return ApiResponse.ok(alertRuleService.get(id));
    }

    @PostMapping
    public ApiResponse<AlertRule> create(@Valid @RequestBody AlertRuleRequest request) {
        return ApiResponse.ok(alertRuleService.create(request));
    }

    @PutMapping("/{id}")
    public ApiResponse<AlertRule> update(@PathVariable Integer id,
                                         @Valid @RequestBody AlertRuleRequest request) {
        return ApiResponse.ok(alertRuleService.update(id, request));
    }

    /** 改阈值 / 启停后的返回值里带 updated_at，调用方可以立刻确认留痕时间。 */
    @PutMapping("/{id}/enabled")
    public ApiResponse<AlertRule> setEnabled(@PathVariable Integer id,
                                             @Valid @RequestBody EnabledRequest request) {
        return ApiResponse.ok(alertRuleService.setEnabled(id, request.enabled()));
    }

    @DeleteMapping("/{id}")
    public ApiResponse<Void> delete(@PathVariable Integer id) {
        alertRuleService.delete(id);
        return ApiResponse.ok();
    }
}
