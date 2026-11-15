package com.slope.platform.security;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * JWT 单元测试（不启动 Spring、不连数据库）。
 *
 * <p>鉴权是最不该"靠手工点一下试试"的部分：token 校验一旦有漏洞，
 * 没有登录也能改告警阈值。这里把签发/解析/篡改/过期四条路径都钉住，
 * 以后换 jjwt 版本或调整密钥配置时能立刻发现回归。
 */
class JwtServiceTest {

    private static final String SECRET = "unit-test-secret-key-with-enough-length-2026";

    @Test
    @DisplayName("签发的 token 能被解析出用户名和角色")
    void issueAndParse() {
        JwtService jwtService = new JwtService(new JwtProperties(SECRET, 60));

        String token = jwtService.issue("admin", "ADMIN");
        JwtService.Parsed parsed = jwtService.parse(token);

        assertThat(parsed).isNotNull();
        assertThat(parsed.username()).isEqualTo("admin");
        assertThat(parsed.role()).isEqualTo("ADMIN");
    }

    @Test
    @DisplayName("被篡改的 token 解析失败（返回 null 而不是抛异常）")
    void tamperedTokenRejected() {
        JwtService jwtService = new JwtService(new JwtProperties(SECRET, 60));
        String token = jwtService.issue("admin", "ADMIN");

        // 改掉签名部分的最后一个字符，模拟伪造
        String tampered = token.substring(0, token.length() - 1)
                + (token.endsWith("A") ? "B" : "A");

        assertThat(jwtService.parse(tampered)).isNull();
        assertThat(jwtService.parse("not-a-jwt")).isNull();
    }

    @Test
    @DisplayName("换一个密钥签发的 token 不能被本服务接受")
    void tokenFromAnotherSecretRejected() {
        JwtService other = new JwtService(new JwtProperties("another-secret-key-also-long-enough-2026", 60));
        JwtService mine = new JwtService(new JwtProperties(SECRET, 60));

        assertThat(mine.parse(other.issue("admin", "ADMIN"))).isNull();
    }

    @Test
    @DisplayName("过期的 token 解析失败")
    void expiredTokenRejected() {
        // 负数有效期 → 过期时间在过去，等价于"签出来就已经过期"
        JwtService expired = new JwtService(new JwtProperties(SECRET, -1));

        assertThat(expired.parse(expired.issue("admin", "ADMIN"))).isNull();
    }

    @Test
    @DisplayName("密钥不足 32 字节时构造即失败，把配置错误暴露在启动阶段")
    void shortSecretRejected() {
        assertThatThrownBy(() -> new JwtService(new JwtProperties("too-short", 60)))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("app.jwt.secret");
    }
}
