"""程序内置音频播放器 - 优先 pygame.mixer.music，否则回退到外部播放器。"""

import logging
import os
import platform
import subprocess
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger("audiobook_converter")

# Windows 子进程隐藏控制台
if platform.system() == "Windows":
    _HIDDEN_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
else:
    _HIDDEN_FLAGS = 0


def _popen(cmd):
    """统一的隐藏控制台 Popen 包装"""
    if _HIDDEN_FLAGS:
        return subprocess.Popen(cmd, creationflags=_HIDDEN_FLAGS,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class AudioPlayer:
    """跨平台音频播放器，支持 MP3 / WAV，能 pause / resume / stop。

    使用顺序：
    1) pygame.mixer.music（首选，原生支持暂停）
    2) 外部播放器（无法暂停，仅作兜底）
    """

    def __init__(self, on_state_change: Optional[Callable[[str], None]] = None):
        self._pg = None
        self._pg_failed = False
        self._fallback_proc: Optional[subprocess.Popen] = None
        self._current_path: Optional[str] = None
        self._paused = False
        self._on_state_change = on_state_change
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_stop = threading.Event()
        # 流式队列
        self._stream_queue: list = []
        self._stream_lock = threading.Lock()
        self._streaming = False

    # ---------- 状态广播 ----------

    def _emit(self, state: str) -> None:
        if self._on_state_change:
            try:
                self._on_state_change(state)
            except Exception as e:
                logger.warning(f"播放状态回调异常: {e}")

    # ---------- pygame 初始化 ----------

    def _ensure_pygame(self) -> bool:
        if self._pg is not None:
            return True
        if self._pg_failed:
            return False
        try:
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            import pygame  # type: ignore
            pygame.mixer.pre_init(frequency=22050, size=-16, channels=2, buffer=1024)
            pygame.mixer.init()
            self._pg = pygame
            logger.info("pygame.mixer 初始化成功")
            return True
        except Exception as e:
            logger.warning(f"pygame 不可用，将回退到外部播放器: {e}")
            self._pg_failed = True
            return False

    @property
    def supports_pause(self) -> bool:
        """当前后端是否支持真正的暂停"""
        return self._ensure_pygame()

    # ---------- 公共 API ----------

    def play(self, path: str) -> None:
        """开始播放指定文件。若已有播放则先停止。"""
        self.stop()
        self._current_path = path
        self._paused = False

        if self._ensure_pygame():
            try:
                self._pg.mixer.music.load(path)
                self._pg.mixer.music.play()
                self._emit("playing")
                self._start_monitor()
                return
            except Exception as e:
                logger.error(f"pygame 播放失败，回退到外部播放器: {e}")
                # 回退一次后下次仍可走 pygame
                self._fallback_play(path)
                return

        self._fallback_play(path)

    def pause(self) -> bool:
        """暂停。返回是否成功。外部播放器后端不支持暂停时返回 False。"""
        if self._pg is not None and self._pg.mixer.music.get_busy() and not self._paused:
            try:
                self._pg.mixer.music.pause()
                self._paused = True
                self._emit("paused")
                return True
            except Exception as e:
                logger.warning(f"pygame 暂停失败: {e}")
                return False
        return False

    def resume(self) -> bool:
        if self._pg is not None and self._paused:
            try:
                self._pg.mixer.music.unpause()
                self._paused = False
                self._emit("playing")
                return True
            except Exception as e:
                logger.warning(f"pygame 继续失败: {e}")
                return False
        return False

    def enqueue(self, path: str) -> None:
        """流式追加一段。第一段会立即起播，后续段在前一段播完后自动接上。"""
        if not self._ensure_pygame():
            self._fallback_play(path)
            return
        with self._stream_lock:
            self._stream_queue.append(path)
            if not self._streaming:
                first = self._stream_queue.pop(0)
                self._streaming = True
                try:
                    self._pg.mixer.music.load(first)
                    self._pg.mixer.music.play()
                    self._current_path = first
                    self._paused = False
                    self._emit("playing")
                except Exception as e:
                    logger.error(f"流式起播失败: {e}")
                    self._streaming = False
                    return
                self._start_stream_monitor()

    def _start_stream_monitor(self):
        self._stop_monitor()
        self._monitor_stop.clear()

        def loop():
            queued: Optional[str] = None
            while not self._monitor_stop.is_set():
                try:
                    busy = self._pg.mixer.music.get_busy() or self._paused
                except Exception:
                    busy = False
                if not busy:
                    with self._stream_lock:
                        nxt = self._stream_queue.pop(0) if self._stream_queue else None
                    if nxt is not None:
                        try:
                            self._pg.mixer.music.load(nxt)
                            self._pg.mixer.music.play()
                            self._current_path = nxt
                            self._paused = False
                            queued = None
                        except Exception as e:
                            logger.error(f"流式接龙失败: {e}")
                            time.sleep(0.2)
                            continue
                    else:
                        time.sleep(0.25)
                        with self._stream_lock:
                            still_busy = bool(self._pg.mixer.music.get_busy())
                            if not still_busy and not self._stream_queue:
                                self._streaming = False
                                self._emit("ended")
                                return
                        continue
                else:
                    # 提前预排下一首
                    if queued is None:
                        with self._stream_lock:
                            if self._stream_queue:
                                queued = self._stream_queue.pop(0)
                        if queued is not None:
                            try:
                                self._pg.mixer.music.queue(queued)
                            except Exception as e:
                                logger.warning(f"queue 失败: {e}")
                                with self._stream_lock:
                                    self._stream_queue.insert(0, queued)
                                queued = None
                time.sleep(0.15)

        t = threading.Thread(target=loop, daemon=True)
        self._monitor_thread = t
        t.start()

    def stop(self) -> None:
        # pygame 后端
        if self._pg is not None:
            try:
                self._pg.mixer.music.stop()
            except Exception:
                pass
        with self._stream_lock:
            self._stream_queue.clear()
            self._streaming = False
        self._stop_monitor()
        # 外部播放器后端
        if self._fallback_proc is not None:
            try:
                if self._fallback_proc.poll() is None:
                    self._fallback_proc.terminate()
                    try:
                        self._fallback_proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self._fallback_proc.kill()
            except Exception:
                pass
            self._fallback_proc = None
        self._paused = False
        self._emit("stopped")

    def is_playing(self) -> bool:
        if self._pg is not None:
            try:
                return bool(self._pg.mixer.music.get_busy()) and not self._paused
            except Exception:
                return False
        if self._fallback_proc is not None:
            return self._fallback_proc.poll() is None
        return False

    def is_paused(self) -> bool:
        return self._paused

    # ---------- 内部 ----------

    def _start_monitor(self):
        self._stop_monitor()
        self._monitor_stop.clear()

        def loop():
            assert self._pg is not None
            while not self._monitor_stop.is_set():
                try:
                    busy = self._pg.mixer.music.get_busy()
                except Exception:
                    busy = False
                # 暂停状态下 get_busy 在不同 SDL 版本表现不一致，结合 _paused 判断
                if not busy and not self._paused:
                    self._emit("ended")
                    return
                time.sleep(0.2)

        t = threading.Thread(target=loop, daemon=True)
        self._monitor_thread = t
        t.start()

    def _stop_monitor(self):
        self._monitor_stop.set()
        self._monitor_thread = None

    def _fallback_play(self, path: str) -> None:
        """系统默认播放器（无法暂停）。仅在 pygame 不可用时使用。"""
        try:
            system = platform.system()
            if system == "Darwin":
                self._fallback_proc = _popen(["afplay", path])
            elif system == "Windows":
                # os.startfile 会启动外部程序但不返回句柄，无法停止；
                # 优先尝试用 ffplay，找不到再退回 startfile。
                from shutil import which
                ffplay = which("ffplay")
                if ffplay:
                    self._fallback_proc = _popen(
                        [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", path]
                    )
                else:
                    os.startfile(path)  # type: ignore[attr-defined]
                    self._fallback_proc = None
            else:
                from shutil import which
                player = which("ffplay") or which("mpv") or which("mplayer") or which("aplay")
                if player and "ffplay" in player:
                    self._fallback_proc = _popen(
                        [player, "-nodisp", "-autoexit", "-loglevel", "quiet", path]
                    )
                elif player and "mpv" in player:
                    self._fallback_proc = _popen([player, "--no-video", path])
                elif player and "mplayer" in player:
                    self._fallback_proc = _popen([player, "-really-quiet", path])
                elif player and "aplay" in player and path.lower().endswith(".wav"):
                    self._fallback_proc = _popen([player, path])
                else:
                    _popen(["xdg-open", path])
                    self._fallback_proc = None
            self._emit("playing")
        except Exception as e:
            logger.error(f"外部播放器启动失败: {e}")
            self._emit("error")
