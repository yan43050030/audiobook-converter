"""Qt6 GUI — 文字转有声读物 v6.0.0（兼容 PySide6 / PyQt6）"""

import os, sys, threading, subprocess, platform, logging, json, tempfile, shutil, time
from typing import Optional

# 自动适配 PySide6 或 PyQt6
try:
    from PySide6.QtWidgets import (
        QMainWindow, QWidget, QSplitter, QTabWidget, QStackedWidget,
        QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QPushButton, QLabel,
        QComboBox, QRadioButton, QButtonGroup, QCheckBox, QSlider,
        QSpinBox, QProgressBar, QPlainTextEdit, QTreeWidget, QTreeWidgetItem,
        QLineEdit, QFileDialog, QMessageBox, QFrame, QSizePolicy,
        QHeaderView, QAbstractItemView, QApplication, QScrollArea, QLayout,
        QTableWidget, QTableWidgetItem, QDialog, QListWidget, QListWidgetItem,
        QInputDialog,
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
        QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QPushButton, QLabel,
        QComboBox, QRadioButton, QButtonGroup, QCheckBox, QSlider,
        QSpinBox, QProgressBar, QPlainTextEdit, QTreeWidget, QTreeWidgetItem,
        QLineEdit, QFileDialog, QMessageBox, QFrame, QSizePolicy,
        QHeaderView, QAbstractItemView, QApplication, QScrollArea, QLayout,
        QTableWidget, QTableWidgetItem, QDialog, QListWidget, QListWidgetItem,
        QInputDialog,
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
    split_text, _generate_one_safe, split_by_duration,
    extract_speakers, export_m4b, refresh_piper_voices,
    fetch_piper_voice_catalog, list_piper_catalog_voices,
    download_piper_voice_from_catalog,
)
from asr_engine import (transcribe, check_asr_ready, WHISPER_MODELS, WHISPER_COMPUTE_TYPES,
                        unload_whisper_model,
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
    item_event = Signal(str, object)    # 生成队列逐章事件 (event, data)，见 tts_engine._emit_item

    def setup(self, text, voice, rate, output_dir, split_mode, time_minutes,
              file_prefix, selected_indices, engine, normalize_audio,
              dialogue_detection, voice_map, resume,
              generate_subtitles=False, write_metadata=False, only_indices=None):
        self._params = (text, voice, rate, output_dir, split_mode, time_minutes,
                        file_prefix, selected_indices, engine, normalize_audio,
                        dialogue_detection, voice_map, resume,
                        generate_subtitles, write_metadata, only_indices)

    def run(self):
        try:
            (text, voice, rate, output_dir, split_mode, time_minutes,
             file_prefix, selected_indices, engine, normalize_audio,
             dialogue_detection, voice_map, resume,
             generate_subtitles, write_metadata, only_indices) = self._params

            def progress_cb(current, total, **kw):
                self.progress_update.emit(current, total)

            def stop_cb():
                return self._should_stop

            def item_cb(event, data):
                # 可能来自 Piper 线程池 / asyncio：经 Qt 信号跨线程投递到界面线程
                self.item_event.emit(event, data)

            files = convert_batch(
                text=text, voice=voice, rate=rate, output_dir=output_dir,
                split_mode=split_mode, time_minutes=time_minutes,
                file_prefix=file_prefix, selected_indices=selected_indices,
                engine=engine, progress_callback=progress_cb,
                should_stop=stop_cb, resume=resume,
                normalize_audio=normalize_audio,
                dialogue_detection=dialogue_detection, voice_map=voice_map,
                generate_subtitles=generate_subtitles, write_metadata=write_metadata,
                item_callback=item_cb, only_indices=only_indices,
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
    """ASR 语音识别工作线程（支持多文件队列）"""
    progress_update = Signal(int, int)
    file_progress = Signal(int, int, str)  # file_idx, total_files, filename

    _FORMAT_EXT = {"txt": ".txt", "srt": ".srt", "json": ".json"}

    def setup(self, input_paths, storage_dir, model_size, language, output_format,
              compute_type="default"):
        if isinstance(input_paths, str):
            input_paths = [input_paths]
        self._params = (list(input_paths), storage_dir, model_size, language,
                        output_format, compute_type)

    def run(self):
        try:
            (input_paths, storage_dir, model_size, language, output_format,
             compute_type) = self._params
            def progress_cb(cur, tot):
                self.progress_update.emit(cur, tot)
            def stop_cb():
                return self._should_stop

            batch = len(input_paths) > 1
            ext = self._FORMAT_EXT.get(output_format, ".txt")
            done = []
            for idx, input_path in enumerate(input_paths, 1):
                if self._should_stop:
                    break
                self.file_progress.emit(idx, len(input_paths), os.path.basename(input_path))
                # 批量模式：结果自动存到源文件旁
                out_path = os.path.splitext(input_path)[0] + ext if batch else None
                result = transcribe(
                    input_path=input_path, storage_dir=storage_dir,
                    model_size=model_size, language=language,
                    output_format=output_format, output_path=out_path,
                    progress_callback=progress_cb, should_stop=stop_cb,
                    compute_type=compute_type,
                )
                done.append((input_path, out_path, result))

            if batch:
                lines = [f"✅ {os.path.basename(p)} → {os.path.basename(o)}"
                         for p, o, _r in done]
                if len(done) < len(input_paths):
                    lines.append(f"（已停止，完成 {len(done)}/{len(input_paths)}）")
                self.finished_ok.emit("批量转录完成:\n" + "\n".join(lines))
            else:
                self.finished_ok.emit(done[0][2] if done else "")
        except Exception as e:
            logger.error(f"ASR 失败: {e}", exc_info=True)
            self.error_occurred.emit(str(e))


class _ModelDownloadWorker(_BaseWorker):
    """模型下载线程（Piper / CosyVoice / Whisper 统一经 audiobook.models.download；
    字节进度经全局下载监听器推送到主窗口底部进度条）"""

    def setup(self, kind, model_id):
        self._params = (kind, model_id)

    def run(self):
        try:
            from audiobook.models import download
            kind, model_id = self._params
            download(kind, model_id, should_stop=self._check_stop)
            self.finished_ok.emit(self._params)
        except Exception as e:
            logger.error(f"模型下载失败: {e}")
            self.error_occurred.emit(str(e))


class _WizardDownloadWorker(_BaseWorker):
    """首次运行向导：依次下载多个模型并逐项汇报。

    全局下载监听器在下载线程里被调用，这里转成 Qt 信号再回到界面线程更新进度条。
    finished_ok 发出失败个数。
    """
    item_started = Signal(int)
    item_done = Signal(int)
    item_failed = Signal(int, str)
    bytes_progress = Signal(str, int, int)

    def setup(self, items):
        self._items = list(items)  # [(kind, model_id), ...]

    def run(self):
        from audiobook.models import download

        def _listener(desc, cur, total):
            self.bytes_progress.emit(desc, int(cur), int(total))

        add_download_listener(_listener)
        failed = 0
        try:
            for i, (kind, model_id) in enumerate(self._items):
                if self._should_stop:
                    break
                self.item_started.emit(i)
                try:
                    download(kind, model_id, should_stop=self._check_stop)
                    self.item_done.emit(i)
                except Exception as e:
                    failed += 1
                    logger.error(f"向导下载失败 {kind}/{model_id}: {e}")
                    self.item_failed.emit(i, str(e))
        finally:
            remove_download_listener(_listener)
        self.finished_ok.emit(failed)


class _CloneCheckWorker(_BaseWorker):
    """声音克隆：检测 CosyVoice 环境（可选实际加载模型）"""

    def setup(self, load=True):
        self._load = load

    def run(self):
        try:
            from audiobook.cloning import client as C
            self.finished_ok.emit(C.check(load=self._load))
        except Exception as e:
            self.error_occurred.emit(str(e))


class _ClonePreviewWorker(_BaseWorker):
    """声音克隆：用某个音色合成一句试听"""

    def setup(self, voice_id, text):
        self._voice, self._text = voice_id, text

    def run(self):
        try:
            from audiobook.engines.base import get_engine
            out = os.path.join(tempfile.mkdtemp(prefix="clone_preview_"), "preview.mp3")
            get_engine("clone").synthesize_segment(self._text, self._voice, "+0%", out,
                                                   should_stop=self._check_stop)
            self.finished_ok.emit(out)
        except Exception as e:
            self.error_occurred.emit(str(e))


class _CloneTranscribeWorker(_BaseWorker):
    """声音克隆：用 Whisper 识别参考音频的文字稿"""

    def setup(self, wav_path):
        self._wav = wav_path

    def run(self):
        try:
            text = transcribe(self._wav, get_storage_dir(), model_size="base", language="auto",
                              output_format="txt", should_stop=self._check_stop)
            self.finished_ok.emit((text or "").strip())
        except Exception as e:
            self.error_occurred.emit(str(e))


class _CatalogWorker(_BaseWorker):
    """Piper 语音目录获取线程"""

    def setup(self, force=False):
        self._force = force

    def run(self):
        try:
            catalog = fetch_piper_voice_catalog(force_refresh=self._force,
                                                should_stop=self._check_stop)
            self.finished_ok.emit(catalog)
        except Exception as e:
            logger.error(f"语音目录获取失败: {e}")
            self.error_occurred.emit(str(e))


class _VoiceDownloadWorker(_BaseWorker):
    """Piper 语音包下载线程（进度经全局下载监听器推送）"""

    def setup(self, voice_key):
        self._voice_key = voice_key

    def run(self):
        try:
            path = download_piper_voice_from_catalog(self._voice_key,
                                                     should_stop=self._check_stop)
            self.finished_ok.emit(path)
        except Exception as e:
            logger.error(f"语音包下载失败: {e}")
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
QTreeWidget::indicator { width: 18px; height: 18px; border: 1.5px solid #888; border-radius: 3px; background: white; margin-right: 6px; }
QTreeWidget::indicator:checked { background: #2563EB; border-color: #2563EB; }
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
/* 分别指定横/竖分隔条厚度：竖向分隔条（如章节/文本）的把手厚度由 height 决定，
   只设 width 会让它塌成不可拖动的细条。 */
QSplitter::handle { background: #D5D0C8; }
QSplitter::handle:horizontal { width: 6px; }
QSplitter::handle:vertical { height: 6px; }

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
QTreeWidget::indicator { width: 18px; height: 18px; border: 1.5px solid #666; border-radius: 3px; background: #1A1A1A; margin-right: 6px; }
QTreeWidget::indicator:checked { background: #3B82F6; border-color: #3B82F6; }
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
/* 分别指定横/竖分隔条厚度（竖向把手厚度看 height，只设 width 会不可拖动）。 */
QSplitter::handle { background: #444; }
QSplitter::handle:horizontal { width: 6px; }
QSplitter::handle:vertical { height: 6px; }

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

    def __init__(self):
        super().__init__()
        self._file_paths: list = []
        self._single_file_path = None
        self.is_converting = False
        self._last_output_dir = None
        self._last_prefix = None          # 上次生成的文件名前缀（续传 / 重试沿用，保持专辑名一致）
        self._queue_items: list = []      # 生成队列快照（与 convert_batch 的条目下标一致）
        self._convert_start_time = None
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
        self._model_dl_worker: Optional[_ModelDownloadWorker] = None

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
        # macOS 下布局默认的 SetDefaultConstraint 会把子控件（如 setMaximumHeight）
        # 推导出有限的 contentMaxSize 并传播到 NSWindow，导致窗口无法自由调整大小。
        root.setSizeConstraint(QLayout.SetNoConstraint)

        # Tabs
        self._tab_widget = QTabWidget()
        self._tts_tab = QWidget()
        self._queue_tab = QWidget()
        self._asr_tab = QWidget()
        self._tab_widget.addTab(self._tts_tab, "文字转语音")
        self._tab_widget.addTab(self._queue_tab, "生成队列")
        self._tab_widget.addTab(self._asr_tab, "语音转文字")
        root.addWidget(self._tab_widget, 1)

        # === TTS Tab ===
        tts_layout = QHBoxLayout(self._tts_tab)
        tts_layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        tts_layout.addWidget(splitter)

        # --- Left: 文件 + 章节 + 文本（自上而下的工作流层级）---
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 0, 4, 0)

        # 文件管理移到左栏顶部
        left_layout.addWidget(self._build_panel_files())

        # 章节列表与文本内容改用竖向分隔条，用户可自由分配二者高度
        left_splitter = QSplitter(Qt.Orientation.Vertical)

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
        self._chapter_tree.itemChanged.connect(self._on_chapter_check_changed)
        # 兜底可见高度：即便分隔条被拖到最上，章节列表也至少显示 ~7 行，
        # 避免被压成只看得到一两条
        self._chapter_tree.setMinimumHeight(160)
        ch_layout.addWidget(self._chapter_tree)
        left_splitter.addWidget(ch_group)

        text_group = QGroupBox("文本内容")
        text_gl = QVBoxLayout(text_group)
        self._text_area = QPlainTextEdit()
        # 使用系统默认字体（对中文回退更可靠），仅指定字号
        _text_font = QFont()
        _text_font.setPointSize(12)
        self._text_area.setFont(_text_font)
        text_gl.addWidget(self._text_area)
        left_splitter.addWidget(text_group)

        # 把手加厚且不可塌陷，保证两侧都能用鼠标拖动调整高度
        left_splitter.setHandleWidth(6)
        left_splitter.setChildrenCollapsible(False)
        # 初始高度分配：章节列表与文本内容都参与伸缩，章节列表拿到更充裕的初始高度
        left_splitter.setStretchFactor(0, 1)
        left_splitter.setStretchFactor(1, 1)
        left_splitter.setSizes([360, 340])
        left_layout.addWidget(left_splitter, 1)
        # 窗口显示后再套用一次初始比例：构造期分隔条尚无实际尺寸，此时 setSizes
        # 常被忽略，延后到事件循环空闲时再设，初始分配才真正生效
        self._left_splitter = left_splitter
        QTimer.singleShot(0, lambda: left_splitter.setSizes([360, 340]))

        splitter.addWidget(left)

        # --- Right: 单列可滚动设置面板（按工作流顺序堆叠，不再用标签导航） ---
        self._panel_scroll = QScrollArea()
        self._panel_scroll.setWidgetResizable(True)
        self._panel_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._panel_scroll.setMinimumWidth(320)
        # 横向按需滚动：极窄窗口下（引擎卡片行的最小宽度可能超过面板宽度）出现
        # 滚动条而非静默裁切，保证所有设置始终可达。
        self._panel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        panel_container = QWidget()
        panel_v = QVBoxLayout(panel_container)
        panel_v.setSizeConstraint(QLayout.SetNoConstraint)
        panel_v.setContentsMargins(4, 4, 4, 4)
        panel_v.setSpacing(8)
        # 引擎语音 → 语速输出 → 对话识别 → 存储依赖 → 外观
        for pid in ("engine", "speed", "dialogue", "storage", "appear"):
            pw = getattr(self, f"_build_panel_{pid}")()
            pw.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            panel_v.addWidget(pw)
        panel_v.addStretch()
        self._panel_scroll.setWidget(panel_container)
        splitter.addWidget(self._panel_scroll)

        # 右侧设置面板内容（长路径/长复选框文案）实际需要约 540px 才能完整显示；
        # 默认给足宽度以免出现横向滚动，窗口放大时多出的空间分配给左侧编辑区。
        splitter.setSizes([640, 560])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

        # === ASR Tab ===
        self._build_queue_tab()

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

        # 单一操作条（引擎/语音/语速摘要 + 生成/停止/继续）
        self._quickbar_widget = self._build_quickbar()
        bottom_layout.addWidget(self._quickbar_widget)

        root.addWidget(bottom)

        # 单一状态行（合并原状态栏 + 状态标签）
        self._status_label = QLabel("● 就绪")
        self._status_label.setObjectName("statusLine")
        self._status_label.setStyleSheet("color:#777;font-size:12px;padding:3px 6px")
        bottom_layout.addWidget(self._status_label)

        # 底部操作条/状态行仅对「文字转语音」有意义；切到 ASR 页时隐藏，避免
        # 与页内「开始识别」按钮重复、误导用户。
        self._tab_widget.currentChanged.connect(self._on_main_tab_changed)
        self._on_main_tab_changed(self._tab_widget.currentIndex())


    def _on_main_tab_changed(self, index):
        """主标签切换：ASR 页隐藏 TTS 专用的底部操作条与状态行（生成队列页保留，便于暂停/继续）。"""
        is_tts = (self._tab_widget.widget(index) is not self._asr_tab)
        if hasattr(self, "_quickbar_widget"):
            self._quickbar_widget.setVisible(is_tts)
        if hasattr(self, "_status_label"):
            self._status_label.setVisible(is_tts)


    # ================ Panel Builders ================

    # 右侧面板已改为单列滚动，无需标签切换回调

    def _build_panel_engine(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        engine_card = QGroupBox("选择引擎")
        self._engine_card_layout = QVBoxLayout(engine_card)
        self._engine_group = QButtonGroup(self)
        self._engine_group.setExclusive(True)
        self._engine_btns = []
        # 引擎按钮用 2 列网格排布（而非单行），避免引擎数量增多时行宽超出面板被裁切
        self._engine_grid = QGridLayout()
        self._engine_grid.setSpacing(6)
        self._engine_card_layout.addLayout(self._engine_grid)
        self._engine_group.buttonClicked.connect(self._on_engine_change)
        self._engine_group.buttonClicked.connect(self._remember_engine)
        layout.addWidget(engine_card)

        voice_card = QGroupBox("语音参数")
        vc_layout = QVBoxLayout(voice_card)
        # 第一行：语音标签 + 下拉（下拉可随面板收窄而压缩，避免溢出裁切）
        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel("语音:"))
        self._voice_combo = QComboBox()
        self._voice_combo.setMinimumWidth(120)
        self._voice_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._voice_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._voice_combo.currentTextChanged.connect(self._on_voice_change)
        voice_row.addWidget(self._voice_combo, 1)
        vc_layout.addLayout(voice_row)
        # 第二行：试听 / 刷新按钮（独立成行，窄面板下不再被挤出可视区）
        btn_row = QHBoxLayout()
        btn_preview = QPushButton("试听")
        btn_preview.clicked.connect(self._preview_voice_sample)
        btn_row.addWidget(btn_preview)
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self._refresh_voices)
        btn_row.addWidget(btn_refresh)
        btn_clone = QPushButton("🎙 克隆音色…")
        btn_clone.setToolTip("用一段 5–15 秒的人声复刻音色（基于 CosyVoice）")
        btn_clone.clicked.connect(self._open_clone_dialog)
        btn_row.addWidget(btn_clone)
        vc_layout.addLayout(btn_row)
        layout.addWidget(voice_card)

        btn_ext = QPushButton("+ 添加 / 配置外挂引擎")
        btn_ext.clicked.connect(self._open_external_dialog)
        layout.addWidget(btn_ext)

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

        self._time_frame = QWidget()
        tf_layout = QVBoxLayout(self._time_frame)
        tf_layout.setContentsMargins(0, 0, 0, 0)
        self._time_label = QLabel("30 分钟")
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tf_layout.addWidget(self._time_label)
        self._time_slider = QSlider(Qt.Orientation.Horizontal)
        self._time_slider.setRange(5, 180)
        self._time_slider.setValue(30)
        self._time_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._time_slider.setTickInterval(15)
        self._time_slider.valueChanged.connect(self._on_time_slider_change)
        tf_layout.addWidget(self._time_slider)
        self._time_frame.setVisible(False)
        oc_layout.addWidget(self._time_frame)

        self._split_estimate = QLabel("")
        self._split_estimate.setStyleSheet("color:gray;font-size:11px")
        oc_layout.addWidget(self._split_estimate)

        self._normalize_cb = QCheckBox("响度归一化（统一各文件音量，需 ffmpeg）")
        oc_layout.addWidget(self._normalize_cb)
        self._subtitle_cb = QCheckBox("同时生成 srt 字幕（按句估算时间轴）")
        oc_layout.addWidget(self._subtitle_cb)
        self._metadata_cb = QCheckBox("写入 ID3 元数据（专辑/章节标题/音轨号，需 ffmpeg）")
        oc_layout.addWidget(self._metadata_cb)
        layout.addWidget(out_card)

        # 快捷操作（仅保留试听/合并/日志）
        ops_card = QGroupBox("快捷操作")
        ops_layout = QVBoxLayout(ops_card)
        self._btn_preview_full = QPushButton("🔊 试听全文（可暂停）")
        self._btn_preview_full.clicked.connect(self._toggle_preview_full)
        ops_layout.addWidget(self._btn_preview_full)
        tool_row = QHBoxLayout()
        tool_row.addWidget(QPushButton("🔀 合并MP3", clicked=self._merge_mp3))
        tool_row.addWidget(QPushButton("📚 导出 m4b", clicked=self._export_m4b))
        tool_row.addWidget(QPushButton("📋 日志", clicked=self._show_log))
        ops_layout.addLayout(tool_row)
        layout.addWidget(ops_card)
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

        # 角色→音色映射（可选，覆盖统一的"对话语音"）
        spk_card = QGroupBox("角色音色映射（可选）")
        sl = QVBoxLayout(spk_card)
        self._btn_detect_speakers = QPushButton("🔍 从文本检测角色")
        self._btn_detect_speakers.clicked.connect(self._detect_speakers)
        sl.addWidget(self._btn_detect_speakers)
        self._speaker_table = QTableWidget(0, 2)
        self._speaker_table.setHorizontalHeaderLabels(["角色", "语音"])
        self._speaker_table.horizontalHeader().setStretchLastSection(True)
        self._speaker_table.verticalHeader().setVisible(False)
        self._speaker_table.setMaximumHeight(180)
        sl.addWidget(self._speaker_table)
        self._speaker_hint = QLabel("未映射的角色使用统一的对话语音")
        self._speaker_hint.setStyleSheet("color:gray;font-size:11px")
        sl.addWidget(self._speaker_hint)
        layout.addWidget(spk_card)
        self._speaker_panel = spk_card

        self._on_dialogue_toggle(False)
        return w

    def _detect_speakers(self):
        text = self._text_area.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "提示", "请先导入或输入文本")
            return
        speakers = extract_speakers(text)
        self._speaker_table.setRowCount(0)
        if not speakers:
            self._speaker_hint.setText("未检测到带名字的对话（形如「XX说：“…”」）")
            return
        engine = self._get_current_engine()
        voices = get_voice_list(engine)
        for name in speakers:
            row = self._speaker_table.rowCount()
            self._speaker_table.insertRow(row)
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._speaker_table.setItem(row, 0, item)
            combo = QComboBox()
            combo.addItem("（使用对话语音）", "")
            for v in voices:
                combo.addItem(v, v)
            self._speaker_table.setCellWidget(row, 1, combo)
        self._speaker_hint.setText(f"检测到 {len(speakers)} 个角色；未映射的角色使用统一的对话语音")

    def _collect_speaker_map(self, engine) -> dict:
        """从角色映射表收集 {角色名: voice_id}，未选择的跳过"""
        result = {}
        for row in range(self._speaker_table.rowCount()):
            name_item = self._speaker_table.item(row, 0)
            combo = self._speaker_table.cellWidget(row, 1)
            if not name_item or combo is None:
                continue
            display = combo.currentData()
            if display:
                result[name_item.text()] = get_voice_id(display, engine)
        return result

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
        # 模型的下载 / 删除 / 占用统一收进「模型管理器」，取代原先分散的下载按钮
        btn_models = QPushButton("🗂 模型管理器（下载 / 删除 / 占用）")
        btn_models.setMinimumHeight(34)
        btn_models.clicked.connect(self._open_model_manager)
        dps.addWidget(btn_models)
        btn_wizard = QPushButton("🧭 首次运行向导（选场景 / 备模型 / 试听）")
        btn_wizard.clicked.connect(self._open_first_run_wizard)
        dps.addWidget(btn_wizard)
        br1 = QHBoxLayout()
        br1.addWidget(QPushButton("⚙ 重新扫描", clicked=self._refresh_deps))
        dps.addLayout(br1)
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
        # 左栏顶部空间有限，限高避免挤占章节列表
        self._file_tree.setMaximumHeight(130)
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
        self._qb_convert.setMinimumHeight(40)
        self._qb_convert.setMinimumWidth(150)
        _bf = QFont("Helvetica", 12, QFont.Weight.Bold)
        self._qb_convert.setFont(_bf)
        self._qb_convert.clicked.connect(self._start_convert)
        layout.addWidget(self._qb_convert)
        self._qb_pause = QPushButton("⏹ 停止")
        self._qb_pause.setObjectName("stopBtn")
        self._qb_pause.setMinimumHeight(40)
        self._qb_pause.setEnabled(False)
        self._qb_pause.clicked.connect(self._pause_convert)
        layout.addWidget(self._qb_pause)
        self._qb_resume = QPushButton("▶ 继续生成")
        self._qb_resume.setObjectName("accentBtn")
        self._qb_resume.setMinimumHeight(40)
        self._qb_resume.setEnabled(False)
        self._qb_resume.clicked.connect(self._qb_resume_convert)
        layout.addWidget(self._qb_resume)
        return w

    # 状态栏已合并进底部单一状态行（见 _update_statusbar）

    # ================ ASR Tab ================

    # ================ 生成队列（逐章状态 / 失败重试） ================

    _QUEUE_STATUS = {
        # status: (显示文字, 颜色)
        "pending": ("⏳ 等待", "#888888"),
        "running": ("🔊 生成中", "#2563EB"),
        "done": ("✅ 完成", "#2E7D32"),
        "error": ("❌ 失败", "#C62828"),
        "skipped": ("— 未勾选", "#AAAAAA"),
    }

    def _build_queue_tab(self):
        layout = QVBoxLayout(self._queue_tab)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self._queue_summary = QLabel("尚未开始生成。开始转换后，这里会逐章显示状态；失败的章节可单独重试。")
        self._queue_summary.setWordWrap(True)
        top.addWidget(self._queue_summary, 1)
        self._queue_retry_all_btn = QPushButton("🔁 重试全部失败项")
        self._queue_retry_all_btn.clicked.connect(self._queue_retry_failed)
        top.addWidget(self._queue_retry_all_btn)
        self._queue_open_btn = QPushButton("📂 打开输出目录")
        self._queue_open_btn.clicked.connect(
            lambda: self._last_output_dir and self._open_path(self._last_output_dir))
        top.addWidget(self._queue_open_btn)
        layout.addLayout(top)

        t = QTableWidget(0, 5)
        t.setHorizontalHeaderLabels(["#", "章节", "状态", "说明", ""])
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setWordWrap(False)
        h = t.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        t.setColumnWidth(1, 280)
        t.verticalHeader().setDefaultSectionSize(32)
        self._queue_table = t
        layout.addWidget(t, 1)
        self._queue_refresh_actions()

    def _on_queue_event(self, event, data):
        """处理 convert_batch 推送的逐章事件（经 ConvertWorker.item_event 投递到界面线程）。"""
        if event in ("init", "final"):
            self._queue_items = [dict(it) for it in data.get("items", [])]
            self._queue_rebuild()
            return
        idx = data.get("index", -1)
        if not (0 <= idx < len(self._queue_items)):
            return
        it = self._queue_items[idx]
        if event == "running":
            it["status"], it["error"] = "running", ""
        elif event == "done":
            it["status"], it["error"] = "done", ""
        elif event == "error":
            it["status"], it["error"] = "error", data.get("error", "")
        elif event == "pending":
            it["status"] = "pending"
        self._queue_set_row(idx)
        self._queue_refresh_actions()

    def _queue_rebuild(self):
        self._queue_table.setRowCount(len(self._queue_items))
        for i in range(len(self._queue_items)):
            self._queue_set_row(i)
        self._queue_refresh_actions()

    def _queue_set_row(self, i):
        it = self._queue_items[i]
        status = it.get("status", "pending")
        label, color = self._QUEUE_STATUS.get(status, (status, "#888888"))
        cells = [str(i + 1), it.get("title") or it.get("filename", ""), label, ""]
        if status == "error":
            cells[3] = it.get("error", "")
        elif status == "done":
            cells[3] = it.get("filename", "")
        for col, text in enumerate(cells):
            cell = QTableWidgetItem(text)
            if col == 2:
                cell.setForeground(QColor(color))
            if col == 3 and status == "error":
                cell.setForeground(QColor(color))
                cell.setToolTip(text)
            elif col == 1:
                cell.setToolTip(it.get("filename", ""))
            self._queue_table.setItem(i, col, cell)
        if status == "error":
            btn = QPushButton("重试")
            # 全局按钮样式（min-height 32 + 上下 padding）放进表格行会溢出；行内用紧凑样式
            btn.setStyleSheet("padding:1px 14px; min-height:0px; font-size:13px;")
            btn.setEnabled(not self.is_converting)
            btn.clicked.connect(lambda _=False, idx=i: self._queue_retry([idx]))
            self._queue_table.setCellWidget(i, 4, btn)
        else:
            self._queue_table.removeCellWidget(i, 4)

    def _queue_refresh_actions(self):
        """刷新汇总行、标签页标题与重试按钮可用状态。"""
        items = self._queue_items
        counts = {}
        for it in items:
            counts[it.get("status")] = counts.get(it.get("status"), 0) + 1
        active = len(items) - counts.get("skipped", 0)
        done, failed = counts.get("done", 0), counts.get("error", 0)
        if items:
            parts = [f"共 {active} 项", f"完成 {done}"]
            if counts.get("running"):
                parts.append(f"生成中 {counts['running']}")
            if counts.get("pending"):
                parts.append(f"等待 {counts['pending']}")
            if failed:
                parts.append(f"<span style='color:#C62828'>失败 {failed}</span>")
            if counts.get("skipped"):
                parts.append(f"未勾选 {counts['skipped']}")
            self._queue_summary.setText(" · ".join(parts))
            tab_text = f"生成队列 ({done}/{active})" + (f" · 失败 {failed}" if failed else "")
        else:
            tab_text = "生成队列"
        idx = self._tab_widget.indexOf(self._queue_tab)
        if idx >= 0:
            self._tab_widget.setTabText(idx, tab_text)

        can_retry = bool(failed) and not self.is_converting
        self._queue_retry_all_btn.setEnabled(can_retry)
        self._queue_open_btn.setEnabled(bool(self._last_output_dir))
        for i, it in enumerate(items):
            w = self._queue_table.cellWidget(i, 4)
            if w is not None:
                w.setEnabled(not self.is_converting)

    def _queue_retry(self, indices):
        if self.is_converting or not indices:
            return
        if not self._last_output_dir or not load_progress(self._last_output_dir):
            QMessageBox.information(self, "无法重试",
                                    "找不到本次生成的进度文件（可能已被删除或目录已移动），请重新开始转换。")
            return
        self._run_convert(self._last_output_dir, self._last_prefix or "有声读物", None,
                          resume=True, only_indices=set(indices))

    def _queue_retry_failed(self):
        self._queue_retry([i for i, it in enumerate(self._queue_items) if it.get("status") == "error"])

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
        model_row.addWidget(QLabel("精度:"))
        self._asr_compute_combo = QComboBox()
        for label, val in WHISPER_COMPUTE_TYPES.items():
            self._asr_compute_combo.addItem(label, val)
        self._asr_compute_combo.setToolTip(
            "auto: CPU 用 int8、GPU 用 float16\n"
            "int8_float16: GPU 上更省显存，速度接近 float16")
        model_row.addWidget(self._asr_compute_combo, 1)
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
        """从注册表动态重建所有引擎按钮。

        重建后保持原选中的引擎（依赖刷新 / 模型增删都会触发重建，此前总会跳回第一个
        引擎）；首次构建时选中配置里记住的 default_engine，没有则选第一个。
        """
        prev = None
        checked = self._engine_group.checkedButton()
        for _eid, _btn, _n in self._engine_btns:
            if _btn is checked:
                prev = _eid
        if prev is None:
            try:
                from tts_engine import _load_config
                prev = _load_config().get("default_engine") or None
            except Exception:
                prev = None

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
            "clone": ("声音克隆", "复刻你的音色"),
        }

        first_btn = None
        COLS = 2  # 每行 2 个引擎，保持右侧面板紧凑
        for i, (eng_id, info) in enumerate(engines.items()):
            if info["type"] == "builtin":
                name, sub = BUILTIN.get(eng_id, (info.get("name", eng_id), ""))
                ready, _ = check_engine_ready(eng_id)
                suffix = "\n（需安装）" if not ready else ""
                btn = QRadioButton(f"{name}{suffix}\n{sub}")
            else:
                name = info.get("name", eng_id)
                btn = QRadioButton(f"⚡ {name}\n外挂引擎")

            btn.setStyleSheet("QRadioButton{font-size:11px;padding:6px}")
            self._engine_grid.addWidget(btn, i // COLS, i % COLS)
            self._engine_group.addButton(btn)
            self._engine_btns.append((eng_id, btn, name))
            if first_btn is None:
                first_btn = btn

        target = next((b for eid, b, _n in self._engine_btns if eid == prev), first_btn)
        if target:
            target.setChecked(True)
        self._on_engine_change()

    def _select_engine(self, engine_id):
        """程序化选中某引擎（向导完成时用）。找不到返回 False。"""
        for eid, btn, _name in self._engine_btns:
            if eid == engine_id:
                btn.setChecked(True)
                self._on_engine_change()
                return True
        return False

    def _remember_engine(self, _btn=None):
        """用户手动切换引擎时记住，下次启动默认选中。"""
        try:
            from tts_engine import _load_config, _save_config
            eid = self._get_current_engine()
            cfg = _load_config()
            if eid and cfg.get("default_engine") != eid:
                cfg["default_engine"] = eid
                _save_config(cfg)
        except Exception:
            pass

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

    def _on_time_slider_change(self, val):
        self._time_label.setText(f"{val} 分钟")
        self._update_split_estimate()

    def _get_rate_string(self):
        return f"+{self._rate_slider.value()}%" if self._rate_slider.value() >= 0 else f"{self._rate_slider.value()}%"

    def _get_selected_indices(self):
        """返回已勾选章节的索引列表（基于 checkbox 状态）"""
        result = []
        for i in range(self._chapter_tree.topLevelItemCount()):
            item = self._chapter_tree.topLevelItem(i)
            if item.checkState(0) != Qt.CheckState.Checked:
                continue
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
        """空闲时把引擎就绪情况汇总到底部单一状态行"""
        if not hasattr(self, '_status_label') or self.is_converting:
            return
        engine = "edge"
        btn = self._engine_group.checkedButton()
        if btn:
            for eng_id, b, _name in self._engine_btns:
                if b is btn:
                    engine = eng_id
                    break
        ready, msg = check_engine_ready(engine)
        voices = get_voice_list(engine)
        dot = "●" if ready else "○"
        self._status_label.setText(f"{dot} {msg[:40]} · {len(voices)} 语音 · v{VERSION}")

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
        self._chapter_tree.blockSignals(True)
        try:
            self._chapter_tree.clear()
            for idx, ch in enumerate(self.chapters):
                if filter_text and filter_text not in ch["title"].lower():
                    continue
                title = ch["title"]
                source = ch.get("source", "")
                if source and len(self._file_paths) > 1:
                    title = f"[{source}] {title}"
                item = QTreeWidgetItem([title])
                item.setData(0, Qt.ItemDataRole.UserRole, idx)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked)
                self._chapter_tree.addTopLevelItem(item)
        finally:
            self._chapter_tree.blockSignals(False)
        self._update_chapter_count()
        self._update_split_estimate()

    def _filter_chapters(self, text):
        self._refresh_chapters_list(text)

    def _select_all_chapters(self):
        self._chapter_tree.blockSignals(True)
        try:
            for i in range(self._chapter_tree.topLevelItemCount()):
                self._chapter_tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Checked)
        finally:
            self._chapter_tree.blockSignals(False)
        self._update_chapter_count()
        self._update_split_estimate()

    def _deselect_all_chapters(self):
        self._chapter_tree.blockSignals(True)
        try:
            for i in range(self._chapter_tree.topLevelItemCount()):
                self._chapter_tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Unchecked)
        finally:
            self._chapter_tree.blockSignals(False)
        self._update_chapter_count()
        self._update_split_estimate()

    def _on_chapter_select(self):
        # 仅在 ExtendedSelection 高亮时触发；统计与估算以"勾选"为准
        self._update_split_estimate()

    def _on_chapter_check_changed(self, item, column):
        self._update_chapter_count()
        self._update_split_estimate()

    def _update_chapter_count(self):
        total = self._chapter_tree.topLevelItemCount()
        checked = 0
        for i in range(total):
            if self._chapter_tree.topLevelItem(i).checkState(0) == Qt.CheckState.Checked:
                checked += 1
        self._chapter_count.setText(f"已选 {checked}/{total} 章" if total else "未检测到章节")

    def _update_split_estimate(self):
        mode = self._mode_var
        sel = set(self._get_selected_indices()) if self._chapter_tree.topLevelItemCount() > 0 else set(range(len(self.chapters)))
        sel_chs = [c for i, c in enumerate(self.chapters) if i in sel]
        if mode == "single":
            self._split_estimate.setText("将生成 1 个合并文件")
        elif mode == "chapter":
            self._split_estimate.setText(f"将生成 {len(sel_chs)} 个文件（每章一个）")
        elif mode == "time":
            mins = self._time_slider.value()
            rate = self._get_rate_string()
            # 与转换流程保持一致：先把选中章节合并，再按时长拆分
            joined = "\n\n".join(
                (c.get("text") or "").strip() for c in sel_chs if (c.get("text") or "").strip()
            )
            if joined:
                try:
                    parts = split_by_duration(joined, mins * 60, rate)
                    count = max(len(parts), 1)
                except Exception:
                    count = 1
            else:
                count = 0
            self._split_estimate.setText(f"将生成约 {count} 个文件（每段 ≈ {mins} 分钟）")
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
        self._open_path(get_storage_dir())

    def _open_path(self, path):
        """用系统文件管理器打开目录"""
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

        try:
            from audiobook import models as _M
            _entries = _M.list_installed()
            lines.append(f"模型总占用: {_M.format_size(_M.total_size(_entries))}"
                         f"（{len(_entries)} 个，详见「模型管理器」）")
        except Exception:
            pass

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

    def _open_model_manager(self):
        """模型管理器：统一查看 Piper / CosyVoice / Whisper 模型的占用，下载与删除。"""
        from audiobook import models as M

        dlg = QDialog(self)
        dlg.setWindowTitle("模型管理器")
        dlg.resize(780, 660)
        v = QVBoxLayout(dlg)

        summary = QLabel()
        summary.setWordWrap(True)
        v.addWidget(summary)

        tree = QTreeWidget()
        tree.setHeaderLabels(["名称", "大小", "位置"])
        tree.setRootIsDecorated(True)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        tree.header().setStretchLastSection(True)
        tree.setColumnWidth(0, 300)
        tree.setColumnWidth(1, 90)
        tree.setMinimumHeight(300)
        v.addWidget(tree, 1)

        act_row = QHBoxLayout()
        btn_refresh = QPushButton("🔄 刷新")
        btn_delete = QPushButton("🗑 删除选中")
        btn_delete.setEnabled(False)
        btn_open = QPushButton("📂 打开所在文件夹")
        btn_open.setEnabled(False)
        for b in (btn_refresh, btn_delete, btn_open):
            act_row.addWidget(b)
        act_row.addStretch()
        v.addLayout(act_row)

        dl_box = QGroupBox("下载模型")
        dl_l = QVBoxLayout(dl_box)
        dl_row = QHBoxLayout()
        dl_combo = QComboBox()
        dl_row.addWidget(dl_combo, 1)
        btn_dl = QPushButton("⬇ 下载")
        dl_row.addWidget(btn_dl)
        dl_l.addLayout(dl_row)
        cat_row = QHBoxLayout()
        cat_row.addWidget(QLabel("更多多语言 Piper 语音包："))
        btn_catalog = QPushButton("🌐 Piper 语音包目录…")
        cat_row.addWidget(btn_catalog)
        cat_row.addStretch()
        dl_l.addLayout(cat_row)
        v.addWidget(dl_box)

        status = QLabel("")
        status.setStyleSheet("color:gray")
        v.addWidget(status)
        close_row = QHBoxLayout()
        close_row.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dlg.close)
        close_row.addWidget(btn_close)
        v.addLayout(close_row)

        ENTRY_ROLE = Qt.ItemDataRole.UserRole

        def _populate():
            tree.clear()
            entries = M.list_installed()
            for kind in M.KIND_ORDER:
                group = [e for e in entries if e.kind == kind]
                top = QTreeWidgetItem([f"{M.KIND_LABELS[kind]}（{len(group)}）",
                                       M.format_size(M.total_size(group)), ""])
                f = top.font(0)
                f.setBold(True)
                top.setFont(0, f)
                top.setFlags(top.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                tree.addTopLevelItem(top)
                for e in group:
                    child = QTreeWidgetItem([e.name, M.format_size(e.size_bytes), e.location])
                    child.setData(0, ENTRY_ROLE, e)
                    if not e.managed:
                        child.setText(0, f"{e.name}  [外部·只读]")
                        child.setForeground(0, QColor("gray"))
                        child.setToolTip(0, "来自「添加模型文件夹」的外部目录，为安全起见不在此删除")
                    top.addChild(child)
                if not group:
                    hint = QTreeWidgetItem(["（未安装）", "", ""])
                    hint.setForeground(0, QColor("gray"))
                    hint.setFlags(hint.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    top.addChild(hint)
                top.setExpanded(True)
            summary.setText(
                f"已安装 <b>{len(entries)}</b> 个模型，共占用 <b>{M.format_size(M.total_size(entries))}</b>"
                f"　·　存储目录：{get_storage_dir()}")

            dl_combo.clear()
            for it in M.downloadable():
                mark = "✓ 已安装 · " if it.installed else ""
                dl_combo.addItem(f"{mark}{M.KIND_LABELS[it.kind]} — {it.label}", (it.kind, it.id))
            _on_select()

        def _selected_entry():
            item = tree.currentItem()
            return item.data(0, ENTRY_ROLE) if item is not None else None

        def _on_select():
            e = _selected_entry()
            btn_delete.setEnabled(bool(e) and e.managed)
            btn_open.setEnabled(bool(e))

        def _after_change():
            # 语音列表 / 引擎就绪状态随模型增删变化
            _populate()
            self._refresh_deps()
            self._on_engine_change()

        def _delete():
            e = _selected_entry()
            if not e:
                return
            if self._convert_worker is not None and self._convert_worker.isRunning():
                QMessageBox.warning(dlg, "正在生成", "生成进行中，暂不能删除模型。请先停止或等待完成。")
                return
            if QMessageBox.question(
                    dlg, "删除模型",
                    f"删除「{e.name}」？\n将释放 {M.format_size(e.size_bytes)}，删除后需重新下载才能使用。"
            ) != QMessageBox.StandardButton.Yes:
                return
            try:
                M.delete_model(e)
            except Exception as ex:
                QMessageBox.critical(dlg, "删除失败", str(ex))
                return
            status.setText(f"已删除 {e.name}，释放 {M.format_size(e.size_bytes)}")
            _after_change()

        def _open_location():
            e = _selected_entry()
            if e:
                self._open_path(e.location)

        def _download():
            data = dl_combo.currentData()
            if not data:
                return
            kind, model_id = data
            status.setText(f"正在下载 {model_id}…（进度见主窗口底部）")
            btn_dl.setEnabled(False)
            self._model_dl_worker = _ModelDownloadWorker()
            self._model_dl_worker.setup(kind, model_id)

            def _done(_r):
                status.setText(f"{model_id} 下载完成")
                btn_dl.setEnabled(True)
                _after_change()

            def _fail(err):
                status.setText(f"下载失败: {err}")
                btn_dl.setEnabled(True)

            self._model_dl_worker.finished_ok.connect(_done)
            self._model_dl_worker.error_occurred.connect(_fail)
            self._model_dl_worker.start()

        def _catalog():
            self._open_voice_catalog()
            _populate()  # 目录对话框关闭后刷新清单

        tree.itemSelectionChanged.connect(_on_select)
        btn_refresh.clicked.connect(_populate)
        btn_delete.clicked.connect(_delete)
        btn_open.clicked.connect(_open_location)
        btn_dl.clicked.connect(_download)
        btn_catalog.clicked.connect(_catalog)
        _populate()
        dlg.exec()

    # ================ 声音克隆 ================

    def _open_clone_dialog(self):
        self._build_clone_dialog().exec()

    def _after_clone_change(self):
        """音色 / 环境变化后刷新引擎卡片（就绪状态）与语音列表（选中的引擎会保持）。"""
        self._refresh_deps()

    def _build_clone_dialog(self):
        """声音克隆：音色管理（新建 / 文字稿 / 重命名 / 删除 / 试听）+ CosyVoice 环境设置与检测。"""
        from audiobook.cloning import profiles as P
        from audiobook.cloning import client as C
        from tts_engine import _load_config, _save_config

        dlg = QDialog(self)
        dlg.setWindowTitle("声音克隆")
        dlg.resize(780, 620)
        root = QVBoxLayout(dlg)
        tabs = QTabWidget()
        tabs.setObjectName("cloneTabs")
        root.addWidget(tabs, 1)
        consent = QLabel("⚠ 请只克隆你本人或已获明确授权的声音。")
        consent.setStyleSheet("color:#B45309")
        root.addWidget(consent)
        status = QLabel("")
        status.setObjectName("cloneStatus")
        status.setWordWrap(True)
        root.addWidget(status)
        close_row = QHBoxLayout()
        close_row.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dlg.close)
        close_row.addWidget(btn_close)
        root.addLayout(close_row)

        # ---------- 音色页 ----------
        pv = QWidget()
        lv = QVBoxLayout(pv)
        tbl = QTableWidget(0, 3)
        tbl.setObjectName("cloneTable")
        tbl.setHorizontalHeaderLabels(["名称", "时长", "文字稿"])
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        hh = tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        tbl.setColumnWidth(0, 180)
        lv.addWidget(tbl, 1)
        ops = QHBoxLayout()
        btn_new = QPushButton("＋ 新建音色…")
        btn_new.setObjectName("cloneNew")
        btn_text = QPushButton("编辑文字稿")
        btn_text.setObjectName("cloneEditText")
        btn_rename = QPushButton("重命名")
        btn_rename.setObjectName("cloneRename")
        btn_del = QPushButton("删除")
        btn_del.setObjectName("cloneDelete")
        for b in (btn_new, btn_text, btn_rename, btn_del):
            ops.addWidget(b)
        ops.addStretch()
        lv.addLayout(ops)
        listen = QHBoxLayout()
        sample = QLineEdit("你好，这是用我的声音朗读的有声书。")
        sample.setObjectName("cloneSampleText")
        listen.addWidget(sample, 1)
        btn_listen = QPushButton("▶ 试听所选音色")
        btn_listen.setObjectName("cloneListen")
        listen.addWidget(btn_listen)
        lv.addLayout(listen)
        tip = QLabel("首次试听需要加载模型（GPU 约十几秒，CPU 可能一分钟以上），之后会快很多。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:gray")
        lv.addWidget(tip)
        tabs.addTab(pv, "音色")

        # ---------- 环境设置页 ----------
        pe = QWidget()
        le = QVBoxLayout(pe)
        intro = QLabel(
            "声音克隆基于开源的 CosyVoice（依赖 torch，建议 NVIDIA 显卡），需要单独安装：<ol>"
            "<li>按官方说明 <code>git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git</code> "
            "并在独立的 conda / venv 环境里安装依赖；</li>"
            "<li>用下面的「下载模型」获取 <b>Fun-CosyVoice3-0.5B</b>（或 CosyVoice2-0.5B），"
            "也可自行下载到存储目录的 <code>cosyvoice-models/</code> 下；</li>"
            "<li>在下面填写该环境的 Python 与仓库路径，点「检测环境」。</li></ol>"
            "详细命令见 README「声音克隆」一节。")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        le.addWidget(intro)
        cfg = _load_config()
        grid = QGridLayout()
        py_edit = QLineEdit(cfg.get("cosyvoice_python", ""))
        py_edit.setObjectName("clonePython")
        default_py = C.resolve_python() if not cfg.get("cosyvoice_python") else ""
        py_edit.setPlaceholderText(f"留空 = 当前解释器（{default_py}）" if default_py
                                   else "必填：CosyVoice 环境里的 python，如 …/envs/cosyvoice/bin/python")
        repo_edit = QLineEdit(cfg.get("cosyvoice_repo", ""))
        repo_edit.setObjectName("cloneRepo")
        repo_edit.setPlaceholderText("CosyVoice 仓库目录（git clone 下来的文件夹）")
        model_edit = QLineEdit(cfg.get("clone_model_dir", ""))
        model_edit.setObjectName("cloneModelDir")
        auto_md = C.resolve_model_dir() if not cfg.get("clone_model_dir") else ""
        model_edit.setPlaceholderText(f"留空 = 自动：{auto_md}" if auto_md
                                      else "留空 = 自动在 cosyvoice-models/ 下查找（当前未找到）")

        def _browse_file(edit):
            path, _ = QFileDialog.getOpenFileName(dlg, "选择 Python 解释器")
            if path:
                edit.setText(path)

        def _browse_dir(edit, title):
            path = QFileDialog.getExistingDirectory(dlg, title)
            if path:
                edit.setText(path)

        for row, (label, edit, browse) in enumerate((
                ("Python 解释器", py_edit, lambda: _browse_file(py_edit)),
                ("CosyVoice 仓库", repo_edit, lambda: _browse_dir(repo_edit, "选择 CosyVoice 仓库目录")),
                ("模型目录", model_edit, lambda: _browse_dir(model_edit, "选择 CosyVoice 模型目录")))):
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(edit, row, 1)
            b = QPushButton("浏览…")
            b.clicked.connect(browse)
            grid.addWidget(b, row, 2)
        le.addLayout(grid)
        # 模型下载（整库下载，HuggingFace → hf-mirror → ModelScope 自动回退，可续传）
        from tts_engine import COSYVOICE_MODELS, COSYVOICE_CLONE_MODEL
        dl_row = QHBoxLayout()
        dl_row.addWidget(QLabel("下载模型"))
        dl_model = QComboBox()
        dl_model.setObjectName("cloneDlModel")
        for key in (COSYVOICE_CLONE_MODEL, "CosyVoice2-0.5B"):
            dl_model.addItem(COSYVOICE_MODELS[key]["description"], key)
        dl_row.addWidget(dl_model, 1)
        btn_dl = QPushButton("⬇ 下载")
        btn_dl.setObjectName("cloneDlStart")
        btn_dl_stop = QPushButton("暂停")
        btn_dl_stop.setObjectName("cloneDlStop")
        btn_dl_stop.setEnabled(False)
        dl_row.addWidget(btn_dl)
        dl_row.addWidget(btn_dl_stop)
        le.addLayout(dl_row)
        dl_bar = QProgressBar()
        dl_bar.setObjectName("cloneDlProgress")
        dl_bar.setVisible(False)
        le.addWidget(dl_bar)
        dl_msg = QLabel("")
        dl_msg.setObjectName("cloneDlMsg")
        dl_msg.setWordWrap(True)
        dl_msg.setStyleSheet("color:gray")
        le.addWidget(dl_msg)
        env_ops = QHBoxLayout()
        btn_save = QPushButton("保存设置")
        btn_save.setObjectName("cloneSave")
        btn_check = QPushButton("🔍 检测环境（会实际加载一次模型）")
        btn_check.setObjectName("cloneCheck")
        env_ops.addWidget(btn_save)
        env_ops.addWidget(btn_check)
        env_ops.addStretch()
        le.addLayout(env_ops)
        report = QPlainTextEdit()
        report.setObjectName("cloneReport")
        report.setReadOnly(True)
        report.setPlaceholderText("检测结果会显示在这里。")
        le.addWidget(report, 1)
        tabs.addTab(pe, "环境设置")

        # ---------- 逻辑 ----------
        ID_ROLE = Qt.ItemDataRole.UserRole

        def _update_status():
            ok, msg = C.readiness()
            status.setText(("✓ " if ok else "○ ") + msg.replace("\n", " "))
            status.setStyleSheet("color:#2E7D32" if ok else "color:gray")

        def _selected():
            r = tbl.currentRow()
            item = tbl.item(r, 0) if r >= 0 else None
            return P.get_profile(item.data(ID_ROLE)) if item is not None else None

        def _update_buttons():
            has = _selected() is not None
            for b in (btn_text, btn_rename, btn_del, btn_listen):
                b.setEnabled(has)

        def _refresh(select_id=None):
            items = P.list_profiles()
            tbl.setRowCount(len(items))
            for r, p in enumerate(items):
                name = QTableWidgetItem(p.name)
                name.setData(ID_ROLE, p.id)
                tbl.setItem(r, 0, name)
                tbl.setItem(r, 1, QTableWidgetItem(f"{p.duration:.1f} 秒"))
                text = QTableWidgetItem(p.prompt_text or "（无文字稿 · 效果会差一些）")
                if not p.prompt_text:
                    text.setForeground(QColor("gray"))
                text.setToolTip(p.prompt_text)
                tbl.setItem(r, 2, text)
                if p.id == select_id:
                    tbl.selectRow(r)
            if select_id is None and items and tbl.currentRow() < 0:
                tbl.selectRow(0)
            _update_buttons()
            _update_status()

        def _changed(select_id=None):
            _refresh(select_id)
            self._after_clone_change()

        def _new():
            nd = self._build_clone_new_dialog(dlg)
            if nd.exec() == QDialog.DialogCode.Accepted and nd.created is not None:
                _changed(nd.created.id)
                status.setText(f"已创建音色「{nd.created.name}」。")

        def _edit_text():
            p = _selected()
            if not p:
                return
            text, ok = QInputDialog.getMultiLineText(dlg, "编辑文字稿", "参考音频里说的话（逐字填写效果更好）：",
                                                     p.prompt_text)
            if ok:
                P.update_profile(p.id, prompt_text=text)
                _changed(p.id)

        def _rename():
            p = _selected()
            if not p:
                return
            name, ok = QInputDialog.getText(dlg, "重命名", "音色名称：", text=p.name)
            if ok and name.strip():
                P.update_profile(p.id, name=name)
                _changed(p.id)

        def _delete():
            p = _selected()
            if not p:
                return
            if QMessageBox.question(dlg, "删除音色", f"删除克隆音色「{p.name}」？") \
                    != QMessageBox.StandardButton.Yes:
                return
            P.delete_profile(p.id)
            _changed()

        def _listen():
            p = _selected()
            if not p:
                return
            ok, msg = C.readiness()
            if not ok:
                status.setText("○ " + msg.replace("\n", " "))
                tabs.setCurrentIndex(1)
                return
            btn_listen.setEnabled(False)
            status.setText(f"正在用「{p.name}」合成试听…")
            w = _ClonePreviewWorker()
            w.setup(p.voice_id, sample.text().strip() or "你好")

            def _ok(path):
                _update_buttons()
                try:
                    self.player.play(path)
                    status.setText(f"✓ 正在播放「{p.name}」的试听。")
                except Exception as e:
                    status.setText(f"试听已生成，但播放失败：{e}")

            def _err(e):
                _update_buttons()
                status.setText(f"✗ 试听失败：{e}")

            w.finished_ok.connect(_ok)
            w.error_occurred.connect(_err)
            self._clone_preview_worker = w
            w.start()

        def _save():
            cfg = _load_config()
            cfg["cosyvoice_python"] = py_edit.text().strip()
            cfg["cosyvoice_repo"] = repo_edit.text().strip()
            cfg["clone_model_dir"] = model_edit.text().strip()
            _save_config(cfg)
            C.shutdown()  # 解释器 / 仓库可能变了：下次按新配置重启桥接进程
            _update_status()
            self._after_clone_change()

        def _check():
            _save()
            btn_check.setEnabled(False)
            report.setPlainText("正在检测：启动 CosyVoice 环境并加载模型，可能需要一两分钟…")
            w = _CloneCheckWorker()
            w.setup(load=True)

            def _done(rep):
                btn_check.setEnabled(True)
                lines = [f"Python 解释器：{rep.get('python') or '（未配置）'}",
                         f"CosyVoice 仓库：{rep.get('repo') or '（未配置）'}",
                         f"模型目录：{rep.get('model_dir') or '（未找到）'}"
                         + (f"  [{rep['model_type']}]" if rep.get("model_type") else "")]
                pg = rep.get("ping") or {}
                if pg:
                    lines.append(f"桥接进程：Python {pg.get('python')}，cosyvoice "
                                 + ("可导入 ✓" if pg.get("cosyvoice")
                                    else f"无法导入 ✗\n  {pg.get('import_error')}"))
                ld = rep.get("load") or {}
                if ld:
                    lines.append(f"模型加载：{ld.get('model_type')}，采样率 {ld.get('sample_rate')} ✓")
                if rep.get("error"):
                    lines.append(f"\n错误：{rep['error']}")
                good = bool(pg.get("cosyvoice")) and bool(ld) and not rep.get("error")
                lines.append("\n✓ 环境可用" if good else "\n✗ 环境未就绪，请按上面的提示检查")
                report.setPlainText("\n".join(lines))
                _update_status()

            def _fail(e):
                btn_check.setEnabled(True)
                report.setPlainText(f"检测失败：{e}")

            w.finished_ok.connect(_done)
            w.error_occurred.connect(_fail)
            self._clone_check_worker = w
            w.start()

        def _refresh_model_hint():
            if not model_edit.text().strip():
                md = C.resolve_model_dir()
                model_edit.setPlaceholderText(f"留空 = 自动：{md}" if md
                                              else "留空 = 自动在 cosyvoice-models/ 下查找（当前未找到）")

        def _download_model():
            key = dl_model.currentData()
            btn_dl.setEnabled(False)
            btn_dl_stop.setEnabled(True)
            dl_bar.setVisible(True)
            dl_bar.setRange(0, 0)
            dl_msg.setText(f"正在获取 {key} 的文件清单…（模型较大，可随时暂停，下次继续下载会续传）")
            w = _WizardDownloadWorker()
            w.setup([("cosyvoice", key)])

            def _progress(desc, cur, total):
                if total > 0:
                    dl_bar.setRange(0, 1000)
                    dl_bar.setValue(int(cur * 1000 / total))
                    dl_msg.setText(f"{desc}　{cur / 1048576:.1f} / {total / 1048576:.1f} MB")

            paused = "已暂停；再次点击「下载」会从断点继续。"

            def _failed(_i, err):
                dl_msg.setText(paused if w._should_stop else f"✗ 下载失败：{err}")

            def _finished(failed):
                btn_dl.setEnabled(True)
                btn_dl_stop.setEnabled(False)
                dl_bar.setVisible(False)
                if not failed:
                    dl_msg.setText(paused if w._should_stop else f"✓ {key} 下载完成。")
                _refresh_model_hint()
                _update_status()
                self._after_clone_change()

            w.bytes_progress.connect(_progress)
            w.item_failed.connect(_failed)
            w.finished_ok.connect(_finished)
            self._clone_dl_worker = w
            w.start()

        def _stop_download():
            w = getattr(self, "_clone_dl_worker", None)
            if w is not None and w.isRunning():
                w.request_stop()
                btn_dl_stop.setEnabled(False)
                dl_msg.setText("正在暂停…")

        tbl.itemSelectionChanged.connect(_update_buttons)
        btn_dl.clicked.connect(_download_model)
        btn_dl_stop.clicked.connect(_stop_download)
        dlg.finished.connect(lambda _r: _stop_download())  # 关闭对话框即暂停（已下部分保留）
        btn_new.clicked.connect(_new)
        btn_text.clicked.connect(_edit_text)
        btn_rename.clicked.connect(_rename)
        btn_del.clicked.connect(_delete)
        btn_listen.clicked.connect(_listen)
        btn_save.clicked.connect(_save)
        btn_check.clicked.connect(_check)
        _refresh()
        # 环境还没配好时先打开「环境设置」页
        if not C.resolve_python() or not C.resolve_model_dir():
            tabs.setCurrentIndex(1)
        return dlg

    def _build_clone_new_dialog(self, parent):
        """新建克隆音色：选参考音频 →（可选）自动识别文字稿 → 命名 → 创建。成功后 dlg.created 为新档案。"""
        from audiobook.cloning import profiles as P

        d = QDialog(parent)
        d.setWindowTitle("新建克隆音色")
        d.resize(580, 460)
        d.created = None
        st = {"src": None, "tmp": None, "dur": 0.0}
        v = QVBoxLayout(d)
        v.addWidget(QLabel("名称"))
        name_edit = QLineEdit()
        name_edit.setObjectName("cloneNewName")
        name_edit.setPlaceholderText("例如：我的声音")
        v.addWidget(name_edit)
        v.addWidget(QLabel("参考音频"))
        frow = QHBoxLayout()
        file_label = QLabel("未选择")
        file_label.setObjectName("cloneNewFile")
        file_label.setWordWrap(True)
        frow.addWidget(file_label, 1)
        btn_pick = QPushButton("选择参考音频…")
        btn_pick.setObjectName("cloneNewPick")
        frow.addWidget(btn_pick)
        v.addLayout(frow)
        hint = QLabel(f"建议 5–15 秒、单人、无背景音乐的清晰人声；短于 {P.MIN_SECONDS:.0f} 秒无法使用，"
                      f"超过 {P.MAX_SECONDS:.0f} 秒只取前 {P.MAX_SECONDS:.0f} 秒。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:gray")
        v.addWidget(hint)
        trow = QHBoxLayout()
        trow.addWidget(QLabel("文字稿（参考音频里说的话）"))
        trow.addStretch()
        btn_asr = QPushButton("🎧 自动识别（Whisper）")
        btn_asr.setObjectName("cloneNewAsr")
        btn_asr.setEnabled(False)
        trow.addWidget(btn_asr)
        v.addLayout(trow)
        text_edit = QPlainTextEdit()
        text_edit.setObjectName("cloneNewText")
        text_edit.setPlaceholderText("逐字填写效果明显更好；留空也能用（只取音色）。")
        v.addWidget(text_edit, 1)
        msg = QLabel("")
        msg.setObjectName("cloneNewMsg")
        msg.setWordWrap(True)
        v.addWidget(msg)
        brow = QHBoxLayout()
        brow.addStretch()
        btn_cancel = QPushButton("取消")
        btn_ok = QPushButton("创建")
        btn_ok.setObjectName("cloneNewCreate")
        btn_ok.setEnabled(False)
        brow.addWidget(btn_cancel)
        brow.addWidget(btn_ok)
        v.addLayout(brow)

        try:
            asr_ok = check_asr_ready(get_storage_dir())[0]
        except Exception:
            asr_ok = False
        if not asr_ok:
            btn_asr.setToolTip("需要安装 faster-whisper（语音转文字）才能自动识别")

        def _cleanup_tmp():
            if st["tmp"]:
                shutil.rmtree(os.path.dirname(st["tmp"]), ignore_errors=True)
                st["tmp"] = None

        def _update_ok():
            usable = st["src"] is not None and st["dur"] >= P.MIN_SECONDS
            btn_ok.setEnabled(usable and bool(name_edit.text().strip()))

        def _pick():
            path, _ = QFileDialog.getOpenFileName(
                d, "选择参考音频", "",
                "音频 (*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.wma *.opus);;所有文件 (*)")
            if not path:
                return
            _cleanup_tmp()
            st["src"], st["dur"] = None, 0.0
            try:
                tmp, dur = P.prepare_reference(path)
            except Exception as e:
                file_label.setText("未选择")
                msg.setStyleSheet("color:#C62828")
                msg.setText(f"✗ {e}")
                btn_asr.setEnabled(False)
                _update_ok()
                return
            st.update(src=path, tmp=tmp, dur=dur)
            trimmed = "（已截取前 30 秒）" if dur >= P.MAX_SECONDS - 0.05 else ""
            file_label.setText(f"{os.path.basename(path)}　{dur:.1f} 秒{trimmed}")
            if dur < P.MIN_SECONDS:
                msg.setStyleSheet("color:#C62828")
                msg.setText(f"✗ 太短（{dur:.1f} 秒），至少需要 {P.MIN_SECONDS:.0f} 秒。")
            else:
                msg.setStyleSheet("color:gray")
                msg.setText("✓ 音频可用。" + ("" if text_edit.toPlainText().strip()
                                              else "建议填写或自动识别文字稿。"))
            if not name_edit.text().strip():
                name_edit.setText(os.path.splitext(os.path.basename(path))[0])
            btn_asr.setEnabled(asr_ok and dur >= P.MIN_SECONDS)
            _update_ok()

        def _asr():
            if not st["tmp"]:
                return
            btn_asr.setEnabled(False)
            msg.setStyleSheet("color:gray")
            msg.setText("正在识别文字稿…（首次使用会下载 Whisper base 模型）")
            w = _CloneTranscribeWorker()
            w.setup(st["tmp"])

            def _ok(text):
                btn_asr.setEnabled(True)
                if text:
                    text_edit.setPlainText(text)
                    msg.setText("✓ 已识别，请核对后再创建（错字会影响效果）。")
                else:
                    msg.setText("没有识别到文字，请手动填写。")

            def _err(e):
                btn_asr.setEnabled(True)
                msg.setStyleSheet("color:#C62828")
                msg.setText(f"✗ 识别失败：{e}")

            w.finished_ok.connect(_ok)
            w.error_occurred.connect(_err)
            self._clone_asr_worker = w
            w.start()

        def _create():
            try:
                d.created = P.create_profile(name_edit.text(), st["src"],
                                             prompt_text=text_edit.toPlainText())
            except Exception as e:
                msg.setStyleSheet("color:#C62828")
                msg.setText(f"✗ {e}")
                return
            d.accept()

        btn_pick.clicked.connect(_pick)
        btn_asr.clicked.connect(_asr)
        btn_ok.clicked.connect(_create)
        btn_cancel.clicked.connect(d.reject)
        name_edit.textChanged.connect(lambda _t: _update_ok())
        d.finished.connect(lambda _r: _cleanup_tmp())
        return d

    # ================ 首次运行向导 ================

    def maybe_show_first_run_wizard(self):
        """启动后由 main.py 调用：首次使用时自动打开向导（判定见 audiobook.onboarding.should_show）。"""
        try:
            from audiobook import onboarding
            if onboarding.should_show():
                self._open_first_run_wizard()
        except Exception as e:
            logger.warning(f"首次运行向导启动失败: {e}")

    def _open_first_run_wizard(self):
        self._build_first_run_wizard().exec()

    def _build_first_run_wizard(self):
        """构建首次运行向导：选场景 → 下载该场景缺的模型 → 试听验证 → 记住引擎。

        任何方式关闭都会记为「已完成」，不再自动弹出；只有点「完成」才会同时设定默认引擎。
        """
        from audiobook import onboarding as OB
        from audiobook import models as M

        dlg = QDialog(self)
        dlg.setWindowTitle("首次运行向导")
        dlg.resize(680, 560)
        root = QVBoxLayout(dlg)
        step_label = QLabel()
        step_label.setStyleSheet("color:gray")
        root.addWidget(step_label)
        stack = QStackedWidget()
        stack.setObjectName("wizardStack")
        root.addWidget(stack, 1)
        nav = QHBoxLayout()
        btn_skip = QPushButton("跳过向导")
        btn_skip.setObjectName("wizardSkip")
        btn_back = QPushButton("← 上一步")
        btn_back.setObjectName("wizardBack")
        btn_next = QPushButton("下一步 →")
        btn_next.setObjectName("wizardNext")
        nav.addWidget(btn_skip)
        nav.addStretch()
        nav.addWidget(btn_back)
        nav.addWidget(btn_next)
        root.addLayout(nav)

        st = {"scenario": None, "items": [], "labels": [], "todo": [], "failed_idx": [],
              "downloading": False, "finished": False}
        ENGINE_NAMES = {"edge": "Edge 在线语音", "piper": "Piper 离线语音", "local": "系统语音"}
        STEPS = ["第 1 步 / 共 3 步 · 选择使用场景", "第 2 步 / 共 3 步 · 准备模型",
                 "第 3 步 / 共 3 步 · 试听验证"]

        # ---------- 第 1 步：选择使用场景 ----------
        p1 = QWidget()
        l1 = QVBoxLayout(p1)
        l1.addWidget(QLabel("<h2>欢迎使用文字转有声读物</h2>"))
        intro = QLabel("先选一个使用场景，向导会帮你备好需要的东西。"
                       "之后可在「存储与依赖」面板随时重新运行本向导。")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:gray")
        l1.addWidget(intro)
        sc_group = QButtonGroup(dlg)
        sc_buttons = {}
        rec = OB.recommend()
        for sc in OB.SCENARIOS:
            ok, why = OB.availability(sc)
            pending = OB.pending_downloads(sc) if ok else []
            if not ok:
                extra = f"不可用：{why}"
            elif pending:
                extra = f"需下载 {sc.size_hint}"
            elif sc.models:
                extra = "模型已就绪"
            else:
                extra = sc.size_hint
            tag = "推荐 · " if sc.id == rec else ""
            rb = QRadioButton(f"{sc.title}　（{tag}{extra}）")
            rb.setObjectName(f"wizardScenario_{sc.id}")
            rb.setStyleSheet("font-weight:bold")
            rb.setEnabled(ok)
            sc_group.addButton(rb)
            sc_buttons[sc.id] = rb
            l1.addWidget(rb)
            desc = QLabel(sc.description)
            desc.setWordWrap(True)
            desc.setStyleSheet("color:gray; margin-left:24px; margin-bottom:6px")
            l1.addWidget(desc)
        if rec in sc_buttons and sc_buttons[rec].isEnabled():
            sc_buttons[rec].setChecked(True)
        l1.addStretch()
        stack.addWidget(p1)

        # ---------- 第 2 步：准备模型 ----------
        p2 = QWidget()
        l2 = QVBoxLayout(p2)
        l2.addWidget(QLabel("<h3>准备模型</h3>"))
        dl_intro = QLabel()
        dl_intro.setWordWrap(True)
        l2.addWidget(dl_intro)
        dl_list = QListWidget()
        dl_list.setObjectName("wizardDownloadList")
        l2.addWidget(dl_list, 1)
        dl_bar = QProgressBar()
        dl_bar.setVisible(False)
        l2.addWidget(dl_bar)
        dl_status = QLabel("")
        dl_status.setObjectName("wizardDownloadStatus")
        dl_status.setWordWrap(True)
        dl_status.setStyleSheet("color:gray")
        l2.addWidget(dl_status)
        btn_retry = QPushButton("🔁 重试失败项")
        btn_retry.setObjectName("wizardRetry")
        btn_retry.setVisible(False)
        l2.addWidget(btn_retry, 0, Qt.AlignmentFlag.AlignLeft)
        stack.addWidget(p2)

        # ---------- 第 3 步：试听验证 ----------
        p3 = QWidget()
        l3 = QVBoxLayout(p3)
        l3.addWidget(QLabel("<h3>试听验证</h3>"))
        ready_label = QLabel()
        ready_label.setWordWrap(True)
        l3.addWidget(ready_label)
        sample = QLineEdit("你好，欢迎使用文字转有声读物。祝你听书愉快！")
        l3.addWidget(sample)
        listen_row = QHBoxLayout()
        btn_listen = QPushButton("▶ 试听")
        btn_listen.setObjectName("wizardListen")
        listen_row.addWidget(btn_listen)
        listen_row.addStretch()
        l3.addLayout(listen_row)
        listen_status = QLabel("")
        listen_status.setObjectName("wizardListenStatus")
        listen_status.setWordWrap(True)
        l3.addWidget(listen_status)
        hint = QLabel("听到声音就说明一切就绪。点「完成」后默认使用该引擎，之后仍可在右侧面板随时切换。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:gray")
        l3.addWidget(hint)
        l3.addStretch()
        stack.addWidget(p3)

        # ---------- 逻辑 ----------
        def _scenario_choice():
            for sid, rb in sc_buttons.items():
                if rb.isChecked():
                    return OB.get(sid)
            return None

        def _update_nav():
            i = stack.currentIndex()
            step_label.setText(STEPS[i])
            btn_back.setVisible(i > 0)
            btn_back.setEnabled(not st["downloading"])
            btn_next.setText("完成 ✓" if i == 2 else "下一步 →")
            if i == 0:
                btn_next.setEnabled(_scenario_choice() is not None)
            elif i == 1:
                btn_next.setEnabled(not st["downloading"])
            else:
                btn_next.setEnabled(True)

        def _set_row(i, status):
            item = dl_list.item(i)
            if item is not None:
                item.setText(f"{st['labels'][i]}　—　{status}")

        def _on_bytes(desc, cur, total):
            if total > 0:
                dl_bar.setRange(0, total)
                dl_bar.setValue(min(cur, total))
                dl_status.setText(f"{desc}：{cur / 1048576:.1f} / {total / 1048576:.1f} MB")
            else:
                dl_bar.setRange(0, 0)

        def _on_failed(k, err):
            i = st["todo"][k]
            st["failed_idx"].append(i)
            _set_row(i, f"✗ 失败：{err}")

        def _on_dl_finished(failed):
            st["downloading"] = False
            dl_bar.setVisible(False)
            if failed:
                dl_status.setText(f"有 {failed} 项下载失败。可以重试，也可以先继续"
                                  f"（之后在「模型管理器」里也能下载）。")
                btn_retry.setVisible(True)
            else:
                dl_status.setText("✓ 全部就绪")
            self._refresh_deps()
            _update_nav()

        def _start_downloads(indices):
            st["downloading"] = True
            st["todo"] = list(indices)
            st["failed_idx"] = []
            btn_retry.setVisible(False)
            dl_bar.setVisible(True)
            dl_bar.setRange(0, 0)
            dl_status.setText("正在下载…")
            for i in indices:
                _set_row(i, "等待")
            w = _WizardDownloadWorker()
            w.setup([st["items"][i] for i in indices])
            w.item_started.connect(lambda k: _set_row(st["todo"][k], "⬇ 下载中…"))
            w.item_done.connect(lambda k: _set_row(st["todo"][k], "✓ 完成"))
            w.item_failed.connect(_on_failed)
            w.bytes_progress.connect(_on_bytes)
            w.finished_ok.connect(_on_dl_finished)
            self._wizard_dl_worker = w
            w.start()
            _update_nav()

        def _enter_downloads():
            sc = st["scenario"]
            pending = OB.pending_downloads(sc)
            st["items"] = [(it.kind, it.id) for it in pending]
            st["labels"] = [f"{M.KIND_LABELS[it.kind]} · {it.label}" for it in pending]
            dl_list.clear()
            btn_retry.setVisible(False)
            if not pending:
                dl_intro.setText(f"「{sc.title}」所需模型已全部就绪。")
                dl_status.setText("✓ 全部就绪")
                return
            for lab in st["labels"]:
                dl_list.addItem(f"{lab}　—　等待")
            dl_intro.setText(f"「{sc.title}」需要下载以下模型（{sc.size_hint}）。下载完成后点「下一步」试听。")
            _start_downloads(list(range(len(pending))))

        def _enter_listen():
            sc = st["scenario"]
            ok, why = OB.availability(sc)
            text = f"已选择：<b>{sc.title}</b>（引擎：{ENGINE_NAMES.get(sc.engine, sc.engine)}）"
            if not ok:
                text += f"<br><span style='color:#C62828'>引擎不可用：{why}</span>"
            ready_label.setText(text)
            btn_listen.setEnabled(ok)
            listen_status.setText("")

        def _goto(i):
            stack.setCurrentIndex(i)
            if i == 1:
                _enter_downloads()
            elif i == 2:
                _enter_listen()
            _update_nav()

        def _listen():
            sc = st["scenario"]
            voices = get_voice_list(sc.engine)
            if not voices:
                listen_status.setText("✗ 该引擎没有可用语音。")
                return
            voice = get_voice_id(voices[0], sc.engine)
            btn_listen.setEnabled(False)
            listen_status.setText("正在生成试听…（Piper 首次使用需要几秒加载模型）")
            w = PreviewWorker()
            w.setup(sample.text().strip() or "你好", voice, "+0%", sc.engine)

            def _ok(path):
                btn_listen.setEnabled(True)
                try:
                    self.player.play(path)
                    listen_status.setText("✓ 正在播放。听到声音就说明一切就绪。")
                except Exception as e:
                    listen_status.setText(f"试听已生成，但播放失败：{e}")

            def _err(e):
                btn_listen.setEnabled(True)
                listen_status.setText(f"✗ 试听失败：{e}")

            w.finished_ok.connect(_ok)
            w.error_occurred.connect(_err)
            self._wizard_preview_worker = w
            w.start()

        def _finish():
            sc = st["scenario"]
            st["finished"] = True
            usable = OB.availability(sc)[0]
            OB.mark_completed(sc.engine if usable else None)
            self._refresh_deps()          # 先刷新（会重建引擎按钮），再选中
            if usable:
                self._select_engine(sc.engine)
            dlg.accept()

        def _next():
            i = stack.currentIndex()
            if i == 0:
                st["scenario"] = _scenario_choice()
                if st["scenario"] is None:
                    return
                # 没有要下载的就直接跳到试听
                _goto(1 if OB.pending_downloads(st["scenario"]) else 2)
            elif i == 1:
                _goto(2)
            else:
                _finish()

        def _back():
            # 回到场景选择（下载步骤做完后再进入会是空列表，没有意义）
            if stack.currentIndex() > 0 and not st["downloading"]:
                _goto(0)

        def _on_closed(_result):
            w = getattr(self, "_wizard_dl_worker", None)
            if w is not None and w.isRunning():
                w.request_stop()
            if not st["finished"]:
                OB.mark_completed(None)   # 跳过 / 关闭：不再自动弹出

        sc_group.buttonClicked.connect(lambda _b: _update_nav())
        btn_next.clicked.connect(_next)
        btn_back.clicked.connect(_back)
        btn_skip.clicked.connect(dlg.reject)
        btn_retry.clicked.connect(lambda: _start_downloads(list(st["failed_idx"])))
        btn_listen.clicked.connect(_listen)
        dlg.finished.connect(_on_closed)
        stack.setCurrentIndex(0)
        _update_nav()
        return dlg

    def _open_voice_catalog(self):
        """打开 Piper 语音包目录对话框：拉取官方 voices.json，按语言筛选下载"""
        dlg = QDialog(self)
        dlg.setWindowTitle("Piper 语音包下载")
        dlg.resize(560, 480)
        v = QVBoxLayout(dlg)

        top = QHBoxLayout()
        top.addWidget(QLabel("语言:"))
        lang_combo = QComboBox()
        for label, code in [("中文 (zh)", "zh"), ("英文 (en)", "en"), ("日文 (ja)", "ja"),
                            ("韩文 (ko)", "ko"), ("法文 (fr)", "fr"), ("德文 (de)", "de"),
                            ("西班牙文 (es)", "es"), ("俄文 (ru)", "ru"), ("全部语言", "")]:
            lang_combo.addItem(label, code)
        top.addWidget(lang_combo, 1)
        btn_refresh = QPushButton("🔄 刷新目录")
        top.addWidget(btn_refresh)
        v.addLayout(top)

        lst = QListWidget()
        v.addWidget(lst, 1)
        status = QLabel("正在获取语音目录…")
        status.setStyleSheet("color:gray")
        v.addWidget(status)
        btn_row = QHBoxLayout()
        btn_download = QPushButton("⬇ 下载选中语音")
        btn_download.setEnabled(False)
        btn_row.addWidget(btn_download)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dlg.close)
        btn_row.addWidget(btn_close)
        v.addLayout(btn_row)

        catalog_holder = {"catalog": None}

        def _populate():
            lst.clear()
            catalog = catalog_holder["catalog"]
            if not catalog:
                return
            entries = list_piper_catalog_voices(catalog, lang_combo.currentData() or "")
            for e in entries:
                size_mb = e["size_bytes"] / 1024 / 1024
                spk = f", {e['num_speakers']} 说话人" if e.get("num_speakers", 1) > 1 else ""
                item = QListWidgetItem(
                    f"{e['key']}  [{e['language_name']} · {e['quality']}{spk} · {size_mb:.0f}MB]")
                item.setData(Qt.ItemDataRole.UserRole, e["key"])
                lst.addItem(item)
            status.setText(f"共 {lst.count()} 个语音；下载后自动加入语音列表")
            btn_download.setEnabled(lst.count() > 0)

        def _fetch(force=False):
            status.setText("正在获取语音目录…")
            btn_download.setEnabled(False)
            self._catalog_worker = _CatalogWorker()
            self._catalog_worker.setup(force)
            self._catalog_worker.finished_ok.connect(
                lambda cat: (catalog_holder.__setitem__("catalog", cat), _populate()))
            self._catalog_worker.error_occurred.connect(
                lambda e: status.setText(f"目录获取失败: {e}"))
            self._catalog_worker.start()

        def _download():
            item = lst.currentItem()
            if item is None:
                status.setText("请先选中一个语音")
                return
            key = item.data(Qt.ItemDataRole.UserRole)
            status.setText(f"正在下载 {key}…（进度见主窗口底部）")
            btn_download.setEnabled(False)
            self._voice_dl_worker = _VoiceDownloadWorker()
            self._voice_dl_worker.setup(key)

            def _done(_r):
                status.setText(f"{key} 下载完成，已加入语音列表")
                btn_download.setEnabled(True)
                self._refresh_deps()
                self._on_engine_change()

            self._voice_dl_worker.finished_ok.connect(_done)
            self._voice_dl_worker.error_occurred.connect(
                lambda e: (status.setText(f"下载失败: {e}"), btn_download.setEnabled(True)))
            self._voice_dl_worker.start()

        lang_combo.currentIndexChanged.connect(lambda _i: _populate())
        btn_refresh.clicked.connect(lambda: _fetch(force=True))
        btn_download.clicked.connect(_download)
        _fetch()
        dlg.exec()

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

    def _run_convert(self, output_dir, file_prefix, selected_indices, resume, only_indices=None):
        self.is_converting = True
        if not resume:
            self._last_prefix = file_prefix
        elif output_dir != self._last_output_dir:
            self._last_prefix = None  # 续传另一个目录：原前缀未知
        self._last_output_dir = output_dir
        self._convert_start_time = time.time()
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._qb_convert.setEnabled(False)
        self._qb_pause.setEnabled(True)
        self._qb_resume.setEnabled(False)
        self._status_label.setText("正在分析文本并拆分..." if not resume else "准备继续转换...")
        self._set_controls_enabled(False)

        engine = self._get_current_engine()
        voice = get_voice_id(self._voice_combo.currentText(), engine)
        rate = self._get_rate_string()
        mode = self._mode_var
        time_min = self._time_slider.value()

        dia_enabled = self._dialogue_enabled_cb.isChecked()
        voice_map = None
        if dia_enabled:
            nv = get_voice_id(self._dialogue_narration_combo.currentText(), engine)
            dv = get_voice_id(self._dialogue_voice_combo.currentText(), engine)
            if nv and dv:
                voice_map = {"narration": nv, "dialogue": dv}
                speakers = self._collect_speaker_map(engine)
                if speakers:
                    voice_map["speakers"] = speakers

        self._convert_worker = ConvertWorker()
        self._convert_worker.setup(
            text=self._text_area.toPlainText().strip(), voice=voice, rate=rate,
            output_dir=output_dir, split_mode=mode, time_minutes=time_min,
            file_prefix=file_prefix, selected_indices=selected_indices,
            engine=engine, normalize_audio=self._normalize_cb.isChecked(),
            dialogue_detection=dia_enabled, voice_map=voice_map, resume=resume,
            generate_subtitles=self._subtitle_cb.isChecked(),
            write_metadata=self._metadata_cb.isChecked(),
            only_indices=only_indices,
        )
        self._convert_worker.item_event.connect(self._on_queue_event)
        self._convert_worker.progress_update.connect(self._on_convert_progress)
        self._convert_worker.finished_ok.connect(lambda files: self._on_convert_done(output_dir, files))
        self._convert_worker.error_occurred.connect(self._on_convert_error)
        self._convert_worker.finished.connect(self._on_convert_finished)
        self._queue_refresh_actions()
        # 生成期间编辑区用处不大，切到生成队列看逐章进度（底部暂停/继续按钮仍可用）
        self._tab_widget.setCurrentWidget(self._queue_tab)
        self._convert_worker.start()

    def _on_convert_progress(self, current, total):
        if total:
            self._progress_bar.setMaximum(total)
            self._progress_bar.setValue(current)
        elapsed = ""
        if hasattr(self, '_convert_start_time') and self._convert_start_time:
            mins = int((time.time() - self._convert_start_time) / 60)
            secs = int((time.time() - self._convert_start_time) % 60)
            elapsed = f"（已用 {mins} 分 {secs} 秒）" if mins else f"（已用 {secs} 秒）"
        self._status_label.setText(f"正在生成: {current}/{total} {elapsed}")

    def _on_convert_done(self, output_dir, files):
        self._progress_bar.setValue(self._progress_bar.maximum())
        elapsed = ""
        if hasattr(self, '_convert_start_time') and self._convert_start_time:
            mins = int((time.time() - self._convert_start_time) / 60)
            secs = int((time.time() - self._convert_start_time) % 60)
            elapsed = f"（用时 {mins} 分 {secs} 秒）" if mins else f"（用时 {secs} 秒）"
        count = len(files)
        self._status_label.setText(f"完成! 共生成 {count} 个文件 {elapsed}")
        if not files:
            self._status_label.setText("未生成文件，请查看日志了解详情")
            QMessageBox.warning(self, "生成结果",
                "未能生成任何文件。\n\n"
                "可能原因：\n"
                "  · 网络不通（Edge 引擎需要访问 speech.platform.bing.com）\n"
                "  · 引擎未就绪\n"
                "  · 文本内容为空\n\n"
                "请查看日志文件了解详细错误。")
            return
        names = "\n".join(os.path.basename(f) for f in files[:8])
        tail = f"\n  ... 等共 {count} 个文件" if count > 8 else ""
        failed = sum(1 for it in self._queue_items if it.get("status") == "error")
        fail_note = f"\n\n⚠ 另有 {failed} 个章节失败，可在「生成队列」页单独重试。" if failed else ""
        if failed:
            self._status_label.setText(f"完成 {count} 个，失败 {failed} 个（见「生成队列」）{elapsed}")
        if QMessageBox.question(self, "完成",
                                f"已生成 {count} 个MP3文件 {elapsed}:\n{names}{tail}{fail_note}\n\n是否打开文件夹？") == QMessageBox.StandardButton.Yes:
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
        self._qb_convert.setEnabled(True)
        self._qb_pause.setEnabled(False)
        self._qb_resume.setEnabled(False)
        self._set_controls_enabled(True)
        self._queue_refresh_actions()

    def _pause_convert(self):
        if self._convert_worker:
            self._convert_worker.request_stop()
        self._qb_pause.setEnabled(False)
        self._qb_resume.setEnabled(True)
        self._status_label.setText("正在暂停...")

    def _qb_resume_convert(self):
        if self._last_output_dir:
            items = load_progress(self._last_output_dir)
            if items:
                self._run_convert(self._last_output_dir, self._last_prefix or "有声读物", None, resume=True)
                return
        self._resume_convert()

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
        if hasattr(self, '_speaker_panel'):
            self._speaker_panel.setEnabled(enabled)
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
        paths, _ = QFileDialog.getOpenFileNames(self, "选择音频文件（可多选，批量转录）", "",
                                                 "音频文件 (*.mp3 *.wav *.m4a *.flac *.ogg *.aac *.wma);;所有文件 (*.*)")
        if not paths:
            return
        self._audio_file_paths = paths
        self._audio_file_path = paths[0]  # 兼容旧引用
        if len(paths) == 1:
            self._audio_file_label.setText(
                f"{os.path.basename(paths[0])} ({os.path.getsize(paths[0])/1024:.0f} KB)")
        else:
            total_mb = sum(os.path.getsize(p) for p in paths) / 1024 / 1024
            self._audio_file_label.setText(
                f"已选 {len(paths)} 个文件（共 {total_mb:.1f} MB，批量转录结果自动存到各文件旁）")

    def _start_asr(self):
        audio_paths = getattr(self, '_audio_file_paths', None) or (
            [self._audio_file_path] if getattr(self, '_audio_file_path', None) else [])
        if not audio_paths:
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
            # 外挂引擎：直接用线程调用（多文件时逐个转录并存到源文件旁）
            self._status_label.setText("外挂 ASR 引擎识别中...")
            batch = len(audio_paths) > 1
            ext_map = {"txt": ".txt", "srt": ".srt", "json": ".json"}
            def _ext_run():
                try:
                    results = []
                    for p in audio_paths:
                        result = external_asr_transcribe(
                            engine_id, p, output_format,
                            model="", language=language)
                        if batch:
                            out_p = os.path.splitext(p)[0] + ext_map.get(output_format, ".txt")
                            with open(out_p, "w", encoding="utf-8") as f:
                                f.write(result)
                            results.append(f"✅ {os.path.basename(p)} → {os.path.basename(out_p)}")
                        else:
                            results.append(result)
                    text = "批量转录完成:\n" + "\n".join(results) if batch else results[0]
                    self._asr_result.setPlainText(text)
                    self._asr_status.setText("识别完成")
                    self._asr_last_result = text
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

        compute_type = self._asr_compute_combo.currentData() or "default"
        self._asr_worker = AsrWorker()
        self._asr_worker.setup(audio_paths, get_storage_dir(), model_size, language,
                               output_format, compute_type=compute_type)
        self._asr_worker.progress_update.connect(lambda c, t: self._asr_status.setText(
            f"{getattr(self, '_asr_current_file', '')}识别进度: {c}/{t}"))
        self._asr_worker.file_progress.connect(self._on_asr_file_progress)
        self._asr_worker.finished_ok.connect(self._on_asr_done)
        self._asr_worker.error_occurred.connect(lambda e: (
            QMessageBox.critical(self, "错误", f"ASR 识别失败: {e}"),
            self._on_asr_finished()
        ))
        self._asr_worker.finished.connect(self._on_asr_finished)
        self._asr_worker.start()

    def _on_asr_file_progress(self, idx, total, filename):
        self._asr_current_file = f"[{idx}/{total}] {filename} " if total > 1 else ""
        if total > 1:
            self._asr_status.setText(f"正在转录 [{idx}/{total}]: {filename}")

    def _on_asr_done(self, result):
        self._asr_result.setPlainText(result)
        self._asr_status.setText("识别完成")
        self._asr_last_result = result
        self._asr_current_file = ""

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

    def _export_m4b(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择要合并为 m4b 的 MP3 文件（按文件名排序为章节顺序）", "",
                                                 "MP3文件 (*.mp3);;所有文件 (*.*)")
        if not files:
            return
        # 用文件夹名作为默认书名
        default_album = os.path.basename(os.path.dirname(files[0])) or "有声读物"
        out, _ = QFileDialog.getSaveFileName(self, "保存 m4b 有声书",
                                              f"{default_album}.m4b", "M4B 有声书 (*.m4b)")
        if not out:
            return
        sorted_files = sorted(files)
        self._status_label.setText("正在导出 m4b（重新编码为 AAC，可能需要几分钟）...")

        def _run():
            try:
                export_m4b(sorted_files, out, album=default_album)
                size_mb = os.path.getsize(out) / (1024 * 1024)
                self._status_label.setText(
                    f"m4b 导出完成: {os.path.basename(out)}（{len(sorted_files)} 章, {size_mb:.1f}MB）")
            except Exception as e:
                logger.error(f"m4b 导出失败: {e}", exc_info=True)
                self._status_label.setText(f"m4b 导出失败: {e}")

        threading.Thread(target=_run, daemon=True).start()

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
        # 停止所有后台 QThread，避免退出时 "QThread: Destroyed while thread is still running" 导致 abort()
        for attr in ("_convert_worker", "_preview_worker", "_stream_worker",
                     "_asr_worker", "_model_dl_worker", "_voice_dl_worker",
                     "_catalog_worker", "_wizard_dl_worker", "_wizard_preview_worker",
                     "_clone_check_worker", "_clone_preview_worker", "_clone_asr_worker",
                     "_clone_dl_worker"):
            w = getattr(self, attr, None)
            if w is None:
                continue
            try:
                if w.isRunning():
                    try:
                        w.request_stop()
                    except Exception:
                        pass
                    # 给 worker 最多 3 秒退出循环
                    if not w.wait(3000):
                        try:
                            w.terminate()
                            w.wait(1000)
                        except Exception:
                            pass
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
