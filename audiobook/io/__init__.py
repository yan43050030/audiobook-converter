"""audiobook.io —— 输入读取与音频输出

输入：txt / md / docx / epub / html / pdf 多格式读取。
输出：mp3 合并 · m4b 有声书（内嵌章节）· srt 字幕 · ID3 元数据。

迁移状态（6.0.0-dev）：读取实现已迁入 `audiobook.io.readers`（A4）；输出实现
仍位于 `tts_engine`，将在后续 A4 迁入本子包。
"""

_REEXPORT = {
    # 输入（已迁入本包）
    "load_file_content": ("audiobook.io.readers", "load_file_content"),
    "read_docx": ("audiobook.io.readers", "read_docx"),
    "read_epub": ("audiobook.io.readers", "read_epub"),
    "read_pdf": ("audiobook.io.readers", "read_pdf"),
    "read_html": ("audiobook.io.readers", "read_html"),
    "read_markdown": ("audiobook.io.readers", "read_markdown"),
    # 输出（尚在 tts_engine，待迁）
    "merge_mp3_files": ("tts_engine", "merge_mp3_files"),
    "export_m4b": ("tts_engine", "export_m4b"),
    "generate_srt_from_text": ("tts_engine", "generate_srt_from_text"),
}


def __getattr__(name):
    import importlib
    target = _REEXPORT.get(name)
    if target is None:
        raise AttributeError(f"module 'audiobook.io' has no attribute {name!r}")
    return getattr(importlib.import_module(target[0]), target[1])


def __dir__():
    return sorted(_REEXPORT)
