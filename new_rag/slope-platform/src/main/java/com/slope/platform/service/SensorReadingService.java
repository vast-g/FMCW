package com.slope.platform.service;

import com.slope.platform.entity.SensorReading;
import com.slope.platform.repository.SensorReadingRepository;
import java.time.LocalDateTime;
import java.util.List;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 读数查询（只读）。
 *
 * <p>Java 侧只读这张表：写入方是采集链路（现场网关 / Python 模拟器）。
 * 业务后端跑到这里来写读数没有任何理由，反而会把"数据来源"这件事搞混。
 */
@Service
@Transactional(readOnly = true)
public class SensorReadingService {

    private static final int MAX_LIMIT = 2000;

    private final SensorReadingRepository sensorReadingRepository;

    public SensorReadingService(SensorReadingRepository sensorReadingRepository) {
        this.sensorReadingRepository = sensorReadingRepository;
    }

    /**
     * 查某个点位最近若干小时的读数。
     *
     * <p>hours / limit 都做上限截断而不是报错：趋势图这种查询，参数给大了
     * 直接把数据库拖慢，截断到安全范围并返回实际生效的值（响应里带 unit 和 ts），
     * 比返回 400 让人去猜更实用。
     */
    public List<SensorReading> recent(Integer siteId, String metric, int hours, int limit) {
        int safeHours = Math.min(Math.max(hours, 1), 24 * 30);
        int safeLimit = Math.min(Math.max(limit, 1), MAX_LIMIT);
        LocalDateTime since = LocalDateTime.now().minusHours(safeHours);
        String metricFilter = (metric == null || metric.isBlank()) ? null : metric.trim();
        return sensorReadingRepository.findRecent(siteId, metricFilter, since, PageRequest.of(0, safeLimit));
    }

    /**
     * 取每个（点位, 指标）的最新读数，供首页实时卡片使用。
     *
     * <p>{@code siteId} 可空：传了就只看这个点位，不传则返回全部点位。
     *
     * <p>不设"多久算过期"的过滤 —— 设备掉线时，最后一条读数恰恰是排查要看的
     * （前端拿到 ts 自己判断新旧）。在这里过滤掉反而会把"设备失联"这个信息抹掉。
     */
    public List<SensorReading> latest(Integer siteId) {
        return sensorReadingRepository.findLatestPerMetric(siteId);
    }
}
