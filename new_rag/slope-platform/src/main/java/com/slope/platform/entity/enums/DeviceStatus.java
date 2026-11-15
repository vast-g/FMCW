package com.slope.platform.entity.enums;

import com.slope.platform.common.BusinessException;
import java.util.Arrays;

/**
 * 设备状态。
 *
 * <p>只区分三种：在线 / 离线 / 故障。
 * "故障"和"离线"必须分开——离线多半是通信或供电问题，故障是设备本身坏了，
 * 两者的处置动作完全不同（重启网关 vs 派人上坡换设备），合并成一个"不可用"会让运维没法排优先级。
 *
 * <p>落库用 {@link #code} 小写码，与库里既有的 {@code alert_rule.level}、{@code sensor_reading.quality}
 * 保持同一风格。
 */
public enum DeviceStatus {

    ONLINE("online", "在线"),
    OFFLINE("offline", "离线"),
    FAULT("fault", "故障");

    private final String code;
    private final String label;

    DeviceStatus(String code, String label) {
        this.code = code;
        this.label = label;
    }

    public String getCode() {
        return code;
    }

    public String getLabel() {
        return label;
    }

    public static DeviceStatus fromCode(String code) {
        if (code == null || code.isBlank()) {
            throw BusinessException.badRequest("设备状态 status 不能为空");
        }
        String normalized = code.trim();
        return Arrays.stream(values())
                .filter(s -> s.code.equalsIgnoreCase(normalized))
                .findFirst()
                .orElseThrow(() -> BusinessException.badRequest(
                        "不支持的设备状态：%s，可选值 %s".formatted(normalized, codes())));
    }

    /** 展示用查表：遇到未知的历史值返回 null，不抛异常。 */
    public static String labelOf(String code) {
        if (code == null) {
            return null;
        }
        return Arrays.stream(values())
                .filter(s -> s.code.equalsIgnoreCase(code.trim()))
                .map(DeviceStatus::getLabel)
                .findFirst()
                .orElse(null);
    }

    private static String codes() {
        return Arrays.toString(Arrays.stream(values()).map(DeviceStatus::getCode).toArray());
    }
}
