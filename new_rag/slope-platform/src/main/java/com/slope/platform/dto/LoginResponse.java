package com.slope.platform.dto;

/**
 * 登录结果。
 *
 * <p>把有效期一起返回，前端可以在过期前主动续期，而不是等接口突然 401 再弹登录框。
 *
 * @param token             JWT
 * @param tokenType         固定 Bearer，放在响应里让前端不用写死
 * @param expiresInMinutes 有效期（分钟）
 * @param username          登录名，用于界面展示当前值班人
 */
public record LoginResponse(String token, String tokenType, long expiresInMinutes, String username) {
}
