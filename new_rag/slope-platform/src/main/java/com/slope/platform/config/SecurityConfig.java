package com.slope.platform.config;

import com.slope.platform.security.JwtAuthFilter;
import com.slope.platform.security.RestAuthenticationEntryPoint;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.userdetails.User;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.provisioning.InMemoryUserDetailsManager;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;

/**
 * Spring Security 配置。
 *
 * <p>本项目的安全目标很具体：<b>业务数据的写操作必须登录后才能做</b>。
 * 告警阈值是安全参数，谁都能改等于没有告警，所以这里刻意不用
 * {@code anyRequest().permitAll()} 那种"挂个 Spring Security 好看"的写法：
 * 白名单只有登录和健康检查两个接口，其余一律要求认证。
 */
@Configuration
@EnableWebSecurity
public class SecurityConfig {

    /** 免鉴权的路径：登录本身当然不能要求先登录；健康检查要留给探针/运维。 */
    private static final String[] PUBLIC_PATHS = {"/api/auth/login", "/api/health"};

    @Bean
    public SecurityFilterChain apiSecurityFilterChain(HttpSecurity http,
                                                      JwtAuthFilter jwtAuthFilter,
                                                      RestAuthenticationEntryPoint entryPoint) throws Exception {
        http
                // 无状态 + JWT：不存在 Cookie 会话，CSRF 攻击面随之消失，因此可以安全关闭 CSRF
                .csrf(AbstractHttpConfigurer::disable)
                .httpBasic(AbstractHttpConfigurer::disable)
                .formLogin(AbstractHttpConfigurer::disable)
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                // 跨域预检请求不带 token，必须放行，否则浏览器直接报 CORS 错误
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers(HttpMethod.OPTIONS, "/**").permitAll()
                        .requestMatchers(PUBLIC_PATHS).permitAll()
                        .anyRequest().authenticated())
                .exceptionHandling(handling -> handling.authenticationEntryPoint(entryPoint))
                .addFilterBefore(jwtAuthFilter, UsernamePasswordAuthenticationFilter.class);
        return http.build();
    }

    /** BCrypt 而非明文/MD5：即使将来换成真实用户表，这条路径也不用重写。 */
    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    /**
     * 演示用的内存用户：admin / admin123。
     *
     * <p>为什么先不做用户表：Python 侧已经在同一个库里建了一张 {@code user} 表（给会话用），
     * Java 侧再去动它就会破坏"业务数据只有一个写入口"的约定。等接入统一认证时，
     * 应该新建 Java 自己的用户表（或对接 SSO），而不是复用 Agent 侧那张。
     *
     * <p>密码仍然走 BCrypt 编码后存在内存里，是为了让"取用户 → 校验密码 → 签发 token"
     * 这条链路和真实实现完全一致，将来只替换 UserDetailsService 这一个 Bean。
     */
    @Bean
    public UserDetailsService userDetailsService(PasswordEncoder passwordEncoder) {
        UserDetails admin = User.withUsername("admin")
                .password(passwordEncoder.encode("admin123"))
                .roles("ADMIN")
                .build();
        return new InMemoryUserDetailsManager(admin);
    }
}
