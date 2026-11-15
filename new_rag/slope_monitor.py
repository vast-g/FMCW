"""边坡多因素监测数据层。

## 为什么要有这个模块

原来的 Agent 只能看**两个量**：超声波距离（`current_distance`）和外部天气 API。
但真实边坡监测的判断依据远不止这些 —— 滑坡的主要诱因是**降雨入渗导致孔隙水压力升高、
抗滑力下降**，而最典型的失稳前兆是**位移加速**（不是位移绝对值）。

所以这里补上四类关键量，并支持**多点位对比**：

| 指标 | 单位 | 为什么关键 |
|---|---|---|
| `displacement_mm` | 毫米 | 累计位移。**看的是速率和加速度，不是绝对值** |
| `rainfall_mm` | 毫米 | 累计雨量与小时雨强 —— 最主要的触发因素 |
| `pore_pressure_kpa` | 千帕 | 孔隙水压力 —— 力学上的直接原因（有效应力下降 → 抗滑力下降） |
| `crack_width_mm` | 毫米 | 地表拉裂 —— 直接前兆 |
| `temperature_c` | 摄氏度 | 影响传感器零漂，用于数据可信度判断 |

## 关于数据是模拟的

真实系统这里应该是**边缘网关写入的实测数据**。本模块用**带种子的伪随机**生成，
好处是可复现（同一种子每次结果一致），便于评测对比 —— 而不是手写死的"标准答案"。

## 与既有代码的关系

`end_new.py` 里的 `tool_get_distance` 等**原样保留**，本模块只是**新增**数据源，
不改动任何既有链路。表也在同一个库里，只是前缀独立。
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Integer, String,
                        Text, create_engine, func, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# ──────────────────────────── ORM ────────────────────────────


class Base(DeclarativeBase):
    pass


class Site(Base):
    """监测点位（一个边坡可以布多个点）。"""

    __tablename__ = "site"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, comment="点位编号")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="点位名称")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    lat: Mapped[float] = mapped_column(Float, default=0.0)
    lng: Mapped[float] = mapped_column(Float, default=0.0)
    # 点位相对关系：同一条边坡上的点位要一起看（整体滑动 vs 局部塌陷）
    slope_group: Mapped[str] = mapped_column(String(32), default="A", comment="所属边坡")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否主监测点")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class SensorReading(Base):
    """传感器读数（多指标时序）。"""

    __tablename__ = "sensor_reading"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[int] = mapped_column(Integer, ForeignKey("site.id"), nullable=False, index=True)
    metric: Mapped[str] = mapped_column(String(32), nullable=False, index=True, comment="指标名")
    value: Mapped[float] = mapped_column(Float, nullable=False, comment="数值")
    unit: Mapped[str] = mapped_column(String(16), default="", comment="单位")
    quality: Mapped[str] = mapped_column(String(16), default="ok", comment="ok/suspect/bad")
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


# 每个点位的"性格"：决定模拟数据的走势。种子固定 → 可复现。
SITE_SEEDS = {
    "S-01": {"seed": 101, "trend": "accelerating", "base_disp": 12.0},
    "S-02": {"seed": 202, "trend": "accelerating", "base_disp": 13.5},
    "S-03": {"seed": 303, "trend": "stable", "base_disp": 6.0},
    "S-04": {"seed": 404, "trend": "stable", "base_disp": 5.0},
}

SITES = [
    ("S-01", "主监测点 · 坡顶", "位于坡顶，正对主要滑动方向", "A", True),
    ("S-02", "东侧邻点 · 坡中", "位于主监测点东侧约 40m，同一潜在滑体", "A", False),
    ("S-03", "西侧邻点 · 坡脚", "位于主监测点西侧约 55m，坡脚位置", "A", False),
    ("S-04", "对照点 · 后山", "不受主滑体影响，用作环境参照", "B", False),
]

METRIC_UNITS = {
    "displacement_mm": "mm",
    "rainfall_mm": "mm",
    "pore_pressure_kpa": "kPa",
    "crack_width_mm": "mm",
    "temperature_c": "℃",
}

HISTORY_HOURS = 72


# ──────────────────────────── 建表与造数 ────────────────────────────


def build_engine(db_url: str, **kw):
    eng = create_engine(db_url, pool_size=3, max_overflow=5, pool_recycle=3600, echo=False, **kw)
    Base.metadata.create_all(eng)
    return eng


def _gen_series(code: str, hours: int = HISTORY_HOURS) -> list[tuple[str, float, datetime]]:
    """生成一个点位过去 N 小时的读数。种子固定 → 结果可复现。"""
    cfg = SITE_SEEDS.get(code, {"seed": 1, "trend": "stable", "base_disp": 8.0})
    rng = random.Random(cfg["seed"])
    now = datetime.now().replace(minute=0, second=0, microsecond=0)

    accel = cfg["trend"] == "accelerating"
    disp = cfg["base_disp"]
    # 降雨：一次持续事件（模拟"久雨+暴雨"）
    rain_event_start = hours - 30
    pore = 18.0
    rows: list[tuple[str, float, datetime]] = []

    for h in range(hours):
        ts = now - timedelta(hours=hours - 1 - h)

        # 位移：加速型点位在降雨事件后**速率线性上升**（滑坡前的典型形态）。
        # 注意这里必须让"最末 12 小时"内的速率有明显变化，否则
        # 位移速率工具算出来的加速度会淹没在噪声里，趋势判定形同虚设。
        if accel and h >= rain_event_start:
            span = max(1, hours - 1 - rain_event_start)
            progress = (h - rain_event_start) / span          # 0 → 1
            step = 0.05 + 0.55 * progress                     # 0.05 → 0.60 mm/h
        else:
            step = 0.04 + 0.01 * math.sin(h / 5.0)
        disp += step + rng.uniform(-0.008, 0.008)

        # 降雨：事件窗口内递增，峰值在事件中段
        rain = 0.0
        if rain_event_start <= h < hours:
            k = (h - rain_event_start) / max(1, (hours - rain_event_start))
            rain = max(0.0, 14.0 * math.exp(-((k - 0.45) ** 2) / 0.05)) + rng.uniform(0, 1.2)

        # 孔压：随降雨滞后上升（入渗需要时间）
        if accel:
            pore += 0.055 * rain + rng.uniform(-0.08, 0.08)
        else:
            pore += 0.008 * rain + rng.uniform(-0.05, 0.05)

        # 裂缝：加速型点位在后期出现拉裂
        crack = 0.4 + (disp - cfg["base_disp"]) * 0.35
        if not accel:
            crack = 0.35 + rng.uniform(-0.05, 0.05)

        temp = 18 + 7 * math.sin((h % 24) / 24 * 2 * math.pi)

        rows += [
            ("displacement_mm", round(disp, 3), ts),
            ("rainfall_mm", round(rain, 2), ts),
            ("pore_pressure_kpa", round(pore, 2), ts),
            ("crack_width_mm", round(crack, 3), ts),
            ("temperature_c", round(temp, 1), ts),
        ]
    return rows


def seed_if_empty(Session, force: bool = False) -> tuple[int, int]:
    """确保点位与历史读数存在。返回 (点位数, 读数条数)。

    `force=True` 会重建点位与读数。**注意要连带清理有外键指向 site 的表**
    （目前是 alert_event）—— 否则直接删 site 会撞外键约束：
        Cannot delete or update a parent row: a foreign key constraint fails
    实测踩到过：单独跑数据层验证时直接报错。
    这里用裸 SQL 而不是导入 alert_engine，是为了不让数据层反向依赖告警层。
    """
    with Session() as s:
        if force:
            from sqlalchemy import text
            for tbl in ("alert_event",):
                try:
                    s.execute(text(f"DELETE FROM {tbl}"))
                except Exception:  # noqa: BLE001
                    pass          # 表还没建时忽略
            s.commit()
            s.query(SensorReading).delete()
            s.query(Site).delete()
            s.commit()

        n_site = s.scalar(select(func.count()).select_from(Site)) or 0
        if n_site == 0:
            for code, name, desc, group, primary in SITES:
                s.add(Site(code=code, name=name, description=desc,
                           slope_group=group, is_primary=primary))
            s.commit()

        n_read = s.scalar(select(func.count()).select_from(SensorReading)) or 0
        if n_read == 0:
            id_by_code = {r.code: r.id for r in s.query(Site).all()}
            for code, sid in id_by_code.items():
                for metric, val, ts in _gen_series(code):
                    s.add(SensorReading(site_id=sid, metric=metric, value=val,
                                        unit=METRIC_UNITS.get(metric, ""), ts=ts))
            s.commit()

        return (s.scalar(select(func.count()).select_from(Site)) or 0,
                s.scalar(select(func.count()).select_from(SensorReading)) or 0)


# ──────────────────────────── 查询 ────────────────────────────


def _site(Session, code: str):
    with Session() as s:
        return s.query(Site).filter(Site.code == code).first()


def _series(Session, site_id: int, metric: str, hours: int):
    since = datetime.now() - timedelta(hours=hours)
    with Session() as s:
        return [(r.ts, r.value) for r in
                s.query(SensorReading)
                 .filter(SensorReading.site_id == site_id,
                         SensorReading.metric == metric,
                         SensorReading.ts >= since)
                 .order_by(SensorReading.ts).all()]


def _slope(a: list[float], b: list[float]) -> float:
    """两段的平均斜率差（b 段 - a 段），正数表示在加速。"""
    if not a or not b:
        return 0.0
    return (sum(b) / len(b)) - (sum(a) / len(a))


def _rate(pts: list[tuple[datetime, float]]) -> float:
    """按**真实时间跨度**算平均速率（mm/h）。

    不能用"点数"当分母：读数可能因丢包/降采样而不均匀，
    那样算出来的速率会随采样频率漂移，和实际无关。
    """
    if len(pts) < 2:
        return 0.0
    (t0, v0), (t1, v1) = pts[0], pts[-1]
    hrs = (t1 - t0).total_seconds() / 3600
    return (v1 - v0) / hrs if hrs > 0.01 else 0.0


def displacement_rate(Session, code: str, hours: int = 12) -> dict:
    """位移速率与趋势（mm/h，以及是否在加速）。

    **看的是速率的变化，不是位移的绝对值** —— 匀速蠕变和加速蠕变
    在"当前位移"上可能一样，但后者是失稳前兆。
    """
    st = _site(Session, code)
    if not st:
        return {"ok": False, "error": f"点位 {code} 不存在"}
    pts = _series(Session, st.id, "displacement_mm", hours)
    if len(pts) < 4:
        return {"ok": False, "error": f"点位 {code} 位移数据不足"}

    vals = [v for _, v in pts]
    half = len(pts) // 2
    rate_first = _rate(pts[:half + 1])
    rate_last = _rate(pts[half:])
    accel = rate_last - rate_first
    trend = "加速" if accel > 0.05 else ("放缓" if accel < -0.05 else "匀速")

    return {
        "ok": True, "site": code, "hours": hours,
        "cumulative_mm": round(vals[-1], 2),
        "rate_mm_per_h": round(rate_last, 3),
        "rate_prev_mm_per_h": round(rate_first, 3),
        "acceleration_mm_per_h2": round(accel, 3),
        "trend": trend,
    }


def rainfall_summary(Session, hours: int = 24) -> dict:
    """降雨汇总（所有点位共享同一场雨，故按点位取即可）。"""
    st = _site(Session, "S-01")
    if not st:
        return {"ok": False, "error": "无点位数据"}
    pts = _series(Session, st.id, "rainfall_mm", hours)
    if not pts:
        return {"ok": False, "error": "无降雨数据"}
    vals = [v for _, v in pts]
    max_h = max(vals) if vals else 0.0
    return {
        "ok": True, "hours": hours,
        "cumulative_mm": round(sum(vals), 1),
        "max_hourly_mm": round(max_h, 1),
        "wet_hours": sum(1 for v in vals if v > 0.5),
    }


def pore_pressure(Session, code: str, hours: int = 12) -> dict:
    """孔隙水压力与抬升幅度（kPa）。"""
    st = _site(Session, code)
    if not st:
        return {"ok": False, "error": f"点位 {code} 不存在"}
    pts = _series(Session, st.id, "pore_pressure_kpa", hours)
    if len(pts) < 2:
        return {"ok": False, "error": "孔压数据不足"}
    vals = [v for _, v in pts]
    return {
        "ok": True, "site": code,
        "current_kpa": round(vals[-1], 2),
        "rise_kpa": round(vals[-1] - vals[0], 2),
        "hours": hours,
    }


def crack_history(Session, code: str, days: int = 7) -> dict:
    """裂缝宽度变化（mm）。地表拉裂是滑坡的直接前兆。"""
    st = _site(Session, code)
    if not st:
        return {"ok": False, "error": f"点位 {code} 不存在"}
    pts = _series(Session, st.id, "crack_width_mm", days * 24)
    if len(pts) < 2:
        return {"ok": False, "error": "裂缝数据不足"}
    vals = [v for _, v in pts]
    return {
        "ok": True, "site": code,
        "current_mm": round(vals[-1], 2),
        "open_delta_mm": round(vals[-1] - vals[0], 2),
        "days": days,
    }


def neighbor_compare(Session, code: str, hours: int = 6) -> dict:
    """同一边坡上其它点位的位移速率对比。

    **这是判断"整体滑动"还是"局部变形"的关键** ——
    多个点同步同向加速，比单点加速危险得多。
    """
    st = _site(Session, code)
    if not st:
        return {"ok": False, "error": f"点位 {code} 不存在"}
    with Session() as s:
        peers = s.query(Site).filter(Site.slope_group == st.slope_group).all()
        peers = [(p.code, p.name, p.id) for p in peers]

    out = []
    for pcode, pname, pid in peers:
        pts = _series(Session, pid, "displacement_mm", hours)
        if len(pts) < 3:
            continue
        vals = [v for _, v in pts]
        out.append({"site": pcode, "name": pname,
                    "rate_mm_per_h": round(_rate(pts), 3),
                    "cumulative_mm": round(vals[-1], 2)})

    if len(out) < 2:
        return {"ok": False, "error": "同一边坡点位不足，无法对比"}

    rates = [o["rate_mm_per_h"] for o in out]
    accelerating = [o["site"] for o in out if o["rate_mm_per_h"] > 0.25]
    return {
        "ok": True, "slope_group": st.slope_group, "hours": hours, "peers": out,
        "accelerating_sites": accelerating,
        "synchronized": len(accelerating) >= 2,
        "max_rate": round(max(rates), 3), "min_rate": round(min(rates), 3),
    }


def current_snapshot(Session, code: str) -> dict:
    """一次性取该点位最新的一整套读数 —— 供"综合评估"用。"""
    st = _site(Session, code)
    if not st:
        return {"ok": False, "error": f"点位 {code} 不存在"}
    snap = {"ok": True, "site": code, "name": st.name}
    for metric in METRIC_UNITS:
        pts = _series(Session, st.id, metric, 3)
        if pts:
            snap[metric] = round(pts[-1][1], 2)
    return snap
