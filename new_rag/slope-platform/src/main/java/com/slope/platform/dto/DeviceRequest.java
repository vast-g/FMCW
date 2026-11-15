package com.slope.platform.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import java.time.LocalDateTime;

/**
 * 设备新增/修改请求。
 *
 * @param code          设备编号，全局唯一
 * @param name          设备名称
 * @param siteId        所属点位 id（必须已存在）
 * @param type          设备类型码：radar/displacement_meter/rain_gauge/crack_meter/
 *                      pore_pressure_meter/camera/gnss/other
 * @param vendor        厂商
 * @param model         型号
 * @param status        状态码：online/offline/fault，缺省 offline
 * @param lastHeartbeat 最近心跳时间
 * @param installedAt   安装时间
 */
public record DeviceRequest(
        @NotBlank(message = "不能为空") @Size(max = 48, message = "长度不能超过 48") String code,
        @NotBlank(message = "不能为空") @Size(max = 96, message = "长度不能超过 96") String name,
        @NotNull(message = "不能为空") Integer siteId,
        @NotBlank(message = "不能为空") @Size(max = 32, message = "长度不能超过 32") String type,
        @Size(max = 64, message = "长度不能超过 64") String vendor,
        @Size(max = 64, message = "长度不能超过 64") String model,
        @Size(max = 16, message = "长度不能超过 16") String status,
        LocalDateTime lastHeartbeat,
        LocalDateTime installedAt) {
}
