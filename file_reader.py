"""文件读取工具 — 从 gui.py 提取，不依赖任何 UI 框架。"""

import re as _re
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser


def read_docx(path: str) -> str:
    """读取 .docx：优先用 python-docx，否则回退到直接解析 zip 中的 document.xml"""
    try:
        import docx  # type: ignore
        d = docx.Document(path)
        return "\n".join(p.text for p in d.paragraphs)
    except ImportError:
        pass
    with zipfile.ZipFile(path) as z:
        with z.open("word/document.xml") as f:
            tree = ET.parse(f)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    root = tree.getroot()
    paragraphs = []
    for p in root.iter(f"{ns}p"):
        texts = [t.text or "" for t in p.iter(f"{ns}t")]
        paragraphs.append("".join(texts))
    return "\n".join(paragraphs)


def read_markdown(path: str) -> str:
    """读取 .md：去掉 Markdown 标记使朗读更自然"""
    raw = None
    for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                raw = f.read()
            break
        except UnicodeDecodeError:
            continue
    if raw is None:
        raise RuntimeError("无法解码 Markdown 文件")

    text = raw
    text = _re.sub(r"```.*?```", "", text, flags=_re.DOTALL)
    text = _re.sub(r"`([^`]+)`", r"\1", text)
    text = _re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = _re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = _re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=_re.MULTILINE)
    text = _re.sub(r"^\s{0,3}>\s?", "", text, flags=_re.MULTILINE)
    text = _re.sub(r"^\s{0,3}[-*+]\s+", "", text, flags=_re.MULTILINE)
    text = _re.sub(r"^\s{0,3}\d+\.\s+", "", text, flags=_re.MULTILINE)
    text = _re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = _re.sub(r"\*([^*]+)\*", r"\1", text)
    text = _re.sub(r"__([^_]+)__", r"\1", text)
    text = _re.sub(r"_([^_]+)_", r"\1", text)
    text = _re.sub(r"~~([^~]+)~~", r"\1", text)
    text = _re.sub(r"^\s{0,3}[-*_]{3,}\s*$", "", text, flags=_re.MULTILINE)
    return text


def read_epub(path: str) -> str:
    """读取 .epub 电子书"""
    try:
        import ebooklib
        from ebooklib import epub
    except ImportError:
        raise ImportError("ebooklib 未安装 (pip install ebooklib)")
    book = epub.read_epub(path)
    chapters = []
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            content = item.get_content()
            text = _extract_text_from_html(content.decode("utf-8", errors="replace"))
            if text.strip():
                chapters.append(text)
    return "\n\n".join(chapters)


def read_html(path: str) -> str:
    """读取 HTML 文件，提取正文文本"""

    class _TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text_parts = []
            self.skip_tags = {"script", "style", "nav", "header", "footer"}
            self._skip_depth = 0

        def handle_starttag(self, tag, attrs):
            if tag in self.skip_tags:
                self._skip_depth += 1

        def handle_endtag(self, tag):
            if tag in self.skip_tags and self._skip_depth > 0:
                self._skip_depth -= 1
            if tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
                self.text_parts.append("\n")

        def handle_data(self, data):
            if self._skip_depth == 0:
                text = data.strip()
                if text:
                    self.text_parts.append(text)

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    extractor = _TextExtractor()
    extractor.feed(content)
    return "".join(extractor.text_parts)


def _extract_text_from_html(html: str) -> str:
    """从 HTML 片段提取纯文本"""

    class _Extractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []

        def handle_data(self, data):
            t = data.strip()
            if t:
                self.parts.append(t)

        def handle_endtag(self, tag):
            if tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li"):
                self.parts.append("\n")

    ex = _Extractor()
    ex.feed(html)
    return " ".join(ex.parts)


def read_pdf(path: str) -> str:
    """读取 PDF 文件"""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(path)
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        return "\n\n".join(text_parts)
    except ImportError:
        pass
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            text_parts = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    text_parts.append(text)
        return "\n\n".join(text_parts)
    except ImportError:
        raise ImportError("需要安装 PyMuPDF 或 pdfplumber 来读取 PDF (pip install PyMuPDF)")


def load_file_content(path: str) -> dict:
    """读取文件内容，根据扩展名自动选择读取方法。
    返回 {"name": str, "path": str, "content": str, "encoding": str}
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        content = read_docx(path)
    elif ext in (".md", ".markdown"):
        content = read_markdown(path)
    elif ext == ".epub":
        content = read_epub(path)
    elif ext in (".html", ".htm"):
        content = read_html(path)
    elif ext == ".pdf":
        content = read_pdf(path)
    else:
        content = None
        for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
            try:
                with open(path, "r", encoding=enc) as f:
                    content = f.read()
                break
            except UnicodeDecodeError:
                continue
        if content is None:
            raise RuntimeError("无法读取文件，编码不支持")

    return {
        "path": path,
        "name": os.path.basename(path),
        "content": content,
        "encoding": ext,
    }


import os
