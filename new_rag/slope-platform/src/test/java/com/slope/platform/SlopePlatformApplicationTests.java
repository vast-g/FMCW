package com.slope.platform;

import static org.assertj.core.api.Assertions.assertThat;

import com.slope.platform.entity.AlertEvent;
import com.slope.platform.entity.AlertRule;
import com.slope.platform.entity.Device;
import com.slope.platform.entity.SensorReading;
import com.slope.platform.entity.Site;
import com.slope.platform.repository.AlertEventRepository;
import com.slope.platform.repository.AlertRuleRepository;
import com.slope.platform.repository.DeviceRepository;
import com.slope.platform.repository.SensorReadingRepository;
import com.slope.platform.repository.SiteRepository;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.domain.PageRequest;
import org.springframework.transaction.annotation.Transactional;

/**
 * 集成测试：真连本机 MySQL 的 slope_agent 库。
 *
 * <p>为什么不用 H2 内存库：这个服务的所有风险都在"Java 实体和其它语言共用的真实表"之间。
 * 换成 H2 之后，方言差异、tinyint(1) 到 Boolean 的映射、datetime 精度这些最容易出问题的地方
 * 恰好都被绕过去了，测试会变成一种自我安慰。
 *
 * <p>整个类只读（{@code @Transactional} 默认回滚，且不新增/修改任何业务数据），
 * 因为库里那 1440 条读数、21 条告警事件是项目的既有数据，测试不许污染它们。
 */
@SpringBootTest
@Transactional
class SlopePlatformApplicationTests {

    @Autowired
    private SiteRepository siteRepository;
    @Autowired
    private SensorReadingRepository sensorReadingRepository;
    @Autowired
    private AlertRuleRepository alertRuleRepository;
    @Autowired
    private AlertEventRepository alertEventRepository;
    @Autowired
    private DeviceRepository deviceRepository;

    @Test
    @DisplayName("Spring 上下文能加载起来（数据源、JPA、Security 配置都正常）")
    void contextLoads() {
        assertThat(siteRepository).isNotNull();
    }

    /**
     * 实体与表结构对齐验证。
     *
     * <p>每张表查一页（而不是用 ddl-auto=validate）：SQL 是 Hibernate 依据实体真实生成的，
     * 只要列名、类型、tinyint(1)→Boolean 的映射有任何一处对不上，
     * MySQL 会直接报错，比 validate 那种类型名字符串比较可靠得多。
     */
    @Test
    @DisplayName("五张表的实体映射都能真正执行查询")
    void entityMappingsMatchRealTables() {
        var limitOne = PageRequest.of(0, 1);

        assertThat(siteRepository.findAll(limitOne).getContent()).isNotNull();
        assertThat(sensorReadingRepository.findAll(limitOne).getContent()).isNotNull();
        assertThat(alertRuleRepository.findAll(limitOne).getContent()).isNotNull();
        assertThat(alertEventRepository.findAll(limitOne).getContent()).isNotNull();
        assertThat(deviceRepository.findAll(limitOne).getContent()).isNotNull();

        // 各自取一条真实数据，触发所有字段的读取（含 tinyint(1) → Boolean、datetime → LocalDateTime）
        Site site = siteRepository.findAll(limitOne).getContent().getFirst();
        assertThat(site.getCode()).isNotBlank();
        assertThat(site.getIsPrimary()).isNotNull();
        assertThat(site.getCreatedAt()).isNotNull();

        SensorReading reading = sensorReadingRepository.findAll(limitOne).getContent().getFirst();
        assertThat(reading.getMetric()).isNotBlank();
        assertThat(reading.getTs()).isNotNull();

        AlertRule rule = alertRuleRepository.findAll(limitOne).getContent().getFirst();
        assertThat(rule.getEnabled()).isNotNull();
        assertThat(rule.getUpdatedAt()).isNotNull();

        AlertEvent event = alertEventRepository.findAll(limitOne).getContent().getFirst();
        assertThat(event.getAcknowledged()).isNotNull();
        assertThat(event.getCreatedAt()).isNotNull();
        assertThat(event.getMessage()).isNotBlank();
    }

    @Test
    @DisplayName("既有业务数据没有被破坏（点位/读数/规则都还在）")
    void existingDataIsIntact() {
        assertThat(siteRepository.count()).isPositive();
        assertThat(sensorReadingRepository.count()).isPositive();
        assertThat(alertRuleRepository.count()).isPositive();
        assertThat(alertEventRepository.count()).isPositive();
        // 设备台账是新建的空表，不应该有任何数据
        assertThat(deviceRepository.count()).isZero();
    }

    @Test
    @DisplayName("schema.sql 建出的 device 表可以被正常写入和删除（事务内回滚）")
    void deviceTableIsUsable() {
        Integer siteId = siteRepository.findAllByOrderByIdAsc().getFirst().getId();

        Device device = new Device();
        device.setCode("TEST-DEVICE-01");
        device.setName("测试设备（不落库）");
        device.setSiteId(siteId);
        device.setType("rain_gauge");

        Device saved = deviceRepository.saveAndFlush(device);

        assertThat(saved.getId()).isNotNull();
        assertThat(saved.getCreatedAt()).isNotNull();
        assertThat(saved.getUpdatedAt()).isNotNull();
        // 没显式给状态时默认离线：新设备还没心跳，不能默认算在线
        assertThat(saved.getStatus()).isEqualTo("offline");
        assertThat(saved.getStatusLabel()).isEqualTo("离线");
        assertThat(saved.getTypeLabel()).isEqualTo("雨量计");
    }
}
