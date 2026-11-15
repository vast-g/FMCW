package com.slope.platform.dto;

import jakarta.validation.constraints.NotBlank;

/**
 * 登录请求。
 *
 * <p>字段名在 JSON 里是 snake_case（全局 Jackson 命名策略），和库里列名、Python 侧命名保持一致。
 *
 * @param username 用户名
 * @param password 明文密码（仅存在于传输与校验过程，服务端只保存 BCrypt 散列）
 */
public record LoginRequest(
        @NotBlank(message = "不能为空") String username,
        @NotBlank(message = "不能为空") String password) {
}
