"""audiobook.io —— 输入读取与音频输出

输入：txt / md / docx / epub / html / pdf 多格式读取。
输出：mp3 合并 · m4b 有声书（内嵌章节）· srt 字幕 · ID3 元数据。

迁移状态（6.0.0-dev）：读取实现位于顶层 `file_reader`，输出实现位于
`tts_engine`；此处先以延迟再导出建立稳定导入路径，阶段 1/A4 迁入本子包。
"""

_REEXPORT = {
    # 输入
    "load_file_content": ("file_reader", "load_file_content"),
    "read_docx": ("file_reader", "read_docx"),
    "read_epub": ("file_reader", "read_epub"),
    "read_pdf": ("file_reader", "read_pdf"),
    "read_html": ("file_reader", "read_html"),
    "read_markdown": ("file_reader", "read_markdown"),
    # 输出
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
