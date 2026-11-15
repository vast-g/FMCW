package com.slope.platform.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;
import java.time.LocalDateTime;

/**
 * 告警规则，对应已有表 {@code alert_rule}。
 *
 * <p>这是 Java 业务域最关键的一张表：<b>阈值不能写死在代码里</b>。
 * 不同边坡的地质条件差别很大，同一个 0.1mm/h 在 A 边坡是正常、在 B 边坡已经是危险，
 * 所以阈值必须是可配置、可留痕的数据。Python 的规则引擎读这张表做判定，
 * 而修改它的入口只有 Java（有鉴权、有 updated_at 留痕）。
 *
 * <p>{@code updated_at} 每次落库都刷新：这是阈值改动的审计线索——
 * 事后必须能回答"这次误报是因为阈值什么时候被谁改过"。
 */
@Entity
@Table(name = "alert_rule")
public class AlertRule {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Integer id;

    /** 规则编码，如 R-RATE-01。告警事件里存的就是它。 */
    @Column(name = "code", nullable = false, length = 48, unique = true)
    private String code;

    @Column(name = "name", nullable = false, length = 96)
    private String name;

    /** 作用的指标名，必须和 sensor_reading.metric 对得上。 */
    @Column(name = "metric", nullable = false, length = 32)
    private String metric;

    @Column(name = "threshold", nullable = false)
    private Float threshold;

    /** 触发等级：blue / yellow / orange / red（与 Python 侧 LEVELS 一致）。 */
    @Column(name = "level", nullable = false, length = 12)
    private String level;

    /** 观察窗口（小时）：判定是在这个时间窗内的统计量上做的。 */
    @Column(name = "window_hours", nullable = false)
    private Integer windowHours;

    /** 抑制窗口（分钟）：同点位同规则在此时间内不重复产生事件，防止告警刷屏。 */
    @Column(name = "dedup_minutes", nullable = false)
    private Integer dedupMinutes;

    /** 启停开关：现场调试或传感器检修时需要临时停用某条规则，但不应该删掉它。 */
    @Column(name = "enabled", nullable = false)
    private Boolean enabled;

    @Column(name = "description", columnDefinition = "text")
    private String description;

    @Column(name = "updated_at", nullable = false)
    private LocalDateTime updatedAt;

    @PrePersist
    void onCreate() {
        applyDefaults();
        updatedAt = LocalDateTime.now();
    }

    /** 改阈值 / 启停都会走到这里，保证审计时间必然更新。 */
    @PreUpdate
    void onUpdate() {
        updatedAt = LocalDateTime.now();
    }

    private void applyDefaults() {
        if (enabled == null) {
            enabled = Boolean.TRUE;
        }
        if (windowHours == null) {
            windowHours = 12;
        }
        if (dedupMinutes == null) {
            dedupMinutes = 30;
        }
    }

    public Integer getId() {
        return id;
    }

    public void setId(Integer id) {
        this.id = id;
    }

    public String getCode() {
        return code;
    }

    public void setCode(String code) {
        this.code = code;
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }

    public String getMetric() {
        return metric;
    }

    public void setMetric(String metric) {
        this.metric = metric;
    }

    public Float getThreshold() {
        return threshold;
    }

    public void setThreshold(Float threshold) {
        this.threshold = threshold;
    }

    public String getLevel() {
        return level;
    }

    public void setLevel(String level) {
        this.level = level;
    }

    public Integer getWindowHours() {
        return windowHours;
    }

    public void setWindowHours(Integer windowHours) {
        this.windowHours = windowHours;
    }

    public Integer getDedupMinutes() {
        return dedupMinutes;
    }

    public void setDedupMinutes(Integer dedupMinutes) {
        this.dedupMinutes = dedupMinutes;
    }

    public Boolean getEnabled() {
        return enabled;
    }

    public void setEnabled(Boolean enabled) {
        this.enabled = enabled;
    }

    public String getDescription() {
        return description;
    }

    public void setDescription(String description) {
        this.description = description;
    }

    public LocalDateTime getUpdatedAt() {
        return updatedAt;
    }

    public void setUpdatedAt(LocalDateTime updatedAt) {
        this.updatedAt = updatedAt;
    }
}
