package com.slope.platform.common;

/**
 * 可预期的业务异常。
 *
 * <p>与 RuntimeException 的区别在语义：这类异常的信息是"给用户看的"，
 * 全局异常处理器会原样返回 {@code message}；其它未捕获异常统一返回
 * "服务器内部错误"，不把堆栈/SQL 细节暴露给客户端（那既是安全问题，也帮不到使用者）。
 *
 * <p>{@code status} 直接当作 HTTP 状态码用：400 参数/状态不合法、404 资源不存在、
 * 409 与当前数据冲突（例如点位下还有设备、告警已被别人确认）。
 */
public class BusinessException extends RuntimeException {

    private final int status;

    public BusinessException(int status, String message) {
        super(message);
        this.status = status;
    }

    public int getStatus() {
        return status;
    }

    public static BusinessException notFound(String message) {
        return new BusinessException(404, message);
    }

    public static BusinessException badRequest(String message) {
        return new BusinessException(400, message);
    }

    public static BusinessException conflict(String message) {
        return new BusinessException(409, message);
    }
}
