package com.slope.platform.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.LocalDateTime;
import org.hibernate.annotations.Immutable;

/**
 * 传感器读数，对应已有表 {@code sensor_reading}。
 *
 * <p><b>这张表不是 Java 写的。</b> 读数由采集侧（Python 的 slope_monitor 模拟器 / 现场网关）持续灌入，
 * Java 侧只提供查询，供前端画趋势图。所以这里加了 {@link Immutable}：
 * 从类型层面禁止通过 Java 业务域修改或删除历史读数——历史数据一旦能被业务接口改写，
 * 事后追溯告警原因就没有可信依据了。
 *
 * <p>当前表里 5 个指标：displacement_mm / rainfall_mm / pore_pressure_kpa /
 * crack_width_mm / temperature_c。
 */
@Entity
@Table(name = "sensor_reading")
@Immutable
public class SensorReading {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Integer id;

    @Column(name = "site_id", nullable = false)
    private Integer siteId;

    /** 指标名，如 displacement_mm。 */
    @Column(name = "metric", nullable = false, length = 32)
    private String metric;

    @Column(name = "value", nullable = false)
    private Float value;

    @Column(name = "unit", nullable = false, length = 16)
    private String unit;

    /** 数据质量标记：ok / suspect / bad。异常值判定时要先看这个字段，否则会把漂移当滑坡。 */
    @Column(name = "quality", nullable = false, length = 16)
    private String quality;

    @Column(name = "ts", nullable = false)
    private LocalDateTime ts;

    public Integer getId() {
        return id;
    }

    public void setId(Integer id) {
        this.id = id;
    }

    public Integer getSiteId() {
        return siteId;
    }

    public void setSiteId(Integer siteId) {
        this.siteId = siteId;
    }

    public String getMetric() {
        return metric;
    }

    public void setMetric(String metric) {
        this.metric = metric;
    }

    public Float getValue() {
        return value;
    }

    public void setValue(Float value) {
        this.value = value;
    }

    public String getUnit() {
        return unit;
    }

    public void setUnit(String unit) {
        this.unit = unit;
    }

    public String getQuality() {
        return quality;
    }

    public void setQuality(String quality) {
        this.quality = quality;
    }

    public LocalDateTime getTs() {
        return ts;
    }

    public void setTs(LocalDateTime ts) {
        this.ts = ts;
    }
}
