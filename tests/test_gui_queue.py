"""GUI 生成队列页测试（v6.0/C2-U3），offscreen 驱动真实主窗口。

转换中途某章失败 → 队列表显示失败与错误信息、行内「重试」→ 仅重跑该章 → 全部完成。
合成函数打桩；Qt 不可加载（未安装 / 无图形库）时整类跳过。

Run with: python3 -m unittest tests/test_gui_queue.py
"""

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["AUDIOBOOK_LANG"] = "zh"  # 断言按中文界面文案写；不受系统语言影响（unittest 不加载 conftest）

try:
    from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
    _QT_ERROR = None
except Exception as e:  # pragma: no cover - 依环境而定
    _QT_ERROR = e

BOOK = "第一章 甲\n内容一。\n\n第二章 乙\n内容二。\n\n第三章 丙\n内容三。\n"


@unittest.skipIf(_QT_ERROR is not None, f"Qt6 不可用: {_QT_ERROR}")
class TestGenerationQueueTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.app = QApplication.instance() or QApplication([])
            import gui_pyside6
            cls.gui = gui_pyside6
        except Exception as e:  # 缺 libEGL 等系统库
            raise unittest.SkipTest(f"Qt 界面不可加载: {e}")

    def setUp(self):
        import tts_engine
        self.te = tts_engine
        self.out = tempfile.mkdtemp(prefix="ab_gui_queue_")
        self.addCleanup(shutil.rmtree, self.out, True)
        self.fail_ch2 = True

        def fake(text, voice, rate, out_path, **kw):
            if self.fail_ch2 and "内容二" in text:
                raise RuntimeError("模拟合成失败")
            with open(out_path, "wb") as f:
                f.write(b"\x00" * 16)

        for p in (mock.patch.object(tts_engine, "_generate_one_safe", fake),
                  mock.patch.object(QMessageBox, "question",
                                    return_value=QMessageBox.StandardButton.No),
                  mock.patch.object(QMessageBox, "critical"),
                  mock.patch.object(QMessageBox, "warning"),
                  mock.patch.object(QMessageBox, "information")):
            p.start()
            self.addCleanup(p.stop)

        self.win = self.gui.AudiobookConverterMain()
        self.win._get_current_engine = lambda: "local"
        self.win._text_area.setPlainText(BOOK)

    def _pump(self):
        w = self.win._convert_worker
        deadline = time.time() + 30
        while w.isRunning() and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        for _ in range(30):
            self.app.processEvents()

    def _rows(self):
        t = self.win._queue_table
        return [(t.item(r, 2).text(), t.item(r, 3).text(),
                 isinstance(t.cellWidget(r, 4), QPushButton)) for r in range(t.rowCount())]

    def _tab_title(self):
        tw = self.win._tab_widget
        return tw.tabText(tw.indexOf(self.win._queue_tab))

    def test_failure_then_row_retry(self):
        win = self.win
        win._run_convert(self.out, "测试书", [0, 1, 2], resume=False)
        self.assertIs(win._tab_widget.currentWidget(), win._queue_tab, "开始生成应切到生成队列页")
        self._pump()

        rows = self._rows()
        self.assertEqual([r[0] for r in rows], ["✅ 完成", "❌ 失败", "✅ 完成"])
        self.assertIn("模拟合成失败", rows[1][1])
        self.assertEqual([r[2] for r in rows], [False, True, False], "只有失败行有「重试」按钮")
        self.assertIn("失败 1", self._tab_title())
        self.assertTrue(win._queue_retry_all_btn.isEnabled())

        self.fail_ch2 = False
        win._queue_table.cellWidget(1, 4).click()
        self._pump()

        self.assertEqual([r[0] for r in self._rows()], ["✅ 完成"] * 3)
        self.assertEqual(self._tab_title(), "生成队列 (3/3)")
        self.assertFalse(win._queue_retry_all_btn.isEnabled())
        self.assertIsNone(self.te.load_progress(self.out))

    def test_retry_all_failed(self):
        win = self.win
        win._run_convert(self.out, "测试书", [0, 1, 2], resume=False)
        self._pump()
        self.fail_ch2 = False
        win._queue_retry_all_btn.click()
        self._pump()
        self.assertEqual([r[0] for r in self._rows()], ["✅ 完成"] * 3)

    def test_bottom_bar_hidden_only_on_asr_tab(self):
        win = self.win
        win.show()
        win._tab_widget.setCurrentWidget(win._queue_tab)
        self.assertTrue(win._quickbar_widget.isVisible())
        win._tab_widget.setCurrentWidget(win._asr_tab)
        self.assertFalse(win._quickbar_widget.isVisible())
        win.hide()


if __name__ == "__main__":
    unittest.main()
