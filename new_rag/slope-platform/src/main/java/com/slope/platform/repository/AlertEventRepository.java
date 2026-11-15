package com.slope.platform.repository;

import com.slope.platform.entity.AlertEvent;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;

/**
 * 告警事件仓储。
 *
 * <p>继承 {@link JpaSpecificationExecutor}：事件表的查询条件最多
 * （点位 / 等级 / 是否已确认 / 时间范围，且都可选），用 Specification 按需拼条件，
 * 既不会生成 `where (:from is null or ...)` 这种让数据库猜类型的 SQL，
 * 也能让 MySQL 用到 created_at 上的索引。
 */
public interface AlertEventRepository extends JpaRepository<AlertEvent, Integer>,
        JpaSpecificationExecutor<AlertEvent> {
}
