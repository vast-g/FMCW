package com.slope.platform.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.List;
import org.springframework.http.HttpHeaders;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.authentication.WebAuthenticationDetailsSource;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * 从 {@code Authorization: Bearer <token>} 还原登录身份。
 *
 * <p>关键点：token 无效时"不设置认证信息"就继续往下走，而不是直接写 401。
 * 因为要不要拦是这个请求说了算——{@code /api/health} 就不需要 token。
 * 放行到 Security 的授权规则后，未认证请求自然会被 AuthenticationEntryPoint 拦下并返回统一的 JSON 401。
 * 这样"哪些路径公开"只有一个地方定义（SecurityConfig），不会散落在过滤器里。
 */
@Component
public class JwtAuthFilter extends OncePerRequestFilter {

    private static final String BEARER_PREFIX = "Bearer ";

    private final JwtService jwtService;

    public JwtAuthFilter(JwtService jwtService) {
        this.jwtService = jwtService;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        String header = request.getHeader(HttpHeaders.AUTHORIZATION);
        if (header != null && header.startsWith(BEARER_PREFIX) && SecurityContextHolder.getContext().getAuthentication() == null) {
            JwtService.Parsed parsed = jwtService.parse(header.substring(BEARER_PREFIX.length()).trim());
            if (parsed != null) {
                var authentication = new UsernamePasswordAuthenticationToken(
                        parsed.username(), null,
                        List.of(new SimpleGrantedAuthority("ROLE_" + parsed.role())));
                authentication.setDetails(new WebAuthenticationDetailsSource().buildDetails(request));
                SecurityContextHolder.getContext().setAuthentication(authentication);
            }
        }
        filterChain.doFilter(request, response);
    }
}
