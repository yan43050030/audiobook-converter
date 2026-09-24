"""audiobook.core —— 文本处理管线（章节 · 对话 · 拆分 · 文本）

目标：承载与引擎/界面无关的纯逻辑，便于独立测试与复用。

迁移状态（6.0.0-dev）：实现暂位于顶层 `tts_engine`，此处先以延迟再导出建立
稳定导入路径；阶段 1/A4 会把相关纯函数迁入本子包。
"""

_REEXPORT = {
    "detect_chapters": ("tts_engine", "detect_chapters"),
    "detect_dialogue_segments": ("tts_engine", "detect_dialogue_segments"),
    "extract_speakers": ("tts_engine", "extract_speakers"),
    "split_text": ("tts_engine", "split_text"),
    "split_by_duration": ("tts_engine", "split_by_duration"),
    "sanitize_filename": ("tts_engine", "sanitize_filename"),
    "estimate_duration": ("tts_engine", "estimate_duration"),
}


def __getattr__(name):
    import importlib
    target = _REEXPORT.get(name)
    if target is None:
        raise AttributeError(f"module 'audiobook.core' has no attribute {name!r}")
    return getattr(importlib.import_module(target[0]), target[1])


def __dir__():
    return sorted(_REEXPORT)
