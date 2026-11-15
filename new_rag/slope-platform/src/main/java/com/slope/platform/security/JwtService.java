package com.slope.platform.security;

import io.jsonwebtoken.JwtException;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Date;
import javax.crypto.SecretKey;
import org.springframework.stereotype.Service;

/**
 * JWT 签发与校验。
 *
 * <p>选 JWT 而不是 session 的原因：这个服务只做业务读写，无状态以后可以随便多开实例，
 * 不用引 Redis 做 session 共享（当前环境也没有 Redis 服务）。
 *
 * <p>密钥长度在校验阶段就检查：HS256 密钥不足 32 字节时 jjwt 会抛异常，
 * 与其等第一次登录才发现，不如启动即失败——配置错误要在部署时暴露，而不是在生产流量里暴露。
 */
@Service
public class JwtService {

    private static final String CLAIM_ROLE = "role";
    private static final int MIN_SECRET_BYTES = 32;

    private final SecretKey key;
    private final long expireMinutes;

    public JwtService(JwtProperties properties) {
        String secret = properties.secret() == null ? "" : properties.secret();
        byte[] raw = secret.getBytes(StandardCharsets.UTF_8);
        if (raw.length < MIN_SECRET_BYTES) {
            throw new IllegalStateException(
                    "app.jwt.secret 至少需要 " + MIN_SECRET_BYTES + " 字节（HS256 要求），当前 " + raw.length + " 字节");
        }
        this.key = Keys.hmacShaKeyFor(raw);
        this.expireMinutes = properties.expireMinutes();
    }

    /** 签发 token。把角色放进 claim，后续要加权限只需读取它，不用改协议。 */
    public String issue(String username, String role) {
        Instant now = Instant.now();
        return Jwts.builder()
                .subject(username)
                .claim(CLAIM_ROLE, role)
                .issuedAt(Date.from(now))
                .expiration(Date.from(now.plusSeconds(expireMinutes * 60)))
                .signWith(key)
                .compact();
    }

    /**
     * 校验签名和有效期，返回解析结果；任何问题（过期、篡改、格式错）统一返回 null。
     *
     * <p>不区分"为什么失败"是刻意的：对客户端而言都是"需要重新登录"，
     * 而在日志里区分才有意义，所以异常在此收敛，由过滤器决定怎么响应。
     */
    public Parsed parse(String token) {
        try {
            var claims = Jwts.parser()
                    .verifyWith(key)
                    .build()
                    .parseSignedClaims(token)
                    .getPayload();
            String role = claims.get(CLAIM_ROLE, String.class);
            return new Parsed(claims.getSubject(), role == null ? "ADMIN" : role);
        } catch (JwtException | IllegalArgumentException ex) {
            return null;
        }
    }

    public long expireMinutes() {
        return expireMinutes;
    }

    /** token 里我们关心的两个字段。 */
    public record Parsed(String username, String role) {
    }
}
