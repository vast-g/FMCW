-- =============================================================================
-- Java 业务域的结构脚本
--
-- 只做一件事：建 device 表（设备台账）。其余四张表 site / sensor_reading /
-- alert_rule / alert_event 已经存在，且 Python Agent 侧也在读写，
-- 这里绝对不去动它们的结构。
--
-- 为什么用 schema.sql + CREATE TABLE IF NOT EXISTS，而不是 jpa 的 ddl-auto=update：
--   1) ddl-auto 会扫描库里所有表并尝试"对齐"，对一个还被 Python 侧同时使用的库来说，
--      这种自动修改结构的行为风险不可控（曾经有过 Hibernate 把 datetime 改成
--      datetime(6) 的先例）；
--   2) IF NOT EXISTS 天然幂等，服务每次启动重复执行也安全，不需要"只在首次启动时跑"的逻辑。
--
-- 字段类型与既有表保持同一套约定：
--   * 主键 INT AUTO_INCREMENT
--   * 布尔用 tinyint(1)（对应 Java Boolean，与 site.is_primary / alert_rule.enabled 一致）
--   * 时间用 datetime，字符串用 utf8mb4
-- =============================================================================

CREATE TABLE IF NOT EXISTS device
(
    id             INT         NOT NULL AUTO_INCREMENT COMMENT '主键',
    code           VARCHAR(48) NOT NULL COMMENT '设备编号，业务唯一键',
    name           VARCHAR(96) NOT NULL COMMENT '设备名称',
    site_id        INT         NOT NULL COMMENT '所属点位，对应 site.id',
    type           VARCHAR(32) NOT NULL COMMENT '设备类型：radar/displacement_meter/rain_gauge/crack_meter/pore_pressure_meter/camera/gnss/other',
    vendor         VARCHAR(64) NULL COMMENT '厂商',
    model          VARCHAR(64) NULL COMMENT '型号',
    status         VARCHAR(16) NOT NULL DEFAULT 'offline' COMMENT '状态：online/offline/fault',
    last_heartbeat DATETIME    NULL COMMENT '最近心跳，用于判定在线状态',
    installed_at   DATETIME    NULL COMMENT '安装时间',
    created_at     DATETIME    NOT NULL COMMENT '创建时间',
    updated_at     DATETIME    NOT NULL COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_device_code (code),
    KEY idx_device_site (site_id),
    KEY idx_device_status (status),
    -- 外键指向 site：设备不可能脱离点位存在。
    -- 注意这里刻意不加 ON DELETE CASCADE —— 删点位时应该被拦下来并提示还有设备，
    -- 而不是把设备台账一起悄悄删掉（那会让资产账对不上）。
    CONSTRAINT fk_device_site FOREIGN KEY (site_id) REFERENCES site (id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT = '设备台账（Java 业务域新建）';
