package com.slope.platform.common;

import java.util.List;
import java.util.function.Function;
import org.springframework.data.domain.Page;

/**
 * 分页返回体。
 *
 * <p>不让 Spring 的 {@code Page} 直接出参：它序列化出来带 {@code pageable}/{@code sort}/
 * {@code first}/{@code last} 一大堆字段，前端只关心"共几条、当前第几页、这页的数据"。
 * 这里做一层收敛，接口契约就固定下来了。
 *
 * @param list  当前页数据
 * @param total 总条数
 * @param page  当前页码（从 1 开始，和前端习惯一致）
 * @param size  每页条数
 */
public record PageResult<T>(List<T> list, long total, int page, int size) {

    /** Spring 的 Page 是 0 基的，接口层统一转成 1 基，避免前端两套页码逻辑。 */
    public static <T> PageResult<T> of(Page<T> page) {
        return new PageResult<>(page.getContent(), page.getTotalElements(),
                page.getNumber() + 1, page.getSize());
    }

    /** 需要换 DTO 时用这个，避免在 Controller 里再手工拼一次。 */
    public static <S, T> PageResult<T> of(Page<S> page, Function<S, T> mapper) {
        return new PageResult<>(page.getContent().stream().map(mapper).toList(),
                page.getTotalElements(), page.getNumber() + 1, page.getSize());
    }
}
