"""Shared test fixtures for audiobook_converter tests."""

import os
import sys
import tempfile

import pytest

# Ensure the project root is on sys.path so tests can import tts_engine, asr_engine
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def sample_chapter_text():
    return (
        "序章：开端\n这是序章的内容，介绍故事背景。\n"
        "第一章 命运\n故事正式开始，主角踏上了旅程。\n"
        "第二章 转折\n事情发生了意想不到的变化。天气很好。\n"
        "尾声：终章\n一切回归平静。\n"
    )


@pytest.fixture
def sample_text():
    return "第一章 初识\n这是一段测试文本。今天天气很好。\n第二章 进阶\n第二部分的内容在这里。"
