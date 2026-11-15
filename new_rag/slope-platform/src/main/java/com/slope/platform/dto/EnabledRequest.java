package com.slope.platform.dto;

import jakarta.validation.constraints.NotNull;

/**
 * 只切换启停的请求体。
 *
 * <p>为什么不复用整个 AlertRuleRequest：现场最常见的操作是"临时停用这条规则"，
 * 如果必须提交完整对象，前端一次误传就可能顺手把阈值改回默认值。
 * 单独一个只含开关的接口，让"启停"这个动作不可能碰到其它字段。
 *
 * @param enabled true 启用 / false 停用
 */
public record EnabledRequest(@NotNull(message = "不能为空") Boolean enabled) {
}
