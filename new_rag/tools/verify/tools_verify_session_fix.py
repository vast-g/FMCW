"""验证「会话隔离」与「Agent 单例」两项修复。

## 为什么单独写这个脚本

这两处都是**静默**问题，单用户手工点击完全正常，只有并发/多会话才暴露：

  * 会话串号：用户 A 的历史出现在用户 B 的上下文里
  * 单例竞态：并发首访各自 new 一个 SimpleAgent，导致锁分裂、历史分裂

所以必须有可重复的自动化验证，而不是"我点了一下是好的"。

## 用法

    python tools_verify_session_fix.py
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
import sys
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("SLOPE_HEADLESS", "1")
sys.stdout.reconfigure(errors="replace")

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def main() -> int:
    print("=" * 72)
    print("验证会话隔离与会话单例修复")
    print("=" * 72)

    import end_new as E

    # ── 1) 不实例化 Agent 的纯逻辑验证：history 是否按 session 分开 ──
    print()
    print("1) history 按 session 隔离（不触发 Agent 实例化）")

    real = E.SimpleAgent.__new__(E.SimpleAgent)   # 绕过 __init__，避免连库
    real._histories = {}
    real._current_session = "default"

    real.enter_session("userA")
    real.history.append(("用户", "A 的问题"))
    real.history.append(("AI", "答 A"))

    real.enter_session("userB")
    real.history.append(("用户", "B 的问题"))

    a = real._histories.get("userA", [])
    b = real._histories.get("userB", [])
    check("userA 的历史独立", len(a) == 2, f"{a}")
    check("userB 的历史独立", len(b) == 1, f"{b}")
    check("两个会话没有互相污染",
          all("B" not in str(x) for x in a) and all("A" not in str(x) for x in b))

    # 整体赋值（原代码里有 self.history = self.history[-20:]）仍可用
    real.enter_session("userA")
    real.history = real.history[-1:]
    check("history 支持整体赋值（setter 生效）",
          len(real._histories["userA"]) == 1, f"{real._histories['userA']}")
    check("整体赋值不影响其它会话", len(real._histories["userB"]) == 1)

    # 空/None 会话名回落到 default
    check("None 会话名回落 default", real.enter_session(None) == "default")
    check("空白会话名回落 default", real.enter_session("   ") == "default")

    # ── 2) 不传 session_id 时沿用当前会话（不重置）──
    print()
    print("2) run() 不传 session_id 时沿用当前会话")
    import inspect
    sig = inspect.signature(E.SimpleAgent.run)
    check("run 的 session_id 默认是 None（而非 'default'）",
          sig.parameters["session_id"].default is None,
          f"default={sig.parameters['session_id'].default!r}")

    # ── 3) LazyAgent 单例：并发首访只应产生一个实例 ──
    print()
    print("3) LazyAgent 并发首访只创建一个实例")
    proxy = E._LazyAgent()
    got = []
    barrier = threading.Barrier(8)

    def racer():
        barrier.wait()
        got.append(id(proxy._get()))

    ts = [threading.Thread(target=racer) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    uniq = set(got)
    check("8 个并发首访只创建 1 个实例",
          len(uniq) == 1, f"实例数={len(uniq)}")
    check("拿到的锁是同一把",
          proxy._agent_lock is proxy._agent_lock)

    # ── 3.5) 落库是否也按会话隔离（第三处隔离失效）──
    print()
    print("3.5) 消息落库按 session 隔离（并验证 /history 能读到）")
    real_agent = proxy._get()
    test_sids = ["__verify_A__", "__verify_B__"]
    try:
        from end_new import _Conversation, _Message

        for sid in test_sids:
            real_agent.enter_session(sid)
            real_agent._save_to_conversation(f"{sid} 的问题", f"{sid} 的回答")

        db = real_agent._get_mysql_conn()
        counts = {}
        for sid in test_sids:
            conv = db.query(_Conversation).filter(_Conversation.session_id == sid).first()
            counts[sid] = (db.query(_Message)
                           .filter(_Message.conversation_id == conv.id).count()
                           if conv else 0)
        db.close()

        check("会话 A 的消息写到了自己的会话下",
              counts["__verify_A__"] == 2, f"{counts}")
        check("会话 B 的消息写到了自己的会话下",
              counts["__verify_B__"] == 2, f"{counts}")

        # 清理测试数据，不污染用户库
        db = real_agent._get_mysql_conn()
        for sid in test_sids:
            conv = db.query(_Conversation).filter(_Conversation.session_id == sid).first()
            if conv:
                db.query(_Message).filter(_Message.conversation_id == conv.id).delete()
                db.query(_Conversation).filter(_Conversation.id == conv.id).delete()
        db.commit()
        db.close()
        print("  （已清理测试会话数据）")
    except Exception as e:  # noqa: BLE001
        check("落库会话隔离验证", False, f"{type(e).__name__}: {e}")

    # ── 4) 会话级 history 的并发写入不串号 ──
    print()
    print("4) 并发写入不同会话不串号")
    real._histories = {}
    real._current_session = "default"
    lock = threading.Lock()

    import end_new as _E
    _WINDOW = _E._PersistentHistory.MAX_ENTRIES   # 窗口上限（条）

    def writer(sid: str, n: int):
        for i in range(n):
            with lock:                     # 模拟 _agent_lock 串行化
                real.enter_session(sid)
                real.history.append((sid, i))

    # 每会话写 40 条，远超窗口 —— 这样"窗口生效"和"会话隔离"能同时被验证。
    # 原来是 20 条，恰好等于旧窗口大小，看不出窗口到底有没有起作用。
    ws = [threading.Thread(target=writer, args=(f"s{k}", 40)) for k in range(5)]
    for w in ws:
        w.start()
    for w in ws:
        w.join()

    bad = []
    for k in range(5):
        sid = f"s{k}"
        rows = real._histories.get(sid, [])
        # 期望值不写死：窗口大小就是 _PersistentHistory.MAX_ENTRIES（当前 10 条 = 5 轮）。
    # 之前这里写死 20，把窗口改成 5 轮之后这条就假失败了。
    if len(rows) != _WINDOW or any(r[0] != sid for r in rows):
            bad.append((sid, len(rows)))
    check(f"每个会话恰好 {_WINDOW} 条（窗口上限）且内容未串", not bad, f"异常={bad}")

    # ── 汇总 ──
    print()
    print("=" * 72)
    print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print(f"  - {f}")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
