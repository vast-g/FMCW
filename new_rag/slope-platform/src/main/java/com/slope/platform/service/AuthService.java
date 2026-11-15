package com.slope.platform.service;

import com.slope.platform.common.BusinessException;
import com.slope.platform.dto.LoginRequest;
import com.slope.platform.dto.LoginResponse;
import com.slope.platform.security.JwtService;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

/**
 * 登录服务。
 *
 * <p>用 {@link UserDetailsService} + {@link PasswordEncoder} 而不是"if (admin.equals(user) && ...)"：
 * 现在用户是内存里的一个 admin，但取用户、比对散列、签发 token 这条链路和真实系统一模一样。
 * 将来接数据库用户表（或 SSO）只需要换掉 UserDetailsService 这个 Bean，登录逻辑一行不用改。
 */
@Service
public class AuthService {

    private final UserDetailsService userDetailsService;
    private final PasswordEncoder passwordEncoder;
    private final JwtService jwtService;

    public AuthService(UserDetailsService userDetailsService,
                       PasswordEncoder passwordEncoder,
                       JwtService jwtService) {
        this.userDetailsService = userDetailsService;
        this.passwordEncoder = passwordEncoder;
        this.jwtService = jwtService;
    }

    public LoginResponse login(LoginRequest request) {
        UserDetails user;
        try {
            user = userDetailsService.loadUserByUsername(request.username());
        } catch (UsernameNotFoundException ex) {
            // 用户不存在和密码错误返回同一句话：否则等于给攻击者一个"哪些用户名有效"的探测接口
            throw new BusinessException(401, "用户名或密码错误");
        }
        if (!passwordEncoder.matches(request.password(), user.getPassword())) {
            throw new BusinessException(401, "用户名或密码错误");
        }
        String role = user.getAuthorities().stream()
                .findFirst()
                .map(a -> a.getAuthority().replace("ROLE_", ""))
                .orElse("ADMIN");
        return new LoginResponse(jwtService.issue(user.getUsername(), role),
                "Bearer", jwtService.expireMinutes(), user.getUsername());
    }
}
