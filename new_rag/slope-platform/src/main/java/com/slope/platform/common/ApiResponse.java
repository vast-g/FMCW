package com.slope.platform.common;

/**
 * 统一响应结构 {@code {code, message, data}}。
 *
 * <p>为什么不用 HTTP 状态码直接表达结果：前端需要一套稳定的解析逻辑，
 * 业务码和 HTTP 码分开以后，将来要加"部分成功""需要人工复核"这类业务状态时不用改协议。
 *
 * <p>约定：{@code code == 0} 表示成功（HTTP 200）；失败时 {@code code} 与 HTTP 状态码保持一致
 * （400 参数错误 / 401 未认证 / 404 不存在 / 409 冲突 / 500 服务端异常），
 * 这样出问题时既能看到 HTTP 码也能看到业务码，排查不用来回对表。
 *
 * @param code    业务码，0 表示成功
 * @param message 提示信息，成功时为 "ok"
 * @param data    业务数据，失败时为 null
 */
public record ApiResponse<T>(int code, String message, T data) {

    public static <T> ApiResponse<T> ok(T data) {
        return new ApiResponse<>(0, "ok", data);
    }

    /** 无返回体的成功（删除、确认等操作）。 */
    public static ApiResponse<Void> ok() {
        return new ApiResponse<>(0, "ok", null);
    }

    public static <T> ApiResponse<T> error(int code, String message) {
        return new ApiResponse<>(code, message, null);
    }
}
