package com.slope.platform.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * 点位新增/修改请求。
 *
 * <p>经纬度、描述可以为空（现场录入常常先建点位后补坐标），所以给默认值而不是强制必填；
 * 编号和名称是必填，因为它们是人和程序定位这个点位的唯一手段。
 *
 * @param code       点位编号，如 S-01
 * @param name       点位名称
 * @param description 补充说明
 * @param lat        纬度，缺省 0
 * @param lng        经度，缺省 0
 * @param slopeGroup 所属边坡分组
 * @param isPrimary  是否主监测点，缺省 false
 */
public record SiteRequest(
        @NotBlank(message = "不能为空") @Size(max = 32, message = "长度不能超过 32") String code,
        @NotBlank(message = "不能为空") @Size(max = 64, message = "长度不能超过 64") String name,
        String description,
        Float lat,
        Float lng,
        @NotBlank(message = "不能为空") @Size(max = 32, message = "长度不能超过 32") String slopeGroup,
        Boolean isPrimary) {
}
