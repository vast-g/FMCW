package com.slope.platform.common;

import jakarta.validation.ConstraintViolationException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

/**
 * 全局异常处理：让所有出口的响应结构保持一致，Controller 里就不用写 try/catch。
 *
 * <p>分三类处理：
 * <ol>
 *   <li>业务异常 —— 原样返回给用户（文案是在抛出处精心写的，例如"该点位下仍有 3 台设备"）；</li>
 *   <li>参数类异常 —— 统一 400，并把第一个字段错误带上，前端能直接定位到表单哪一项；</li>
 *   <li>数据库完整性异常 —— 转成 409 而不是 500。外键冲突、唯一键重复属于"请求与数据现状冲突"，
 *       不是服务端故障，给 500 会让前端以为是 bug 而不是提示用户；</li>
 *   <li>兜底 —— 记完整日志，对外只说"服务器内部错误"，不泄漏堆栈和 SQL。</li>
 * </ol>
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(BusinessException.class)
    public ResponseEntity<ApiResponse<Void>> handleBusiness(BusinessException ex) {
        return ResponseEntity.status(ex.getStatus())
                .body(ApiResponse.error(ex.getStatus(), ex.getMessage()));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiResponse<Void>> handleInvalidBody(MethodArgumentNotValidException ex) {
        String detail = ex.getBindingResult().getFieldErrors().stream()
                .findFirst()
                .map(e -> e.getField() + " " + e.getDefaultMessage())
                .orElse("请求参数不合法");
        return badRequest(detail);
    }

    @ExceptionHandler(ConstraintViolationException.class)
    public ResponseEntity<ApiResponse<Void>> handleConstraint(ConstraintViolationException ex) {
        String detail = ex.getConstraintViolations().stream()
                .findFirst()
                .map(v -> v.getPropertyPath() + " " + v.getMessage())
                .orElse("请求参数不合法");
        return badRequest(detail);
    }

    @ExceptionHandler({HttpMessageNotReadableException.class,
            MissingServletRequestParameterException.class,
            MethodArgumentTypeMismatchException.class})
    public ResponseEntity<ApiResponse<Void>> handleMalformedRequest(Exception ex) {
        return badRequest("请求参数格式错误：" + ex.getMessage());
    }

    @ExceptionHandler(DataIntegrityViolationException.class)
    public ResponseEntity<ApiResponse<Void>> handleIntegrity(DataIntegrityViolationException ex) {
        log.warn("数据完整性冲突: {}", ex.getMostSpecificCause().getMessage());
        return ResponseEntity.status(HttpStatus.CONFLICT)
                .body(ApiResponse.error(HttpStatus.CONFLICT.value(),
                        "操作与现有数据冲突（唯一编号重复，或该记录仍被其它数据引用）"));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiResponse<Void>> handleUnexpected(Exception ex) {
        log.error("未预期的服务端异常", ex);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(ApiResponse.error(HttpStatus.INTERNAL_SERVER_ERROR.value(), "服务器内部错误"));
    }

    private static ResponseEntity<ApiResponse<Void>> badRequest(String message) {
        return ResponseEntity.badRequest().body(ApiResponse.error(400, message));
    }
}
