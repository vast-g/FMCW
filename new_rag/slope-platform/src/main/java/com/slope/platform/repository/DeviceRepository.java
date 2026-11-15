package com.slope.platform.repository;

import com.slope.platform.entity.Device;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

/**
 * 设备仓储。
 *
 * <p>这里用"参数可为空"的 @Query，是因为按 site_id / status 过滤有 4 种组合，
 * 派生方法名会膨胀成 findBySiteIdAndStatus / findBySiteId / findByStatus / findAll 四个方法。
 *
 * <p>可以这样写的前提：参数都是字符串/整数，MySQL 能确定参数类型。
 * 涉及<b>时间</b>的条件不这么写（见 AlertEventService），
 * 那里用 Specification 显式拼条件，避免数据库对可空时间参数做类型推断时出问题。
 */
public interface DeviceRepository extends JpaRepository<Device, Integer> {

    @Query("""
            select d from Device d
            where (:siteId is null or d.siteId = :siteId)
              and (:status is null or d.status = :status)
            order by d.id
            """)
    List<Device> search(@Param("siteId") Integer siteId, @Param("status") String status);

    boolean existsByCode(String code);

    boolean existsByCodeAndIdNot(String code, Integer id);

    boolean existsBySiteId(Integer siteId);

    long countBySiteId(Integer siteId);
}
