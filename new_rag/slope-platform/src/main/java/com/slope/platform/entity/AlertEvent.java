package com.slope.platform.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.LocalDateTime;

/**
 * 告警事件，对应已有表 {@code alert_event}。
 *
 * <p>事件的产生方是 Python 的规则引擎（alert_engine.py），Java 侧只做两件事：
 * 查询 + 确认。这里的字段权限设置就是按这个边界来的——
 * <b>业务字段全部 {@code updatable = false}，Java 能改的只有确认三件套</b>。
 *
 * <p>为什么这么严：告警事件是事后倒查的依据。如果业务接口能把 value/threshold
 * 改掉，"当时为什么报警"就永远说不清了。所以从 ORM 层就把这条路堵死，
 * ENTITY 生成 UPDATE 语句时根本不会带上这些列。
 *
 * <p>确认留痕（acknowledged / acknowledged_by / acknowledged_at）是工业现场的硬要求：
 * 谁在什么时候把哪条告警压下去了，必须可查。这也是"Java 负责权限"最直接的体现——
 * 这三个字段的值来自登录身份，而不是请求参数。
 */
@Entity
@Table(name = "alert_event")
public class AlertEvent {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Integer id;

    @Column(name = "site_id", nullable = false, updatable = false)
    private Integer siteId;

    /** 触发它的规则编码（弱引用，故意不建外键：规则可以删，历史告警必须留着）。 */
    @Column(name = "rule_code", nullable = false, length = 48, updatable = false)
    private String ruleCode;

    @Column(name = "level", nullable = false, length = 12, updatable = false)
    private String level;

    /** 触发时的实测值。 */
    @Column(name = "value", nullable = false, updatable = false)
    private Float value;

    /** 触发时的阈值快照——注意不是去 join alert_rule 取现值，规则改了也要能还原当时。 */
    @Column(name = "threshold", nullable = false, updatable = false)
    private Float threshold;

    @Column(name = "message", columnDefinition = "text", nullable = false, updatable = false)
    private String message;

    /** 判定依据 JSON，把当时用到的数字原样存下来，用于复现。 */
    @Column(name = "evidence", columnDefinition = "text", updatable = false)
    private String evidence;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Column(name = "acknowledged", nullable = false)
    private Boolean acknowledged;

    /** 确认人，取自 JWT 身份。 */
    @Column(name = "acknowledged_by", length = 64)
    private String acknowledgedBy;

    @Column(name = "acknowledged_at")
    private LocalDateTime acknowledgedAt;

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

    public String getRuleCode() {
        return ruleCode;
    }

    public void setRuleCode(String ruleCode) {
        this.ruleCode = ruleCode;
    }

    public String getLevel() {
        return level;
    }

    public void setLevel(String level) {
        this.level = level;
    }

    public Float getValue() {
        return value;
    }

    public void setValue(Float value) {
        this.value = value;
    }

    public Float getThreshold() {
        return threshold;
    }

    public void setThreshold(Float threshold) {
        this.threshold = threshold;
    }

    public String getMessage() {
        return message;
    }

    public void setMessage(String message) {
        this.message = message;
    }

    public String getEvidence() {
        return evidence;
    }

    public void setEvidence(String evidence) {
        this.evidence = evidence;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }

    public Boolean getAcknowledged() {
        return acknowledged;
    }

    public void setAcknowledged(Boolean acknowledged) {
        this.acknowledged = acknowledged;
    }

    public String getAcknowledgedBy() {
        return acknowledgedBy;
    }

    public void setAcknowledgedBy(String acknowledgedBy) {
        this.acknowledgedBy = acknowledgedBy;
    }

    public LocalDateTime getAcknowledgedAt() {
        return acknowledgedAt;
    }

    public void setAcknowledgedAt(LocalDateTime acknowledgedAt) {
        this.acknowledgedAt = acknowledgedAt;
    }
}
