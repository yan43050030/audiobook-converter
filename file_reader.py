"""兼容垫片 — 文件读取实现已迁至 `audiobook.io.readers`（v6.0/A4）。

保留此顶层模块，让旧的 `from file_reader import ...` 与 PyInstaller 导入图
继续可用。新代码请改用 `from audiobook.io import load_file_content` 等。
"""

from audiobook.io.readers import (  # noqa: F401
    read_docx,
    read_markdown,
    read_epub,
    read_html,
    read_pdf,
    load_file_content,
)

__all__ = [
    "read_docx",
    "read_markdown",
    "read_epub",
    "read_html",
    "read_pdf",
    "load_file_content",
]
