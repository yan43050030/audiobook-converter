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
        try:
            import PySide6  # noqa: F401
        except ImportError:
            try:
                import PyQt6  # noqa: F401
            except ImportError:
                self.skipTest("未安装 Qt6，跳过 audiobook.ui 再导出检查")
        ui = importlib.import_module("audiobook.ui")
        self.assertTrue(callable(getattr(ui, "AudiobookConverterMain")))


if __name__ == "__main__":
    unittest.main()
