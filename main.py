#!/usr/bin/env python3
"""文字转有声读物 — 程序入口（自动选择 PySide6 / PyQt6 / Tkinter 后端）"""

import sys
import os


def _get_qt_backend():
    """检测可用的 Qt6 后端，返回 ('PySide6'|'PyQt6'|None, module_prefix)"""
    try:
        import PySide6  # noqa: F401
        return "PySide6", "PySide6"
    except ImportError:
        pass
    try:
        import PyQt6  # noqa: F401
        return "PyQt6", "PyQt6"
    except ImportError:
        pass
    return None, None


def _run_qt(backend, prefix):
    """使用 Qt6 启动"""
    os.environ.setdefault("QT_MAC_WANTS_LAYER", "1")

    # PyInstaller 打包后，需手动指定 Qt 插件路径
    if getattr(sys, 'frozen', False):
        app_dir = os.path.dirname(sys.executable)
        if sys.platform == 'darwin':
            contents_dir = os.path.dirname(app_dir)
            search_dirs = [os.path.join(contents_dir, 'Frameworks'),
                          os.path.join(contents_dir, 'Resources')]
        else:
            search_dirs = [os.path.join(app_dir, '_internal')]
        for base in search_dirs:
            for sub in ['PySide6', 'PyQt6']:
                plugins_base = os.path.join(base, sub, 'Qt', 'plugins')
                if os.path.isdir(plugins_base):
                    os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = os.path.join(plugins_base, 'platforms')
                    os.environ['QT_PLUGIN_PATH'] = plugins_base
                    break
            if os.environ.get('QT_QPA_PLATFORM_PLUGIN_PATH'):
                break

    if prefix == "PySide6":
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPalette, QColor
    else:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QPalette, QColor

    from tts_engine import _load_config, VERSION
    cfg = _load_config()
    theme = cfg.get("theme", "light")

    app = QApplication(sys.argv)
    app.setApplicationName("文字转有声读物")
    app.setOrganizationName("AudiobookConverter")

    if theme == "dark":
        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(224, 224, 224))
        pal.setColor(QPalette.ColorRole.Base, QColor(25, 25, 25))
        pal.setColor(QPalette.ColorRole.Text, QColor(224, 224, 224))
        pal.setColor(QPalette.ColorRole.Button, QColor(45, 45, 45))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor(224, 224, 224))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(66, 133, 244))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
        app.setPalette(pal)

    from gui_pyside6 import AudiobookConverterMain
    window = AudiobookConverterMain()
    window.setWindowTitle(f"文字转有声读物 v{VERSION}")
    window.resize(1200, 800)
    window.show()
    sys.exit(app.exec())


def _run_tkinter():
    """使用 Tkinter 启动（回退）"""
    import platform
    import tkinter as tk

    if platform.system() == "Windows":
        try:
            from ctypes import windll
            try: windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                try: windll.shcore.SetProcessDpiAwareness(1)
                except Exception: windll.user32.SetProcessDPIAware()
        except Exception: pass

    from gui_tkinter_backup import AudiobookConverterApp
    from tts_engine import _load_config

    root = tk.Tk()
    try:
        dpi = root.winfo_fpixels("1i")
        if dpi and dpi > 0:
            scale = max(1.0, min(dpi / 72.0, 2.5))
            root.tk.call("tk", "scaling", scale)
    except Exception: pass

    cfg = _load_config()
    import sv_ttk
    sv_ttk.set_theme(cfg.get("theme", "light"))
    app = AudiobookConverterApp(root)
    root.mainloop()


def main():
    backend, prefix = _get_qt_backend()
    if backend:
        print(f"使用 {backend} 启动")
        _run_qt(backend, prefix)
    else:
        print("Qt6 未安装（PySide6 / PyQt6），回退到 Tkinter")
        _run_tkinter()


if __name__ == "__main__":
    main()
