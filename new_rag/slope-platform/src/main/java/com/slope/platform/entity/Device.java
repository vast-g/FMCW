package com.slope.platform.entity;

import com.slope.platform.entity.enums.DeviceStatus;
import com.slope.platform.entity.enums.DeviceType;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;
import jakarta.persistence.Transient;
import java.time.LocalDateTime;

/**
 * 设备台账（Java 侧新建的表 {@code device}，见 resources/schema.sql）。
 *
 * <p>为什么要有这张表：原来系统里"点位"和"传感器读数"是直接连着的，
 * 一旦某个传感器掉线，看数据的人分不清是"坡体真没动"还是"设备坏了"。
 * 设备台账把硬件状态单独管起来，last_heartbeat 就是判断在线的依据。
 * 这在安全监测里不是可选项：数据不变可能是稳定，也可能是设备已经死了。
 *
 * <p>type / status 用英文枚举存库、中文只做展示，是为了和库里既有的
 * {@code alert_rule.level}（yellow/orange/red）、{@code sensor_reading.quality}（ok/suspect/bad）
 * 保持同一套约定——同一套系统里两种风格混用，后面做统计和告警联动会很难受。
 */
@Entity
@Table(name = "device")
public class Device {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Integer id;

    @Column(name = "code", nullable = false, length = 48, unique = true)
    private String code;

    @Column(name = "name", nullable = false, length = 96)
    private String name;

    /** 所属点位。只存 ID，删除点位时由 Service 显式检查，不做级联删除。 */
    @Column(name = "site_id", nullable = false)
    private Integer siteId;

    /**
     * 类型码（小写，如 rain_gauge）。
     *
     * <p>实体里存 String 而不是枚举：一是让实体与表结构严格一一对应（和其它四个实体一致），
     * 二是小写码要和 {@code alert_rule.level} 那套既有编码对齐，
     * 而 Java 枚举常量按惯例是大写。合法性校验放在 Service 入口（DeviceType.fromCode），
     * 枚举则作为"取值域 + 中文名"的定义来源。
     */
    @Column(name = "type", nullable = false, length = 32)
    private String type;

    @Column(name = "vendor", length = 64)
    private String vendor;

    @Column(name = "model", length = 64)
    private String model;

    /** 状态码（小写：online / offline / fault）。 */
    @Column(name = "status", nullable = false, length = 16)
    private String status;

    /** 最近一次心跳。超过约定时长没更新，即判定为离线。 */
    @Column(name = "last_heartbeat")
    private LocalDateTime lastHeartbeat;

    @Column(name = "installed_at")
    private LocalDateTime installedAt;

    @Column(name = "created_at", nullable = false)
    private LocalDateTime createdAt;

    @Column(name = "updated_at", nullable = false)
    private LocalDateTime updatedAt;

    @PrePersist
    void onCreate() {
        LocalDateTime now = LocalDateTime.now();
        createdAt = now;
        updatedAt = now;
        if (status == null || status.isBlank()) {
            // 新登记的设备还没收到心跳，默认按离线处理——不能默认"在线"，
            // 否则故障设备会被当成正常设备，掩盖真实的数据缺口。
            status = DeviceStatus.OFFLINE.getCode();
        }
    }

    @PreUpdate
    void onUpdate() {
        updatedAt = LocalDateTime.now();
    }

    /**
     * 设备类型的中文名，只用于展示。
     *
     * <p>库里存英文码、接口额外给中文名，是为了不让前端各自维护一份
     * "RADAR 对应雷达"的映射表——这种映射表一旦有两份，迟早会不一致。
     */
    @Transient
    public String getTypeLabel() {
        return DeviceType.labelOf(type);
    }

    @Transient
    public String getStatusLabel() {
        return DeviceStatus.labelOf(status);
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

    public Integer getSiteId() {
        return siteId;
    }

    public void setSiteId(Integer siteId) {
        this.siteId = siteId;
    }

    public String getType() {
        return type;
    }

    public void setType(String type) {
        this.type = type;
    }

    public String getVendor() {
        return vendor;
    }

    public void setVendor(String vendor) {
        this.vendor = vendor;
    }

    public String getModel() {
        return model;
    }

    public void setModel(String model) {
        this.model = model;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public LocalDateTime getLastHeartbeat() {
        return lastHeartbeat;
    }

    public void setLastHeartbeat(LocalDateTime lastHeartbeat) {
        this.lastHeartbeat = lastHeartbeat;
    }

    public LocalDateTime getInstalledAt() {
        return installedAt;
    }

    public void setInstalledAt(LocalDateTime installedAt) {
        this.installedAt = installedAt;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }

    public LocalDateTime getUpdatedAt() {
        return updatedAt;
    }

    public void setUpdatedAt(LocalDateTime updatedAt) {
        this.updatedAt = updatedAt;
    }
}
