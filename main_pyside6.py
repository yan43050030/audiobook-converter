#!/usr/bin/env python3
"""文字转有声读物 — PySide6 程序入口"""

import sys
import os

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
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

    from tts_engine import VERSION
    window = AudiobookConverterMain()
    window.setWindowTitle(f"文字转有声读物 v{VERSION}")
    window.resize(1200, 800)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
