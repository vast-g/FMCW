"""告警规则引擎（确定性判定）。

## 为什么必须独立于 Agent

边坡监测是**安全场景**。告警必须满足三个硬要求：

| 要求 | 大模型能给吗 |
|---|---|
| **确定性** —— 同样数据必须得出同样结论 | ❌ 每次输出可能不同 |
| **可复现** —— 事后能倒查"当时为什么报警" | ❌ 不可复现 |
| **可审计** —— 谁在什么时候确认了什么 | ❌ 无留痕 |

所以**判定归这里**，Agent 只负责"解释和辅助建议"。这就是所谓的**双轨制**：

    数据 → 规则引擎（本模块）→ 确定性告警   ← 自动链路，必须可靠
         ↘
           Agent（Python）→ 综合评估建议      ← 辅助链路，供人参考

两条链路都留痕，值班人员看到两个：规则说"橙色"，Agent 说"疑似整体滑动前兆"。

## 规则是数据驱动的

阈值存在 `alert_rule` 表里，不在代码里写死 —— 这样：
  * 现场可以根据地质条件调阈值（不同边坡差别很大）
  * 每次改动留痕（`updated_at`）
  * 后续 Java 业务域可以直接对这些规则做 CRUD

评估逻辑本身仍是代码（每个指标一条），因为复合条件、去重窗口这些
不是简单比较能表达的 —— 工业系统普遍也是这么做的。

## 去重（抑制）

同一点位 + 同一规则，在 `dedup_minutes` 内不重复产生事件。
否则持续降雨会让"降雨超标"刷出几百条告警，值班人员直接忽略 —— 这叫告警疲劳。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Integer, String,
                        Text, func, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from slope_monitor import Base, SensorReading, Site  # 复用同一套 Base/元数据

LEVELS = {"blue": "蓝色", "yellow": "黄色", "orange": "橙色", "red": "红色"}
LEVEL_RANK = {"blue": 1, "yellow": 2, "orange": 3, "red": 4}

DEFAULT_DEDUP_MINUTES = 30


class AlertRule(Base):
    """告警规则（阈值可配置、可审计）。"""

    __tablename__ = "alert_rule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    metric: Mapped[str] = mapped_column(String(32), nullable=False, comment="作用指标")
    threshold: Mapped[float] = mapped_column(Float, nullable=False, comment="阈值")
    level: Mapped[str] = mapped_column(String(12), default="yellow", comment="触发等级")
    window_hours: Mapped[int] = mapped_column(Integer, default=12, comment="观察窗口")
    dedup_minutes: Mapped[int] = mapped_column(Integer, default=DEFAULT_DEDUP_MINUTES)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now,
                                                 onupdate=datetime.now)


class AlertEvent(Base):
    """告警事件（含判定依据，可倒查）。"""

    __tablename__ = "alert_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[int] = mapped_column(Integer, ForeignKey("site.id"), nullable=False, index=True)
    rule_code: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    value: Mapped[float] = mapped_column(Float, default=0.0, comment="实测值")
    threshold: Mapped[float] = mapped_column(Float, default=0.0, comment="触发阈值")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # 判定依据：把当时用到的数字原样存下来，事后可复现
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True, comment="JSON 依据")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# 默认规则集。阈值参考通用边坡监测实践，现场可按地质条件调整。
DEFAULT_RULES = [
    dict(code="R-RATE-01", name="位移速率超限", metric="displacement_rate",
         threshold=0.10, level="yellow", window_hours=12,
         description="近窗平均位移速率超过 0.10 mm/h"),
    dict(code="R-RATE-02", name="位移速率显著超限", metric="displacement_rate",
         threshold=0.30, level="orange", window_hours=12,
         description="近窗平均位移速率超过 0.30 mm/h"),
    dict(code="R-ACCEL-01", name="位移加速", metric="displacement_accel",
         threshold=0.05, level="orange", window_hours=12,
         description="后段速率比前段高 0.05 mm/h 以上（加速蠕变是失稳前兆）"),
    dict(code="R-RAIN-01", name="小时雨强超限", metric="rainfall_hourly",
         threshold=10.0, level="yellow", window_hours=24,
         description="最大小时雨强超过 10 mm/h"),
    dict(code="R-RAIN-02", name="累计雨量超限", metric="rainfall_cum",
         threshold=100.0, level="orange", window_hours=24,
         description="24 小时累计雨量超过 100 mm（久雨型）"),
    dict(code="R-PORE-01", name="孔压抬升", metric="pore_pressure_rise",
         threshold=5.0, level="yellow", window_hours=12,
         description="孔隙水压力抬升超过 5 kPa（有效应力下降）"),
    dict(code="R-CRACK-01", name="裂缝张开", metric="crack_open",
         threshold=1.0, level="orange", window_hours=168,
         description="近 7 天裂缝张开超过 1.0 mm（地表拉裂是直接前兆）"),
    dict(code="R-SYNC-01", name="多点同步加速", metric="sync_accel",
         threshold=2.0, level="red", window_hours=6,
         description="同一边坡 ≥2 个点位同步加速 —— 提示整体滑动，而非局部变形"),
]


# ──────────────────────────── 指标计算 ────────────────────────────


def _vals(s, site_id: int, metric: str, hours: int):
    since = datetime.now() - timedelta(hours=hours)
    return [(r.ts, r.value) for r in
            s.query(SensorReading)
             .filter(SensorReading.site_id == site_id,
                     SensorReading.metric == metric,
                     SensorReading.ts >= since)
             .order_by(SensorReading.ts).all()]


def _rate(pts) -> float:
    if len(pts) < 2:
        return 0.0
    (t0, v0), (t1, v1) = pts[0], pts[-1]
    h = (t1 - t0).total_seconds() / 3600
    return (v1 - v0) / h if h > 0.01 else 0.0


def compute_metrics(s, site_id: int, hours: int = 12) -> dict:
    """算出规则要用到的全部指标。**纯计算，不涉及阈值判断。**"""
    disp = _vals(s, site_id, "displacement_mm", hours)
    half = len(disp) // 2
    rate_first = _rate(disp[:half + 1]) if half else 0.0
    rate_last = _rate(disp[half:]) if half else 0.0

    rain = _vals(s, site_id, "rainfall_mm", 24)
    pore = _vals(s, site_id, "pore_pressure_kpa", hours)
    crack = _vals(s, site_id, "crack_width_mm", 168)

    return {
        "displacement_rate": round(rate_last, 4),
        "displacement_rate_prev": round(rate_first, 4),
        "displacement_accel": round(rate_last - rate_first, 4),
        "cumulative_mm": round(disp[-1][1], 3) if disp else 0.0,
        "rainfall_hourly": round(max((v for _, v in rain), default=0.0), 2),
        "rainfall_cum": round(sum(v for _, v in rain), 2),
        "pore_pressure_rise": round(
            (pore[-1][1] - pore[0][1]) if len(pore) > 1 else 0.0, 3),
        "pore_pressure_now": round(pore[-1][1], 3) if pore else 0.0,
        "crack_open": round((crack[-1][1] - crack[0][1]) if len(crack) > 1 else 0.0, 3),
        "crack_now": round(crack[-1][1], 3) if crack else 0.0,
    }


# ──────────────────────────── 判定 ────────────────────────────


def _sync_accel(s, site: Site, hours: int, rate_threshold: float = 0.25) -> tuple[float, list]:
    """同一边坡上加速的点位数（≥2 视为整体滑动征兆）。"""
    peers = s.query(Site).filter(Site.slope_group == site.slope_group).all()
    accel = []
    for p in peers:
        pts = _vals(s, p.id, "displacement_mm", hours)
        if len(pts) >= 3 and _rate(pts) > rate_threshold:
            accel.append(p.code)
    return float(len(accel)), accel


def evaluate_site(s, site: Site, *, record: bool = True) -> list[dict]:
    """对单个点位跑全部规则，返回本轮触发的事件（并按去重窗口过滤）。"""
    rules = s.query(AlertRule).filter(AlertRule.enabled == True).all()  # noqa: E712
    metrics = compute_metrics(s, site.id, 12)
    fired: list[dict] = []

    for rule in rules:
        if rule.metric == "sync_accel":
            value, extra = _sync_accel(s, site, rule.window_hours)
        else:
            value = float(metrics.get(rule.metric, 0.0))
            extra = None

        if value < rule.threshold:
            continue

        # 去重：同点位同规则在窗口内只报一次（防告警疲劳）
        since = datetime.now() - timedelta(minutes=rule.dedup_minutes)
        dup = (s.query(AlertEvent)
                .filter(AlertEvent.site_id == site.id,
                        AlertEvent.rule_code == rule.code,
                        AlertEvent.created_at >= since)
                .first())
        if dup is not None:
            continue

        evidence = {k: v for k, v in metrics.items()}
        if extra is not None:
            evidence["accelerating_sites"] = extra
        msg = (f"[{LEVELS.get(rule.level, rule.level)}] {site.code}（{site.name}）："
               f"{rule.name} —— 实测 {value:.3f}，阈值 {rule.threshold:.3f}")

        ev = AlertEvent(site_id=site.id, rule_code=rule.code, level=rule.level,
                        value=float(value), threshold=float(rule.threshold),
                        message=msg, evidence=json.dumps(evidence, ensure_ascii=False))
        if record:
            s.add(ev)
        fired.append({"site": site.code, "rule": rule.code, "name": rule.name,
                      "level": rule.level, "value": round(float(value), 3),
                      "threshold": rule.threshold, "message": msg})

    # ── 复合升级：多因素叠加时提级 ──
    # 单项超标和"久雨+位移加速+孔压升高"同时出现，风险等级完全不同。
    # 规则表里逐条配不出这种组合，所以在引擎里显式做一次合成判断。
    combo = []
    if metrics["displacement_accel"] > 0.05:
        combo.append("位移加速")
    if metrics["rainfall_cum"] > 80:
        combo.append("持续强降雨")
    if metrics["pore_pressure_rise"] > 3:
        combo.append("孔压显著抬升")
    if combo:
        n_sync, sites = _sync_accel(s, site, 6)
        if n_sync >= 2:
            combo.append(f"多点同步加速({','.join(sites)})")

    if len(combo) >= 3:
        level = "red" if any("多点同步" in c for c in combo) else "orange"
        since = datetime.now() - timedelta(minutes=60)
        dup = (s.query(AlertEvent)
                .filter(AlertEvent.site_id == site.id,
                        AlertEvent.rule_code == "R-COMPOSITE-01",
                        AlertEvent.created_at >= since).first())
        if dup is None:
            msg = (f"[{LEVELS[level]}] {site.code}：多因素叠加 —— "
                   f"{'、'.join(combo)}")
            evidence = dict(metrics)
            evidence["combo"] = combo
            ev = AlertEvent(site_id=site.id, rule_code="R-COMPOSITE-01", level=level,
                            value=float(len(combo)), threshold=3.0, message=msg,
                            evidence=json.dumps(evidence, ensure_ascii=False))
            if record:
                s.add(ev)
            fired.append({"site": site.code, "rule": "R-COMPOSITE-01",
                          "name": "多因素叠加", "level": level,
                          "value": len(combo), "threshold": 3,
                          "message": msg})
    return fired


def seed_rules(s, force: bool = False) -> int:
    """写入默认规则集（幂等）。返回规则总数。"""
    if force:
        s.query(AlertRule).delete()
        s.commit()
    existing = {r.code for r in s.query(AlertRule).all()}
    for d in DEFAULT_RULES:
        if d["code"] not in existing:
            s.add(AlertRule(**d))
    s.commit()
    return s.scalar(select(func.count()).select_from(AlertRule)) or 0


def run_once(s, *, record: bool = True) -> dict:
    """对所有点位跑一轮规则。返回汇总（供定时任务调用）。"""
    sites = s.query(Site).all()
    all_fired = []
    for site in sites:
        all_fired += evaluate_site(s, site, record=record)
    if record:
        s.commit()
    top = max((LEVEL_RANK.get(f["level"], 0) for f in all_fired), default=0)
    return {
        "sites": len(sites),
        "fired": len(all_fired),
        "top_level": [k for k, v in LEVEL_RANK.items() if v == top][0] if top else None,
        "events": all_fired,
    }


def active_alerts(s, hours: int = 24, only_unacked: bool = False) -> list[dict]:
    """近期未处理告警（供 Agent 解释用）。"""
    since = datetime.now() - timedelta(hours=hours)
    q = (s.query(AlertEvent, Site)
          .join(Site, AlertEvent.site_id == Site.id)
          .filter(AlertEvent.created_at >= since))
    if only_unacked:
        q = q.filter(AlertEvent.acknowledged == False)  # noqa: E712
    rows = q.order_by(AlertEvent.created_at.desc()).all()
    out = []
    for ev, site in rows:
        out.append({
            "id": ev.id, "site": site.code, "site_name": site.name,
            "rule": ev.rule_code, "level": ev.level,
            "level_text": LEVELS.get(ev.level, ev.level),
            "message": ev.message, "value": ev.value, "threshold": ev.threshold,
            "at": ev.created_at.strftime("%Y-%m-%d %H:%M"),
            "acknowledged": bool(ev.acknowledged),
            "evidence": json.loads(ev.evidence) if ev.evidence else {},
        })
    return out


def acknowledge(s, event_id: int, by: str) -> bool:
    """确认告警。**必须留痕** —— 谁确认的、什么时候。"""
    ev = s.get(AlertEvent, event_id)
    if not ev:
        return False
    ev.acknowledged = True
    ev.acknowledged_by = by
    ev.acknowledged_at = datetime.now()
    s.commit()
    return True
