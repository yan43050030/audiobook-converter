#!/usr/bin/env python3
"""文字转有声读物 - 程序入口"""

import platform
import sys
import tkinter as tk

from gui import AudiobookConverterApp
from tts_engine import _load_config, _save_config


def _enable_hidpi() -> None:
    """在 Windows 上启用高 DPI 感知，避免界面模糊。"""
    if platform.system() != "Windows":
        return
    try:
        from ctypes import windll
        # 2 = Per-Monitor V2（Windows 10 1703+）
        try:
            windll.shcore.SetProcessDpiAwareness(2)
            return
        except Exception:
            pass
        # 回退：Per-Monitor V1
        try:
            windll.shcore.SetProcessDpiAwareness(1)
            return
        except Exception:
            pass
        # 再回退：System-DPI Aware（Windows 7+）
        windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _apply_tk_scaling(root: tk.Tk) -> None:
    """根据屏幕 DPI 给 Tk 设置合适的缩放系数，并调整默认字体。"""
    try:
        dpi = root.winfo_fpixels("1i")  # 每英寸像素数
        if not dpi or dpi <= 0:
            return
        # Tk 默认基于 72 DPI
        scale = dpi / 72.0
        # 控制范围，避免 4K 以上过度放大
        scale = max(1.0, min(scale, 2.5))
        root.tk.call("tk", "scaling", scale)

        # 调整默认命名字体，保证文本同步放大（注意：scaling 已对 pt 单位缩放，
        # 这里仅做小幅校正，避免个别系统字体偏小）
        try:
            from tkinter import font as tkfont
            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                         "TkHeadingFont", "TkTooltipFont", "TkFixedFont"):
                f = tkfont.nametofont(name)
                size = f.cget("size")
                if isinstance(size, int) and size > 0 and scale >= 1.5:
                    # 对未随 scaling 缩放的情形稍作放大
                    f.configure(size=max(size, int(size * 1.0)))
        except Exception:
            pass
    except Exception:
        pass


def _try_init_theme(root: tk.Tk) -> bool:
    """尝试加载 sv-ttk 主题（深色/浅色），返回是否成功"""
    try:
        import sv_ttk
        cfg = _load_config()
        theme = cfg.get("theme", "light")
        sv_ttk.set_theme(theme)
        return True
    except ImportError:
        return False


def main():
    _enable_hidpi()
    root = tk.Tk()
    _apply_tk_scaling(root)
    _try_init_theme(root)
    app = AudiobookConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
