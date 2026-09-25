"""audiobook.core —— 文本处理管线（章节 · 对话 · 拆分 · 文本）

目标：承载与引擎/界面无关的纯逻辑，便于独立测试与复用。

迁移状态（6.0.0-dev）：纯文本处理逻辑已迁入 `audiobook.core.text`（A4）；此处
再导出指向该模块。`tts_engine` 顶部再导入这些名字，保持旧 import 兼容。
"""

_REEXPORT = {
    "detect_chapters": ("audiobook.core.text", "detect_chapters"),
    "detect_dialogue_segments": ("audiobook.core.text", "detect_dialogue_segments"),
    "extract_speakers": ("audiobook.core.text", "extract_speakers"),
    "split_text": ("audiobook.core.text", "split_text"),
    "split_by_duration": ("audiobook.core.text", "split_by_duration"),
    "sanitize_filename": ("audiobook.core.text", "sanitize_filename"),
    "estimate_duration": ("audiobook.core.text", "estimate_duration"),
    "generate_srt_from_text": ("audiobook.core.text", "generate_srt_from_text"),
}


def __getattr__(name):
    import importlib
    target = _REEXPORT.get(name)
    if target is None:
        raise AttributeError(f"module 'audiobook.core' has no attribute {name!r}")
    return getattr(importlib.import_module(target[0]), target[1])


def __dir__():
    return sorted(_REEXPORT)
