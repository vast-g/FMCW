package com.slope.platform.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.slope.platform.common.ApiResponse;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.security.core.AuthenticationException;
import org.springframework.security.web.AuthenticationEntryPoint;
import org.springframework.stereotype.Component;

/**
 * 未认证请求的响应体定制。
 *
 * <p>Spring Security 默认返回一个空 body 的 401（或跳转登录页），前端拿不到统一结构的提示。
 * 这里改成和其它接口一致的 {@code {code, message, data}}，前端一套解析逻辑到底。
 */
@Component
public class RestAuthenticationEntryPoint implements AuthenticationEntryPoint {

    private final ObjectMapper objectMapper;

    public RestAuthenticationEntryPoint(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    @Override
    public void commence(HttpServletRequest request, HttpServletResponse response,
                         AuthenticationException authException) throws IOException {
        response.setStatus(HttpStatus.UNAUTHORIZED.value());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setCharacterEncoding("UTF-8");
        objectMapper.writeValue(response.getWriter(),
                ApiResponse.error(HttpStatus.UNAUTHORIZED.value(), "未认证或 token 无效，请先登录"));
    }
}
