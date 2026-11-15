"""验证告警规则引擎：确定性、去重、可审计、复合升级。"""
from __future__ import annotations


# ── 路径引导（本脚本已整理进 tools/ 子目录）──────────────────────────
# 项目根**不能靠"上跳固定层数"来算** —— 脚本可能在 tools/（一层）
# 也可能在 tools/verify/ 或 tools/ops/（两层），层数一变就算错。
# 所以改成「向上找标志文件」：一路往上，第一个含 config.py 的目录就是项目根。
#
# 需要它做三件事：
#   1) 项目根加进 sys.path → import config / end_new 才能成功
#   2) 本目录与同级 tools/* 也加进去 → verify/ 与 ops/ 之间可以互相 import
#   3) chdir 到项目根 → rag_docs / reports/ 这类相对路径仍指向同一个地方
import os as _os
import sys as _sys

_ME = _os.path.dirname(_os.path.abspath(__file__))
_ROOT = _ME
while _ROOT != _os.path.dirname(_ROOT):
    if _os.path.exists(_os.path.join(_ROOT, "config.py")):
        break
    _ROOT = _os.path.dirname(_ROOT)

for _p in (_ROOT, _ME):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
for _sib in ("verify", "ops"):
    _p = _os.path.join(_ROOT, "tools", _sib)
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.append(_p)
_os.chdir(_ROOT)
# ────────────────────────────────────────────────────────────────
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("SLOPE_HEADLESS", "1")
sys.stdout.reconfigure(errors="replace")

from sqlalchemy.orm import sessionmaker  # noqa: E402

import alert_engine as AE  # noqa: E402
import config as _cfg  # noqa: E402
import slope_monitor as M  # noqa: E402

url = (f"mysql+pymysql://{_cfg.DB_USER}:{quote_plus(_cfg.DB_PASSWORD)}"
       f"@{_cfg.DB_HOST}:{_cfg.DB_PORT}/{_cfg.DB_NAME}")
eng = M.build_engine(url)
M.Base.metadata.create_all(eng)      # 建 site/sensor_reading/alert_rule/alert_event
S = sessionmaker(bind=eng)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("告警规则引擎验证")
print("=" * 74)

M.seed_if_empty(S)
with S() as s:
    n_rule = AE.seed_rules(s, force=True)
# 清掉历史事件，保证本脚本可重复运行。
# 引擎有 30 分钟去重窗口，若不清，第二次运行会因为"刚报过"而 0 条触发，
# 看起来像功能坏了 —— 其实是测试不幂等（实测踩到）。
with S() as s:
    s.query(AE.AlertEvent).delete()
    s.commit()
print(f"\n规则数：{n_rule}（已清空历史事件以保证可重复运行）")

print()
print("1) 首轮评估：危险点位应触发，平稳点不应触发")
with S() as s:
    for code in ("S-01", "S-02", "S-03", "S-04"):
        site = s.query(M.Site).filter(M.Site.code == code).first()
        fired = AE.evaluate_site(s, site, record=False)
        lv = ",".join(sorted({f["level"] for f in fired})) or "-"
        print(f"   {code}: 触发 {len(fired)} 条 [{lv}]")
        for f in fired:
            print(f"        · {f['name']}（{f['level']}）实测 {f['value']} vs 阈值 {f['threshold']}")

with S() as s:
    site01 = s.query(M.Site).filter(M.Site.code == "S-01").first()
    f01 = AE.evaluate_site(s, site01, record=False)
    site04 = s.query(M.Site).filter(M.Site.code == "S-04").first()
    f04 = AE.evaluate_site(s, site04, record=False)
check("危险点位触发多条告警", len(f01) >= 3, f"{len(f01)} 条")
check("平稳点位触发更少告警", len(f04) < len(f01), f"{len(f04)} vs {len(f01)}")
check("出现了复合升级（多因素叠加）",
      any(f["rule"] == "R-COMPOSITE-01" for f in f01),
      str([f["rule"] for f in f01]))

print()
print("2) 确定性：同样数据跑两次，结论必须一致")
with S() as s:
    site = s.query(M.Site).filter(M.Site.code == "S-01").first()
    a = [f["rule"] for f in AE.evaluate_site(s, site, record=False)]
    b = [f["rule"] for f in AE.evaluate_site(s, site, record=False)]
check("两次评估结果完全一致（可复现）", a == b, f"{a}")

print()
print("3) 去重：写入后立刻重跑，不应重复产生事件")
with S() as s:
    s.query(AE.AlertEvent).filter(AE.AlertEvent.rule_code.like("R-%")).delete(
        synchronize_session=False)
    s.commit()
with S() as s:
    r1 = AE.run_once(s, record=True)
with S() as s:
    r2 = AE.run_once(s, record=True)
check("首轮产生了事件", r1["fired"] > 0, f"{r1['fired']} 条")
check("紧接着的第二轮被去重窗口抑制", r2["fired"] == 0, f"{r2['fired']} 条")

print()
print("4) 可审计：事件里保存了当时的判定依据")
with S() as s:
    alerts = AE.active_alerts(s, hours=24)
print(f"   近期告警 {len(alerts)} 条")
if alerts:
    top = alerts[0]
    print(f"   最高等级示例: [{top['level_text']}] {top['message'][:70]}")
    ev = top["evidence"]
    check("事件带 evidence（可倒查当时数据）",
          isinstance(ev, dict) and len(ev) > 3, f"{len(ev)} 个字段")
    need = {"displacement_rate", "rainfall_cum", "pore_pressure_rise"}
    check("evidence 含关键指标", need & set(ev.keys()) == need, str(sorted(ev)[:6]))
    check("最高等级达到 red（多点同步 → 整体滑动征兆）",
          any(a["level"] == "red" for a in alerts),
          str(sorted({a["level"] for a in alerts})))

print()
print("5) 确认留痕")
if alerts:
    with S() as s:
        ok = AE.acknowledge(s, alerts[0]["id"], "值班员-张三")
    with S() as s:
        again = AE.active_alerts(s, hours=24)
        target = next((x for x in again if x["id"] == alerts[0]["id"]), None)
    check("确认成功", ok)
    check("确认后 acknowledged 置位", bool(target and target["acknowledged"]))
    with S() as s:
        ev = s.get(AE.AlertEvent, alerts[0]["id"])
        check("记录了确认人和时间",
              bool(ev.acknowledged_by and ev.acknowledged_at),
              f"{ev.acknowledged_by} / {ev.acknowledged_at}")
    with S() as s:
        unacked = AE.active_alerts(s, hours=24, only_unacked=True)
    check("未确认过滤生效", all(not x["acknowledged"] for x in unacked),
          f"未确认 {len(unacked)} 条")

print()
print("6) 阈值可配置（不是写死在代码里）")
with S() as s:
    rule = s.query(AE.AlertRule).filter(AE.AlertRule.code == "R-RATE-01").first()
    old = rule.threshold
    rule.threshold = 99.0          # 调高到不可能触发
    s.commit()
with S() as s:
    site = s.query(M.Site).filter(M.Site.code == "S-01").first()
    fired = AE.evaluate_site(s, site, record=False)
    hit = [f for f in fired if f["rule"] == "R-RATE-01"]
with S() as s:
    rule = s.query(AE.AlertRule).filter(AE.AlertRule.code == "R-RATE-01").first()
    rule.threshold = old
    s.commit()
check("调高阈值后该规则不再触发（阈值真的生效）", len(hit) == 0, f"{old} → 99.0")

print()
print("=" * 74)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
for f in FAIL:
    print(f"  失败: {f}")
print("=" * 74)
