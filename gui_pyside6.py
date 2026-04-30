"""Qt6 GUI — 文字转有声读物 v5.0.2（兼容 PySide6 / PyQt6）"""

import os, sys, threading, subprocess, platform, logging, json, tempfile, shutil
from typing import Optional

# 自动适配 PySide6 或 PyQt6
try:
    from PySide6.QtWidgets import (
        QMainWindow, QWidget, QSplitter, QTabWidget, QStackedWidget,
        QVBoxLayout, QHBoxLayout, QGroupBox, QPushButton, QLabel,
        QComboBox, QRadioButton, QButtonGroup, QCheckBox, QSlider,
        QSpinBox, QProgressBar, QPlainTextEdit, QTreeWidget, QTreeWidgetItem,
        QLineEdit, QFileDialog, QMessageBox, QFrame, QSizePolicy,
        QHeaderView, QAbstractItemView, QApplication,
    )
    from PySide6.QtCore import (
        Qt, Signal, Slot, QThread, QTimer, QSize, QRect,
    )
    from PySide6.QtGui import (
        QFont, QIcon, QPalette, QColor, QTextCursor,
    )
    _QT_BACKEND = "PySide6"
except ImportError:
    from PyQt6.QtWidgets import (
        QMainWindow, QWidget, QSplitter, QTabWidget, QStackedWidget,
        QVBoxLayout, QHBoxLayout, QGroupBox, QPushButton, QLabel,
        QComboBox, QRadioButton, QButtonGroup, QCheckBox, QSlider,
        QSpinBox, QProgressBar, QPlainTextEdit, QTreeWidget, QTreeWidgetItem,
        QLineEdit, QFileDialog, QMessageBox, QFrame, QSizePolicy,
        QHeaderView, QAbstractItemView, QApplication,
    )
    from PyQt6.QtCore import (
        Qt, QThread, QTimer, QSize, QRect, pyqtSignal as Signal, pyqtSlot as Slot,
    )
    from PyQt6.QtGui import (
        QFont, QIcon, QPalette, QColor, QTextCursor,
    )
    _QT_BACKEND = "PyQt6"

from tts_engine import (
    VERSION, LOG_PATH, get_voice_list, get_voice_id, generate_preview,
    convert_batch, detect_chapters, load_progress, merge_mp3_files, logger,
    get_storage_dir, set_storage_dir, add_download_listener, remove_download_listener,
    refresh_local_voices, check_engine_ready, scan_storage_dependencies,
    get_registered_engines, add_model_search_path, _invalidate_scan_cache,
    COSYVOICE_PYTHON_AVAILABLE, get_cosyvoice_model_dir,
    PIPER_VOICES, _ensure_piper_model,
    COSYVOICE_MODEL_URLS, _ensure_cosyvoice_model,
    split_text, _generate_one_safe, split_by_duration,
)
from asr_engine import (transcribe, check_asr_ready, WHISPER_MODELS, unload_whisper_model,
                        scan_external_asr_engines, external_asr_transcribe)
from audio_player import AudioPlayer
from file_reader import load_file_content


# ===================== Worker Threads =====================

class _BaseWorker(QThread):
    """所有后台工作线程的基类，提供统一的错误和完成信号"""
    finished_ok = Signal(object)   # result
    error_occurred = Signal(str)  # error message
    _should_stop = False

    def request_stop(self):
        self._should_stop = True

    def _check_stop(self):
        return self._should_stop


class ConvertWorker(_BaseWorker):
    """批量转换工作线程"""
    progress_update = Signal(int, int)  # current, total

    def setup(self, text, voice, rate, output_dir, split_mode, time_minutes,
              file_prefix, selected_indices, engine, normalize_audio,
              dialogue_detection, voice_map, resume):
        self._params = (text, voice, rate, output_dir, split_mode, time_minutes,
                        file_prefix, selected_indices, engine, normalize_audio,
                        dialogue_detection, voice_map, resume)

    def run(self):
        try:
            (text, voice, rate, output_dir, split_mode, time_minutes,
             file_prefix, selected_indices, engine, normalize_audio,
             dialogue_detection, voice_map, resume) = self._params

            def progress_cb(current, total, **kw):
                self.progress_update.emit(current, total)

            def stop_cb():
                return self._should_stop

            files = convert_batch(
                text=text, voice=voice, rate=rate, output_dir=output_dir,
                split_mode=split_mode, time_minutes=time_minutes,
                file_prefix=file_prefix, selected_indices=selected_indices,
                engine=engine, progress_callback=progress_cb,
                should_stop=stop_cb, resume=resume,
                normalize_audio=normalize_audio,
                dialogue_detection=dialogue_detection, voice_map=voice_map,
            )
            self.finished_ok.emit(files)
        except Exception as e:
            logger.error(f"转换异常: {e}", exc_info=True)
            self.error_occurred.emit(str(e))


class PreviewWorker(_BaseWorker):
    """试听生成工作线程"""
    def setup(self, text, voice, rate, engine, max_chars=200):
        self._params = (text, voice, rate, engine, max_chars)

    def run(self):
        try:
            text, voice, rate, engine, max_chars = self._params
            path = generate_preview(text, voice, rate, engine=engine,
                                    should_stop=self._check_stop, max_chars=max_chars)
            self.finished_ok.emit(path)
        except Exception as e:
            logger.error(f"试听生成失败: {e}")
            self.error_occurred.emit(str(e))


class StreamPreviewWorker(_BaseWorker):
    """流式试听工作线程：逐段生成并发射路径信号"""
    segment_ready = Signal(str)  # file path of next segment

    def setup(self, text, voice, rate, engine, seg_chars=400):
        self._params = (text, voice, rate, engine, seg_chars)

    def run(self):
        try:
            text, voice, rate, engine, seg_chars = self._params
            first = text[:seg_chars]
            rest = text[seg_chars:]
            rest_segs = split_text(rest, max_length=seg_chars * 2) if rest else []
            all_segs = [first] + rest_segs

            tmp_dir = tempfile.mkdtemp(prefix="audiobook_preview_")
            for seg in all_segs:
                if self._check_stop():
                    break
                out = os.path.join(tmp_dir, f"seg_{len(os.listdir(tmp_dir)):04d}.mp3")
                _generate_one_safe(seg, voice, rate, out, engine=engine,
                                   should_stop=self._check_stop)
                if self._check_stop():
                    break
                self.segment_ready.emit(out)
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception as e:
            logger.error(f"流式试听失败: {e}")
            self.error_occurred.emit(str(e))


class AsrWorker(_BaseWorker):
    """ASR 语音识别工作线程"""
    progress_update = Signal(int, int)

    def setup(self, input_path, storage_dir, model_size, language, output_format):
        self._params = (input_path, storage_dir, model_size, language, output_format)

    def run(self):
        try:
            input_path, storage_dir, model_size, language, output_format = self._params
            def progress_cb(cur, tot):
                self.progress_update.emit(cur, tot)
            def stop_cb():
                return self._should_stop
            result = transcribe(
                input_path=input_path, storage_dir=storage_dir,
                model_size=model_size, language=language,
                output_format=output_format,
                progress_callback=progress_cb, should_stop=stop_cb,
            )
            self.finished_ok.emit(result)
        except Exception as e:
            logger.error(f"ASR 失败: {e}", exc_info=True)
            self.error_occurred.emit(str(e))


class DownloadWorker(_BaseWorker):
    """模型下载工作线程"""
    progress_text = Signal(str)

    def setup(self, download_type, model_key=None):
        self._params = (download_type, model_key)

    def run(self):
        try:
            dtype, model_key = self._params
            if dtype == "piper":
                for v in list(PIPER_VOICES.values()):
                    _ensure_piper_model(v, should_stop=self._check_stop)
                self.finished_ok.emit("piper")
            elif dtype == "cosyvoice":
                models = list(COSYVOICE_MODEL_URLS.keys())
                for mk in models:
                    _ensure_cosyvoice_model(mk, should_stop=self._check_stop)
                self.finished_ok.emit("cosyvoice")
        except Exception as e:
            logger.error(f"下载失败: {e}")
            self.error_occurred.emit(str(e))


# ===================== QSS Stylesheet =====================

LIGHT_QSS = """
/* === 全局 === */
* { font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", "Helvetica Neue", sans-serif; font-size: 14px; }
QWidget { background: #F2F0EC; color: #1D1B1A; }
QLabel { color: #333; background: transparent; }

/* === 卡片 === */
QGroupBox {
    font-size: 14px; font-weight: bold; color: #1D1B1A;
    border: 1px solid #DDD9D2; border-radius: 8px;
    margin-top: 14px; padding-top: 18px;
    background: #FAF8F5;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 14px; padding: 0 8px; color: #1D1B1A;
    background: transparent;
}

/* === 按钮 === */
QPushButton {
    font-size: 14px; padding: 8px 18px; border-radius: 6px; color: #1D1B1A;
    border: 1px solid #D5D0C8; background: #F5F2ED; min-height: 32px;
}
QPushButton:hover { background: #EBE6DE; border-color: #B5AFA5; }
QPushButton:pressed { background: #DDD9D2; }
QPushButton:disabled { color: #AAA; background: #EBE6DE; }
QPushButton#accentBtn {
    background: #2563EB; color: white; border: none; font-weight: bold; font-size: 15px; padding: 10px 24px;
}
QPushButton#accentBtn:hover { background: #1d4ed8; }
QPushButton#accentBtn:pressed { background: #1e40af; }
QPushButton#accentBtn:disabled { background: #93A8D8; }
QPushButton#stopBtn { color: #666; }
QPushButton#stopBtn:hover { background: #F5E5E5; border-color: #E0B0B0; color: #dc2626; }

/* === 输入控件 === */
QComboBox {
    font-size: 14px; padding: 5px 10px; border: 1px solid #D5D0C8; color: #1D1B1A;
    border-radius: 6px; min-height: 30px; background: #FAF8F5;
}
QComboBox:hover { border-color: #2563EB; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    color: #1D1B1A; background: #FAF8F5; selection-background-color: #2563EB;
    selection-color: white; outline: none; border: 1px solid #D5D0C8;
}
QSpinBox {
    font-size: 14px; padding: 4px 8px; border: 1px solid #D5D0C8; border-radius: 6px;
    color: #1D1B1A; background: #FAF8F5; min-height: 28px;
}
QLineEdit {
    font-size: 14px; padding: 5px 10px; border: 1px solid #D5D0C8; border-radius: 6px;
    color: #1D1B1A; background: #FAF8F5;
}

/* === 单选 / 复选 — 关键：指示器必须可见 === */
QRadioButton {
    font-size: 14px; spacing: 8px; color: #1D1B1A; padding: 5px 2px; background: transparent;
}
QRadioButton::indicator {
    width: 18px; height: 18px; border-radius: 9px;
    border: 2px solid #B5AFA5; background: #FAF8F5;
}
QRadioButton::indicator:hover { border-color: #2563EB; }
QRadioButton::indicator:checked {
    border: 2px solid #2563EB; background: #2563EB;
}
QCheckBox {
    font-size: 14px; spacing: 8px; color: #1D1B1A; background: transparent;
}
QCheckBox::indicator {
    width: 18px; height: 18px; border-radius: 4px;
    border: 2px solid #B5AFA5; background: #FAF8F5;
}
QCheckBox::indicator:hover { border-color: #2563EB; }
QCheckBox::indicator:checked {
    border: 2px solid #2563EB; background: #2563EB;
}

/* === 滑块 === */
QSlider::groove:horizontal {
    height: 6px; background: #D5D0C8; border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 18px; height: 18px; margin: -6px 0; border-radius: 9px;
    background: #2563EB; border: 2px solid #FAF8F5;
}
QSlider::handle:horizontal:hover { background: #1d4ed8; }

/* === 文本 / 树 / 表格 === */
QPlainTextEdit {
    font-size: 15px; border: 1px solid #DDD9D2; border-radius: 6px;
    color: #1D1B1A; background: #FAF8F5; padding: 6px;
}
QTreeWidget {
    font-size: 14px; border: 1px solid #DDD9D2; border-radius: 6px;
    color: #1D1B1A; background: #FAF8F5;
}
QTreeWidget::item { padding: 4px 4px; }
QTreeWidget::item:selected { background: #2563EB; color: white; }
QTreeWidget::item:hover { background: #EBE6DE; }
QTableWidget, QTableView { color: #1D1B1A; background: #FAF8F5; }
QHeaderView::section {
    color: #333; background: #F5F2ED; padding: 6px 10px; border: 1px solid #DDD9D2;
    font-size: 14px; font-weight: bold;
}

/* === 进度条 === */
QProgressBar {
    border: 1px solid #DDD9D2; border-radius: 4px; text-align: center;
    font-size: 12px; color: #333; background: #EBE6DE; min-height: 18px;
}
QProgressBar::chunk { background: #2563EB; border-radius: 3px; }

/* === 标签页 === */
QTabWidget::pane { border: 1px solid #DDD9D2; border-radius: 6px; background: #F2F0EC; }
QTabBar::tab {
    font-size: 14px; padding: 8px 20px; color: #666;
    background: #EBE6DE; border: 1px solid #DDD9D2; border-bottom: none;
}
QTabBar::tab:selected { color: #1D1B1A; background: #F2F0EC; font-weight: bold; }
QTabBar::tab:hover { color: #2563EB; }

/* === 分割 === */
QSplitter::handle { width: 4px; background: #D5D0C8; }

/* === 侧栏 === */
QFrame#sidebarFrame { background: #E8E4DE; border-right: 1px solid #D5D0C8; }
QPushButton#sidebarBtn {
    text-align: left; padding: 14px 16px; border: none; color: #555;
    border-radius: 8px; font-size: 15px; background: transparent; min-height: 40px;
}
QPushButton#sidebarBtn:hover { background: #DDD9D2; color: #1D1B1A; }
QPushButton#sidebarBtn[active="true"] { background: #2563EB; color: white; font-weight: bold; }

/* === 底部栏 === */
QFrame#quickBar { background: #F5F2ED; border-top: 1px solid #DDD9D2; }
QFrame#quickBar QLabel { font-size: 13px; background: transparent; }
QFrame#statusBar { background: #EBE6DE; border-top: 1px solid #DDD9D2; }
QFrame#statusBar QLabel { color: #777; font-size: 12px; background: transparent; }
"""

DARK_QSS = """
/* === 全局 — 必须设 background 防止 Windows 默认白色 === */
* { font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", "Helvetica Neue", sans-serif; font-size: 14px; }
QWidget { background: #1E1E1E; color: #E0E0E0; }
QLabel { color: #CCC; background: transparent; }

/* === 卡片 === */
QGroupBox {
    font-size: 14px; font-weight: bold; color: #E0E0E0;
    border: 1px solid #444; border-radius: 8px;
    margin-top: 14px; padding-top: 18px;
    background: #282828;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 14px; padding: 0 8px; color: #E0E0E0;
    background: transparent;
}

/* === 按钮 === */
QPushButton {
    font-size: 14px; padding: 8px 18px; border-radius: 6px; min-height: 32px;
    background: #333; border: 1px solid #555; color: #E0E0E0;
}
QPushButton:hover { background: #444; border-color: #666; }
QPushButton:pressed { background: #2A2A2A; }
QPushButton:disabled { color: #666; background: #2A2A2A; }
QPushButton#accentBtn {
    background: #3B82F6; color: white; border: none; font-weight: bold; font-size: 15px; padding: 10px 24px;
}
QPushButton#accentBtn:hover { background: #2563EB; }
QPushButton#accentBtn:disabled { background: #1E3A5F; color: #7AA2F7; }
QPushButton#stopBtn { color: #999; }
QPushButton#stopBtn:hover { background: #442222; border-color: #663333; color: #F87171; }

/* === 输入控件 === */
QComboBox {
    font-size: 14px; padding: 5px 10px; min-height: 30px;
    background: #2A2A2A; border: 1px solid #555; color: #E0E0E0;
    border-radius: 6px;
}
QComboBox:hover { border-color: #3B82F6; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    color: #E0E0E0; background: #2A2A2A; selection-background-color: #3B82F6;
    selection-color: white; outline: none; border: 1px solid #555;
}
QSpinBox {
    font-size: 14px; padding: 4px 8px; border: 1px solid #555; border-radius: 6px;
    color: #E0E0E0; background: #2A2A2A; min-height: 28px;
}
QLineEdit {
    font-size: 14px; padding: 5px 10px; border: 1px solid #555; border-radius: 6px;
    color: #E0E0E0; background: #2A2A2A;
}

/* === 单选 / 复选 — 指示器必须可见 === */
QRadioButton {
    font-size: 14px; spacing: 8px; color: #E0E0E0; padding: 5px 2px; background: transparent;
}
QRadioButton::indicator {
    width: 18px; height: 18px; border-radius: 9px;
    border: 2px solid #666; background: #333;
}
QRadioButton::indicator:hover { border-color: #3B82F6; }
QRadioButton::indicator:checked {
    border: 2px solid #3B82F6; background: #3B82F6;
}
QCheckBox {
    font-size: 14px; spacing: 8px; color: #E0E0E0; background: transparent;
}
QCheckBox::indicator {
    width: 18px; height: 18px; border-radius: 4px;
    border: 2px solid #666; background: #333;
}
QCheckBox::indicator:hover { border-color: #3B82F6; }
QCheckBox::indicator:checked {
    border: 2px solid #3B82F6; background: #3B82F6;
}

/* === 滑块 === */
QSlider::groove:horizontal {
    height: 6px; background: #444; border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 18px; height: 18px; margin: -6px 0; border-radius: 9px;
    background: #3B82F6; border: 2px solid #1E1E1E;
}
QSlider::handle:horizontal:hover { background: #2563EB; }

/* === 文本 / 树 / 表格 === */
QPlainTextEdit {
    font-size: 15px; border: 1px solid #444; border-radius: 6px;
    color: #E0E0E0; background: #1A1A1A; padding: 6px;
}
QTreeWidget {
    font-size: 14px; border: 1px solid #444; border-radius: 6px;
    color: #E0E0E0; background: #1A1A1A;
}
QTreeWidget::item { padding: 4px 4px; }
QTreeWidget::item:selected { background: #3B82F6; color: white; }
QTreeWidget::item:hover { background: #2A2A2A; }
QTableWidget, QTableView { color: #E0E0E0; background: #1A1A1A; }
QHeaderView::section {
    color: #CCC; background: #2A2A2A; padding: 6px 10px; border: 1px solid #444;
    font-size: 14px; font-weight: bold;
}

/* === 进度条 === */
QProgressBar {
    border: 1px solid #444; border-radius: 4px; text-align: center;
    font-size: 12px; color: #E0E0E0; background: #2A2A2A; min-height: 18px;
}
QProgressBar::chunk { background: #3B82F6; border-radius: 3px; }

/* === 标签页 === */
QTabWidget::pane { border: 1px solid #444; border-radius: 6px; background: #1E1E1E; }
QTabBar::tab {
    font-size: 14px; padding: 8px 20px; color: #888;
    background: #282828; border: 1px solid #444; border-bottom: none;
}
QTabBar::tab:selected { color: #E0E0E0; background: #1E1E1E; font-weight: bold; }
QTabBar::tab:hover { color: #3B82F6; }

/* === 分割 === */
QSplitter::handle { width: 4px; background: #444; }

/* === 侧栏 === */
QFrame#sidebarFrame { background: #151515; border-right: 1px solid #333; }
QPushButton#sidebarBtn {
    text-align: left; padding: 14px 16px; border: none; color: #888;
    border-radius: 8px; font-size: 15px; background: transparent; min-height: 40px;
}
QPushButton#sidebarBtn:hover { background: #2A2A2A; color: #CCC; }
QPushButton#sidebarBtn[active="true"] { background: #3B82F6; color: white; font-weight: bold; }

/* === 底部栏 === */
QFrame#quickBar { background: #252525; border-top: 1px solid #333; }
QFrame#quickBar QLabel { font-size: 13px; background: transparent; }
QFrame#statusBar { background: #1A1A1A; border-top: 1px solid #2A2A2A; }
QFrame#statusBar QLabel { color: #888; font-size: 12px; background: transparent; }
"""


# ===================== Main Window =====================

class AudiobookConverterMain(QMainWindow):
    SIDEBAR_ITEMS = [
        ("files",    "📂 文件管理"),
        ("engine",   "🎤 引擎语音"),
        ("speed",    "⚡ 语速输出"),
        ("dialogue", "🎭 对话识别"),
        ("storage",  "💾 存储依赖"),
        ("appear",   "🎨 外观"),
    ]

    def __init__(self):
        super().__init__()
        self._file_paths: list = []
        self._single_file_path = None
        self.is_converting = False
        self._preview_state = "idle"
        self._theme = "light"
        self._audio_file_path = None
        self._asr_last_result = ""
        self.chapters = []
        self._download_desc = ""

        # Audio player
        self.player = AudioPlayer(on_state_change=self._on_player_state_changed)

        # Workers
        self._convert_worker: Optional[ConvertWorker] = None
        self._preview_worker: Optional[PreviewWorker] = None
        self._stream_worker: Optional[StreamPreviewWorker] = None
        self._asr_worker: Optional[AsrWorker] = None
        self._download_worker: Optional[DownloadWorker] = None

        self._build_ui()
        self._wire_signals()
        self._load_theme()

        # Download progress listener
        add_download_listener(self._on_download_progress)

    # ================ UI Construction ================

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 0)
        root.setSpacing(0)

        # Tabs
        self._tab_widget = QTabWidget()
        self._tts_tab = QWidget()
        self._asr_tab = QWidget()
        self._tab_widget.addTab(self._tts_tab, "文字转语音")
        self._tab_widget.addTab(self._asr_tab, "语音转文字")
        root.addWidget(self._tab_widget, 1)

        # === TTS Tab ===
        tts_layout = QHBoxLayout(self._tts_tab)
        tts_layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        tts_layout.addWidget(splitter)

        # --- Left: Chapters + Text ---
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 0, 4, 0)

        ch_group = QGroupBox("章节列表（勾选要生成的章节）")
        ch_layout = QVBoxLayout(ch_group)
        ch_toolbar = QHBoxLayout()
        btn_all = QPushButton("全选"); btn_all.clicked.connect(self._select_all_chapters)
        btn_none = QPushButton("全不选"); btn_none.clicked.connect(self._deselect_all_chapters)
        ch_toolbar.addWidget(btn_all); ch_toolbar.addWidget(btn_none)
        self._chapter_count = QLabel(""); self._chapter_count.setStyleSheet("color:gray")
        ch_toolbar.addStretch(); ch_toolbar.addWidget(self._chapter_count)
        ch_layout.addLayout(ch_toolbar)
        search_row = QHBoxLayout()
        self._chapter_search = QLineEdit(); self._chapter_search.setPlaceholderText("搜索章节...")
        self._chapter_search.textChanged.connect(self._filter_chapters)
        search_row.addWidget(self._chapter_search)
        ch_layout.addLayout(search_row)
        self._chapter_tree = QTreeWidget()
        self._chapter_tree.setHeaderHidden(True)
        self._chapter_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._chapter_tree.itemSelectionChanged.connect(self._on_chapter_select)
        ch_layout.addWidget(self._chapter_tree)
        left_layout.addWidget(ch_group)

        text_group = QGroupBox("文本内容")
        text_gl = QVBoxLayout(text_group)
        self._text_area = QPlainTextEdit()
        self._text_area.setFont(QFont("Helvetica", 12))
        text_gl.addWidget(self._text_area)
        left_layout.addWidget(text_group, 1)

        splitter.addWidget(left)

        # --- Sidebar ---
        sidebar = self._build_sidebar()
        splitter.addWidget(sidebar)

        # --- Right: Settings panels ---
        self._panel_stack = QStackedWidget()
        self._panels = {}
        for pid, _label in self.SIDEBAR_ITEMS:
            w = getattr(self, f"_build_panel_{pid}")()
            self._panels[pid] = w
            self._panel_stack.addWidget(w)
        splitter.addWidget(self._panel_stack)

        splitter.setSizes([520, 100, 380])
        self._show_panel("files")

        # === ASR Tab ===
        self._build_asr_tab()
        self._refresh_asr_engines()

        # === Bottom: QuickBar + StatusBar + Progress ===
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 4, 0, 0)
        bottom_layout.setSpacing(0)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        bottom_layout.addWidget(self._progress_bar)

        # Download progress
        self._dl_label = QLabel("")
        self._dl_label.setStyleSheet("color:#2563EB;font-size:12px")
        self._dl_label.setVisible(False)
        self._dl_progress = QProgressBar()
        self._dl_progress.setVisible(False)
        bottom_layout.addWidget(self._dl_label)
        bottom_layout.addWidget(self._dl_progress)

        # Status bar
        self._statusbar_widget = self._build_statusbar()
        bottom_layout.addWidget(self._statusbar_widget)

        # Quick bar
        self._quickbar_widget = self._build_quickbar()
        bottom_layout.addWidget(self._quickbar_widget)

        root.addWidget(bottom)

        self._status_label = QLabel("就绪")
        self._status_label.setStyleSheet("color:gray;font-size:12px;padding:2px")
        bottom_layout.addWidget(self._status_label)

    # ================ Sidebar ================

    def _build_sidebar(self):
        frame = QFrame()
        frame.setObjectName("sidebarFrame")
        frame.setFixedWidth(130)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(2)
        title = QLabel("导航")
        title.setFont(QFont("Helvetica", 10, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(8)
        self._sidebar_btns = {}
        for pid, label in self.SIDEBAR_ITEMS:
            btn = QPushButton(label)
            btn.setObjectName("sidebarBtn")
            btn.clicked.connect(lambda checked, p=pid: self._show_panel(p))
            layout.addWidget(btn)
            self._sidebar_btns[pid] = btn
        layout.addStretch()
        return frame

    def _show_panel(self, panel_id: str):
        if panel_id in self._panels:
            self._panel_stack.setCurrentWidget(self._panels[panel_id])
        for pid, btn in self._sidebar_btns.items():
            btn.setProperty("active", "true" if pid == panel_id else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        if panel_id == "storage":
            self._refresh_deps()

    # ================ Panel Builders ================

    def _build_panel_engine(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        engine_card = QGroupBox("选择引擎")
        self._engine_card_layout = QVBoxLayout(engine_card)
        self._engine_group = QButtonGroup(self)
        self._engine_group.setExclusive(True)
        self._engine_btns = []
        self._engine_grid = QHBoxLayout()
        self._engine_grid.setSpacing(6)
        self._engine_card_layout.addLayout(self._engine_grid)
        self._engine_group.buttonClicked.connect(self._on_engine_change)
        layout.addWidget(engine_card)

        voice_card = QGroupBox("语音参数")
        vc_layout = QHBoxLayout(voice_card)
        vc_layout.addWidget(QLabel("语音:"))
        self._voice_combo = QComboBox()
        self._voice_combo.setMinimumWidth(180)
        self._voice_combo.currentTextChanged.connect(self._on_voice_change)
        vc_layout.addWidget(self._voice_combo, 1)
        btn_preview = QPushButton("试听")
        btn_preview.clicked.connect(self._preview_voice_sample)
        vc_layout.addWidget(btn_preview)
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self._refresh_voices)
        vc_layout.addWidget(btn_refresh)
        layout.addWidget(voice_card)

        btn_ext = QPushButton("+ 添加 / 配置外挂引擎")
        btn_ext.clicked.connect(self._open_external_dialog)
        layout.addWidget(btn_ext)

        layout.addStretch()
        self._rebuild_engine_buttons()
        return w

    def _build_panel_speed(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        rate_card = QGroupBox("语速控制")
        rc_layout = QVBoxLayout(rate_card)
        self._rate_label = QLabel("正常")
        self._rate_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        rc_layout.addWidget(self._rate_label)
        self._rate_slider = QSlider(Qt.Orientation.Horizontal)
        self._rate_slider.setRange(-50, 50)
        self._rate_slider.setValue(0)
        self._rate_slider.valueChanged.connect(self._on_rate_change)
        rc_layout.addWidget(self._rate_slider)
        layout.addWidget(rate_card)

        out_card = QGroupBox("输出模式")
        oc_layout = QVBoxLayout(out_card)
        self._mode_group = QButtonGroup(self)
        for label, val in [("按章节拆分", "chapter"), ("按时间拆分", "time"), ("合并为一个文件", "single")]:
            rb = QRadioButton(label)
            self._mode_group.addButton(rb)
            oc_layout.addWidget(rb)
        self._mode_group.buttonClicked.connect(self._on_mode_change)
        self._mode_group.buttons()[0].setChecked(True)
        self._mode_var = "chapter"

        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("每段:"))
        self._time_spin = QSpinBox()
        self._time_spin.setRange(5, 180)
        self._time_spin.setValue(30)
        self._time_spin.setSingleStep(5)
        self._time_spin.setSuffix(" 分钟")
        self._time_spin.valueChanged.connect(lambda: self._update_split_estimate())
        self._time_frame = QWidget()
        self._time_frame.setLayout(time_row)
        self._time_frame.setVisible(False)
        oc_layout.addWidget(self._time_frame)
        time_row.addWidget(self._time_spin)

        self._split_estimate = QLabel("")
        self._split_estimate.setStyleSheet("color:gray;font-size:11px")
        oc_layout.addWidget(self._split_estimate)

        self._normalize_cb = QCheckBox("响度归一化（统一各文件音量，需 ffmpeg）")
        oc_layout.addWidget(self._normalize_cb)
        layout.addWidget(out_card)

        # 快捷操作（从操作面板移入）
        ops_card = QGroupBox("快捷操作")
        ops_layout = QVBoxLayout(ops_card)
        self._btn_preview_full = QPushButton("🔊 试听全文（可暂停）")
        self._btn_preview_full.clicked.connect(self._toggle_preview_full)
        ops_layout.addWidget(self._btn_preview_full)
        self._btn_convert = QPushButton("▶ 生成MP3")
        self._btn_convert.setObjectName("accentBtn")
        self._btn_convert.clicked.connect(self._start_convert)
        ops_layout.addWidget(self._btn_convert)
        self._btn_pause = QPushButton("⏹ 暂停")
        self._btn_pause.setObjectName("stopBtn")
        self._btn_pause.setEnabled(False)
        self._btn_pause.clicked.connect(self._pause_convert)
        ops_layout.addWidget(self._btn_pause)
        self._btn_resume = QPushButton("▶ 继续生成")
        self._btn_resume.clicked.connect(self._resume_convert)
        ops_layout.addWidget(self._btn_resume)
        tool_row = QHBoxLayout()
        tool_row.addWidget(QPushButton("🔀 合并MP3", clicked=self._merge_mp3))
        tool_row.addWidget(QPushButton("📋 日志", clicked=self._show_log))
        ops_layout.addLayout(tool_row)
        layout.addWidget(ops_card)

        layout.addStretch()
        return w

    def _build_panel_dialogue(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)
        card = QGroupBox("多人对话")
        cl = QVBoxLayout(card)
        self._dialogue_enabled_cb = QCheckBox("启用对话检测（为旁白/对话分配不同语音）")
        self._dialogue_enabled_cb.toggled.connect(self._on_dialogue_toggle)
        cl.addWidget(self._dialogue_enabled_cb)
        nr = QHBoxLayout()
        nr.addWidget(QLabel("旁白语音:"))
        self._dialogue_narration_combo = QComboBox()
        nr.addWidget(self._dialogue_narration_combo, 1)
        cl.addLayout(nr)
        dr = QHBoxLayout()
        dr.addWidget(QLabel("对话语音:"))
        self._dialogue_voice_combo = QComboBox()
        dr.addWidget(self._dialogue_voice_combo, 1)
        cl.addLayout(dr)
        layout.addWidget(card)
        layout.addStretch()
        self._on_dialogue_toggle(False)
        return w

    def _build_panel_storage(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        dir_card = QGroupBox("便携存储目录")
        dc = QVBoxLayout(dir_card)
        dir_row = QHBoxLayout()
        self._storage_label = QLabel(get_storage_dir())
        self._storage_label.setWordWrap(True)
        self._storage_label.setStyleSheet("padding:4px;border:1px solid #ddd;border-radius:4px")
        dir_row.addWidget(self._storage_label, 1)
        btn_choose = QPushButton("选择文件夹")
        btn_choose.clicked.connect(self._choose_storage)
        dir_row.addWidget(btn_choose)
        btn_open = QPushButton("打开")
        btn_open.clicked.connect(self._open_storage)
        dir_row.addWidget(btn_open)
        btn_reset = QPushButton("恢复默认")
        btn_reset.clicked.connect(self._reset_storage)
        dir_row.addWidget(btn_reset)
        dc.addLayout(dir_row)
        dc.addWidget(QLabel("bin/ 存放可执行文件；piper-models/ 存放语音包。程序自动在子目录搜索。"))
        layout.addWidget(dir_card)

        deps_card = QGroupBox("依赖检测")
        dps = QVBoxLayout(deps_card)
        self._deps_text = QPlainTextEdit()
        self._deps_text.setReadOnly(True)
        self._deps_text.setMaximumHeight(200)
        self._deps_text.setFont(QFont("Helvetica", 11))
        dps.addWidget(self._deps_text)
        br1 = QHBoxLayout()
        br1.addWidget(QPushButton("⚙ 重新扫描", clicked=self._refresh_deps))
        br1.addWidget(QPushButton("⬇ 下载 Piper 模型", clicked=self._download_piper_models))
        dps.addLayout(br1)
        dps.addWidget(QPushButton("⬇ 下载 CosyVoice 模型", clicked=self._download_cosyvoice_models))
        br3 = QHBoxLayout()
        br3.addWidget(QPushButton("📁 添加模型文件夹", clicked=self._add_model_folder))
        br3.addWidget(QPushButton("📁 添加程序文件夹", clicked=self._add_binary_folder))
        dps.addLayout(br3)
        layout.addWidget(deps_card)
        return w

    def _build_panel_files(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)
        card = QGroupBox("已加载文件")
        cl = QVBoxLayout(card)
        fr = QHBoxLayout()
        fr.addWidget(QPushButton("📂 添加文件（可多选）", clicked=self._add_files))
        fr.addWidget(QPushButton("🗑 移除选中", clicked=self._remove_selected_file))
        cl.addLayout(fr)
        self._file_tree = QTreeWidget()
        self._file_tree.setHeaderLabels(["文件名", "大小", "状态"])
        self._file_tree.setRootIsDecorated(False)
        self._file_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        cl.addWidget(self._file_tree)
        self._file_count_label = QLabel("未加载文件")
        self._file_count_label.setStyleSheet("color:gray")
        cl.addWidget(self._file_count_label)
        layout.addWidget(card)
        return w

    def _build_panel_appear(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)
        card = QGroupBox("主题")
        cl = QVBoxLayout(card)
        self._theme_btn = QPushButton("🔄 切换深色/浅色主题")
        self._theme_btn.clicked.connect(self._toggle_theme)
        cl.addWidget(self._theme_btn)
        self._theme_status = QLabel("")
        self._theme_status.setStyleSheet("color:gray")
        cl.addWidget(self._theme_status)
        layout.addWidget(card)
        layout.addStretch()
        return w

    def _build_quickbar(self):
        w = QFrame()
        w.setObjectName("quickBar")
        layout = QHBoxLayout(w)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(12)
        self._qb_engine = QLabel("● Edge")
        self._qb_engine.setFont(QFont("Helvetica", 11, QFont.Weight.Bold))
        layout.addWidget(self._qb_engine)
        layout.addWidget(QLabel("|"))
        self._qb_voice = QLabel("▸ 未选择语音")
        layout.addWidget(self._qb_voice)
        layout.addWidget(QLabel("|"))
        layout.addWidget(QLabel("语速:"))
        self._qb_rate = QLabel("正常")
        self._qb_rate.setStyleSheet("color:#2563EB;font-weight:bold")
        layout.addWidget(self._qb_rate)
        layout.addStretch()
        self._qb_convert = QPushButton("▶ 开始转换")
        self._qb_convert.setObjectName("accentBtn")
        self._qb_convert.clicked.connect(self._start_convert)
        layout.addWidget(self._qb_convert)
        self._qb_pause = QPushButton("⏹ 停止")
        self._qb_pause.setObjectName("stopBtn")
        self._qb_pause.setEnabled(False)
        self._qb_pause.clicked.connect(self._pause_convert)
        layout.addWidget(self._qb_pause)
        return w

    def _build_statusbar(self):
        w = QFrame()
        w.setObjectName("statusBar")
        layout = QHBoxLayout(w)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(8)
        self._sb_status = QLabel("● 就绪")
        layout.addWidget(self._sb_status)
        layout.addWidget(QLabel("|"))
        self._sb_engine = QLabel("引擎: Edge（联网）")
        layout.addWidget(self._sb_engine)
        layout.addWidget(QLabel("|"))
        self._sb_voices = QLabel("6 语音")
        layout.addWidget(self._sb_voices)
        layout.addStretch()
        layout.addWidget(QLabel(f"v{VERSION}"))
        return w

    # ================ ASR Tab ================

    def _build_asr_tab(self):
        layout = QVBoxLayout(self._asr_tab)
        layout.setSpacing(8)

        audio_row = QHBoxLayout()
        self._audio_file_label = QLabel("未选择文件")
        self._audio_file_label.setStyleSheet("color:gray")
        audio_row.addWidget(QLabel("音频文件:"))
        audio_row.addWidget(self._audio_file_label, 1)
        audio_row.addWidget(QPushButton("📂 选择音频文件", clicked=self._select_audio_file))
        layout.addLayout(audio_row)

        # ASR 引擎选择（内置 + 外挂）
        eng_row = QHBoxLayout()
        eng_row.addWidget(QLabel("ASR 引擎:"))
        self._asr_engine_combo = QComboBox()
        self._asr_engine_combo.addItem("faster-whisper（内置）", "builtin")
        eng_row.addWidget(self._asr_engine_combo, 1)
        self._asr_engine_combo.currentIndexChanged.connect(self._on_asr_engine_change)
        layout.addLayout(eng_row)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Whisper 模型:"))
        self._asr_model_combo = QComboBox()
        self._asr_model_combo.addItems(list(WHISPER_MODELS.keys()))
        self._asr_model_combo.setCurrentText("base")
        model_row.addWidget(self._asr_model_combo, 1)
        layout.addLayout(model_row)
        layout.addWidget(QLabel("tiny=最快 ~150MB | base=推荐 ~300MB | small ~1GB | medium ~3GB | large-v3=最准 ~6GB"))

        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("语言:"))
        self._asr_lang_combo = QComboBox()
        self._asr_lang_combo.addItems([
            "auto（自动检测）", "zh（中文）", "en（英文）", "ja（日文）",
            "ko（韩文）", "fr（法文）", "de（德文）", "es（西班牙文）", "ru（俄文）",
        ])
        lang_row.addWidget(self._asr_lang_combo, 1)
        layout.addLayout(lang_row)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("输出格式:"))
        self._asr_format_group = QButtonGroup(self)
        for label, val in [("纯文本 (txt)", "txt"), ("字幕 (srt)", "srt"), ("JSON", "json")]:
            rb = QRadioButton(label)
            self._asr_format_group.addButton(rb)
            fmt_row.addWidget(rb)
        self._asr_format_group.buttons()[0].setChecked(True)
        layout.addLayout(fmt_row)

        self._btn_asr_start = QPushButton("▶ 开始识别")
        self._btn_asr_start.setObjectName("accentBtn")
        self._btn_asr_start.clicked.connect(self._start_asr)
        layout.addWidget(self._btn_asr_start)

        self._asr_status = QLabel("")
        self._asr_status.setStyleSheet("color:gray")
        layout.addWidget(self._asr_status)

        self._asr_result = QPlainTextEdit()
        self._asr_result.setReadOnly(True)
        self._asr_result.setFont(QFont("Helvetica", 12))
        layout.addWidget(self._asr_result, 1)

        btn_row = QHBoxLayout()
        btn_row.addWidget(QPushButton("📋 复制结果", clicked=self._copy_asr_result))
        btn_row.addWidget(QPushButton("💾 保存到文件", clicked=self._save_asr_result))
        layout.addLayout(btn_row)

    # ================ Signal Wiring ================

    def _wire_signals(self):
        # Download listener (from tts_engine, may fire from any thread)
        pass  # handled via add_download_listener in __init__

    # ================ Engine Management ================

    def _rebuild_engine_buttons(self):
        """从注册表动态重建所有引擎按钮"""
        # 清除旧的
        for _bid, btn, _name in self._engine_btns:
            self._engine_group.removeButton(btn)
            self._engine_grid.removeWidget(btn)
            btn.deleteLater()
        self._engine_btns = []

        engines = get_registered_engines()
        BUILTIN = {
            "edge": ("Edge", "联网 · 快速"),
            "local": ("本地", "离线 · 系统内置"),
            "piper": ("Piper", "离线高质量"),
            "cosyvoice": ("CosyVoice", "离线神经网络"),
        }

        first_btn = None
        for eng_id, info in engines.items():
            if info["type"] == "builtin":
                name, sub = BUILTIN.get(eng_id, (info.get("name", eng_id), ""))
                ready, _ = check_engine_ready(eng_id)
                suffix = "\n（需安装）" if not ready else ""
                btn = QRadioButton(f"{name}{suffix}\n{sub}")
            else:
                name = info.get("name", eng_id)
                btn = QRadioButton(f"⚡ {name}\n外挂引擎")

            btn.setStyleSheet("QRadioButton{font-size:11px;padding:6px}")
            self._engine_grid.addWidget(btn)
            self._engine_group.addButton(btn)
            self._engine_btns.append((eng_id, btn, name))
            if first_btn is None:
                first_btn = btn

        if first_btn:
            first_btn.setChecked(True)
        self._on_engine_change()

    def _on_engine_change(self):
        engine = self._engine_group.checkedButton()
        if engine is None:
            return
        for eng_id, btn, _name in self._engine_btns:
            if btn is engine:
                engine = eng_id
                break
        else:
            engine = "edge"

        if self.is_converting:
            self._status_label.setText("正在生成中，引擎/语音切换将在下次生成时生效")
            return

        voices = get_voice_list(engine)
        self._voice_combo.blockSignals(True)
        self._voice_combo.clear()
        self._voice_combo.addItems(voices)
        self._voice_combo.blockSignals(False)

        ready, msg = check_engine_ready(engine)
        if hasattr(self, '_btn_convert'):
            self._btn_convert.setEnabled(ready)
        if hasattr(self, '_qb_convert'):
            self._qb_convert.setEnabled(ready)
        if hasattr(self, '_status_label'):
            self._status_label.setText("就绪")

        self._update_quickbar()
        self._update_statusbar()

    def _on_voice_change(self, _text):
        self._update_quickbar()
        self._update_statusbar()

    def _refresh_voices(self):
        engine = self._engine_group.checkedButton()
        if engine:
            for eng_id, btn, _name in self._engine_btns:
                if btn is engine:
                    if eng_id == "local":
                        refresh_local_voices()
                    break
        self._on_engine_change()

    def _on_rate_change(self, val):
        if val == 0: label = "正常"
        elif val > 0: label = f"快 +{val}%"
        else: label = f"慢 {val}%"
        self._rate_label.setText(label)
        self._qb_rate.setText(label)

    def _on_mode_change(self, btn=None):
        if btn:
            for label, val in [("按章节拆分","chapter"),("按时间拆分","time"),("合并为一个文件","single")]:
                if btn.text().startswith(label):
                    self._mode_var = val
                    break
        self._time_frame.setVisible(self._mode_var == "time")
        self._update_split_estimate()

    def _get_rate_string(self):
        return f"+{self._rate_slider.value()}%" if self._rate_slider.value() >= 0 else f"{self._rate_slider.value()}%"

    def _get_selected_indices(self):
        result = []
        for item in self._chapter_tree.selectedItems():
            idx = item.data(0, Qt.ItemDataRole.UserRole)
            if idx is not None:
                result.append(idx)
        return sorted(result)

    # ================ QuickBar / StatusBar Updates ================

    def _update_quickbar(self):
        if not hasattr(self, '_qb_engine'):
            return
        engine = "edge"
        btn = self._engine_group.checkedButton()
        if btn:
            for eng_id, b, _name in self._engine_btns:
                if b is btn:
                    engine = eng_id
                    break
        ready, _ = check_engine_ready(engine)
        dot = "●" if ready else "○"
        names = {"edge":"Edge","local":"本地","piper":"Piper","cosyvoice":"CosyVoice"}
        self._qb_engine.setText(f"{dot} {names.get(engine, engine)}")
        voice = self._voice_combo.currentText()
        self._qb_voice.setText(f"▸ {voice}" if voice else "▸ 未选择语音")

    def _update_statusbar(self):
        if not hasattr(self, '_sb_status'):
            return
        engine = "edge"
        btn = self._engine_group.checkedButton()
        if btn:
            for eng_id, b, _name in self._engine_btns:
                if b is btn:
                    engine = eng_id
                    break
        ready, msg = check_engine_ready(engine)
        self._sb_status.setText("● 就绪" if ready else "○ 引擎不可用")
        self._sb_engine.setText(f"引擎: {msg[:30]}")
        voices = get_voice_list(engine)
        self._sb_voices.setText(f"{len(voices)} 语音")

    # ================ Chapters ================

    def _refresh_chapters(self):
        text = self._text_area.toPlainText().strip()
        source_map = []
        offset = 0
        for fi in self._file_paths:
            source_map.append((offset, fi["name"]))
            offset += len(fi["content"]) + 2
        self.chapters = detect_chapters(text, source_map=source_map)
        self._refresh_chapters_list()

    def _refresh_chapters_list(self, filter_text=""):
        self._chapter_tree.clear()
        for idx, ch in enumerate(self.chapters):
            if filter_text and filter_text not in ch["title"].lower():
                continue
            title = ch["title"]
            source = ch.get("source", "")
            if source and len(self._file_paths) > 1:
                title = f"[{source}] {title}"
            item = QTreeWidgetItem([f"☐ {title}"])
            item.setData(0, Qt.ItemDataRole.UserRole, idx)
            item.setCheckState(0, Qt.CheckState.Checked)
            self._chapter_tree.addTopLevelItem(item)
        self._update_chapter_count()

    def _filter_chapters(self, text):
        self._refresh_chapters_list(text)

    def _select_all_chapters(self):
        for i in range(self._chapter_tree.topLevelItemCount()):
            self._chapter_tree.topLevelItem(i).setSelected(True)

    def _deselect_all_chapters(self):
        self._chapter_tree.clearSelection()

    def _on_chapter_select(self):
        self._update_chapter_count()
        self._update_split_estimate()

    def _update_chapter_count(self):
        total = self._chapter_tree.topLevelItemCount()
        sel = len(self._chapter_tree.selectedItems()) or total
        self._chapter_count.setText(f"已选 {sel}/{total} 章" if total else "未检测到章节")

    def _update_split_estimate(self):
        mode = self._mode_var
        sel = set(self._get_selected_indices()) if self._chapter_tree.topLevelItemCount() > 0 else set(range(len(self.chapters)))
        sel_chs = [c for i, c in enumerate(self.chapters) if i in sel]
        if mode == "single":
            self._split_estimate.setText("将生成 1 个合并文件")
        elif mode == "chapter":
            self._split_estimate.setText(f"将生成 {len(sel_chs)} 个文件（每章一个）")
        elif mode == "time":
            mins = self._time_spin.value()
            rate = self._get_rate_string()
            count = 0
            for ch in sel_chs:
                t = ch.get("text", "")
                if t:
                    try:
                        parts = split_by_duration(t, mins * 60, rate)
                        count += max(len(parts), 1)
                    except Exception:
                        count += 1
            self._split_estimate.setText(f"将生成约 {count} 个文件（每段 ≤ {mins} 分钟）")
        else:
            self._split_estimate.setText("")

    # ================ Files ================

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择一个或多个文本文件", "",
            "支持的文档 (*.txt *.md *.markdown *.docx *.epub *.html *.htm *.pdf);;所有文件 (*.*)")
        if not paths:
            return
        existing = {f["path"] for f in self._file_paths}
        for path in paths:
            if path not in existing:
                try:
                    info = load_file_content(path)
                    self._file_paths.append(info)
                except Exception as e:
                    QMessageBox.warning(self, "错误", f"读取文件失败: {e}")
        self._rebuild_file_tree()
        self._reconcile_text()

    def _remove_selected_file(self):
        selected = self._file_tree.selectedItems()
        if not selected:
            return
        for item in selected:
            path = item.data(0, Qt.ItemDataRole.UserRole)
            self._file_paths = [f for f in self._file_paths if f["path"] != path]
        self._rebuild_file_tree()
        self._reconcile_text()

    def _rebuild_file_tree(self):
        self._file_tree.clear()
        for fi in self._file_paths:
            item = QTreeWidgetItem([fi["name"], f"{len(fi['content'])} 字", "已加载"])
            item.setData(0, Qt.ItemDataRole.UserRole, fi["path"])
            self._file_tree.addTopLevelItem(item)
        n = len(self._file_paths)
        self._file_count_label.setText(f"已加载 {n} 个文件" if n else "未加载文件")

    def _reconcile_text(self):
        if not self._file_paths:
            self._text_area.clear()
            self.chapters = []
            self._refresh_chapters()
            return
        merged = "\n\n".join(f["content"] for f in self._file_paths)
        self._text_area.setPlainText(merged)
        total = sum(len(f["content"]) for f in self._file_paths)
        self._status_label.setText(f"已加载 {len(self._file_paths)} 个文件（{total}字）")
        self._refresh_chapters()

    # ================ Storage & Deps ================

    def _choose_storage(self):
        path = QFileDialog.getExistingDirectory(self, "选择便携存储文件夹")
        if not path:
            return
        try:
            set_storage_dir(path)
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))
            return
        self._storage_label.setText(get_storage_dir())
        self._refresh_deps()

    def _open_storage(self):
        path = get_storage_dir()
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", path])
            elif platform.system() == "Windows":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            QMessageBox.information(self, "目录", path)

    def _reset_storage(self):
        if QMessageBox.question(self, "确认", "恢复为默认存储目录（用户主目录）？") != QMessageBox.StandardButton.Yes:
            return
        set_storage_dir("")
        self._storage_label.setText(get_storage_dir())
        self._refresh_deps()

    def _refresh_deps(self):
        try:
            info = scan_storage_dependencies()
        except Exception as e:
            self._deps_text.setPlainText(f"扫描失败: {e}")
            return

        lines = []
        mark = lambda x: "✓" if x else "✗"
        lines.append(f"{mark(info['ffmpeg'])} ffmpeg: {info['ffmpeg'] or '未找到'}")
        lines.append(f"{mark(info['ffprobe'])} ffprobe: {info['ffprobe'] or '未找到'}")
        lines.append(f"{mark(info['piper_python'])} Piper Python 包: {'已安装' if info['piper_python'] else '未安装'}")
        if info["piper_cli"]:
            lines.append(f"✓ Piper CLI: {info['piper_cli']}")
        lines.append(f"Piper 语音包: {len(info['piper_models'])} 个")
        for m in info["piper_models"][:4]:
            lines.append(f"   · {os.path.basename(m)}")

        gpu = info.get("gpu_status", {})
        lines.append(f"{mark(gpu.get('cuda_available'))} CUDA")
        lines.append(f"{mark(gpu.get('onnxruntime_gpu'))} onnxruntime GPU")

        try:
            if COSYVOICE_PYTHON_AVAILABLE:
                lines.append("✓ CosyVoice Python 包: 已安装")
                md = get_cosyvoice_model_dir()
                if os.path.isdir(md):
                    cv_models = [d for d in os.listdir(md) if os.path.isdir(os.path.join(md, d))]
                    lines.append(f"CosyVoice 模型: {len(cv_models)} 个" if cv_models else "○ CosyVoice 模型: 未下载")
            else:
                lines.append("○ CosyVoice Python 包: 未安装")
        except Exception:
            lines.append("○ CosyVoice: 检测失败")

        if info["missing"]:
            lines.append("")
            lines.append("缺少:")
            for m in info["missing"]:
                lines.append(f"  - {m}")
            lines.append("")
            lines.append("下载地址:")
            if any("ffmpeg" in m for m in info["missing"]):
                lines.append("  ffmpeg: https://www.gyan.dev/ffmpeg/builds/")
            if any("Piper" in m for m in info["missing"]):
                lines.append("  Piper: https://github.com/rhasspy/piper/releases")

        # Current engine
        engine = "edge"
        btn = self._engine_group.checkedButton()
        if btn:
            for eng_id, b, _name in self._engine_btns:
                if b is btn:
                    engine = eng_id
                    break
        ready, msg = check_engine_ready(engine)
        lines.append(f"\n{'✓' if ready else '✗'} 当前引擎: {msg}")

        self._deps_text.setPlainText("\n".join(lines))
        self._rebuild_engine_buttons()

    def _download_piper_models(self):
        if QMessageBox.question(self, "下载 Piper 模型",
                                "将下载中文语音模型到便携存储目录。继续？") != QMessageBox.StandardButton.Yes:
            return
        self._status_label.setText("开始下载 Piper 模型...")
        self._download_worker = DownloadWorker()
        self._download_worker.setup("piper")
        self._download_worker.finished_ok.connect(lambda r: (
            self._status_label.setText("Piper 模型下载完成"), self._refresh_deps()))
        self._download_worker.error_occurred.connect(lambda e: QMessageBox.critical(self, "下载失败", e))
        self._download_worker.start()

    def _download_cosyvoice_models(self):
        # 模型下载无需 Python 包，只需网络连接。合成时才需要 cosyvoice 包。
        if QMessageBox.question(self, "下载 CosyVoice 模型",
                                "将下载 CosyVoice 模型到便携存储目录（≈600MB）。继续？") != QMessageBox.StandardButton.Yes:
            return
        self._status_label.setText("开始下载 CosyVoice 模型...")
        self._download_worker = DownloadWorker()
        self._download_worker.setup("cosyvoice")
        self._download_worker.finished_ok.connect(lambda r: (
            self._status_label.setText("CosyVoice 模型下载完成"), self._refresh_deps(), self._on_engine_change()))
        self._download_worker.error_occurred.connect(lambda e: QMessageBox.critical(self, "下载失败", e))
        self._download_worker.start()

    def _add_model_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择包含语音模型的文件夹")
        if not path:
            return
        try:
            added = add_model_search_path(path)
            self._refresh_deps()
            QMessageBox.information(self, "已添加", f"已{'添加' if added else '存在'}: {path}")
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    def _add_binary_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择包含可执行文件的文件夹")
        if not path:
            return
        try:
            added = add_model_search_path(path)
            _invalidate_scan_cache()
            self._refresh_deps()
            QMessageBox.information(self, "已添加", f"已{'添加' if added else '存在'}: {path}")
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    # ================ Download Progress ================

    def _on_download_progress(self, desc, current, total):
        self._dl_label.setVisible(True)
        self._dl_progress.setVisible(True)
        if total > 0:
            self._dl_progress.setMaximum(total)
            self._dl_progress.setValue(min(current, total))
            self._dl_label.setText(f"下载中 {desc}: {current/1024/1024:.1f}/{total/1024/1024:.1f} MB")
            if current >= total:
                QTimer.singleShot(1500, self._hide_dl_progress)
        else:
            self._dl_progress.setMaximum(0)
            self._dl_label.setText(f"下载中 {desc}: {current/1024/1024:.1f} MB")

    def _hide_dl_progress(self):
        self._dl_label.setVisible(False)
        self._dl_progress.setVisible(False)
        self._dl_progress.setMaximum(100)
        self._dl_progress.setValue(0)

    # ================ Preview & Playback ================

    SAMPLE_PREVIEW_TEXT = "你好，这是一段语音试听示例。春江潮水连海平，海上明月共潮生。"

    def _preview_voice_sample(self):
        engine = self._get_current_engine()
        ready, msg = check_engine_ready(engine)
        if not ready:
            QMessageBox.critical(self, "引擎不可用", msg)
            return
        voice = get_voice_id(self._voice_combo.currentText(), engine)
        self._status_label.setText("正在生成语音试听...")
        self._preview_worker = PreviewWorker()
        self._preview_worker.setup(self.SAMPLE_PREVIEW_TEXT, voice, self._get_rate_string(), engine)
        self._preview_worker.finished_ok.connect(self._on_preview_ready)
        self._preview_worker.error_occurred.connect(lambda e: QMessageBox.critical(self, "错误", e))
        self._preview_worker.start()

    def _on_preview_ready(self, path):
        self.player.play(path)
        self._status_label.setText("语音试听播放中...")

    def _toggle_preview_full(self):
        state = self._preview_state
        if state == "idle":
            self._start_preview_full()
        elif state == "generating":
            if self._stream_worker:
                self._stream_worker.request_stop()
            self.player.stop()
            self._preview_state = "idle"
            self._btn_preview_full.setText("🔊 试听全文（可暂停）")
        elif state == "playing":
            if self.player.pause():
                self._preview_state = "paused"
                self._btn_preview_full.setText("▶ 继续播放")
        elif state == "paused":
            if self.player.resume():
                self._preview_state = "playing"
                self._btn_preview_full.setText("⏸ 暂停播放")

    def _start_preview_full(self):
        text = self._text_area.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "提示", "请先输入或导入文字内容")
            return
        engine = self._get_current_engine()
        ready, msg = check_engine_ready(engine)
        if not ready:
            QMessageBox.critical(self, "引擎不可用", msg)
            return
        voice = get_voice_id(self._voice_combo.currentText(), engine)
        self._preview_state = "generating"
        self._btn_preview_full.setText("⏹ 中止生成")
        self._status_label.setText(f"正在生成首段试听...（{len(text)}字）")
        self._stream_worker = StreamPreviewWorker()
        self._stream_worker.setup(text, voice, self._get_rate_string(), engine)
        self._stream_worker.segment_ready.connect(self._on_stream_segment_ready)
        self._stream_worker.finished_ok.connect(self._on_stream_done)
        self._stream_worker.error_occurred.connect(lambda e: QMessageBox.critical(self, "错误", e))
        self._stream_worker.start()

    def _on_stream_segment_ready(self, path):
        self.player.enqueue(path)
        if self._preview_state == "generating":
            self._preview_state = "playing"
            self._btn_preview_full.setText("⏸ 暂停播放")

    def _on_stream_done(self, _):
        self._preview_state = "idle"
        self._btn_preview_full.setText("🔊 试听全文（可暂停）")
        self._status_label.setText("全部段已生成，等待播放完毕")

    def _on_player_state_changed(self, state):
        if state == "ended":
            self._preview_state = "idle"
            self._btn_preview_full.setText("🔊 试听全文（可暂停）")

    # ================ Conversion ================

    def _get_current_engine(self):
        btn = self._engine_group.checkedButton()
        if btn:
            for eng_id, b, _name in self._engine_btns:
                if b is btn:
                    return eng_id
        return "edge"

    def _start_convert(self):
        selected = self._get_selected_indices()
        if not selected:
            QMessageBox.warning(self, "提示", "请至少勾选一个章节")
            return
        text = self._text_area.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "提示", "请先输入或导入文字内容")
            return
        if self.is_converting:
            return

        engine = self._get_current_engine()
        ready, msg = check_engine_ready(engine)
        if not ready:
            QMessageBox.critical(self, "引擎不可用", msg)
            return

        output_dir = QFileDialog.getExistingDirectory(self, "选择保存目录")
        if not output_dir:
            return

        prefix = os.path.splitext(self._file_paths[0]["name"])[0] if len(self._file_paths) == 1 else "有声读物"
        self._run_convert(output_dir, prefix, selected, resume=False)

    def _resume_convert(self):
        output_dir = QFileDialog.getExistingDirectory(self, "选择之前保存的目录（包含进度文件）")
        if not output_dir:
            return
        items = load_progress(output_dir)
        if not items:
            QMessageBox.information(self, "提示", "该目录下没有找到进度文件")
            return
        self._run_convert(output_dir, "有声读物", None, resume=True)

    def _run_convert(self, output_dir, file_prefix, selected_indices, resume):
        self.is_converting = True
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._btn_convert.setEnabled(False)
        self._btn_pause.setEnabled(True)
        self._qb_convert.setEnabled(False)
        self._qb_pause.setEnabled(True)
        self._status_label.setText("准备转换..." if not resume else "准备继续转换...")
        self._set_controls_enabled(False)

        engine = self._get_current_engine()
        voice = get_voice_id(self._voice_combo.currentText(), engine)
        rate = self._get_rate_string()
        mode = self._mode_var
        time_min = self._time_spin.value()

        dia_enabled = self._dialogue_enabled_cb.isChecked()
        voice_map = None
        if dia_enabled:
            nv = get_voice_id(self._dialogue_narration_combo.currentText(), engine)
            dv = get_voice_id(self._dialogue_voice_combo.currentText(), engine)
            if nv and dv:
                voice_map = {"narration": nv, "dialogue": dv}

        self._convert_worker = ConvertWorker()
        self._convert_worker.setup(
            text=self._text_area.toPlainText().strip(), voice=voice, rate=rate,
            output_dir=output_dir, split_mode=mode, time_minutes=time_min,
            file_prefix=file_prefix, selected_indices=selected_indices,
            engine=engine, normalize_audio=self._normalize_cb.isChecked(),
            dialogue_detection=dia_enabled, voice_map=voice_map, resume=resume,
        )
        self._convert_worker.progress_update.connect(self._on_convert_progress)
        self._convert_worker.finished_ok.connect(lambda files: self._on_convert_done(output_dir, files))
        self._convert_worker.error_occurred.connect(self._on_convert_error)
        self._convert_worker.finished.connect(self._on_convert_finished)
        self._convert_worker.start()

    def _on_convert_progress(self, current, total):
        if total:
            self._progress_bar.setMaximum(total)
            self._progress_bar.setValue(current)
        self._status_label.setText(f"正在处理: {current}/{total}")

    def _on_convert_done(self, output_dir, files):
        self._progress_bar.setValue(self._progress_bar.maximum())
        self._status_label.setText(f"完成! 共生成 {len(files)} 个文件")
        names = "\n".join(os.path.basename(f) for f in files[:8])
        if QMessageBox.question(self, "完成",
                                f"已生成 {len(files)} 个MP3文件:\n{names}\n\n是否打开文件夹？") == QMessageBox.StandardButton.Yes:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", output_dir])
            elif platform.system() == "Windows":
                os.startfile(output_dir)
            else:
                subprocess.Popen(["xdg-open", output_dir])

    def _on_convert_error(self, msg):
        QMessageBox.critical(self, "生成失败", msg)

    def _on_convert_finished(self):
        self.is_converting = False
        self._progress_bar.setVisible(False)
        self._btn_convert.setEnabled(True)
        self._btn_pause.setEnabled(False)
        self._qb_convert.setEnabled(True)
        self._qb_pause.setEnabled(False)
        self._set_controls_enabled(True)

    def _pause_convert(self):
        if self._convert_worker:
            self._convert_worker.request_stop()
        self._btn_pause.setEnabled(False)
        self._qb_pause.setEnabled(False)
        self._status_label.setText("正在暂停...")

    def _set_controls_enabled(self, enabled):
        for btn in self._engine_group.buttons():
            btn.setEnabled(enabled)
        self._voice_combo.setEnabled(enabled)
        self._dialogue_narration_combo.setEnabled(enabled)
        self._dialogue_voice_combo.setEnabled(enabled)

    # ================ Theme ================

    def _load_theme(self):
        from tts_engine import _load_config
        cfg = _load_config()
        self._theme = cfg.get("theme", "light")
        self._apply_theme()

    def _apply_theme(self):
        qss = DARK_QSS if self._theme == "dark" else LIGHT_QSS
        self.setStyleSheet(qss)
        self._theme_status.setText(f"当前: {self._theme} 模式" if hasattr(self, '_theme_status') else "")

    def _toggle_theme(self):
        self._theme = "dark" if self._theme == "light" else "light"
        self._apply_theme()
        try:
            from tts_engine import _load_config, _save_config
            cfg = _load_config()
            cfg["theme"] = self._theme
            _save_config(cfg)
        except Exception:
            pass

    # ================ Dialogue ================

    def _on_dialogue_toggle(self, checked):
        enabled = checked if isinstance(checked, bool) else self._dialogue_enabled_cb.isChecked()
        self._dialogue_narration_combo.setEnabled(enabled)
        self._dialogue_voice_combo.setEnabled(enabled)
        if enabled:
            engine = self._get_current_engine()
            voices = get_voice_list(engine)
            self._dialogue_narration_combo.clear()
            self._dialogue_narration_combo.addItems(voices)
            self._dialogue_voice_combo.clear()
            self._dialogue_voice_combo.addItems(voices)

    # ================ ASR ================

    def _on_asr_engine_change(self, _idx):
        """ASR 引擎切换：外挂引擎时隐藏模型/语言选择"""
        is_builtin = self._asr_engine_combo.currentData() == "builtin"
        self._asr_model_combo.setVisible(is_builtin)
        # 找到模型标签行并隐藏（简化：直接用 findChildren）

    def _refresh_asr_engines(self):
        """扫描外挂 ASR 引擎"""
        self._asr_engine_combo.blockSignals(True)
        self._asr_engine_combo.clear()
        self._asr_engine_combo.addItem("faster-whisper（内置）", "builtin")
        try:
            ext = scan_external_asr_engines(get_storage_dir())
            for eid, info in ext.items():
                self._asr_engine_combo.addItem(f"⚡ {info['name']}（外挂）", eid)
        except Exception:
            pass
        self._asr_engine_combo.blockSignals(False)

    def _select_audio_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择音频文件", "",
                                               "音频文件 (*.mp3 *.wav *.m4a *.flac *.ogg *.aac *.wma);;所有文件 (*.*)")
        if not path:
            return
        self._audio_file_path = path
        self._audio_file_label.setText(f"{os.path.basename(path)} ({os.path.getsize(path)/1024:.0f} KB)")

    def _start_asr(self):
        if not hasattr(self, '_audio_file_path') or not self._audio_file_path:
            QMessageBox.warning(self, "提示", "请先选择音频文件")
            return
        if self.is_converting:
            return
        self._btn_asr_start.setEnabled(False)
        self._btn_asr_start.setText("识别中...")
        self._asr_status.setText("正在准备...")
        self._asr_result.clear()

        model_size = self._asr_model_combo.currentText()
        lang_raw = self._asr_lang_combo.currentText()
        language = lang_raw.split("（")[0] if "（" in lang_raw else lang_raw
        if language == "auto":
            language = "auto"

        fmt_btn = self._asr_format_group.checkedButton()
        output_format = "txt"
        if fmt_btn:
            for label, val in [("纯文本 (txt)","txt"),("字幕 (srt)","srt"),("JSON","json")]:
                if fmt_btn.text() == label:
                    output_format = val
                    break

        # 检查是否选择了外挂引擎
        engine_id = self._asr_engine_combo.currentData()
        if engine_id and engine_id != "builtin":
            # 外挂引擎：直接用线程调用
            self._status_label.setText("外挂 ASR 引擎识别中...")
            def _ext_run():
                try:
                    result = external_asr_transcribe(
                        engine_id, self._audio_file_path, output_format,
                        model="", language=language)
                    self._asr_result.setPlainText(result)
                    self._asr_status.setText("识别完成")
                    self._asr_last_result = result
                except Exception as e:
                    QMessageBox.critical(self, "错误", f"外挂 ASR 识别失败: {e}")
                finally:
                    self._btn_asr_start.setEnabled(True)
                    self._btn_asr_start.setText("▶ 开始识别")
            threading.Thread(target=_ext_run, daemon=True).start()
            return

        ready, msg = check_asr_ready(get_storage_dir())
        if not ready:
            QMessageBox.critical(self, "ASR 不可用", msg)
            self._btn_asr_start.setEnabled(True)
            self._btn_asr_start.setText("▶ 开始识别")
            return

        self._asr_worker = AsrWorker()
        self._asr_worker.setup(self._audio_file_path, get_storage_dir(), model_size, language, output_format)
        self._asr_worker.progress_update.connect(lambda c, t: self._asr_status.setText(f"识别进度: {c}/{t}"))
        self._asr_worker.finished_ok.connect(self._on_asr_done)
        self._asr_worker.error_occurred.connect(lambda e: (
            QMessageBox.critical(self, "错误", f"ASR 识别失败: {e}"),
            self._on_asr_finished()
        ))
        self._asr_worker.finished.connect(self._on_asr_finished)
        self._asr_worker.start()

    def _on_asr_done(self, result):
        self._asr_result.setPlainText(result)
        self._asr_status.setText("识别完成")
        self._asr_last_result = result

    def _on_asr_finished(self):
        self._btn_asr_start.setEnabled(True)
        self._btn_asr_start.setText("▶ 开始识别")

    def _copy_asr_result(self):
        text = self._asr_result.toPlainText().strip()
        if text:
            QApplication.clipboard().setText(text)

    def _save_asr_result(self):
        text = self._asr_result.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "提示", "没有可保存的内容")
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存识别结果", "transcript.txt",
                                               "文本文件 (*.txt);;所有文件 (*.*)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)

    # ================ Merge MP3 ================

    def _merge_mp3(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择要合并的MP3文件", "",
                                                 "MP3文件 (*.mp3);;所有文件 (*.*)")
        if len(files) < 2:
            QMessageBox.information(self, "提示", "请选择至少2个文件")
            return
        out, _ = QFileDialog.getSaveFileName(self, "保存合并后的MP3", "合并_有声读物.mp3",
                                              "MP3文件 (*.mp3)")
        if not out:
            return
        try:
            merge_mp3_files(sorted(list(files)), out)
            size_mb = os.path.getsize(out) / (1024 * 1024)
            QMessageBox.information(self, "完成", f"已合并 {len(files)} 个文件:\n{os.path.basename(out)}\n大小: {size_mb:.1f}MB")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"合并失败: {e}")

    # ================ Log ================

    def _show_log(self):
        if os.path.exists(LOG_PATH):
            if platform.system() == "Darwin":
                subprocess.Popen(["open", LOG_PATH])
            elif platform.system() == "Windows":
                os.startfile(LOG_PATH)
            else:
                subprocess.Popen(["xdg-open", LOG_PATH])
        else:
            QMessageBox.information(self, "提示", "日志文件不存在")

    # ================ External Engine Dialog ================

    def _open_external_dialog(self):
        QMessageBox.information(self, "外挂引擎",
                                "请在便携存储目录的 engines/ 子目录中放置外挂引擎。\n"
                                "详见 README.md 中的「外挂引擎插件」章节。")

    # ================ Cleanup ================

    def closeEvent(self, event):
        try:
            remove_download_listener(self._on_download_progress)
        except Exception:
            pass
        try:
            self.player.stop()
        except Exception:
            pass
        try:
            unload_whisper_model()
        except Exception:
            pass
        try:
            from tts_engine import _load_config, _save_config
            cfg = _load_config()
            cfg["window_geometry"] = f"{self.width()}x{self.height()}+{self.x()}+{self.y()}"
            _save_config(cfg)
        except Exception:
            pass
        super().closeEvent(event)
