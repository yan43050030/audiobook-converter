"""GUI 首次运行向导测试（v6.0/C2-U2），offscreen 驱动真实对话框。

配置 / 存储目录重定向到临时位置；模型下载与试听合成打桩。Qt 不可加载时整类跳过。

Run with: python3 -m unittest tests/test_gui_wizard.py
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["AUDIOBOOK_LANG"] = "zh"  # 断言按中文界面文案写；不受系统语言影响（unittest 不加载 conftest）

try:
    from PySide6.QtWidgets import (QApplication, QDialog, QListWidget, QMessageBox,
                                   QPushButton, QRadioButton, QStackedWidget, QLabel)
    _QT_ERROR = None
except Exception as e:  # pragma: no cover - 依环境而定
    _QT_ERROR = e


@unittest.skipIf(_QT_ERROR is not None, f"Qt6 不可用: {_QT_ERROR}")
class TestFirstRunWizard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.app = QApplication.instance() or QApplication([])
            import gui_pyside6
            cls.gui = gui_pyside6
        except Exception as e:
            raise unittest.SkipTest(f"Qt 界面不可加载: {e}")

    def setUp(self):
        import tts_engine
        from audiobook import onboarding, models
        self.te, self.OB, self.M = tts_engine, onboarding, models
        self.tmp = tempfile.mkdtemp(prefix="ab_wiz_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.storage = os.path.join(self.tmp, "store")
        os.makedirs(self.storage)
        self.cfg_path = os.path.join(self.tmp, "config.json")
        old_cache = tts_engine._config_cache
        tts_engine._config_cache = None
        self.addCleanup(setattr, tts_engine, "_config_cache", old_cache)
        self.fail_downloads = set()
        self.downloaded = []

        def fake_download(kind, model_id, should_stop=None, storage_dir=None):
            if (kind, model_id) in self.fail_downloads:
                raise RuntimeError("网络超时")
            self.downloaded.append((kind, model_id))
            if kind == "piper":
                d = os.path.join(self.storage, "piper-models")
                os.makedirs(d, exist_ok=True)
                for fn in (f"{model_id}.onnx", f"{model_id}.onnx.json"):
                    open(os.path.join(d, fn), "wb").write(b"\0")
            elif kind == "whisper":
                d = os.path.join(self.storage, "whisper-models", model_id)
                os.makedirs(d, exist_ok=True)
                open(os.path.join(d, ".complete"), "w").close()
                open(os.path.join(d, "model.bin"), "wb").write(b"\0")

        self.preview_mp3 = os.path.join(self.tmp, "p.mp3")
        open(self.preview_mp3, "wb").write(b"\0")
        patches = [
            mock.patch.object(tts_engine, "CONFIG_PATH", self.cfg_path),
            mock.patch.object(tts_engine, "get_storage_dir", lambda: self.storage),
            mock.patch.object(tts_engine, "get_model_search_paths", lambda: []),
            mock.patch.object(models, "download", fake_download),
            # 所有场景都可选，推荐联网（与运行环境装了哪些引擎无关）
            mock.patch.object(onboarding, "availability", lambda s: (True, "ok")),
            mock.patch.object(self.gui, "generate_preview", lambda *a, **k: self.preview_mp3),
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No),
            mock.patch.object(QMessageBox, "critical"),
            mock.patch.object(QMessageBox, "warning"),
            mock.patch.object(QMessageBox, "information"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.win = self.gui.AudiobookConverterMain()
        self.win.player.play = mock.MagicMock()

    # ---- helpers ----
    def _wait(self, worker_attr):
        w = getattr(self.win, worker_attr, None)
        deadline = time.time() + 20
        while w is not None and w.isRunning() and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        for _ in range(30):
            self.app.processEvents()

    def _w(self, dlg, cls, name):
        return dlg.findChild(cls, name)

    def _cfg(self):
        with open(self.cfg_path, encoding="utf-8") as f:
            return json.load(f)

    # ---- tests ----
    def test_offline_flow_downloads_listens_and_selects_engine(self):
        dlg = self.win._build_first_run_wizard()
        dlg.show()
        stack = self._w(dlg, QStackedWidget, "wizardStack")
        nxt = self._w(dlg, QPushButton, "wizardNext")
        self.assertTrue(self._w(dlg, QRadioButton, "wizardScenario_online").isChecked(),
                        "默认选中推荐场景")

        self._w(dlg, QRadioButton, "wizardScenario_offline").click()
        nxt.click()
        self.assertEqual(stack.currentIndex(), 1)
        self._wait("_wizard_dl_worker")
        rows = self._w(dlg, QListWidget, "wizardDownloadList")
        self.assertEqual(rows.count(), 1)
        self.assertIn("✓ 完成", rows.item(0).text())
        self.assertEqual(self.downloaded, [("piper", "zh_CN-huayan-medium")])
        self.assertTrue(nxt.isEnabled())

        nxt.click()
        self.assertEqual(stack.currentIndex(), 2)
        self._w(dlg, QPushButton, "wizardListen").click()
        self._wait("_wizard_preview_worker")
        self.assertIn("✓", self._w(dlg, QLabel, "wizardListenStatus").text())
        self.win.player.play.assert_called_once_with(self.preview_mp3)

        nxt.click()  # 完成
        self.assertEqual(dlg.result(), QDialog.DialogCode.Accepted)
        cfg = self._cfg()
        self.assertIs(cfg["wizard_completed"], True)
        self.assertEqual(cfg["default_engine"], "piper")
        self.assertEqual(self.win._get_current_engine(), "piper")

    def test_online_skips_download_step(self):
        dlg = self.win._build_first_run_wizard()
        dlg.show()
        stack = self._w(dlg, QStackedWidget, "wizardStack")
        self._w(dlg, QPushButton, "wizardNext").click()       # 推荐的联网场景
        self.assertEqual(stack.currentIndex(), 2, "无需下载应直接到试听")
        self._w(dlg, QPushButton, "wizardBack").click()
        self.assertEqual(stack.currentIndex(), 0)
        self.assertEqual(self.downloaded, [])

    def test_failed_download_can_be_retried(self):
        self.fail_downloads.add(("whisper", "base"))
        dlg = self.win._build_first_run_wizard()
        dlg.show()
        self._w(dlg, QRadioButton, "wizardScenario_offline_asr").click()
        self._w(dlg, QPushButton, "wizardNext").click()
        self._wait("_wizard_dl_worker")
        rows = self._w(dlg, QListWidget, "wizardDownloadList")
        self.assertIn("✓ 完成", rows.item(0).text())
        self.assertIn("失败", rows.item(1).text())
        retry = self._w(dlg, QPushButton, "wizardRetry")
        self.assertTrue(retry.isVisible())

        self.fail_downloads.clear()
        retry.click()
        self._wait("_wizard_dl_worker")
        self.assertIn("✓ 完成", rows.item(1).text())
        self.assertFalse(retry.isVisible())
        # 重试只重下失败的那项
        self.assertEqual(self.downloaded, [("piper", "zh_CN-huayan-medium"), ("whisper", "base")])

    def test_skip_marks_completed_without_engine(self):
        before = self.win._get_current_engine()
        dlg = self.win._build_first_run_wizard()
        dlg.show()
        self._w(dlg, QPushButton, "wizardSkip").click()
        for _ in range(10):
            self.app.processEvents()
        cfg = self._cfg()
        self.assertIs(cfg["wizard_completed"], True)
        self.assertNotIn("default_engine", cfg)
        self.assertEqual(self.win._get_current_engine(), before)
        self.assertFalse(self.OB.should_show())


@unittest.skipIf(_QT_ERROR is not None, f"Qt6 不可用: {_QT_ERROR}")
class TestEngineSelectionSticks(unittest.TestCase):
    """回归：依赖刷新重建引擎按钮时不再跳回第一个引擎；手动切换会被记住。"""

    @classmethod
    def setUpClass(cls):
        try:
            cls.app = QApplication.instance() or QApplication([])
            import gui_pyside6
            cls.gui = gui_pyside6
        except Exception as e:
            raise unittest.SkipTest(f"Qt 界面不可加载: {e}")

    def setUp(self):
        import tts_engine
        self.te = tts_engine
        self.tmp = tempfile.mkdtemp(prefix="ab_eng_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg_path = os.path.join(self.tmp, "config.json")
        old_cache = tts_engine._config_cache
        tts_engine._config_cache = None
        self.addCleanup(setattr, tts_engine, "_config_cache", old_cache)
        p = mock.patch.object(tts_engine, "CONFIG_PATH", self.cfg_path)
        p.start()
        self.addCleanup(p.stop)

    def _btn(self, win, eid):
        return next(b for e, b, _n in win._engine_btns if e == eid)

    def test_rebuild_keeps_selection_and_click_is_remembered(self):
        win = self.gui.AudiobookConverterMain()
        first = win._engine_btns[0][0]
        other = next(e for e, _b, _n in win._engine_btns if e != first)
        self._btn(win, other).click()                 # 用户点击
        self.assertEqual(win._get_current_engine(), other)
        win._rebuild_engine_buttons()                 # 依赖刷新 / 模型增删会触发
        self.assertEqual(win._get_current_engine(), other)
        with open(self.cfg_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["default_engine"], other)

        win2 = self.gui.AudiobookConverterMain()      # 「下次启动」
        self.assertEqual(win2._get_current_engine(), other)


if __name__ == "__main__":
    unittest.main()
