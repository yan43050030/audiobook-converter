"""Tests for the v6.0 `audiobook` package import surface.

这些测试锁定 A3 建立的稳定公共导入面：在阶段 1/A4 把实现迁入子包的过程中，
`audiobook` 及其子包对外暴露的名字必须始终可解析，且指向真实可调用对象。

Run with: python3 -m unittest tests/test_package_api.py
"""

import importlib
import os
import subprocess
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestTopLevelPackage(unittest.TestCase):
    def test_import_is_cheap(self):
        # import audiobook 本身不应触发底层重模块加载（PEP 562 延迟再导出）。
        # 在独立子进程中验证，避免同进程其它测试已加载 tts_engine 造成干扰。
        code = (
            "import sys, audiobook;"
            "assert 'tts_engine' not in sys.modules, 'tts_engine 被提前加载';"
            "print('ok')"
        )
        env = dict(os.environ, PYTHONPATH=_PROJECT_ROOT)
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, env=env, cwd=_PROJECT_ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)

    def test_version_matches_source(self):
        import audiobook
        import tts_engine
        self.assertEqual(audiobook.VERSION, tts_engine.VERSION)

    def test_all_reexports_resolve(self):
        import audiobook
        for name, (mod_name, attr) in audiobook._REEXPORT.items():
            with self.subTest(name=name):
                value = getattr(audiobook, name)
                mod = importlib.import_module(mod_name)
                self.assertIs(value, getattr(mod, attr))

    def test_dir_lists_public_names(self):
        import audiobook
        listed = set(dir(audiobook))
        for name in ("VERSION", "convert_batch", "transcribe", "load_file_content"):
            self.assertIn(name, listed)

    def test_unknown_attribute_raises(self):
        import audiobook
        with self.assertRaises(AttributeError):
            audiobook.does_not_exist  # noqa: B018


class TestSubPackages(unittest.TestCase):
    SUBPACKAGES = ("core", "engines", "io")  # ui 需要 Qt，单独测

    def test_subpackage_reexports_resolve(self):
        for sub in self.SUBPACKAGES:
            mod = importlib.import_module(f"audiobook.{sub}")
            self.assertTrue(hasattr(mod, "_REEXPORT"), f"{sub} 缺少 _REEXPORT")
            for name, (mod_name, attr) in mod._REEXPORT.items():
                with self.subTest(sub=sub, name=name):
                    value = getattr(mod, name)
                    src = importlib.import_module(mod_name)
                    self.assertIs(value, getattr(src, attr))
                    self.assertTrue(callable(value), f"{sub}.{name} 应为可调用对象")

    def test_ui_reexport_resolves_if_qt_present(self):
        # 判据必须是"Qt 界面能否真正加载"，而非"Qt 的 Python 包是否存在"：
        # 无头 CI 上 PySide6 包在、但其 C 扩展缺 libEGL 等系统库仍会 ImportError，
        # 且可能回退到未安装的 PyQt6。任一情况都应跳过而非失败。
        ui = importlib.import_module("audiobook.ui")
        try:
            cls = getattr(ui, "AudiobookConverterMain")
        except ImportError:
            self.skipTest("Qt6 界面不可加载（未安装或缺系统库），跳过 audiobook.ui 再导出检查")
        self.assertTrue(callable(cls))


class TestCompatShims(unittest.TestCase):
    """顶层兼容垫片必须与包内规范实现指向同一对象（A4 迁移保持旧 import 可用）。"""

    def test_file_reader_shim(self):
        import file_reader
        from audiobook.io import readers
        for name in ("load_file_content", "read_docx", "read_markdown",
                     "read_epub", "read_html", "read_pdf"):
            with self.subTest(name=name):
                self.assertIs(getattr(file_reader, name), getattr(readers, name))

    def test_asr_engine_shim(self):
        import asr_engine
        from audiobook.engines import asr
        for name in ("transcribe", "check_asr_ready", "unload_whisper_model",
                     "scan_external_asr_engines", "external_asr_transcribe",
                     "WHISPER_MODELS", "WHISPER_COMPUTE_TYPES", "WHISPER_HF_REPOS",
                     "_format_timestamp", "_format_srt"):
            with self.subTest(name=name):
                self.assertIs(getattr(asr_engine, name), getattr(asr, name))

    def test_io_audio_reexported_by_tts_engine(self):
        # 音频输出层已迁至 audiobook.io.audio；tts_engine 顶部再导入，
        # 旧 `from tts_engine import merge_mp3_files, export_m4b, ...` 须为同一对象。
        import tts_engine
        from audiobook.io import audio
        for name in ("_merge_mp3_files", "merge_mp3_files", "get_audio_duration",
                     "_ffmeta_escape", "build_ffmetadata_chapters", "export_m4b",
                     "write_id3_tags", "normalize_loudness"):
            with self.subTest(name=name):
                self.assertIs(getattr(tts_engine, name), getattr(audio, name))

    def test_core_text_reexported_by_tts_engine(self):
        # 纯文本层已迁至 audiobook.core.text；tts_engine 顶部再导入这些名字，
        # 旧 `from tts_engine import detect_chapters, ...` 必须解析为同一对象。
        import tts_engine
        from audiobook.core import text
        for name in ("detect_chapters", "_find_source", "detect_dialogue_segments",
                     "extract_speakers", "_resolve_segment_voice", "split_text",
                     "_split_by_sentences", "split_by_duration",
                     "generate_srt_from_text", "_srt_timestamp",
                     "sanitize_filename", "estimate_duration",
                     "CHARS_PER_SECOND_BASE", "CHAPTER_PATTERNS",
                     "DIALOGUE_PATTERNS", "SPEAKER_PATTERN", "_SENTENCE_SPLIT_RE"):
            with self.subTest(name=name):
                self.assertIs(getattr(tts_engine, name), getattr(text, name))


if __name__ == "__main__":
    unittest.main()
