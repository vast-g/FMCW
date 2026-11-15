"""无头（headless）运行支持。

## 问题

`end_new.py` 在**模块级**就创建 tkinter 根窗口（`root=tk.Tk()`）并启动采集线程，
因此只要 `import end_new` 就会尝试建立真实窗口。在没有窗口系统的环境
（Docker 容器、CI runner、无 DISPLAY 的 Linux）中导入会直接失败，
导致核心推理链路无法被评测、无法被容器化。

`eval_rag_params.py` 里已经有 20 行临时猴子补丁来绕过这个问题，但它散落在
评测脚本里、不可复用、也不覆盖 API 模式。

## 方案

把"无头降级"收敛成本模块。当 `HEADLESS`（环境变量 `SLOPE_HEADLESS=1` 或
`HEADLESS=true`）打开时，用轻量 stub 替换 tkinter 的窗口/控件类：

* stub 的实例化**不产生任何真实窗口**；
* 属性访问与链式调用全部返回自身或空操作，因此既有的 GUI 构建代码
  可以**原样执行**而不抛异常；
* `mainloop()` 立即返回，不阻塞；
* 常量（`LEFT`/`END`/...）映射为字符串，`TclError` 映射为真实异常类。

这**不是删除 GUI 逻辑**：在有显示的环境下 `HEADLESS=false`，走的是真正的
`tkinter`，行为与改造前完全一致。

## 用法

    from headless import tk, ttk, messagebox

替代原先的：

    import tkinter as tk
    from tkinter import ttk, messagebox
"""
from __future__ import annotations

import os


def _headless_enabled() -> bool:
    if os.getenv("SLOPE_HEADLESS") == "1":
        return True
    val = os.getenv("HEADLESS", "").strip().lower()
    return val in ("1", "true", "yes", "on")


HEADLESS = _headless_enabled()


# ══════════════════════════════════════════════════════════════════
# 无头 stub 实现
# ══════════════════════════════════════════════════════════════════
class _Stub:
    """万能空操作对象：可调用、可链式、可下标、可迭代、可当上下文管理器。"""

    # 让 `isinstance(x, str)` 之类的误判最小化：不伪装成任何具体类型
    def __init__(self, *args, **kwargs) -> None:
        pass

    # 属性访问 → 返回空操作（不是类，避免被误当构造器）
    def __getattr__(self, name: str):
        if name.startswith("__") and name.endswith("__"):
            # 保留 pickle / 复制 / 上下文协议等，避免破坏 Python 内部机制
            raise AttributeError(name)
        return _noop

    def __setattr__(self, name: str, value) -> None:  # 允许 GUI 代码随意赋属性
        object.__setattr__(self, name, value)

    def __call__(self, *args, **kwargs):
        return self

    def __getitem__(self, item):
        return self

    def __setitem__(self, item, value) -> None:
        pass

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return True

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def __repr__(self) -> str:
        return "<headless-stub>"


def _noop(*args, **kwargs):
    """空操作函数。"""
    return None


class _StubRoot(_Stub):
    """Tk 根窗口 stub：mainloop / after / wait_window 等阻塞或调度调用直接返回。"""

    def mainloop(self, *args, **kwargs):
        return None

    def after(self, *args, **kwargs):
        return None

    def after_idle(self, *args, **kwargs):
        return None

    def wait_window(self, *args, **kwargs):
        return None

    def wait_variable(self, *args, **kwargs):
        return None

    def update(self, *args, **kwargs):
        return None

    def update_idletasks(self, *args, **kwargs):
        return None

    def destroy(self, *args, **kwargs):
        return None

    def quit(self, *args, **kwargs):
        return None

    def withdraw(self, *args, **kwargs):
        return None

    def deiconify(self, *args, **kwargs):
        return None

    def winfo_screenwidth(self) -> int:
        return 1920

    def winfo_screenheight(self) -> int:
        return 1080

    def __getattr__(self, name: str):
        # 非阻塞方法返回 noop，其余走通用 stub
        if name in ("mainloop", "after", "after_idle", "wait_window", "wait_variable"):
            return _noop
        return super().__getattr__(name)


class _StubVar(_Stub):
    """StringVar / IntVar / BooleanVar stub：get() 返回与类型相符的空值。"""

    def __init__(self, master=None, value=None, name=None) -> None:
        if value is not None:
            object.__setattr__(self, "_value", value)
        else:
            object.__setattr__(self, "_value", None)

    def get(self):
        v = object.__getattribute__(self, "_value")
        if v is not None:
            return v
        # 按类名推断空值，避免 float()/int() 直接炸
        cls = type(self).__name__.lower()
        if "int" in cls or "double" in cls:
            return 0
        if "bool" in cls:
            return False
        return ""

    def set(self, value):
        object.__setattr__(self, "_value", value)

    def trace_add(self, *args, **kwargs):
        return ""


class _StubModule:
    """伪模块：任意属性都解析为某个 stub 类或常量。"""

    def __init__(self, name: str, constants: dict | None = None) -> None:
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_constants", constants or {})

    def __getattr__(self, name: str):
        consts = object.__getattribute__(self, "_constants")
        if name in consts:
            return consts[name]
        # 变量类返回变量 stub，其余返回通用控件 stub 类
        lowered = name.lower()
        if lowered.endswith("var"):
            return type(name, (_StubVar,), {})
        if name in ("Tk", "Toplevel"):
            return _StubRoot
        return type(name, (_Stub,), {})

    def __repr__(self) -> str:
        return f"<headless-module {object.__getattribute__(self, '_name')}>"


def _build_tk_module() -> _StubModule:
    """构造 tkinter 主模块 stub，含常用常量。"""
    constants = {
        # 布局 / 锚点
        "LEFT": "left", "RIGHT": "right", "TOP": "top", "BOTTOM": "bottom",
        "X": "x", "Y": "y", "BOTH": "both", "NONE": "none",
        "N": "n", "S": "s", "E": "e", "W": "w", "CENTER": "center",
        "NW": "nw", "NE": "ne", "SW": "sw", "SE": "se",
        # 状态
        "NORMAL": "normal", "DISABLED": "disabled", "ACTIVE": "active",
        # 文本 / 列表
        "END": "end", "INSERT": "insert", "WORD": "word", "CHAR": "char",
        "SINGLE": "single", "MULTIPLE": "multiple", "EXTENDED": "extended",
        "BROWSE": "browse",
        # 朝向
        "HORIZONTAL": "horizontal", "VERTICAL": "vertical",
    }
    return _StubModule("tkinter", constants)


class _StubTclError(Exception):
    """tkinter.TclError 的替身，保证 `except tk.TclError` 可用。"""


class _StubFigureCanvasTkAgg:
    """matplotlib 的 Tk 画布替身。

    必须在此单独 stub：matplotlib 内部 `import tkinter as tk` 用的是**真实**
    tkinter，会绕过本模块对 tk 的替换，从而在无显示环境下创建真窗口并崩溃。
    """

    def __init__(self, figure=None, master=None, **kwargs) -> None:
        self.figure = figure
        self._widget = _Stub()

    def get_tk_widget(self):
        return self._widget

    def draw(self):
        return None

    def draw_idle(self, *args, **kwargs):
        return None

    def flush_events(self):
        return None

    def resize(self, *args, **kwargs):
        return None

    def mpl_connect(self, *args, **kwargs):
        return 0

    def mpl_disconnect(self, *args, **kwargs):
        return None


class _StubMessagebox(_StubModule):
    def __getattr__(self, name: str):
        # showinfo / showerror / askyesno ... 一律空操作
        return _noop


if HEADLESS:
    tk = _build_tk_module()
    tk.TclError = _StubTclError
    ttk = _StubModule("tkinter.ttk")
    messagebox = _StubMessagebox("tkinter.messagebox")
    FigureCanvasTkAgg = _StubFigureCanvasTkAgg
else:  # pragma: no cover - 有显示环境下走真实实现
    import tkinter as tk  # type: ignore
    from tkinter import messagebox, ttk  # type: ignore
    from matplotlib.backends.backend_tkagg import (  # type: ignore
        FigureCanvasTkAgg,
    )

    tk.TclError = tk.TclError  # 显式导出，保证 API 一致


__all__ = ["tk", "ttk", "messagebox", "FigureCanvasTkAgg", "HEADLESS"]
