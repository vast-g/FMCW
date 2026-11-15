package com.slope.platform.repository;

import com.slope.platform.entity.SensorReading;
import java.time.LocalDateTime;
import java.util.List;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

/**
 * 读数仓储（只读）。
 *
 * <p>接口只提供"最近 N 小时"的查询：趋势图永远看的是窗口内的数据，
 * 不提供全表扫描式的接口，避免前端一个失误把 1440 行（以后是几百万行）全拖出来。
 */
public interface SensorReadingRepository extends JpaRepository<SensorReading, Integer> {

    @Query("""
            select r from SensorReading r
            where r.siteId = :siteId
              and (:metric is null or r.metric = :metric)
              and r.ts >= :since
            order by r.ts desc
            """)
    List<SensorReading> findRecent(@Param("siteId") Integer siteId,
                                   @Param("metric") String metric,
                                   @Param("since") LocalDateTime since,
                                   Pageable pageable);

    /**
     * 每个（点位, 指标）组合的**最新一条**读数。
     *
     * <p>为什么单独开这个查询，而不是让调用方拿 {@link #findRecent} 的第一行：
     * 首页的实时卡片要的是"4 个点位 × 5 个指标 = 20 个当前值"。
     * 用 findRecent 得按 (点位,指标) 循环发 20 次查询，每次还要先把窗口数据拉回来再取第一条。
     * 这里一条 SQL 直接返回 20 行，既省往返也省数据传输。
     *
     * <p>用的是 {@code GROUP BY + JOIN} 而不是相关子查询：
     * 相关子查询对每一行都要再扫一次表，数据量上来（时序表按天增长）会明显变慢。
     * 这个写法能吃到 (site_id, metric, ts) 上的索引。
     *
     * <p>用 nativeQuery 是因为 JPQL 没有"每组取第一条"的稳定写法；
     * 这里 SQL 是常量、参数只有可选的 siteId，不存在拼接注入。
     */
    @Query(value = """
            SELECT r.* FROM sensor_reading r
            JOIN (
                SELECT site_id, metric, MAX(ts) AS mts
                FROM sensor_reading
                WHERE (:siteId IS NULL OR site_id = :siteId)
                GROUP BY site_id, metric
            ) m ON r.site_id = m.site_id AND r.metric = m.metric AND r.ts = m.mts
            ORDER BY r.site_id, r.metric
            """, nativeQuery = true)
    List<SensorReading> findLatestPerMetric(@Param("siteId") Integer siteId);
}
