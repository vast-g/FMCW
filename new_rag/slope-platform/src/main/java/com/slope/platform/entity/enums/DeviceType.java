package com.slope.platform.entity.enums;

import com.slope.platform.common.BusinessException;
import java.util.Arrays;

/**
 * 设备类型。
 *
 * <p>枚举常量是大写（Java 惯例），但**落库和出参用的是 {@link #code} 小写码**。
 * 原因：库里既有的编码都是小写——{@code alert_rule.level} 是 yellow/orange/red、
 * {@code sensor_reading.quality} 是 ok/suspect/bad。如果 device 表里出现 ONLINE / RAIN_GAUGE，
 * 同一个库里就有两套写法，将来写 SQL 做统计、或者 Python 侧要读这张表时都要额外记住这件事。
 *
 * <p>用枚举而不是裸字符串：可选值只有这些，写错了应该在入口就报 400，
 * 而不是等某天统计"雨量计数量"时才发现库里存着 "rain_gague"。
 */
public enum DeviceType {

    RADAR("radar", "雷达"),
    DISPLACEMENT_METER("displacement_meter", "位移计"),
    RAIN_GAUGE("rain_gauge", "雨量计"),
    CRACK_METER("crack_meter", "裂缝计"),
    PORE_PRESSURE_METER("pore_pressure_meter", "孔压计"),
    CAMERA("camera", "摄像头"),
    GNSS("gnss", "GNSS 接收机"),
    OTHER("other", "其它");

    private final String code;
    private final String label;

    DeviceType(String code, String label) {
        this.code = code;
        this.label = label;
    }

    /** 存库/出参用的小写码。 */
    public String getCode() {
        return code;
    }

    /** 给人看的中文名。 */
    public String getLabel() {
        return label;
    }

    /** 解析入参，非法值直接给出可选清单，方便调用方自查。大小写不敏感。 */
    public static DeviceType fromCode(String code) {
        if (code == null || code.isBlank()) {
            throw BusinessException.badRequest("设备类型 type 不能为空");
        }
        String normalized = code.trim();
        return Arrays.stream(values())
                .filter(t -> t.code.equalsIgnoreCase(normalized))
                .findFirst()
                .orElseThrow(() -> BusinessException.badRequest(
                        "不支持的设备类型：%s，可选值 %s".formatted(normalized, codes())));
    }

    /**
     * 展示用查表：库里若存在本项目不认识的历史值，返回 null 而不是抛异常。
     * 出参序列化不该因为一条脏数据整个请求失败。
     */
    public static String labelOf(String code) {
        if (code == null) {
            return null;
        }
        return Arrays.stream(values())
                .filter(t -> t.code.equalsIgnoreCase(code.trim()))
                .map(DeviceType::getLabel)
                .findFirst()
                .orElse(null);
    }

    private static String codes() {
        return Arrays.toString(Arrays.stream(values()).map(DeviceType::getCode).toArray());
    }
}
