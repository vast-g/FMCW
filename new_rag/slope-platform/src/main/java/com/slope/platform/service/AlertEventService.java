package com.slope.platform.service;

import com.slope.platform.common.BusinessException;
import com.slope.platform.common.PageResult;
import com.slope.platform.entity.AlertEvent;
import com.slope.platform.repository.AlertEventRepository;
import jakarta.persistence.criteria.Predicate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 告警事件业务：查询 + 确认。
 *
 * <p>事件本身由 Python 的规则引擎写入，Java 不生产事件，只负责"给人看"和"给人确认"。
 */
@Service
@Transactional(readOnly = true)
public class AlertEventService {

    private static final int MAX_PAGE_SIZE = 200;

    private final AlertEventRepository alertEventRepository;

    public AlertEventService(AlertEventRepository alertEventRepository) {
        this.alertEventRepository = alertEventRepository;
    }

    /**
     * 条件分页查询。
     *
     * <p>用 Specification 按需拼条件而不是写 {@code (:from is null or created_at >= :from)}：
     * 后者会让 MySQL 面对一个类型不确定的可空时间参数，且条件恒真时用不上 created_at 索引，
     * 告警事件表迟早会长到需要索引的程度。
     *
     * @param page 页码，从 1 开始（接口层统一 1 基，Spring 内部是 0 基，在这里换算）
     */
    public PageResult<AlertEvent> search(Integer siteId, String level, Boolean acknowledged,
                                         LocalDateTime from, LocalDateTime to,
                                         int page, int size) {
        Specification<AlertEvent> spec = (root, query, cb) -> {
            List<Predicate> predicates = new ArrayList<>();
            if (siteId != null) {
                predicates.add(cb.equal(root.get("siteId"), siteId));
            }
            if (level != null && !level.isBlank()) {
                predicates.add(cb.equal(root.get("level"), level.trim().toLowerCase()));
            }
            if (acknowledged != null) {
                predicates.add(cb.equal(root.get("acknowledged"), acknowledged));
            }
            if (from != null) {
                predicates.add(cb.greaterThanOrEqualTo(root.get("createdAt"), from));
            }
            if (to != null) {
                predicates.add(cb.lessThanOrEqualTo(root.get("createdAt"), to));
            }
            return cb.and(predicates.toArray(new Predicate[0]));
        };

        int safeSize = Math.min(Math.max(size, 1), MAX_PAGE_SIZE);
        int safePage = Math.max(page, 1) - 1;
        // 按时间倒序：值班人员最关心刚发生的告警
        var pageable = PageRequest.of(safePage, safeSize, Sort.by(Sort.Direction.DESC, "createdAt"));
        return PageResult.of(alertEventRepository.findAll(spec, pageable));
    }

    public AlertEvent get(Integer id) {
        return alertEventRepository.findById(id)
                .orElseThrow(() -> BusinessException.notFound("告警事件不存在: id=" + id));
    }

    /**
     * 确认告警（留痕）。
     *
     * <p>三个设计决定：
     * <ul>
     *   <li><b>操作人来自登录身份</b>（Controller 从 SecurityContext 取，见 AlertEventController），
     *       不接受请求体传入。否则任何人都能以别人的名义"确认"掉一条告警——
     *       留痕就变成了一句空话。</li>
     *   <li><b>时间由服务端生成</b>，不接受客户端时间：现场机器时间不准是常态。</li>
     *   <li><b>重复确认直接拒绝</b>（409）而不是覆盖。谁先确认的才是有效记录，
     *       允许覆盖等于允许事后改历史。前端拿到 409 时已经把"谁、什么时候确认的"显示出来了。</li>
     * </ul>
     */
    @Transactional
    public AlertEvent acknowledge(Integer id, String operator) {
        AlertEvent event = get(id);
        if (Boolean.TRUE.equals(event.getAcknowledged())) {
            throw BusinessException.conflict(
                    "该告警已由 %s 于 %s 确认，不能重复确认".formatted(
                            event.getAcknowledgedBy(), event.getAcknowledgedAt()));
        }
        event.setAcknowledged(Boolean.TRUE);
        event.setAcknowledgedBy(operator);
        event.setAcknowledgedAt(LocalDateTime.now());
        // 实体的其余字段都是 updatable = false，因此这里生成的 UPDATE 只会碰到确认三列
        return alertEventRepository.save(event);
    }
}
