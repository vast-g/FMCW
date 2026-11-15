# -*- coding: utf-8 -*-
"""回归：模型思考泄漏进正文（这个 bug 已经复现两次，必须锁住）。

## 泄漏是怎么发生的

qwen3 把内部推理写进 content，正常会以 `</think>` 收尾，剥掉即可。
真正的坑在于：**`reasoning=False` 会让思考混进 content 且不带标记** ——
那时 `strip_thinking` 没有任何标记可依，整段推理就被当成答案返回。

实测（num_predict 压到 128 逼它截断，同一问题）：
    reasoning=False → content = 188 字【推理】，无 </think>   ← 必然泄漏
    reasoning=True  → content = 0 字                          ← 能明确判定没产出

## 本脚本覆盖三层

  1. `strip_thinking` 的**纯函数**行为（快，不依赖模型）
  2. 模型创建处**参数设置**（静态检查，防止有人改回去）
  3. **真实调用** Ollama，确认 reasoning=True 时 content 里没有推理
"""
from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(errors="replace")

# ── 路径引导（本脚本在 tools/verify/ 下）──────────────────────────
_ME = os.path.dirname(os.path.abspath(__file__))
_ROOT = _ME
while _ROOT != os.path.dirname(_ROOT):
    if os.path.exists(os.path.join(_ROOT, "config.py")):
        break
    _ROOT = os.path.dirname(_ROOT)
for _p in (_ROOT, _ME):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.chdir(_ROOT)
# ────────────────────────────────────────────────────────────────

PASS: list[str] = []
FAIL: list[str] = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("回归：思考泄漏")
print("=" * 74)

from end_new import strip_thinking          # noqa: E402

print("\n1) strip_thinking 纯函数行为")

# 1a. 标准情况：思考 + </think> + 答案
s = "好的，用户问…我需要调用 tool_get_rainfall…</think> 当前边坡风险较高，建议加密监测。"
out = strip_thinking(s)
check("有 </think> → 只留标记之后", out == "当前边坡风险较高，建议加密监测。", repr(out[:40]))

# 1b. 只有开标签（被截断）→ 整段都是思考
s = "<think>好的，用户问…我需要调用工具…然后"
check("只有 <think> 未闭合 → 返回空", strip_thinking(s) == "", repr(strip_thinking(s)[:40]))

# 1c. ★ 核心：无任何标签的"未闭合思考"（这正是泄漏的形态）
s = ("好的，我现在需要处理用户的问题：这几个参数怎么影响边坡监测的？"
     "首先，用户提到'这几个参数'，但之前的对话中没有明确说明具体是哪些参数。"
     "不过，根据上下文，用户可能指的是之前告警触发的参数，比如位移加速、降雨量、孔压等。"
     "接下来，我需要根据规则和工具来回答。根据规则4，当用户问…"
     * 3)
check("无标记 + 元叙述腔 → 判为思考，返回空", strip_thinking(s) == "",
      f"输入 {len(s)} 字")

# 1d. ★ 不能误伤：同长度但谈业务的真答案必须保留
s = ("位移速率 0.56mm/h 已超过 0.30 的报警阈值，说明坡体处于加速变形阶段。"
     "累计雨量 170.82mm 持续补给地下水，使孔隙水压力升到 1.65kPa，"
     "有效应力随之下降、抗滑力减弱。S-01 与 S-02 同步加速，"
     "说明是整体滑动而不是局部变形，建议立即现场核查并加密监测。" * 2)
out = strip_thinking(s)
check("真答案（谈业务）不被误伤", out == s, f"保留 {len(out)} 字")

# 1e. 短文本不该被判为推理
s = "根据规则，当前风险较高。"
check("短文本不误判", strip_thinking(s) == s)

# 1f. 空输入
check("空输入安全", strip_thinking("") == "" and strip_thinking(None) is None)

print("\n2) 模型创建处的参数（静态检查）")
src = open("end_new.py", encoding="utf-8").read()
n_true = src.count("reasoning=True,")
n_false = src.count("reasoning=False,")
check("不再有 reasoning=False", n_false == 0, f"reasoning=False 出现 {n_false} 次")
check("4 处都是 reasoning=True", n_true == 4, f"reasoning=True 出现 {n_true} 次")

print("\n3) 真实调用：reasoning=True 时 content 里不应有推理")
try:
    import requests
    from config import settings as CFG

    base = getattr(CFG, "llm_base_url", "http://127.0.0.1:11434")
    model = getattr(CFG, "llm_model", "qwen3:4b")

    # 3a. 正常预算：content 应是干净答案
    #
    # ⚠️ 预算必须留够"思考 + 作答"两部分。
    # 实测（2026-10-17，同一 prompt）：
    #     num_predict=2048 → thinking 2970 字，content 只剩 14 字（被截断）
    #     num_predict=4096 → thinking 3406 字，content 72 字   ✅
    # 这个 prompt 会让 qwen3 思考近 3000 字，所以 2048 是**不够**的 ——
    # 原来写 2048 时这条断言会因为"预算被思考吃光"而假失败，
    # 表现出来是"content 0 字"，看起来像是思考泄漏回归，其实只是预算不足。
    r = requests.post(base.rstrip("/") + "/api/chat", json={
        "model": model,
        "messages": [{"role": "user",
                      "content": "位移速率0.56mm/h、雨量170mm、孔压1.65kPa，"
                                 "一句话说明这几个参数对边坡的影响。"}],
        "stream": False,
        "think": True,                       # ← 与项目现在传的 reasoning=True 一致
        "options": {"num_predict": 4096},
    }, timeout=900, proxies={"http": None, "https": None})
    msg = (r.json() or {}).get("message") or {}
    content = (msg.get("content") or "").strip()
    thinking = msg.get("thinking")

    check("正常预算下有产出", len(content) > 20, f"content {len(content)} 字")
    check("content 里不含 </think> 标记", "</think>" not in content)
    check("思考被分到独立字段", bool(thinking), f"thinking {len(thinking or '')} 字")
    check("strip_thinking 后仍是完整答案", len(strip_thinking(content)) > 20)

    # 3b. ★ 预算被思考吃光时：content 应为空（可判定"没产出"），而不是一段推理
    r = requests.post(base.rstrip("/") + "/api/chat", json={
        "model": model,
        "messages": [{"role": "user",
                      "content": "请详细论述位移速率、累计雨量、孔隙水压力、地表裂缝"
                                 "四个参数分别通过什么机理影响边坡稳定性。"}],
        "stream": False,
        "think": True,
        "options": {"num_predict": 128},
    }, timeout=600, proxies={"http": None, "https": None})
    msg = (r.json() or {}).get("message") or {}
    content = (msg.get("content") or "").strip()
    thinking = (msg.get("thinking") or "").strip()

    # 这是本次修复的**核心断言**：截断时不能把推理当答案返回
    leaked = len(content) > 0 and len(strip_thinking(content)) > 0
    check("★ 预算耗尽时不泄漏推理（content 为空或可剥离）", not leaked,
          f"content={len(content)} 字, thinking={len(thinking)} 字")
    check("预算耗尽时思考确实发生了（说明是被截断而非没思考）",
          len(thinking) > 0, f"thinking={len(thinking)} 字")
except Exception as exc:
    check("真实调用检查", False, f"{type(exc).__name__}: {exc}（Ollama 未启动？）")

print()
print("=" * 74)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
for f in FAIL:
    print(f"  失败: {f}")
print("=" * 74)
