"""GUI 声音克隆对话框测试（v6.0/C1-2），offscreen 驱动真实对话框。

配置 / 存储目录重定向到临时位置；CosyVoice 合成、Whisper 识别、环境检测打桩。
需要 ffmpeg（规整参考音频）；Qt 不可加载时整类跳过。

Run with: python3 -m unittest tests/test_gui_clone.py
"""

import json
import math
import os
import shutil
import struct
import tempfile
import time
import unittest
import wave
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import (QApplication, QInputDialog, QLabel, QLineEdit,
                                   QMessageBox, QPlainTextEdit, QPushButton, QTableWidget,
                                   QTabWidget, QFileDialog)
    _QT_ERROR = None
except Exception as e:  # pragma: no cover
    _QT_ERROR = e


def make_wav(path, seconds, rate=22050):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(i / 8.0)))
                               for i in range(int(seconds * rate))))
    return path


@unittest.skipIf(_QT_ERROR is not None, f"Qt6 不可用: {_QT_ERROR}")
class TestCloneDialogs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tts_engine
        if not tts_engine._ffmpeg_path():
            raise unittest.SkipTest("需要 ffmpeg")
        try:
            cls.app = QApplication.instance() or QApplication([])
            import gui_pyside6
            cls.gui = gui_pyside6
        except Exception as e:
            raise unittest.SkipTest(f"Qt 界面不可加载: {e}")

    def setUp(self):
        import tts_engine
        from audiobook.cloning import client as C
        from audiobook.cloning import profiles as P
        self.te, self.C, self.P = tts_engine, C, P
        self.tmp = tempfile.mkdtemp(prefix="ab_gclone_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.storage = os.path.join(self.tmp, "store")
        os.makedirs(self.storage)
        self.cfg_path = os.path.join(self.tmp, "config.json")
        old_cache = tts_engine._config_cache
        tts_engine._config_cache = None
        self.addCleanup(setattr, tts_engine, "_config_cache", old_cache)
        for p in (mock.patch.object(tts_engine, "CONFIG_PATH", self.cfg_path),
                  mock.patch.object(tts_engine, "get_storage_dir", lambda: self.storage),
                  mock.patch.object(self.gui, "get_storage_dir", lambda: self.storage),
                  mock.patch.object(QMessageBox, "critical"),
                  mock.patch.object(QMessageBox, "warning")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(C.shutdown)
        self.win = self.gui.AudiobookConverterMain()
        self.win.player.play = mock.MagicMock()

    # ---- helpers ----
    def pump(self, attr=None, timeout=20):
        w = getattr(self.win, attr, None) if attr else None
        end = time.time() + timeout
        while w is not None and w.isRunning() and time.time() < end:
            self.app.processEvents()
            time.sleep(0.01)
        for _ in range(30):
            self.app.processEvents()

    def find(self, dlg, cls, name):
        w = dlg.findChild(cls, name)
        self.assertIsNotNone(w, name)
        return w

    def cfg(self):
        with open(self.cfg_path, encoding="utf-8") as f:
            return json.load(f)

    def add_model(self):
        d = os.path.join(self.storage, "cosyvoice-models", "Fun-CosyVoice3-0.5B")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "cosyvoice3.yaml"), "w").close()

    # ---- tests ----
    def test_entry_button_and_engine_card(self):
        labels = [b.text() for b in self.win.findChildren(QPushButton)]
        self.assertIn("🎙 克隆音色…", labels)
        clone_btn = next(b for e, b, _n in self.win._engine_btns if e == "clone")
        self.assertIn("声音克隆", clone_btn.text())

    def test_settings_tab_first_then_save(self):
        dlg = self.win._build_clone_dialog()
        tabs = self.find(dlg, QTabWidget, "cloneTabs")
        self.assertEqual(tabs.currentIndex(), 1, "未找到模型时先打开环境设置页")
        self.find(dlg, QLineEdit, "clonePython").setText("/opt/envs/cosyvoice/bin/python")
        self.find(dlg, QLineEdit, "cloneRepo").setText("/opt/CosyVoice")
        self.find(dlg, QPushButton, "cloneSave").click()
        cfg = self.cfg()
        self.assertEqual(cfg["cosyvoice_python"], "/opt/envs/cosyvoice/bin/python")
        self.assertEqual(cfg["cosyvoice_repo"], "/opt/CosyVoice")
        self.assertIn("不存在", self.find(dlg, QLabel, "cloneStatus").text())

    def test_download_model_from_settings(self):
        from PySide6.QtWidgets import QComboBox
        from audiobook import models as M
        dlg = self.win._build_clone_dialog()
        combo = self.find(dlg, QComboBox, "cloneDlModel")
        self.assertEqual(combo.currentData(), "Fun-CosyVoice3-0.5B", "默认推荐 CosyVoice3")
        model_edit = self.find(dlg, QLineEdit, "cloneModelDir")
        self.assertIn("当前未找到", model_edit.placeholderText())

        def fake_download(kind, model_id, should_stop=None, storage_dir=None):
            self.te._notify_download("CosyVoice x [1/2] llm.pt", 512, 1024)
            self.add_model()

        with mock.patch.object(M, "download", side_effect=fake_download) as dl:
            self.find(dlg, QPushButton, "cloneDlStart").click()
            self.pump("_clone_dl_worker")
        self.assertEqual(dl.call_args[0], ("cosyvoice", "Fun-CosyVoice3-0.5B"))
        self.assertIn("下载完成", self.find(dlg, QLabel, "cloneDlMsg").text())
        self.assertIn("Fun-CosyVoice3-0.5B", model_edit.placeholderText(), "下载后自动识别模型目录")
        self.assertTrue(self.find(dlg, QPushButton, "cloneDlStart").isEnabled())

    def test_download_pause(self):
        from audiobook import models as M
        dlg = self.win._build_clone_dialog()
        started = []

        def slow_download(kind, model_id, should_stop=None, storage_dir=None):
            started.append(1)
            while not should_stop():
                time.sleep(0.01)
            raise self.te.StopRequested("用户暂停")

        with mock.patch.object(M, "download", side_effect=slow_download):
            self.find(dlg, QPushButton, "cloneDlStart").click()
            end = time.time() + 5
            while not started and time.time() < end:
                self.app.processEvents()
                time.sleep(0.01)
            stop = self.find(dlg, QPushButton, "cloneDlStop")
            self.assertTrue(stop.isEnabled())
            stop.click()
            self.pump("_clone_dl_worker")
        self.assertIn("已暂停", self.find(dlg, QLabel, "cloneDlMsg").text())
        self.assertFalse(self.find(dlg, QPushButton, "cloneDlStop").isEnabled())

    def test_check_environment_report(self):
        good = {"python": "/py", "repo": "/repo", "model_dir": "/m", "model_type": "CosyVoice3",
                "ping": {"python": "3.10.14", "cosyvoice": True},
                "load": {"model_type": "CosyVoice3", "sample_rate": 24000}, "error": ""}
        dlg = self.win._build_clone_dialog()
        with mock.patch.object(self.C, "check", return_value=good) as chk:
            self.find(dlg, QPushButton, "cloneCheck").click()
            self.pump("_clone_check_worker")
        chk.assert_called_once_with(load=True)
        text = self.find(dlg, QPlainTextEdit, "cloneReport").toPlainText()
        self.assertIn("可导入 ✓", text)
        self.assertIn("✓ 环境可用", text)

        bad = dict(good, ping={"python": "3.10", "cosyvoice": False, "import_error": "No module named torch"},
                   load=None)
        with mock.patch.object(self.C, "check", return_value=bad):
            self.find(dlg, QPushButton, "cloneCheck").click()
            self.pump("_clone_check_worker")
        text = self.find(dlg, QPlainTextEdit, "cloneReport").toPlainText()
        self.assertIn("No module named torch", text)
        self.assertIn("✗ 环境未就绪", text)

    def test_new_profile_with_auto_transcribe(self):
        src = make_wav(os.path.join(self.tmp, "我的声音.wav"), 6)
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=(src, "")), \
             mock.patch.object(self.gui, "check_asr_ready", return_value=(True, "ok")):
            d = self.win._build_clone_new_dialog(None)
            self.find(d, QPushButton, "cloneNewPick").click()
        self.assertIn("6.0 秒", self.find(d, QLabel, "cloneNewFile").text())
        self.assertEqual(self.find(d, QLineEdit, "cloneNewName").text(), "我的声音", "默认用文件名")
        self.assertTrue(self.find(d, QPushButton, "cloneNewCreate").isEnabled())

        seen = {}

        def fake_transcribe(path, storage, **kw):
            seen["path"] = path
            return " 希望你以后能够做的比我还好呦。 "
        with mock.patch.object(self.gui, "transcribe", fake_transcribe):
            self.find(d, QPushButton, "cloneNewAsr").click()
            self.pump("_clone_asr_worker")
        self.assertTrue(seen["path"].endswith("prompt.wav"), "应识别规整后（≤30 秒）的那段音频")
        self.assertEqual(self.find(d, QPlainTextEdit, "cloneNewText").toPlainText(),
                         "希望你以后能够做的比我还好呦。")

        self.find(d, QPushButton, "cloneNewCreate").click()
        self.assertIsNotNone(d.created)
        self.assertEqual(d.created.prompt_text, "希望你以后能够做的比我还好呦。")
        self.assertEqual([p.name for p in self.P.list_profiles()], ["我的声音"])
        self.assertFalse(os.path.exists(os.path.dirname(seen["path"])), "临时音频应清理")

    def test_too_short_cannot_create(self):
        src = make_wav(os.path.join(self.tmp, "短.wav"), 1.5)
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=(src, "")):
            d = self.win._build_clone_new_dialog(None)
            self.find(d, QPushButton, "cloneNewPick").click()
        self.assertIn("太短", self.find(d, QLabel, "cloneNewMsg").text())
        self.assertFalse(self.find(d, QPushButton, "cloneNewCreate").isEnabled())

    def test_manage_and_listen(self):
        self.add_model()
        p = self.P.create_profile("小明", make_wav(os.path.join(self.tmp, "a.wav"), 5), "旧文字稿")
        dlg = self.win._build_clone_dialog()
        tbl = self.find(dlg, QTableWidget, "cloneTable")
        self.assertEqual(tbl.rowCount(), 1)
        self.assertEqual(tbl.item(0, 0).text(), "小明")
        self.assertEqual(self.find(dlg, QTabWidget, "cloneTabs").currentIndex(), 0)
        self.assertIn("声音克隆可用", self.find(dlg, QLabel, "cloneStatus").text())

        with mock.patch.object(QInputDialog, "getMultiLineText", return_value=("新文字稿", True)):
            self.find(dlg, QPushButton, "cloneEditText").click()
        self.assertEqual(self.P.get_profile(p.id).prompt_text, "新文字稿")
        with mock.patch.object(QInputDialog, "getText", return_value=("小红", True)):
            self.find(dlg, QPushButton, "cloneRename").click()
        self.assertEqual(tbl.item(0, 0).text(), "小红")

        def fake_synth(self_, text, voice, rate, out_path, should_stop=None):
            with open(out_path, "wb") as f:
                f.write(b"ID3")
        from audiobook.engines.clone import CloneEngine
        with mock.patch.object(CloneEngine, "synthesize_segment", fake_synth):
            self.find(dlg, QPushButton, "cloneListen").click()
            self.pump("_clone_preview_worker")
        self.win.player.play.assert_called_once()
        self.assertIn("正在播放", self.find(dlg, QLabel, "cloneStatus").text())

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.find(dlg, QPushButton, "cloneDelete").click()
        self.assertEqual(tbl.rowCount(), 0)
        self.assertEqual(self.P.list_profiles(), [])

    def test_listen_when_not_ready_goes_to_settings(self):
        self.P.create_profile("小明", make_wav(os.path.join(self.tmp, "a.wav"), 5))
        dlg = self.win._build_clone_dialog()
        tabs = self.find(dlg, QTabWidget, "cloneTabs")
        tabs.setCurrentIndex(0)
        self.find(dlg, QTableWidget, "cloneTable").selectRow(0)
        self.find(dlg, QPushButton, "cloneListen").click()
        self.assertEqual(tabs.currentIndex(), 1)
        self.assertIn("模型", self.find(dlg, QLabel, "cloneStatus").text())
        self.win.player.play.assert_not_called()


if __name__ == "__main__":
    unittest.main()
