package com.slope.platform.security;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * JWT 配置（对应 application.yml 里的 app.jwt.*）。
 *
 * <p>用 record + 构造器绑定，配置对象天然不可变，不会被别处改掉。
 * secret 支持用环境变量 {@code SLOPE_JWT_SECRET} 覆盖：密钥不该硬编码在仓库里，
 * 这里给默认值只是为了本机开箱即跑。
 *
 * @param secret        签名密钥，HS256 要求至少 32 字节
 * @param expireMinutes token 有效期（分钟）
 */
@ConfigurationProperties(prefix = "app.jwt")
public record JwtProperties(String secret, long expireMinutes) {
}
