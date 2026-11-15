package com.slope.platform.repository;

import com.slope.platform.entity.AlertRule;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

/**
 * 告警规则仓储。
 *
 * <p>Python 的规则引擎按 {@code code} 读规则，所以 findByCode 是有实际调用方的接口（跨语言契约），
 * 不是"顺手加的"。
 */
public interface AlertRuleRepository extends JpaRepository<AlertRule, Integer> {

    @Query("""
            select r from AlertRule r
            where (:metric is null or r.metric = :metric)
              and (:enabled is null or r.enabled = :enabled)
            order by r.id
            """)
    List<AlertRule> search(@Param("metric") String metric, @Param("enabled") Boolean enabled);

    Optional<AlertRule> findByCode(String code);

    boolean existsByCode(String code);

    boolean existsByCodeAndIdNot(String code, Integer id);
}
