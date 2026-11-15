package com.slope.platform.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;
import java.time.LocalDateTime;

/**
 * 监测点位，对应已有表 {@code site}（Python 侧 slope_monitor.Site 也在用它）。
 *
 * <p>两个刻意的设计：
 * <ul>
 *   <li><b>不用 JPA 关联</b>（不写 {@code @ManyToOne Site}），只存 {@code siteId}。
 *       因为跨表级联删除在业务上是危险的：删点位时应该报错提示"还有设备挂在上面"，
 *       而不是悄悄把设备/告警一起删掉。关联关系交给 Service 显式判断。</li>
 *   <li><b>不改列定义</b>：字段与既有表一一对应，{@code ddl-auto=none}，
 *       避免 ORM 为了"对齐实体"去改线上表结构。</li>
 * </ul>
 */
@Entity
@Table(name = "site")
public class Site {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Integer id;

    /** 点位编号，如 S-01。业务上的唯一标识，对外用它比用自增 id 更稳定。 */
    @Column(name = "code", nullable = false, length = 32, unique = true)
    private String code;

    @Column(name = "name", nullable = false, length = 64)
    private String name;

    @Column(name = "description", columnDefinition = "text")
    private String description;

    @Column(name = "lat", nullable = false)
    private Float lat;

    @Column(name = "lng", nullable = false)
    private Float lng;

    /** 所属边坡分组：同一条边坡上的点位要一起看，才能区分整体滑动和局部塌陷。 */
    @Column(name = "slope_group", nullable = false, length = 32)
    private String slopeGroup;

    /** tinyint(1) → Boolean。主监测点是对比分析的基准点。 */
    @Column(name = "is_primary", nullable = false)
    private Boolean isPrimary;

    @Column(name = "created_at", nullable = false)
    private LocalDateTime createdAt;

    /** 建表时该列 NOT NULL 且无数据库默认值，必须在入库前补上。 */
    @PrePersist
    void onCreate() {
        if (createdAt == null) {
            createdAt = LocalDateTime.now();
        }
        if (lat == null) {
            lat = 0F;
        }
        if (lng == null) {
            lng = 0F;
        }
        if (isPrimary == null) {
            isPrimary = Boolean.FALSE;
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

    public String getDescription() {
        return description;
    }

    public void setDescription(String description) {
        this.description = description;
    }

    public Float getLat() {
        return lat;
    }

    public void setLat(Float lat) {
        this.lat = lat;
    }

    public Float getLng() {
        return lng;
    }

    public void setLng(Float lng) {
        this.lng = lng;
    }

    public String getSlopeGroup() {
        return slopeGroup;
    }

    public void setSlopeGroup(String slopeGroup) {
        this.slopeGroup = slopeGroup;
    }

    public Boolean getIsPrimary() {
        return isPrimary;
    }

    public void setIsPrimary(Boolean isPrimary) {
        this.isPrimary = isPrimary;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
