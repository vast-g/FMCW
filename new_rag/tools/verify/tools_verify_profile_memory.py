"""验证结构化长期画像记忆（slope_memory.ProfileMemory）。

自测边界（重要）：
    **不连生产 MySQL**，全部跑在临时 SQLite 文件上。
    原因很直接 —— 这个脚本要能被反复运行、且不能污染 `slope_agent` 库里的
    真实 `user_profile` / `conversation_summary` 数据；用内存/临时库最安全。

覆盖：
    1. 建表幂等（连续两次初始化不报错）
    2. save_profile → load_profile 往返一致
    3. llm=None 时 maybe_update_from_turns 返回 None 且不抛异常
    4. add_summary → recent_summaries 往返
    5. render_for_prompt 空画像返回空串、非空画像含字段值
    6. stats() 返回合理数字
    7. （附加）有 LLM 时按 extract_every 倍数触发抽取
    8. （附加）LLM 抛异常时降级为 None，不打断主流程
"""
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
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
# 项目习惯：Windows 控制台默认 GBK，摘要/画像里的 emoji 或特殊字符会直接抛
# UnicodeEncodeError 把自测搞挂，所以统一把 stdout 的错误处理改成 replace。
sys.stdout.reconfigure(errors="replace")

from sqlalchemy import create_engine  # noqa: E402

from slope_memory import PROFILE_FIELDS, ProfileMemory  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


# ── 假 LLM：模拟"模型返回一段 JSON"和"模型直接炸了"两种极端 ──
class _FakeLLM:
    """只实现 invoke，验证 _call_llm 的最小适配路径。"""

    def __init__(self, payload: str):
        self.payload = payload
        self.calls = 0

    def invoke(self, prompt: str) -> str:  # noqa: ARG002
        self.calls += 1
        return self.payload


class _BoomLLM:
    def invoke(self, prompt: str) -> str:  # noqa: ARG002
        raise RuntimeError("模拟 LLM 服务不可用")


tmpdir = Path(tempfile.mkdtemp(prefix="profile_mem_"))
db_file = tmpdir / "test_memory.db"
# SQLite URL 必须用 POSIX 风格路径，Windows 的反斜杠会被当成转义
engine = create_engine(f"sqlite:///{db_file.as_posix()}")

print("=" * 74)
print("结构化长期画像记忆自测（SQLite 临时库，不连生产 MySQL）")
print("=" * 74)
print(f"临时库：{db_file}")

# ══════════ 1. 建表幂等 ══════════
print("\n1) 建表幂等")
try:
    m1 = ProfileMemory(engine=engine, llm=None, user_id=1, extract_every=5)
    m2 = ProfileMemory(engine=engine, llm=None, user_id=1, extract_every=5)
    check("连续两次 ProfileMemory 初始化不报错", True)
except Exception as e:  # noqa: BLE001
    m1 = m2 = None
    check("连续两次 ProfileMemory 初始化不报错", False, f"{type(e).__name__}: {e}")

mem = m1 or ProfileMemory(engine=engine, llm=None, user_id=1, extract_every=5)

# ══════════ 2. 画像往返 ══════════
print("\n2) save_profile → load_profile 往返")
sample = {
    "role": "运维人员",
    "focus_sites": ["S-01", "S-03"],
    "concerns": ["位移速率", "降雨预警"],
    "threshold_prefs": "认为0.3m阈值过严",
    "interaction_style": "要求给数字依据",
    "last_risk_topic": "连续降雨后的位移加速",
}
mem.save_profile(sample)
loaded = mem.load_profile()
check("往返字段值一致", loaded == sample, f"loaded={loaded}")
check("load_profile 未写过的用户返回 {}",
      mem.load_profile(user_id=999) == {}, f"{mem.load_profile(user_id=999)}")

# 空值/未知值应被剔除，不能把 "未知" 当成真实画像
mem.save_profile({"role": "管理员", "concerns": [], "focus_sites": "未知", "threshold_prefs": ""})
cleaned = mem.load_profile()
check("空值/未知值被归一化剔除", cleaned == {"role": "管理员"}, f"{cleaned}")

# ══════════ 3. llm=None 优雅跳过 ══════════
print("\n3) llm=None 时优雅跳过")
try:
    r = mem.maybe_update_from_turns([("用户", "S-01 这周位移多少"), ("AI", "本周 12mm")])
    check("llm=None 时返回 None 且不抛异常", r is None, f"返回 {r!r}")
except Exception as e:  # noqa: BLE001
    check("llm=None 时返回 None 且不抛异常", False, f"{type(e).__name__}: {e}")

# ══════════ 4. 摘要往返 ══════════
print("\n4) add_summary → recent_summaries 往返")
mem2 = ProfileMemory(engine=engine, llm=None, user_id=42, extract_every=5)
for i in range(1, 5):
    mem2.add_summary(f"第{i}段摘要：讨论 S-0{i} 的位移趋势")
got = mem2.recent_summaries(limit=3)
check("limit 生效（只取最近 3 条）", len(got) == 3, f"len={len(got)}")
check("取到的是最近三条且按时间正序",
      got == [f"第{i}段摘要：讨论 S-0{i} 的位移趋势" for i in (2, 3, 4)], f"{got}")
check("摘要按用户隔离（用户1 没有摘要）", mem.recent_summaries(limit=3) == [])

# ══════════ 5. render_for_prompt ══════════
print("\n5) render_for_prompt 渲染")
empty_render = mem.render_for_prompt({})
check("空画像返回空串", empty_render == "", repr(empty_render))
prof = mem.load_profile()
text = mem.render_for_prompt(prof)
check("非空画像包含字段值", ("运维人员" in text) or ("管理员" in text), text.replace("\n", " | "))
full = mem.render_for_prompt(sample)
check("渲染包含点位与阈值偏好",
      "S-01" in full and "认为0.3m阈值过严" in full and "关注点位" in full,
      full.replace("\n", " | "))
check("渲染包含全部字段标签",
      all(lbl in full for lbl in ("用户角色", "关注点位", "长期关注",
                                  "阈值偏好", "沟通偏好", "上次风险话题")))

# ══════════ 6. stats ══════════
print("\n6) stats()")
st = mem.stats()
check("stats 返回 profile_fields 合理数字",
      st.get("profile_fields") == len(mem.load_profile()) and st.get("profile_fields", 0) >= 1,
      f"{st}")
check("stats 返回 summaries 合理数字",
      mem2.stats().get("summaries") == 4, f"{mem2.stats()}")
check("stats 的 db_ok 为 True", st.get("db_ok") is True, f"{st}")

# ══════════ 7. 有 LLM 时按周期触发 ══════════
print("\n7) 有 LLM 时按 extract_every 倍数触发")
fake = _FakeLLM('```json\n{"role": "工程师", "focus_sites": ["S-02"], '
                '"concerns": ["设备排障"]}\n```')
mem3 = ProfileMemory(engine=engine, llm=fake, user_id=7, extract_every=2)
r1 = mem3.maybe_update_from_turns([("用户", "S-02 的测斜仪老掉线")])
check("未到周期（第1轮）返回 None", r1 is None, f"{r1!r}")
r2 = mem3.maybe_update_from_turns([("AI", "建议检查供电")])
check("到周期（第2轮）触发抽取并返回画像",
      isinstance(r2, dict) and r2.get("role") == "工程师", f"{r2!r}")
check("代码块包裹的 JSON 也能解析", r2 is not None and r2.get("focus_sites") == ["S-02"])
check("抽取结果已落库", mem3.load_profile() == r2, f"{mem3.load_profile()}")

# 合并语义：列表字段并集、字符串字段覆盖
mem3.save_profile({"concerns": ["位移速率"], "role": "访客", "focus_sites": ["S-05"]})
fake.payload = '{"concerns": ["设备排障"], "role": "管理员", "focus_sites": ["S-02"]}'
# 注意这里给 2 轮：mem3 的周期是 2，第 2 次触发后累计是 2，再补 1 轮只有 3（不触发），
# 必须凑到 4 才会再抽一次。这个坑在写脚本时实测撞到了。
r3 = mem3.maybe_update_from_turns([("用户", "再说一次"), ("AI", "好的")])
check("累计到第4轮再次触发", isinstance(r3, dict), f"{r3!r}")
merged = mem3.load_profile()
check("列表字段取并集（不丢历史）", merged.get("focus_sites") == ["S-05", "S-02"], f"{merged}")
check("字符串字段非空覆盖", merged.get("role") == "管理员", f"{merged}")
check("concerns 也是并集", set(merged.get("concerns", [])) == {"位移速率", "设备排障"}, f"{merged}")

# ══════════ 8. LLM 异常降级 ══════════
print("\n8) LLM 异常时降级")
mem4 = ProfileMemory(engine=engine, llm=_BoomLLM(), user_id=8, extract_every=1)
before = mem4.load_profile()
try:
    r4 = mem4.maybe_update_from_turns([("用户", "测试异常降级")])
    check("LLM 抛异常时返回 None 且不冒泡", r4 is None, f"{r4!r}")
except Exception as e:  # noqa: BLE001
    check("LLM 抛异常时返回 None 且不冒泡", False, f"{type(e).__name__}: {e}")
check("异常后画像保持不变", mem4.load_profile() == before)

# 非法 JSON 同样不该写脏数据
bad = ProfileMemory(engine=engine, llm=_FakeLLM("我觉得这个用户是运维人员"), user_id=9, extract_every=1)
bad_r = bad.maybe_update_from_turns([("用户", "随便聊聊")])
check("非 JSON 输出不写脏数据", bad_r is None and bad.load_profile() == {}, f"{bad_r!r}")

# ══════════ 收尾 ══════════
total = len(PASS) + len(FAIL)
print("\n" + "=" * 74)
if FAIL:
    print("失败项：")
    for name in FAIL:
        print(f"  - {name}")
print(f"通过 {len(PASS)} / {total}")
print("=" * 74)

try:
    engine.dispose()
    shutil.rmtree(tmpdir, ignore_errors=True)
except Exception:  # noqa: BLE001
    pass

sys.exit(0 if not FAIL else 1)
