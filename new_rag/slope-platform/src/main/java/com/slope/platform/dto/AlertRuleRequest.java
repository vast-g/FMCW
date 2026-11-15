package com.slope.platform.dto;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

/**
 * 告警规则新增/修改请求。
 *
 * <p>这里是整个服务"最危险"的入参——阈值直接决定现场会不会报警。
 * 所以每个字段都有边界：window_hours/dedup_minutes 不允许为负（负数会让判定逻辑失效），
 * level 必须是已有等级之一（见 AlertRuleService）。
 *
 * @param code         规则编码，唯一，如 R-RATE-01
 * @param name         规则名称
 * @param metric       作用指标，需与 sensor_reading.metric 对应
 * @param threshold    阈值
 * @param level        触发等级：blue/yellow/orange/red
 * @param windowHours  观察窗口（小时）
 * @param dedupMinutes 抑制窗口（分钟）
 * @param enabled      是否启用，缺省 true
 * @param description  规则说明
 */
public record AlertRuleRequest(
        @NotBlank(message = "不能为空") @Size(max = 48, message = "长度不能超过 48") String code,
        @NotBlank(message = "不能为空") @Size(max = 96, message = "长度不能超过 96") String name,
        @NotBlank(message = "不能为空") @Size(max = 32, message = "长度不能超过 32") String metric,
        @NotNull(message = "不能为空") Float threshold,
        @NotBlank(message = "不能为空") @Size(max = 12, message = "长度不能超过 12") String level,
        @NotNull(message = "不能为空") @Min(value = 1, message = "至少为 1 小时") Integer windowHours,
        @NotNull(message = "不能为空") @Min(value = 0, message = "不能为负数") Integer dedupMinutes,
        Boolean enabled,
        String description) {
}
