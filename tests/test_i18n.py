"""界面多语言（audiobook.i18n + audiobook.locales.en）测试。

- tr()：中文原样、英文查词典、缺词回落中文、具名占位符；
- 语言解析与优先级：AUDIOBOOK_LANG > 配置 language > 系统区域；
- 词典完整性：译文占位符与原文一致；源码里每个 tr("…") 都有英文（漏翻会直接报出是哪条）；
- 导入后端模块不会提前确定语言（否则 macOS 从 Finder 启动时会在 Qt 加载前误判）；
- 子进程以英文构建主窗口与各对话框：可见文字不含中文，语音下拉显示译名、取值仍是原键。
"""

import ast
import glob
import json
import os
import re
import string
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from audiobook import i18n  # noqa: E402
from audiobook.locales.en import MESSAGES  # noqa: E402

CJK = re.compile("[一-鿿　-〿＀-￯]")

# 刻意不翻译：送进 TTS 合成的试听文本（内置语音多为中文语音，英文读不出来）
INTENTIONALLY_UNTRANSLATED = {
    "你好，这是一段语音试听示例。春江潮水连海平，海上明月共潮生。",
    "你好，这是用我的声音朗读的有声书。",
    "你好，欢迎使用文字转有声读物。祝你听书愉快！",
    "你好",
}


class _LangCase(unittest.TestCase):
    def setUp(self):
        self.addCleanup(i18n.set_language, None)


class TestTr(_LangCase):
    def test_chinese_returns_source(self):
        i18n.set_language("zh")
        self.assertEqual(i18n.tr("▶ 开始转换"), "▶ 开始转换")
        self.assertEqual(i18n.tr("已加载 {n} 个文件", n=3), "已加载 3 个文件")

    def test_english_uses_catalog(self):
        i18n.set_language("en")
        self.assertEqual(i18n.tr("▶ 开始转换"), "▶ Start")
        self.assertEqual(i18n.tr("已加载 {n} 个文件", n=3), "3 files loaded")

    def test_missing_key_falls_back_to_source(self):
        i18n.set_language("en")
        self.assertEqual(i18n.tr("这条文案不在词典里 {x}", x=1), "这条文案不在词典里 1")

    def test_placeholder_named_text_is_allowed(self):
        i18n.set_language("zh")
        self.assertEqual(i18n.tr("{text}！", text="好"), "好！")


class TestLanguageResolution(_LangCase):
    def test_explicit_values(self):
        for value, expected in [("zh", "zh"), ("zh_CN", "zh"), ("zh-TW", "zh"), ("ZH", "zh"),
                                ("en", "en"), ("en_US", "en")]:
            self.assertEqual(i18n.resolve_language(value), expected, value)

    def test_auto_follows_system_locale(self):
        for name, expected in [("zh_CN.UTF-8", "zh"), ("en_US.UTF-8", "en"), ("C.UTF-8", "en"),
                               ("Chinese (Simplified)_China", "zh"), ("de_DE", "en")]:
            with mock.patch.object(i18n, "_system_locale_name", return_value=name):
                self.assertEqual(i18n.resolve_language("auto"), expected, name)

    def test_priority_env_over_config_over_system(self):
        tmp = tempfile.mkdtemp()
        cfg = os.path.join(tmp, "config.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"language": "en"}, f)
        with mock.patch.object(i18n, "_config_path", return_value=cfg), \
             mock.patch.object(i18n, "_system_locale_name", return_value="zh_CN.UTF-8"):
            with mock.patch.dict(os.environ, {"AUDIOBOOK_LANG": "zh"}):
                i18n.set_language(None)
                self.assertEqual(i18n.current_language(), "zh", "环境变量优先")
            with mock.patch.dict(os.environ, {"AUDIOBOOK_LANG": ""}):
                i18n.set_language(None)
                self.assertEqual(i18n.current_language(), "en", "其次是配置")
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump({"language": "auto"}, f)
            with mock.patch.dict(os.environ, {"AUDIOBOOK_LANG": ""}):
                i18n.set_language(None)
                self.assertEqual(i18n.current_language(), "zh", "auto 跟随系统")

    def test_bad_config_value_means_auto(self):
        tmp = tempfile.mkdtemp()
        cfg = os.path.join(tmp, "config.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"language": "klingon"}, f)
        with mock.patch.object(i18n, "_config_path", return_value=cfg):
            self.assertEqual(i18n.configured_language(), "auto")

    def test_config_schema_accepts_language(self):
        from audiobook import config
        self.assertEqual(config.normalize({"language": "en"})["language"], "en")
        self.assertEqual(config.normalize({"language": "fr"})["language"], "auto")
        self.assertEqual(config.defaults()["language"], "auto")


def _fields(s):
    return {(name, spec or "", conv or "")
            for _lit, name, spec, conv in string.Formatter().parse(s) if name is not None}


def _source_tr_keys():
    files = ["gui_pyside6.py", "main.py", "tts_engine.py"] + glob.glob(os.path.join("audiobook", "**", "*.py"),
                                                                       recursive=True)
    keys = {}
    for rel in files:
        path = os.path.join(ROOT, rel)
        if rel.startswith(os.path.join("audiobook", "locales")) or not os.path.isfile(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "tr" and n.args
                    and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                keys.setdefault(n.args[0].value, f"{rel}:{n.lineno}")
    return keys


class TestCatalog(unittest.TestCase):
    def test_placeholders_match(self):
        for zh, en in MESSAGES.items():
            self.assertEqual(_fields(zh), _fields(en), f"占位符不一致：{zh!r} → {en!r}")
            dummy = {name: (1.5 if spec else "x") for name, spec, _c in _fields(zh)}
            en.format(**dummy)  # 不应抛 KeyError / ValueError

    def test_translations_are_not_chinese(self):
        for zh, en in MESSAGES.items():
            self.assertIsNone(re.search("[一-鿿]", en.replace("XX说：“…”", "")),
                              f"译文含中文：{zh!r} → {en!r}")

    def test_every_tr_literal_is_translated(self):
        missing = [f"{where}: {key!r}" for key, where in _source_tr_keys().items()
                   if CJK.search(key) and key not in MESSAGES and key not in INTENTIONALLY_UNTRANSLATED]
        self.assertEqual(missing, [], "以下界面文案缺英文译文（请补到 audiobook/locales/en.py）：\n"
                         + "\n".join(missing))

    def test_module_level_display_data_is_translated(self):
        # 这些模块级数据保持中文原文（部分兼作查找键），在显示处 tr(值) 翻译
        import tts_engine as te
        from audiobook import models as M, onboarding as OB
        from audiobook.engines import asr as A
        from audiobook.io import hub as H
        values = list(M.KIND_LABELS.values()) + list(A.WHISPER_COMPUTE_TYPES) + list(A.WHISPER_MODELS.values())
        values += [m["description"] for m in te.COSYVOICE_MODELS.values()]
        values += list(te.PIPER_BUILTIN_VOICES) + list(te.EDGE_VOICES) + list(te.COSYVOICE_SFT_SPEAKERS)
        values += [v for sc in OB.SCENARIOS for v in (sc.title, sc.description, sc.size_hint)]
        values += [v for v in H.SOURCE_LABELS.values() if CJK.search(v)]
        missing = [v for v in values if v not in MESSAGES]
        self.assertEqual(missing, [])


def _run(code, lang, timeout=180):
    env = dict(os.environ, AUDIOBOOK_LANG=lang, QT_QPA_PLATFORM="offscreen")
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=timeout)


class TestNoImportTimeResolution(unittest.TestCase):
    def test_backend_imports_do_not_fix_language(self):
        r = _run("""
            import audiobook.i18n as i
            import tts_engine, asr_engine, file_reader
            import audiobook.models, audiobook.onboarding, audiobook.cli, audiobook.config
            import audiobook.engines.piper, audiobook.engines.edge, audiobook.engines.local
            import audiobook.engines.cosyvoice, audiobook.engines.clone, audiobook.engines.asr
            import audiobook.io.audio, audiobook.io.readers, audiobook.io.hub
            import audiobook.cloning.client, audiobook.cloning.profiles
            print("LANG=", i._lang)
        """, lang="")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("LANG= None", r.stdout, "导入后端模块时调用了 tr()，会在 Qt 加载前确定界面语言")


_GUI_SCAN = """
    import os, re, sys, tempfile
    from unittest import mock
    try:
        from PySide6.QtWidgets import (QApplication, QDialog, QLabel, QAbstractButton, QGroupBox,
            QLineEdit, QPlainTextEdit, QComboBox, QTabWidget, QTableWidget, QTreeWidget, QListWidget,
            QWidget, QMessageBox)
        app = QApplication.instance() or QApplication([])
    except Exception as e:
        print("SKIP", e); sys.exit(0)
    import tts_engine
    tmp = tempfile.mkdtemp(); store = os.path.join(tmp, "s"); os.makedirs(store)
    for p in [mock.patch.object(tts_engine, "CONFIG_PATH", os.path.join(tmp, "c.json")),
              mock.patch.object(tts_engine, "get_storage_dir", lambda: store),
              mock.patch.object(tts_engine, "get_model_search_paths", lambda: []),
              mock.patch.object(tts_engine, "fetch_piper_voice_catalog", side_effect=RuntimeError("offline")),
              mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No)]:
        p.start()
    tts_engine._config_cache = None
    import gui_pyside6
    gui_pyside6.fetch_piper_voice_catalog = tts_engine.fetch_piper_voice_catalog
    opened = []
    QDialog.exec = lambda self: opened.append(self) or 0
    win = gui_pyside6.AudiobookConverterMain()
    roots = [win]
    win._open_model_manager(); roots.append(opened[-1])
    win._open_voice_catalog(); roots.append(opened[-1])
    roots += [win._build_clone_dialog(), win._build_first_run_wizard(), win._build_clone_new_dialog(win)]
    han = re.compile("[\\u4e00-\\u9fff\\u3000-\\u303f\\uff00-\\uffef]")
    allowed = {"简体中文"}   # 语言名称用其自身书写
    left = set()
    for root in roots:
        for w in [root] + root.findChildren(QWidget):
            texts = [w.toolTip()]
            if isinstance(w, (QLabel, QAbstractButton)): texts.append(w.text())
            if isinstance(w, QGroupBox): texts.append(w.title())
            if isinstance(w, (QLineEdit, QPlainTextEdit)): texts.append(w.placeholderText())
            if isinstance(w, QComboBox): texts += [w.itemText(i) for i in range(w.count())]
            if isinstance(w, QTabWidget): texts += [w.tabText(i) for i in range(w.count())]
            if isinstance(w, QTableWidget):
                texts += [w.horizontalHeaderItem(c).text() for c in range(w.columnCount())
                          if w.horizontalHeaderItem(c)]
            if isinstance(w, QTreeWidget):
                texts += [w.headerItem().text(c) for c in range(w.columnCount())]
                texts += [w.topLevelItem(i).text(0) for i in range(w.topLevelItemCount())]
            if isinstance(w, QListWidget): texts += [w.item(i).text() for i in range(w.count())]
            if w.isWindow(): texts.append(w.windowTitle())
            left |= {t for t in texts if t and han.search(t) and t not in allowed}
    print("LEFT", sorted(left))
    # 语音下拉：显示译名，取值仍是原文键，能解析到正确的 voice_id
    combo = win._voice_combo
    i = combo.findData("云希（男声，自然）")
    print("VOICE", combo.itemText(i), tts_engine.get_voice_id(combo.itemData(i), "edge"))
"""


class TestEnglishGui(unittest.TestCase):
    def test_no_chinese_left_in_english_ui(self):
        r = _run(_GUI_SCAN, lang="en")
        if "SKIP" in r.stdout:
            self.skipTest(r.stdout.strip())
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        left = [ln for ln in r.stdout.splitlines() if ln.startswith("LEFT")]
        self.assertEqual(left, ["LEFT []"], "英文界面仍有中文：" + r.stdout[-2000:])
        self.assertIn("VOICE Yunxi (male, natural) zh-CN-YunxiNeural", r.stdout)

    def test_language_setting_is_saved(self):
        r = _run("""
            import json, os, sys, tempfile
            from unittest import mock
            try:
                from PySide6.QtWidgets import QApplication
                app = QApplication.instance() or QApplication([])
            except Exception as e:
                print("SKIP", e); sys.exit(0)
            import tts_engine
            tmp = tempfile.mkdtemp(); cfg = os.path.join(tmp, "c.json")
            mock.patch.object(tts_engine, "CONFIG_PATH", cfg).start()
            tts_engine._config_cache = None
            import gui_pyside6
            win = gui_pyside6.AudiobookConverterMain()
            win._lang_combo.setCurrentIndex(win._lang_combo.findData("en"))
            print("SAVED", json.load(open(cfg, encoding="utf-8")).get("language"))
            print("HINT", win._lang_status.text())
        """, lang="zh")
        if "SKIP" in r.stdout:
            self.skipTest(r.stdout.strip())
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        self.assertIn("SAVED en", r.stdout)
        self.assertIn("Restart", r.stdout, "切换语言后应提示重启（双语）")


if __name__ == "__main__":
    unittest.main()
