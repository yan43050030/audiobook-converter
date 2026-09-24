"""audiobook.ui —— PySide6/Qt6 界面

迁移状态（6.0.0-dev）：主窗口实现位于顶层 `gui_pyside6`，此处以延迟再导出
暴露主窗口类，避免在无 Qt 环境下 `import audiobook.ui` 即失败。
阶段 1/A4 会把界面实现迁入本子包。
"""

_REEXPORT = {
    "AudiobookConverterMain": ("gui_pyside6", "AudiobookConverterMain"),
}


def __getattr__(name):
    import importlib
    target = _REEXPORT.get(name)
    if target is None:
        raise AttributeError(f"module 'audiobook.ui' has no attribute {name!r}")
    return getattr(importlib.import_module(target[0]), target[1])


def __dir__():
    return sorted(_REEXPORT)
