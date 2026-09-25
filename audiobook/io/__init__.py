"""audiobook.io —— 输入读取与音频输出

输入：txt / md / docx / epub / html / pdf 多格式读取。
输出：mp3 合并 · m4b 有声书（内嵌章节）· srt 字幕 · ID3 元数据。

迁移状态（6.0.0-dev）：读取实现已迁入 `audiobook.io.readers`、音频输出实现已迁入
`audiobook.io.audio`（A4）；srt 字幕生成在 `audiobook.core.text`。
"""

_REEXPORT = {
    # 输入（audiobook.io.readers）
    "load_file_content": ("audiobook.io.readers", "load_file_content"),
    "read_docx": ("audiobook.io.readers", "read_docx"),
    "read_epub": ("audiobook.io.readers", "read_epub"),
    "read_pdf": ("audiobook.io.readers", "read_pdf"),
    "read_html": ("audiobook.io.readers", "read_html"),
    "read_markdown": ("audiobook.io.readers", "read_markdown"),
    # 音频输出（audiobook.io.audio）
    "merge_mp3_files": ("audiobook.io.audio", "merge_mp3_files"),
    "export_m4b": ("audiobook.io.audio", "export_m4b"),
    "get_audio_duration": ("audiobook.io.audio", "get_audio_duration"),
    "write_id3_tags": ("audiobook.io.audio", "write_id3_tags"),
    "normalize_loudness": ("audiobook.io.audio", "normalize_loudness"),
    "build_ffmetadata_chapters": ("audiobook.io.audio", "build_ffmetadata_chapters"),
    # 字幕（audiobook.core.text）
    "generate_srt_from_text": ("audiobook.core.text", "generate_srt_from_text"),
}


def __getattr__(name):
    import importlib
    target = _REEXPORT.get(name)
    if target is None:
        raise AttributeError(f"module 'audiobook.io' has no attribute {name!r}")
    return getattr(importlib.import_module(target[0]), target[1])


def __dir__():
    return sorted(_REEXPORT)
