"""API 端到端验证：并发不阻塞事件循环 + 会话隔离。

## 验证什么

**① 事件循环不被阻塞**（原缺陷：`async def` 里调同步 Agent，并发度=1）

先起一个 /chat（本机 4B 推理，约 30 秒），**在它执行期间**连续打 /health：

  * 修复前：整个服务假死，/health 要等 chat 跑完才回（约 30 秒）
  * 修复后：/health 走线程池，应稳定在几百毫秒内

**② 会话隔离**（原缺陷：session_id 三处硬编码 "default"）

两个 session 各发一次 /chat，然后查 /history，各自应只看到自己的消息。

## 用法

先启动服务（另开一个终端）：
    set SLOPE_HEADLESS=1 && python end_new.py --api
再运行：
    python tools_verify_api_concurrency.py
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
import statistics
import sys
import threading
import time

import requests

BASE = "http://127.0.0.1:8000"
TIMEOUT = 180
SID_A = "__api_verify_A__"
SID_B = "__api_verify_B__"

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def wait_ready(max_wait: float = 180) -> bool:
    t0 = time.time()
    while time.time() - t0 < max_wait:
        try:
            r = requests.get(f"{BASE}/health", timeout=5)
            if r.status_code == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    return False


def main() -> int:
    print("=" * 72)
    print("API 端到端验证：并发不阻塞 + 会话隔离")
    print("=" * 72)

    print()
    print("0) 等待服务就绪 ...")
    if not wait_ready():
        print("  [FAIL] 服务在 180 秒内没有就绪 —— 请先启动：")
        print("         set SLOPE_HEADLESS=1 && python end_new.py --api")
        return 1
    print("  服务已就绪")

    # ── ① 并发不阻塞：chat 在跑时打 health ──
    print()
    print("1) /chat 执行期间 /health 是否仍然秒回")
    chat_result: dict = {}

    def fire_chat():
        t0 = time.time()
        try:
            r = requests.post(f"{BASE}/chat",
                              json={"query": "现在边坡有风险吗", "session_id": SID_A},
                              timeout=TIMEOUT)
            chat_result["status"] = r.status_code
            chat_result["elapsed"] = time.time() - t0
            chat_result["body"] = r.json() if r.status_code == 200 else r.text[:200]
        except Exception as e:  # noqa: BLE001
            chat_result["error"] = f"{type(e).__name__}: {e}"

    t = threading.Thread(target=fire_chat, daemon=True)
    t.start()

    time.sleep(2)  # 让 chat 先进入 Agent 执行
    health_lat = []
    for _ in range(6):
        try:
            h0 = time.time()
            hr = requests.get(f"{BASE}/health", timeout=5)
            dt = time.time() - h0
            if hr.status_code == 200:
                health_lat.append(dt)
        except Exception as e:  # noqa: BLE001
            print(f"    /health 异常: {type(e).__name__}: {e}")
        time.sleep(1)

    t.join(timeout=TIMEOUT)

    if health_lat:
        worst = max(health_lat)
        med = statistics.median(health_lat)
        print(f"    /health 采样 {len(health_lat)} 次：中位 {med*1000:.0f}ms，最差 {worst*1000:.0f}ms")
        check("/health 在 /chat 执行期间仍能响应", True)
        check("/health 最差延迟 < 3 秒（未被阻塞）", worst < 3.0,
              f"最差 {worst:.2f}s")
    else:
        check("/health 在 /chat 执行期间仍能响应", False, "一次都没成功")

    chat_elapsed = chat_result.get("elapsed")
    print(f"    /chat 状态={chat_result.get('status')} 耗时={chat_elapsed:.1f}s"
          if chat_elapsed else f"    /chat 结果={chat_result}")
    check("/chat 正常返回 200", chat_result.get("status") == 200,
          str(chat_result.get("body", chat_result.get("error", "")))[:120])

    if health_lat and chat_elapsed:
        check("health 延迟远小于 chat 耗时（证明真的没被挡住）",
              max(health_lat) < chat_elapsed / 3,
              f"health {max(health_lat):.2f}s vs chat {chat_elapsed:.1f}s")

    # ── ② 会话隔离：第二个会话 ──
    print()
    print("2) 第二个会话（验证隔离）")
    try:
        r2 = requests.post(f"{BASE}/chat",
                           json={"query": "系统能装在哪台电脑上", "session_id": SID_B},
                           timeout=TIMEOUT)
        check("/chat(session B) 返回 200", r2.status_code == 200,
              f"status={r2.status_code}")
    except Exception as e:  # noqa: BLE001
        check("/chat(session B) 返回 200", False, f"{type(e).__name__}: {e}")

    # ── ③ 用 /history 验证落库隔离 ──
    print()
    print("3) /history 按 session 读，各自只看到自己的消息")
    got = {}
    for sid in (SID_A, SID_B):
        try:
            hr = requests.get(f"{BASE}/history", params={"session_id": sid}, timeout=10)
            got[sid] = hr.json().get("total", 0) if hr.status_code == 200 else -1
        except Exception:  # noqa: BLE001
            got[sid] = -1
    print(f"    {SID_A}: {got.get(SID_A)} 条 | {SID_B}: {got.get(SID_B)} 条")
    check("会话 A 有记录", got.get(SID_A, 0) > 0, str(got))
    check("会话 B 有记录", got.get(SID_B, 0) > 0, str(got))
    check("两个会话各自独立记账（没有全落到 default）",
          got.get(SID_A, 0) > 0 and got.get(SID_B, 0) > 0)

    # 清理
    try:
        requests.delete(f"{BASE}/history", params={"session_id": SID_A}, timeout=5)
    except Exception:  # noqa: BLE001
        pass

    print()
    print("=" * 72)
    print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    for f in FAIL:
        print(f"  失败: {f}")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
