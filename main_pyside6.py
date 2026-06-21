#!/usr/bin/env python3
"""文字转有声读物 — PySide6 程序入口"""

import sys
import os

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPalette, QColor

from gui_pyside6 import AudiobookConverterMain


def _apply_theme(app: QApplication) -> None:
    """从配置加载主题并应用"""
    from tts_engine import _load_config
    cfg = _load_config()
    theme = cfg.get("theme", "light")
    if theme == "dark":
        _set_dark_palette(app)
    # light 模式用系统默认即可


def _set_dark_palette(app: QApplication) -> None:
    """设置暗色主题调色板"""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(224, 224, 224))
    palette.setColor(QPalette.ColorRole.Base, QColor(25, 25, 25))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(38, 38, 38))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(224, 224, 224))
    palette.setColor(QPalette.ColorRole.Text, QColor(224, 224, 224))
    palette.setColor(QPalette.ColorRole.Button, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(224, 224, 224))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 50, 50))
    palette.setColor(QPalette.ColorRole.Link, QColor(66, 133, 244))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(66, 133, 244))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    # 禁用色
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
                     QColor(128, 128, 128))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,
                     QColor(128, 128, 128))
    app.setPalette(palette)


def main():
    # macOS 适配
    os.environ.setdefault("QT_MAC_WANTS_LAYER", "1")

    app = QApplication(sys.argv)
    app.setApplicationName("文字转有声读物")
    app.setOrganizationName("AudiobookConverter")
    _apply_theme(app)

    from tts_engine import VERSION, _load_config
    window = AudiobookConverterMain()
    window.setWindowTitle(f"文字转有声读物 v{VERSION}")

    cfg = _load_config()

    def _init_window():
        """在窗口显示后初始化尺寸和位置"""
        screen = app.primaryScreen()
        avail = screen.availableGeometry() if screen else None

        # 尝试恢复上次窗口位置
        saved_geo = cfg.get("window_geometry", "")
        restored = False
        if saved_geo:
            import re as _re
            m = _re.match(r"(\d+)x(\d+)\+(\-?\d+)\+(\-?\d+)", saved_geo)
            if m and avail:
                sw, sh, sx, sy = int(m[1]), int(m[2]), int(m[3]), int(m[4])
                sw = max(800, min(sw, avail.width() - 20))
                sh = max(400, min(sh, avail.height() - 40))
                sx = max(avail.x(), min(sx, avail.x() + avail.width() - sw))
                sy = max(avail.y(), min(sy, avail.y() + avail.height() - sh))
                window.resize(sw, sh)
                window.move(sx, sy)
                restored = True

        if not restored and avail:
            target_w = min(1200, avail.width() - 40)
            target_h = max(400, min(800, avail.height() - 40))
            window.resize(target_w, target_h)
            x = avail.x() + (avail.width() - target_w) // 2
            y = avail.y() + (avail.height() - target_h) // 2
            window.move(max(avail.x(), x), max(avail.y(), y))

        # 显示后设置最小/最大尺寸（此时 NSWindow 已创建，不会误解约束）
        window.setMinimumSize(800, 400)
        window.setMaximumSize(16777215, 16777215)

    # 先显示窗口，让 NSWindow 创建完成
    window.show()
    QTimer.singleShot(0, _init_window)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
