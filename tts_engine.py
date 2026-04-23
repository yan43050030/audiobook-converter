"""TTS引擎封装 - 支持 edge-tts（联网）、系统语音（跨平台离线）、Piper（离线高质量） v2.5.0"""

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import wave
from shutil import which
from typing import Callable, List, Optional

import edge_tts

# 可选导入 - Piper TTS和音频处理
try:
    from piper import PiperVoice
    PIPER_PYTHON_AVAILABLE = True
except ImportError:
    PIPER_PYTHON_AVAILABLE = False
    PiperVoice = None

# Piper 新 API (1.3.0+) 的 SynthesisConfig（可选）
try:
    from piper import SynthesisConfig as _PiperSynthesisConfig
except Exception:
    _PiperSynthesisConfig = None

try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False
    AudioSegment = None

VERSION = "2.5.0"

# 当前平台
_PLATFORM = platform.system()  # "Darwin" / "Windows" / "Linux"

# ======== 日志 ========

LOG_PATH = os.path.join(tempfile.gettempdir(), "audiobook_converter.log")
logger = logging.getLogger("audiobook_converter")
logger.setLevel(logging.DEBUG)

_fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fh)

_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)
_ch.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(_ch)

logger.info(f"=== Audiobook Converter v{VERSION} 启动 ===")
logger.info(f"日志文件: {LOG_PATH}")
logger.info(f"运行平台: {_PLATFORM}")


# ======== 应用配置（便携存储目录） ========

DEFAULT_STORAGE_DIR = os.path.join(os.path.expanduser("~"), ".audiobook_converter")
CONFIG_PATH = os.path.join(DEFAULT_STORAGE_DIR, "config.json")

_config_cache: Optional[dict] = None


def _load_config() -> dict:
    """加载配置文件"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                _config_cache = json.load(f) or {}
        else:
            _config_cache = {}
    except Exception as e:
        logger.warning(f"读取配置失败，使用默认值: {e}")
        _config_cache = {}
    return _config_cache


def _save_config(cfg: dict) -> None:
    """保存配置文件"""
    global _config_cache
    _config_cache = cfg
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存配置失败: {e}")


def get_storage_dir() -> str:
    """获取当前存储目录（便携模式优先，否则默认用户主目录）"""
    cfg = _load_config()
    path = cfg.get("storage_dir", "").strip()
    if path and os.path.isdir(path):
        return path
    # 默认：~/.audiobook_converter
    os.makedirs(DEFAULT_STORAGE_DIR, exist_ok=True)
    return DEFAULT_STORAGE_DIR


def set_storage_dir(path: str) -> None:
    """设置便携存储目录。传空字符串恢复默认。"""
    cfg = _load_config()
    if path:
        path = os.path.abspath(path)
        os.makedirs(path, exist_ok=True)
        cfg["storage_dir"] = path
    else:
        cfg.pop("storage_dir", None)
    _save_config(cfg)
    _invalidate_scan_cache()
    logger.info(f"存储目录已更新: {get_storage_dir()}")


def get_piper_model_dir() -> str:
    """Piper 模型目录（跟随存储目录）"""
    path = os.path.join(get_storage_dir(), "piper-models")
    os.makedirs(path, exist_ok=True)
    return path


def get_portable_bin_dir() -> str:
    """便携可执行文件目录。用户可将 ffmpeg、piper CLI 等放入此目录。"""
    path = os.path.join(get_storage_dir(), "bin")
    os.makedirs(path, exist_ok=True)
    return path


# 扫描缓存：避免重复递归大目录；存储目录变更时自动失效
_scan_cache: dict = {"storage_dir": None, "found": {}, "models": None}


def _invalidate_scan_cache() -> None:
    _scan_cache["storage_dir"] = None
    _scan_cache["found"] = {}
    _scan_cache["models"] = None


def _search_in_tree(root_dir: str, target_names: List[str], max_depth: int = 4) -> Optional[str]:
    """在根目录及其子目录（有限深度）中递归查找文件，返回首个匹配的绝对路径"""
    if not root_dir or not os.path.isdir(root_dir):
        return None
    root_dir = os.path.abspath(root_dir)
    lowered = {n.lower() for n in target_names}
    try:
        for dirpath, _dirs, files in os.walk(root_dir, followlinks=False):
            # 限制递归深度
            rel_depth = dirpath[len(root_dir):].count(os.sep)
            if rel_depth > max_depth:
                _dirs[:] = []
                continue
            for fn in files:
                if fn.lower() in lowered:
                    return os.path.join(dirpath, fn)
    except Exception as e:
        logger.warning(f"搜索 {root_dir} 失败: {e}")
    return None


def _search_models_in_tree(root_dir: str, max_depth: int = 4) -> List[str]:
    """在根目录及其子目录中查找 Piper .onnx 模型文件"""
    results = []
    if not root_dir or not os.path.isdir(root_dir):
        return results
    root_dir = os.path.abspath(root_dir)
    try:
        for dirpath, _dirs, files in os.walk(root_dir, followlinks=False):
            rel_depth = dirpath[len(root_dir):].count(os.sep)
            if rel_depth > max_depth:
                _dirs[:] = []
                continue
            for fn in files:
                if fn.lower().endswith(".onnx"):
                    results.append(os.path.join(dirpath, fn))
    except Exception as e:
        logger.warning(f"搜索模型 {root_dir} 失败: {e}")
    return results


def _which_portable(name: str) -> Optional[str]:
    """查找顺序：便携 bin 目录 → 便携存储目录递归 → 系统 PATH。结果缓存直到目录变更。"""
    storage = get_storage_dir()
    if _scan_cache["storage_dir"] != storage:
        _scan_cache["storage_dir"] = storage
        _scan_cache["found"] = {}
        _scan_cache["models"] = None

    if name in _scan_cache["found"]:
        cached = _scan_cache["found"][name]
        # 若缓存路径已不存在则重新查找
        if cached is None or os.path.isfile(cached):
            return cached

    candidates = [name]
    if _PLATFORM == "Windows" and not name.lower().endswith(".exe"):
        candidates.insert(0, name + ".exe")

    bin_dir = get_portable_bin_dir()
    result: Optional[str] = None
    # 1) bin/ 直接命中
    for cand in candidates:
        p = os.path.join(bin_dir, cand)
        if os.path.isfile(p):
            if _PLATFORM == "Windows" or os.access(p, os.X_OK):
                result = p
                break

    # 2) 存储目录递归搜索
    if result is None:
        result = _search_in_tree(storage, candidates, max_depth=4)

    # 3) 系统 PATH
    if result is None:
        for cand in candidates:
            p = which(cand)
            if p:
                result = p
                break

    _scan_cache["found"][name] = result
    return result


def scan_storage_dependencies() -> dict:
    """扫描便携存储目录，汇总依赖与语音包现状"""
    storage = get_storage_dir()
    ffmpeg = _which_portable("ffmpeg")
    ffprobe = _which_portable("ffprobe")
    piper_cli = _which_portable("piper")

    # 先找已预置的 Piper 模型目录（piper-models/），再递归全目录
    piper_model_dir = get_piper_model_dir()
    models: List[str] = []
    seen = set()
    for candidate in _search_models_in_tree(piper_model_dir) + _search_models_in_tree(storage):
        key = os.path.abspath(candidate)
        if key not in seen:
            seen.add(key)
            models.append(candidate)

    missing: List[str] = []
    if ffmpeg is None:
        missing.append("ffmpeg（Piper/本地语音 MP3 转换依赖）")
    if not PIPER_PYTHON_AVAILABLE and piper_cli is None:
        missing.append("Piper 可执行文件（pip 未装 piper-tts 时需外置 piper）")
    if not models:
        missing.append("Piper 语音包（.onnx 模型）")

    return {
        "storage_dir": storage,
        "bin_dir": get_portable_bin_dir(),
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "piper_python": PIPER_PYTHON_AVAILABLE,
        "piper_cli": piper_cli,
        "piper_models": models,
        "missing": missing,
    }


def _refresh_piper_cli_path() -> Optional[str]:
    """每次使用时重新解析 piper CLI 路径（便携目录可能动态变更）"""
    return _which_portable("piper")


# ======== 通用配置 ========

EDGE_VOICES = {
    "晓晓（女声，自然）": "zh-CN-XiaoxiaoNeural",
    "云希（男声，自然）": "zh-CN-YunxiNeural",
    "云健（男声，播音）": "zh-CN-YunjianNeural",
    "晓伊（女声，活泼）": "zh-CN-XiaoyiNeural",
    "云扬（男声，温暖）": "zh-CN-YunyangNeural",
    "晓辰（女声，温柔）": "zh-CN-XiaochenNeural",
}
EDGE_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


# ======== 跨平台本地系统语音 ========

# 运行时诊断信息，便于 UI 展示具体失败原因
_LOCAL_UNAVAILABLE_REASON: str = ""


def _detect_local_voices_macos() -> dict:
    """macOS: 通过 say -v ? 枚举中文语音"""
    voices: dict = {}
    say_path = which("say")
    if not say_path:
        return voices
    try:
        result = subprocess.run([say_path, "-v", "?"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            match = re.match(r'^(\S+)\s+\([^)]*\)\s+(zh_CN|zh_TW|zh_HK)', line)
            if match:
                voice_name = match.group(1)
                lang = match.group(2)
                display = f"{voice_name}（中文）" if lang == "zh_CN" else f"{voice_name}（{lang}）"
                voices[display] = voice_name
    except Exception as e:
        logger.warning(f"macOS 语音检测失败: {e}")
    return voices


_POWERSHELL_LIST_VOICES = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$s.GetInstalledVoices() | ForEach-Object { "
    "  $i = $_.VoiceInfo; "
    "  \"$($i.Name)|$($i.Culture.Name)|$($i.Gender)\" "
    "}"
)


def _detect_local_voices_windows() -> dict:
    """Windows: 通过 PowerShell 枚举 SAPI 已安装语音，筛选中文"""
    voices: dict = {}
    ps = which("powershell") or which("pwsh")
    if not ps:
        return voices
    try:
        result = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command", _POWERSHELL_LIST_VOICES],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            logger.warning(f"PowerShell 枚举语音失败: {result.stderr.strip()}")
            return voices
        for line in result.stdout.splitlines():
            parts = line.strip().split("|")
            if len(parts) < 2:
                continue
            name, culture = parts[0].strip(), parts[1].strip()
            if not name:
                continue
            if culture.lower().startswith("zh"):
                gender = parts[2].strip() if len(parts) > 2 else ""
                gender_tag = "女声" if "Female" in gender else ("男声" if "Male" in gender else "")
                display = f"{name}（{culture}{('，' + gender_tag) if gender_tag else ''}）"
                voices[display] = name
    except Exception as e:
        logger.warning(f"Windows 语音检测失败: {e}")
    return voices


def _detect_local_voices_linux() -> dict:
    """Linux: 通过 espeak-ng --voices 枚举中文语音"""
    voices: dict = {}
    espeak = which("espeak-ng") or which("espeak")
    if not espeak:
        return voices
    try:
        result = subprocess.run(
            [espeak, "--voices=zh"], capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines()[1:]:  # 跳过标题
            cols = line.split()
            if len(cols) >= 4:
                lang = cols[1]
                voice_id = cols[3]
                if lang.lower().startswith("zh"):
                    display = f"{voice_id}（{lang}）"
                    voices[display] = voice_id
        # 至少保留一个默认选项
        if not voices:
            voices["espeak-ng 中文"] = "zh"
    except Exception as e:
        logger.warning(f"Linux 语音检测失败: {e}")
    return voices


def _detect_local_voices() -> dict:
    """根据当前平台检测可用的本地语音，并记录不可用原因"""
    global _LOCAL_UNAVAILABLE_REASON
    _LOCAL_UNAVAILABLE_REASON = ""

    if _PLATFORM == "Darwin":
        voices = _detect_local_voices_macos()
        if not voices:
            _LOCAL_UNAVAILABLE_REASON = (
                "macOS 未检测到中文语音。\n"
                "请在 系统设置 → 辅助功能 → 语音内容 → 系统语音 中下载中文语音包。"
            )
        return voices

    if _PLATFORM == "Windows":
        ps = which("powershell") or which("pwsh")
        if not ps:
            _LOCAL_UNAVAILABLE_REASON = "未找到 powershell，无法调用 Windows 系统语音。"
            return {}
        voices = _detect_local_voices_windows()
        if not voices:
            _LOCAL_UNAVAILABLE_REASON = (
                "Windows 未检测到中文语音。\n"
                "请在 设置 → 时间和语言 → 语言 → 添加中文 → 安装语音包；\n"
                "或 设置 → 辅助功能 → 讲述人 → 添加自然语音 中下载中文语音。"
            )
        return voices

    if _PLATFORM == "Linux":
        espeak = which("espeak-ng") or which("espeak")
        if not espeak:
            _LOCAL_UNAVAILABLE_REASON = (
                "未检测到 espeak-ng。请安装：\n"
                "  Debian/Ubuntu: sudo apt install espeak-ng\n"
                "  Fedora:        sudo dnf install espeak-ng\n"
                "  Arch:          sudo pacman -S espeak-ng"
            )
            return {}
        return _detect_local_voices_linux()

    _LOCAL_UNAVAILABLE_REASON = f"暂不支持的平台：{_PLATFORM}"
    return {}


def refresh_local_voices() -> None:
    """重新扫描本地语音（在用户安装语音包后可手动触发）"""
    global LOCAL_VOICES, LOCAL_DEFAULT_VOICE
    LOCAL_VOICES = _detect_local_voices()
    LOCAL_DEFAULT_VOICE = list(LOCAL_VOICES.values())[0] if LOCAL_VOICES else ""


LOCAL_VOICES = _detect_local_voices()
LOCAL_DEFAULT_VOICE = list(LOCAL_VOICES.values())[0] if LOCAL_VOICES else ""

PIPER_VOICES = {
    "Piper中文女声（中等质量）": "zh_CN-huayan-medium",
    "Piper中文女声（较低质量）": "zh_CN-huayan-low",
}
PIPER_DEFAULT_VOICE = "zh_CN-huayan-medium"

# Piper模型下载URL（Hugging Face）
PIPER_MODEL_URLS = {
    "zh_CN-huayan-medium": "https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx",
    "zh_CN-huayan-low": "https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/low/zh_CN-huayan-low.onnx",
}
# 模型配置URL（与模型文件同名，扩展名.json）
PIPER_CONFIG_URLS = {
    "zh_CN-huayan-medium": "https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx.json",
    "zh_CN-huayan-low": "https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/low/zh_CN-huayan-low.onnx.json",
}

# 国内镜像
HF_MIRROR_DOMAIN = "hf-mirror.com"
HF_ORIGIN_DOMAIN = "huggingface.co"


def _get_mirror_url(url: str) -> str:
    """将HuggingFace URL替换为hf-mirror镜像"""
    return url.replace(HF_ORIGIN_DOMAIN, HF_MIRROR_DOMAIN)


# Piper运行模式
PIPER_MODE_PYTHON = "python"
PIPER_MODE_CLI = "cli"

CHARS_PER_SECOND_BASE = 2.5
PROGRESS_FILENAME = ".audiobook_progress.json"
MAX_RETRIES = 3  # 生成失败时重试次数
RETRY_DELAY = 5  # 重试前等待秒数（指数退避基础值）
CONCURRENCY = 3  # 同时处理的章节数（降低并发避免触发限流/网络拥塞）


# ======== 下载进度监听器（供 UI 订阅） ========

DownloadProgressCallback = Callable[[str, int, int], None]
# 签名: callback(description, current_bytes, total_bytes)
# total_bytes <= 0 表示未知，current_bytes == total_bytes 表示完成

_download_listeners: List[DownloadProgressCallback] = []


def add_download_listener(cb: DownloadProgressCallback) -> None:
    """订阅下载进度事件（UI 用）"""
    if cb not in _download_listeners:
        _download_listeners.append(cb)


def remove_download_listener(cb: DownloadProgressCallback) -> None:
    if cb in _download_listeners:
        _download_listeners.remove(cb)


def _notify_download(description: str, current: int, total: int) -> None:
    for cb in list(_download_listeners):
        try:
            cb(description, current, total)
        except Exception as e:
            logger.warning(f"下载监听器异常: {e}")


# ======== 可中断辅助（暂停支持） ========

class StopRequested(Exception):
    """用户请求暂停时抛出，由调度层捕获以跳过后续处理"""


def _interruptible_sleep(total_seconds: float, should_stop=None, step: float = 0.2) -> bool:
    """将长 sleep 切成小段，检测到 should_stop 立刻返回。返回 True 表示被中断。"""
    elapsed = 0.0
    while elapsed < total_seconds:
        if should_stop and should_stop():
            return True
        time.sleep(min(step, total_seconds - elapsed))
        elapsed += step
    return False


def _run_subprocess_interruptible(cmd, should_stop=None, input_bytes: Optional[bytes] = None,
                                  timeout: Optional[float] = None, poll_interval: float = 0.2,
                                  **popen_kwargs):
    """以 Popen 启动子进程，支持在运行中响应 should_stop 立刻终止。"""
    popen_kwargs.setdefault("stdout", subprocess.PIPE)
    popen_kwargs.setdefault("stderr", subprocess.PIPE)
    if input_bytes is not None:
        popen_kwargs.setdefault("stdin", subprocess.PIPE)

    proc = subprocess.Popen(cmd, **popen_kwargs)

    # 写 stdin（阻塞但通常很短）
    if input_bytes is not None and proc.stdin:
        try:
            proc.stdin.write(input_bytes)
            proc.stdin.close()
        except Exception:
            pass

    start = time.monotonic()
    while True:
        rc = proc.poll()
        if rc is not None:
            break
        if should_stop and should_stop():
            logger.info(f"用户暂停，终止子进程: {cmd[0] if cmd else '?'}")
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            finally:
                raise StopRequested("用户暂停")
        if timeout is not None and (time.monotonic() - start) > timeout:
            proc.kill()
            raise subprocess.TimeoutExpired(cmd, timeout)
        time.sleep(poll_interval)

    stdout, stderr = b"", b""
    try:
        stdout = proc.stdout.read() if proc.stdout else b""
        stderr = proc.stderr.read() if proc.stderr else b""
    except Exception:
        pass
    return proc.returncode, stdout, stderr


# ======== 工具函数 ========

def get_voice_list(engine="edge"):
    if engine == "local":
        return list(LOCAL_VOICES.keys())
    elif engine == "piper":
        return list(PIPER_VOICES.keys())
    return list(EDGE_VOICES.keys())


def get_voice_id(display_name, engine="edge"):
    if engine == "local":
        return LOCAL_VOICES.get(display_name, LOCAL_DEFAULT_VOICE)
    elif engine == "piper":
        return PIPER_VOICES.get(display_name, PIPER_DEFAULT_VOICE)
    return EDGE_VOICES.get(display_name, EDGE_DEFAULT_VOICE)


def _piper_available() -> bool:
    """动态检测 Piper 是否可用（Python 包或便携 / PATH 中的 CLI）"""
    if PIPER_PYTHON_AVAILABLE:
        return True
    return _refresh_piper_cli_path() is not None


def _ffmpeg_path() -> Optional[str]:
    """动态解析 ffmpeg 路径（便携目录优先）"""
    return _which_portable("ffmpeg")


def _ffmpeg_install_hint() -> str:
    if _PLATFORM == "Darwin":
        return "macOS: brew install ffmpeg"
    if _PLATFORM == "Windows":
        return "Windows: 从 https://www.gyan.dev/ffmpeg/builds/ 下载 ffmpeg.exe，放入便携 bin 目录或系统 PATH"
    return "Linux: sudo apt install ffmpeg（或对应发行版包管理器）"


def _piper_install_hint() -> str:
    return (
        "方式 1: pip install piper-tts\n"
        "方式 2: 从 https://github.com/rhasspy/piper/releases 下载 piper 可执行文件，\n"
        "        放入便携 bin 目录或系统 PATH"
    )


def check_engine_ready(engine="edge"):
    """检测引擎是否可用，返回 (ready: bool, message: str)"""
    if engine == "edge":
        return True, "Edge TTS 可用（微软在线语音，需要联网）"

    if engine == "local":
        if LOCAL_VOICES:
            plat_name = {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}.get(_PLATFORM, _PLATFORM)
            return True, f"{plat_name} 系统语音可用（{len(LOCAL_VOICES)} 个中文语音）"
        reason = _LOCAL_UNAVAILABLE_REASON or "未检测到中文系统语音"
        return False, f"本地语音不可用：\n{reason}"

    if engine == "piper":
        if not _piper_available():
            return False, "Piper 未安装。\n" + _piper_install_hint()
        if _ffmpeg_path() is None:
            return False, "ffmpeg 未安装（Piper 需要 ffmpeg 转换音频）。\n" + _ffmpeg_install_hint()
        return True, "Piper 可用（首次使用将自动下载语音模型）"

    return False, f"未知引擎: {engine}"


def estimate_duration(text, rate="+0%"):
    rate_val = int(rate.replace("%", "").replace("+", ""))
    speed_factor = 1 + rate_val / 100.0
    cps = CHARS_PER_SECOND_BASE * speed_factor
    if cps <= 0:
        cps = 0.5
    return len(text) / cps


def sanitize_filename(name):
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    name = name.strip(". ")
    return name[:80] if name else "untitled"


def _configure_pydub_ffmpeg() -> bool:
    """让 pydub 使用便携 bin 目录中的 ffmpeg/ffprobe（若存在）"""
    if not PYDUB_AVAILABLE:
        return False
    ff = _ffmpeg_path()
    if ff is None:
        return False
    try:
        # 让 pydub 知道 ffmpeg 具体位置
        AudioSegment.converter = ff
        ffprobe = _which_portable("ffprobe")
        if ffprobe:
            AudioSegment.ffprobe = ffprobe
    except Exception as e:
        logger.warning(f"pydub ffmpeg 配置失败: {e}")
    return True


# ======== 章节识别 ========

CHAPTER_PATTERNS = [
    re.compile(r'^(第[一二三四五六七八九十百千\d]+\s*[章节回顾卷集篇幕话])', re.MULTILINE),
    re.compile(r'^(序章|楔子|引子|尾声|后记|番外[篇]?)\s*[：:\s]*', re.MULTILINE),
]


def detect_chapters(text):
    lines = text.split("\n")
    chapter_starts = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        for pat in CHAPTER_PATTERNS:
            if pat.search(stripped):
                chapter_starts.append((i, stripped))
                break

    if not chapter_starts:
        return [{"title": "全文", "start": 0, "end": len(text), "text": text}]

    chapters = []
    for idx, (line_idx, title) in enumerate(chapter_starts):
        char_start = sum(len(lines[j]) + 1 for j in range(line_idx))
        if idx + 1 < len(chapter_starts):
            char_end = sum(len(lines[j]) + 1 for j in range(chapter_starts[idx + 1][0]))
        else:
            char_end = len(text)
        chapters.append({
            "title": title,
            "start": char_start,
            "end": char_end,
            "text": text[char_start:char_end].strip(),
        })
    return chapters


# ======== 文本分段 ========

def split_text(text, max_length=3000):
    if len(text) <= max_length:
        return [text]

    paragraphs = text.split("\n")
    segments, current = [], ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            if current:
                segments.append(current)
                current = ""
            continue
        if len(current) + len(para) + 1 <= max_length:
            current = current + "\n" + para if current else para
        else:
            if current:
                segments.append(current)
            if len(para) > max_length:
                parts = _split_by_sentences(para, max_length)
                segments.extend(parts[:-1])
                current = parts[-1] if parts else ""
            else:
                current = para

    if current:
        segments.append(current)
    return segments


def _split_by_sentences(text, max_length):
    sentences = re.split(r'([。！？；])', text)
    segments, current = [], ""
    i = 0
    while i < len(sentences):
        sentence = sentences[i]
        if i + 1 < len(sentences) and sentences[i + 1] in "。！？；":
            sentence += sentences[i + 1]
            i += 2
        else:
            i += 1
        if len(current) + len(sentence) <= max_length:
            current += sentence
        else:
            if current:
                segments.append(current)
            current = sentence
    if current:
        segments.append(current)
    return segments


def split_by_duration(chapter_text, max_seconds, rate="+0%"):
    rate_val = int(rate.replace("%", "").replace("+", ""))
    max_chars = int(max_seconds * CHARS_PER_SECOND_BASE * (1 + rate_val / 100.0))
    max_chars = max(max_chars, 500)
    return split_text(chapter_text, max_length=max_chars)


# ======== Piper 模型缓存 ========

_piper_model_cache: dict = {}


def _get_piper_mode() -> str:
    """确定Piper运行模式：优先Python包，其次CLI（便携目录优先）"""
    if PIPER_PYTHON_AVAILABLE:
        return PIPER_MODE_PYTHON
    if _refresh_piper_cli_path():
        return PIPER_MODE_CLI
    raise RuntimeError("Piper TTS 不可用。\n" + _piper_install_hint())


def _load_piper_model(voice_name, should_stop=None):
    """加载Piper模型，使用缓存避免重复加载"""
    model_path = _ensure_piper_model(voice_name, should_stop=should_stop)
    config_path = model_path + ".json"
    cache_key = (voice_name, model_path)

    if cache_key in _piper_model_cache:
        logger.debug(f"使用缓存的Piper模型: {voice_name}")
        return _piper_model_cache[cache_key]

    mode = _get_piper_mode()

    if mode == PIPER_MODE_PYTHON:
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Piper配置文件不存在: {config_path}")
        voice_model = PiperVoice.load(model_path, config_path)
        _piper_model_cache[cache_key] = voice_model
        logger.info(f"Piper模型已加载并缓存: {voice_name}")
        return voice_model
    else:
        # CLI模式：仅验证文件存在，不加载到内存
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Piper配置文件不存在: {config_path}")
        _piper_model_cache[cache_key] = None  # CLI模式缓存标记
        logger.info(f"Piper CLI模式就绪: {voice_name}")
        return None


def _unload_piper_model(voice_name=None):
    """卸载Piper模型，释放内存"""
    global _piper_model_cache
    if voice_name is None:
        _piper_model_cache.clear()
        logger.info("已卸载所有Piper模型")
    else:
        model_path = _get_piper_model_path(voice_name)
        cache_key = (voice_name, model_path)
        if cache_key in _piper_model_cache:
            del _piper_model_cache[cache_key]
            logger.info(f"已卸载Piper模型: {voice_name}")


# ======== Piper TTS 引擎 ========

def _get_piper_model_path(voice_name):
    """获取Piper模型文件路径（跟随当前存储目录）"""
    model_dir = get_piper_model_dir()
    model_file = f"{voice_name}.onnx"
    return os.path.join(model_dir, model_file)


def _download_file_with_progress(url, filepath, description="", timeout=60, should_stop=None):
    """下载文件并显示进度，支持镜像回退"""
    urls_to_try = [url, _get_mirror_url(url)]
    last_exception = None

    for try_url in urls_to_try:
        if should_stop and should_stop():
            raise StopRequested("用户暂停")
        try:
            _download_file_single(try_url, filepath, description, timeout, should_stop=should_stop)
            return
        except StopRequested:
            raise
        except Exception as e:
            last_exception = e
            logger.warning(f"下载失败，尝试镜像: {try_url} -> {e}")

    raise RuntimeError(f"所有下载源均失败: {last_exception}")


def _download_file_single(url, filepath, description="", timeout=60, should_stop=None):
    """单次下载实现，支持断点续传，同时向 UI 监听器推送进度"""
    import requests
    from tqdm import tqdm

    headers = {}
    existing_size = 0
    mode = "wb"

    if os.path.exists(filepath):
        existing_size = os.path.getsize(filepath)
        headers["Range"] = f"bytes={existing_size}-"
        mode = "ab"
        logger.info(f"断点续传 {description}: {filepath} (已下载 {existing_size} bytes)")

    logger.info(f"下载 {description}: {url}")
    response = requests.get(url, stream=True, headers=headers, timeout=timeout)

    # 如果服务器不支持Range且文件已存在，重新下载
    if response.status_code == 416:  # Range Not Satisfiable
        logger.info("服务器不支持断点续传，重新下载")
        os.remove(filepath)
        response = requests.get(url, stream=True, timeout=timeout)
        mode = "wb"
        existing_size = 0
    elif response.status_code == 200 and existing_size > 0 and "Content-Range" not in response.headers:
        # 服务器忽略Range，返回完整内容
        logger.info("服务器忽略Range头，重新下载")
        os.remove(filepath)
        mode = "wb"
        existing_size = 0
    else:
        response.raise_for_status()

    total_size = int(response.headers.get('content-length', 0))
    if response.status_code == 206:
        total_size += existing_size

    block_size = 8192
    current = existing_size
    last_notify = 0.0
    _notify_download(description, current, total_size)

    with open(filepath, mode) as f, tqdm(
        desc=description,
        total=total_size,
        initial=existing_size,
        unit='B',
        unit_scale=True,
        unit_divisor=1024,
    ) as pbar:
        for chunk in response.iter_content(chunk_size=block_size):
            if should_stop and should_stop():
                try:
                    response.close()
                except Exception:
                    pass
                raise StopRequested("用户暂停")
            if chunk:
                f.write(chunk)
                pbar.update(len(chunk))
                current += len(chunk)
                now = time.monotonic()
                if now - last_notify >= 0.2 or current == total_size:
                    _notify_download(description, current, total_size)
                    last_notify = now

    _notify_download(description, total_size or current, total_size or current)
    logger.info(f"下载完成: {filepath}")


def _download_piper_model(voice_name, should_stop=None):
    """下载Piper模型文件"""
    if voice_name not in PIPER_MODEL_URLS:
        raise ValueError(f"不支持的Piper语音: {voice_name}")

    model_url = PIPER_MODEL_URLS[voice_name]
    model_path = _get_piper_model_path(voice_name)
    config_url = PIPER_CONFIG_URLS[voice_name]
    config_path = model_path + ".json"

    _download_file_with_progress(model_url, model_path, f"Piper模型 {voice_name}", should_stop=should_stop)
    _download_file_with_progress(config_url, config_path, f"Piper配置 {voice_name}", should_stop=should_stop)

    logger.info(f"Piper模型下载完成: {voice_name}")


def _ensure_piper_model(voice_name, should_stop=None):
    """确保Piper模型存在，如果不存在则下载"""
    model_path = _get_piper_model_path(voice_name)
    if not os.path.exists(model_path):
        logger.info(f"模型不存在，开始下载: {voice_name}")
        _download_piper_model(voice_name, should_stop=should_stop)
    return model_path


def _convert_wav_to_mp3(wav_path, mp3_path=None):
    """将WAV文件转换为MP3格式"""
    if not PYDUB_AVAILABLE:
        raise ImportError("pydub库未安装，无法转换音频格式")

    if mp3_path is None:
        mp3_path = wav_path.replace('.wav', '.mp3')

    audio = AudioSegment.from_wav(wav_path)
    audio.export(mp3_path, format="mp3", bitrate="128k")
    return mp3_path


def _piper_generate_cli(text, voice_name, speed, wav_path, should_stop=None):
    """使用piper CLI命令生成音频（可中断）"""
    model_path = _get_piper_model_path(voice_name)
    config_path = model_path + ".json"
    cli_path = _refresh_piper_cli_path()
    if not cli_path:
        raise RuntimeError("Piper CLI 不可用。\n" + _piper_install_hint())

    cmd = [
        cli_path,
        "--model", model_path,
        "--config", config_path,
        "--output_file", wav_path,
    ]
    if speed != 1.0:
        cmd.extend(["--length_scale", str(1.0 / speed)])

    rc, _out, err = _run_subprocess_interruptible(
        cmd, should_stop=should_stop, input_bytes=text.encode("utf-8"),
    )
    if rc != 0:
        stderr = err.decode("utf-8", errors="replace") if err else ""
        raise RuntimeError(f"Piper CLI失败 (code {rc}): {stderr}")


def _piper_sample_rate(voice_model) -> int:
    """从 Piper 模型读取采样率，兼容新旧属性路径"""
    cfg = getattr(voice_model, "config", None)
    if cfg is None:
        return 22050
    for attr in ("sample_rate", "sampling_rate"):
        v = getattr(cfg, attr, None)
        if isinstance(v, int) and v > 0:
            return v
    audio_cfg = getattr(cfg, "audio", None)
    if audio_cfg is not None:
        for attr in ("sample_rate", "sampling_rate"):
            v = getattr(audio_cfg, attr, None)
            if isinstance(v, int) and v > 0:
                return v
    return 22050


def _piper_chunk_bytes(chunk) -> Optional[bytes]:
    """从 AudioChunk 取出 int16 PCM 字节（兼容多种属性名）"""
    for attr in ("audio_int16_bytes", "audio_bytes", "int16_bytes"):
        v = getattr(chunk, attr, None)
        if isinstance(v, (bytes, bytearray)):
            return bytes(v)
    arr = getattr(chunk, "audio_int16_array", None)
    if arr is not None:
        try:
            return arr.tobytes()
        except Exception:
            pass
    # 兜底：假设 chunk 本身就是字节
    if isinstance(chunk, (bytes, bytearray)):
        return bytes(chunk)
    return None


def _piper_synthesize_to_wav(voice_model, text: str, speed: float,
                             wav_path: str, should_stop=None) -> None:
    """将 Piper 合成结果写入 WAV 文件（兼容新旧 API，支持暂停）"""
    # 新 API (piper-tts 1.3.0+): 使用 synthesize(text) -> Iterable[AudioChunk]
    if hasattr(voice_model, "synthesize"):
        syn_config = None
        if _PiperSynthesisConfig is not None and speed != 1.0:
            try:
                syn_config = _PiperSynthesisConfig(length_scale=1.0 / speed)
            except Exception as e:
                logger.warning(f"构造 SynthesisConfig 失败，忽略速度: {e}")
                syn_config = None

        try:
            iterator = (
                voice_model.synthesize(text, syn_config=syn_config)
                if syn_config is not None
                else voice_model.synthesize(text)
            )
        except TypeError:
            # 某些版本签名不同
            iterator = voice_model.synthesize(text)

        sample_rate = _piper_sample_rate(voice_model)
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            for chunk in iterator:
                if should_stop and should_stop():
                    raise StopRequested("用户暂停")
                # 首块若携带 sample_rate，则修正
                sr = getattr(chunk, "sample_rate", None)
                if isinstance(sr, int) and sr > 0 and sr != sample_rate:
                    wf.setframerate(sr)
                    sample_rate = sr
                data = _piper_chunk_bytes(chunk)
                if not data:
                    continue
                wf.writeframes(data)
        return

    # 旧 API (piper-tts <1.3.0): synthesize_stream_raw 返回原始 PCM 字节流
    if hasattr(voice_model, "synthesize_stream_raw"):
        sample_rate = _piper_sample_rate(voice_model)
        pcm = bytearray()
        for audio_bytes in voice_model.synthesize_stream_raw(text, speed=speed):
            if should_stop and should_stop():
                raise StopRequested("用户暂停")
            pcm.extend(audio_bytes)
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(bytes(pcm))
        return

    raise RuntimeError(
        "Piper API 不兼容：未找到 synthesize 或 synthesize_stream_raw 方法。"
        "\n请升级或重装 piper-tts。"
    )


def _piper_generate(text, voice, rate, output_path, should_stop=None):
    """使用Piper TTS生成音频（支持Python包和CLI两种模式）"""
    if not _piper_available():
        raise RuntimeError("Piper TTS 不可用。\n" + _piper_install_hint())

    if not _configure_pydub_ffmpeg():
        raise RuntimeError("ffmpeg 未安装，Piper 引擎需要 ffmpeg 进行 WAV→MP3 转换。\n" + _ffmpeg_install_hint())

    voice_model = _load_piper_model(voice, should_stop=should_stop)

    rate_val = int(rate.replace("%", "").replace("+", ""))
    speed = 1.0 + rate_val / 100.0
    speed = max(0.5, min(2.0, speed))

    wav_path = output_path.replace('.mp3', '.wav')

    try:
        mode = _get_piper_mode()

        if mode == PIPER_MODE_PYTHON:
            _piper_synthesize_to_wav(voice_model, text, speed, wav_path, should_stop=should_stop)
        else:
            _piper_generate_cli(text, voice, speed, wav_path, should_stop=should_stop)

        if should_stop and should_stop():
            raise StopRequested("用户暂停")
        _convert_wav_to_mp3(wav_path, output_path)

    finally:
        if os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass


def _piper_generate_safe(text, voice, rate, output_path, should_stop=None):
    """安全的Piper生成函数，包含重试机制（可中断）"""
    for attempt in range(MAX_RETRIES + 1):
        if should_stop and should_stop():
            raise StopRequested("用户暂停")
        try:
            logger.info(f"Piper生成 → {output_path} (尝试 {attempt + 1})")
            _piper_generate(text, voice, rate, output_path, should_stop=should_stop)

            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                logger.info(f"Piper完成: {output_path} ({os.path.getsize(output_path)} bytes)")
                return
            else:
                raise RuntimeError(f"生成文件为空: {output_path}")

        except StopRequested:
            raise
        except Exception as e:
            delay = RETRY_DELAY * (2 ** attempt)
            logger.error(f"Piper生成失败 尝试{attempt + 1}/{MAX_RETRIES + 1}: {e}")
            if attempt < MAX_RETRIES:
                logger.info(f"等待 {delay}秒 后重试...")
                if _interruptible_sleep(delay, should_stop):
                    raise StopRequested("用户暂停")
            else:
                raise


# ======== 音频文件合并 ========

def _merge_mp3_files(file_paths, output_path):
    """合并多个MP3文件为一个，跳过后续文件的ID3标签"""
    with open(output_path, "wb") as outfile:
        for idx, path in enumerate(file_paths):
            with open(path, "rb") as infile:
                data = infile.read()
            if not data:
                logger.warning(f"合并时跳过空文件: {path}")
                continue
            if idx == 0:
                outfile.write(data)
            else:
                # 跳过 ID3v2 标签
                if len(data) > 10 and data[:3] == b'ID3':
                    size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
                    header_end = 10 + size
                    if header_end < len(data):
                        outfile.write(data[header_end:])
                    else:
                        logger.warning(f"ID3标签异常大，跳过: {path}")
                else:
                    outfile.write(data)


def merge_mp3_files(file_paths, output_path):
    """公开接口：合并多个MP3文件"""
    _merge_mp3_files(file_paths, output_path)


# ======== 并发数配置 ========


# ======== Edge TTS 引擎 ========

async def _edge_generate(text, voice, rate, output_path):
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_path)


async def _edge_generate_multi(segments, voice, rate, output_path):
    """单个文件内多段文本串行生成（避免并发过多触发限流）"""
    temp_dir = tempfile.mkdtemp()
    temp_files = []
    try:
        for i, seg in enumerate(segments):
            tp = os.path.join(temp_dir, f"seg_{i:04d}.mp3")
            await _edge_generate(seg, voice, rate, tp)
            temp_files.append(tp)
        _merge_mp3_files(temp_files, output_path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ======== 批量 Edge TTS（并发 + 单一事件循环） ========

async def _interruptible_asleep(seconds: float, stop_event: "asyncio.Event") -> bool:
    """异步版可中断睡眠。返回 True 表示被 stop_event 唤醒。"""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def _generate_one_item(item, voice, rate, output_dir, semaphore, stop_event):
    """生成单个章节，由信号量控制并发（支持暂停）"""
    if stop_event.is_set():
        return
    out_path = os.path.join(output_dir, item["filename"])
    segments = split_text(item["text"])

    async with semaphore:
        if stop_event.is_set():
            return
        for attempt in range(MAX_RETRIES + 1):
            if stop_event.is_set():
                return
            try:
                logger.info(f"生成 [{item['title']}] → {item['filename']} (尝试 {attempt + 1}/{MAX_RETRIES + 1})")

                if len(segments) == 1:
                    await _edge_generate(segments[0], voice, rate, out_path)
                else:
                    await _edge_generate_multi(segments, voice, rate, out_path)

                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    item["status"] = "done"
                    logger.info(f"完成 [{item['title']}] ({os.path.getsize(out_path)} bytes)")
                    return
                else:
                    raise RuntimeError(f"生成文件为空: {out_path}")

            except Exception as e:
                delay = RETRY_DELAY * (2 ** attempt)  # 指数退避: 5s, 10s, 20s
                logger.error(f"生成失败 [{item['title']}] 尝试{attempt + 1}/{MAX_RETRIES + 1}: {e}")
                if attempt < MAX_RETRIES:
                    logger.info(f"等待 {delay}秒 后重试...")
                    if await _interruptible_asleep(delay, stop_event):
                        return
                else:
                    item["status"] = "error"
                    item["error"] = str(e)


async def _edge_batch_generate(items, voice, rate, output_dir, progress_callback, should_stop):
    """并发批量生成，支持暂停（stop_event 即时传播）"""
    pending_items = [it for it in items if it["status"] not in ("done", "skipped")]
    total_active = len(pending_items)
    done_count = 0

    semaphore = asyncio.Semaphore(CONCURRENCY)
    stop_event = asyncio.Event()

    async def _watch_stop():
        while not stop_event.is_set():
            if should_stop and should_stop():
                logger.info("用户暂停：广播取消事件")
                stop_event.set()
                return
            await asyncio.sleep(0.2)

    async def _process_item(item):
        nonlocal done_count
        await _generate_one_item(item, voice, rate, output_dir, semaphore, stop_event)
        done_count += 1
        if progress_callback:
            try:
                progress_callback(done_count, total_active)
            except Exception:
                pass
        save_progress(output_dir, items)

    watcher = asyncio.create_task(_watch_stop())
    tasks = [asyncio.create_task(_process_item(item)) for item in pending_items]

    try:
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        stop_event.set()
        watcher.cancel()
        try:
            await watcher
        except Exception:
            pass


# ======== 本地 TTS 引擎 (跨平台) ========

def _wav_to_mp3(wav_path: str, mp3_path: str) -> None:
    """用 pydub/ffmpeg 或直接调用 ffmpeg 将 WAV 转为 MP3"""
    if PYDUB_AVAILABLE and _configure_pydub_ffmpeg():
        audio = AudioSegment.from_wav(wav_path)
        audio.export(mp3_path, format="mp3", bitrate="128k")
        return

    ff = _ffmpeg_path()
    if not ff:
        raise RuntimeError("ffmpeg 不可用，无法转换为 MP3。\n" + _ffmpeg_install_hint())
    subprocess.run(
        [ff, "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-b:a", "128k", mp3_path],
        check=True, capture_output=True,
    )


def _local_generate_macos(text: str, voice: str, rate: str, output_path: str, should_stop=None) -> None:
    rate_val = int(rate.replace("%", "").replace("+", ""))
    wpm = max(int(175 * (1 + rate_val / 100.0)), 50)

    aiff_path = output_path.rsplit(".", 1)[0] + ".aiff"
    try:
        rc, _o, err = _run_subprocess_interruptible(
            ["say", "-v", voice, "-r", str(wpm), "-o", aiff_path, text],
            should_stop=should_stop,
        )
        if rc != 0:
            raise RuntimeError(f"say 失败: {err.decode('utf-8', errors='replace')}")
        rc, _o, err = _run_subprocess_interruptible(
            ["afconvert", "-f", "mp4f", "-d", "aac", aiff_path, output_path],
            should_stop=should_stop,
        )
        if rc != 0:
            raise RuntimeError(f"afconvert 失败: {err.decode('utf-8', errors='replace')}")
    finally:
        if os.path.exists(aiff_path):
            try:
                os.remove(aiff_path)
            except Exception:
                pass


# PowerShell：使用 SAPI 合成到 WAV
_POWERSHELL_SPEAK_TEMPLATE = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "try {{ $s.SelectVoice('{voice}') }} catch {{ }} "
    "$s.Rate = {rate}; "
    "$s.SetOutputToWaveFile('{wav}'); "
    "$txt = [IO.File]::ReadAllText('{txtfile}', [Text.Encoding]::UTF8); "
    "$s.Speak($txt); "
    "$s.Dispose();"
)


def _local_generate_windows(text: str, voice: str, rate: str, output_path: str, should_stop=None) -> None:
    """Windows: 用 PowerShell 调用 SAPI 合成 WAV，再转 MP3"""
    ps = which("powershell") or which("pwsh")
    if not ps:
        raise RuntimeError("未找到 PowerShell，无法调用 Windows 系统语音")

    rate_val = int(rate.replace("%", "").replace("+", ""))
    sapi_rate = max(min(int(rate_val / 10), 10), -10)

    tmpdir = tempfile.mkdtemp()
    wav_path = os.path.join(tmpdir, "tts.wav")
    txt_path = os.path.join(tmpdir, "tts.txt")
    try:
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text)
        safe_voice = voice.replace("'", "''")
        safe_wav = wav_path.replace("'", "''")
        safe_txt = txt_path.replace("'", "''")
        script = _POWERSHELL_SPEAK_TEMPLATE.format(
            voice=safe_voice, rate=sapi_rate, wav=safe_wav, txtfile=safe_txt
        )
        rc, _out, err = _run_subprocess_interruptible(
            [ps, "-NoProfile", "-NonInteractive", "-Command", script],
            should_stop=should_stop, timeout=600,
        )
        if rc != 0 or not os.path.exists(wav_path) or os.path.getsize(wav_path) == 0:
            raise RuntimeError(f"Windows SAPI 合成失败: {err.decode('utf-8', errors='replace').strip()}")
        _wav_to_mp3(wav_path, output_path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _local_generate_linux(text: str, voice: str, rate: str, output_path: str, should_stop=None) -> None:
    """Linux: 用 espeak-ng 合成 WAV，再转 MP3"""
    espeak = which("espeak-ng") or which("espeak")
    if not espeak:
        raise RuntimeError("未找到 espeak-ng，请先安装（sudo apt install espeak-ng）")

    rate_val = int(rate.replace("%", "").replace("+", ""))
    wpm = max(int(175 * (1 + rate_val / 100.0)), 80)

    tmpdir = tempfile.mkdtemp()
    wav_path = os.path.join(tmpdir, "tts.wav")
    try:
        rc, _out, err = _run_subprocess_interruptible(
            [espeak, "-v", voice or "zh", "-s", str(wpm), "-w", wav_path, text],
            should_stop=should_stop, timeout=600,
        )
        if rc != 0:
            raise RuntimeError(f"espeak 失败: {err.decode('utf-8', errors='replace')}")
        _wav_to_mp3(wav_path, output_path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _local_generate(text: str, voice: str, rate: str, output_path: str, should_stop=None) -> None:
    """跨平台本地 TTS 调度器"""
    if _PLATFORM == "Darwin":
        _local_generate_macos(text, voice, rate, output_path, should_stop=should_stop)
    elif _PLATFORM == "Windows":
        _local_generate_windows(text, voice, rate, output_path, should_stop=should_stop)
    elif _PLATFORM == "Linux":
        _local_generate_linux(text, voice, rate, output_path, should_stop=should_stop)
    else:
        raise RuntimeError(f"暂不支持的平台: {_PLATFORM}")


# ======== 统一生成接口 ========

def _generate_one_safe(text, voice, rate, output_path, engine="edge", should_stop=None):
    """生成单个MP3，带重试和验证（可中断）"""
    segments = split_text(text)

    for attempt in range(MAX_RETRIES + 1):
        if should_stop and should_stop():
            raise StopRequested("用户暂停")
        try:
            logger.info(f"生成单文件 → {output_path} (尝试 {attempt + 1})")

            if engine == "local":
                if len(segments) == 1:
                    _local_generate(segments[0], voice, rate, output_path, should_stop=should_stop)
                else:
                    temp_dir = tempfile.mkdtemp()
                    temp_files = []
                    try:
                        for i, seg in enumerate(segments):
                            if should_stop and should_stop():
                                raise StopRequested("用户暂停")
                            tp = os.path.join(temp_dir, f"seg_{i:04d}.mp3")
                            _local_generate(seg, voice, rate, tp, should_stop=should_stop)
                            temp_files.append(tp)
                        _merge_mp3_files(temp_files, output_path)
                    finally:
                        shutil.rmtree(temp_dir, ignore_errors=True)
            elif engine == "piper":
                if len(segments) == 1:
                    _piper_generate_safe(segments[0], voice, rate, output_path, should_stop=should_stop)
                else:
                    temp_dir = tempfile.mkdtemp()
                    temp_files = []
                    try:
                        for i, seg in enumerate(segments):
                            if should_stop and should_stop():
                                raise StopRequested("用户暂停")
                            tp = os.path.join(temp_dir, f"seg_{i:04d}.mp3")
                            _piper_generate_safe(seg, voice, rate, tp, should_stop=should_stop)
                            temp_files.append(tp)
                        _merge_mp3_files(temp_files, output_path)
                    finally:
                        shutil.rmtree(temp_dir, ignore_errors=True)
            else:
                if len(segments) == 1:
                    asyncio.run(_edge_generate(segments[0], voice, rate, output_path))
                else:
                    asyncio.run(_edge_generate_multi(segments, voice, rate, output_path))

            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                logger.info(f"完成: {output_path} ({os.path.getsize(output_path)} bytes)")
                return
            else:
                raise RuntimeError(f"生成文件为空: {output_path}")

        except StopRequested:
            raise
        except Exception as e:
            delay = RETRY_DELAY * (2 ** attempt)
            logger.error(f"生成失败 尝试{attempt + 1}/{MAX_RETRIES + 1}: {e}")
            if attempt < MAX_RETRIES:
                logger.info(f"等待 {delay}秒 后重试...")
                if _interruptible_sleep(delay, should_stop):
                    raise StopRequested("用户暂停")
            else:
                raise


# ======== 进度管理 ========

def save_progress(output_dir, items):
    path = os.path.join(output_dir, PROGRESS_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def load_progress(output_dir):
    path = os.path.join(output_dir, PROGRESS_FILENAME)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def clear_progress(output_dir):
    path = os.path.join(output_dir, PROGRESS_FILENAME)
    if os.path.exists(path):
        os.remove(path)


# ======== 批量生成（支持断点续传 + 选择生成） ========

def convert_batch(
    text,
    voice,
    rate,
    output_dir,
    split_mode="chapter",
    time_minutes=30,
    file_prefix="有声读物",
    selected_indices=None,
    engine="edge",
    progress_callback=None,
    should_stop=None,
    resume=False,
):
    os.makedirs(output_dir, exist_ok=True)

    # 构建任务列表
    if resume:
        items = load_progress(output_dir)
        if not items:
            resume = False

    if not resume:
        chapters = detect_chapters(text)
        items = []

        if split_mode == "single":
            items = [{"title": file_prefix, "text": text, "filename": f"{file_prefix}.mp3", "status": "pending"}]
        elif split_mode == "chapter":
            for idx, ch in enumerate(chapters):
                fn = f"{idx + 1:03d}_{sanitize_filename(ch['title'])}.mp3"
                items.append({"title": ch["title"], "text": ch["text"], "filename": fn, "status": "pending"})
        elif split_mode == "time":
            max_sec = time_minutes * 60
            file_idx = 0
            for ch in chapters:
                parts = split_by_duration(ch["text"], max_sec, rate)
                for pi, part in enumerate(parts):
                    file_idx += 1
                    label = ch["title"] if ch["title"] != "全文" else ""
                    if len(parts) > 1:
                        suffix = f"_第{pi + 1}部分"
                        label = f"{label}{suffix}" if label else f"第{pi + 1}部分"
                    fn = f"{file_idx:03d}_{sanitize_filename(label or file_prefix)}.mp3"
                    items.append({"title": label or file_prefix, "text": part, "filename": fn, "status": "pending"})

        if selected_indices is not None:
            for i, item in enumerate(items):
                if i not in selected_indices:
                    item["status"] = "skipped"

        save_progress(output_dir, items)

    logger.info(f"批量生成开始: {sum(1 for it in items if it['status'] not in ('done','skipped'))} 个待处理, 引擎={engine}")

    if engine == "edge":
        # 用单一事件循环批量生成
        asyncio.run(_edge_batch_generate(items, voice, rate, output_dir, progress_callback, should_stop))
    elif engine in ("local", "piper"):
        # 本地引擎或Piper引擎同步逐个生成（支持暂停）
        done_count = 0
        total_active = sum(1 for it in items if it["status"] not in ("done", "skipped"))

        try:
            for item in items:
                if item["status"] in ("done", "skipped"):
                    continue
                if should_stop and should_stop():
                    logger.info("用户暂停生成")
                    break

                out_path = os.path.join(output_dir, item["filename"])
                try:
                    _generate_one_safe(item["text"], voice, rate, out_path,
                                       engine=engine, should_stop=should_stop)
                    item["status"] = "done"
                except StopRequested:
                    logger.info(f"用户暂停：[{item['title']}] 未完成")
                    save_progress(output_dir, items)
                    break
                except Exception as e:
                    item["status"] = "error"
                    item["error"] = str(e)
                    logger.error(f"{engine}引擎生成失败 [{item['title']}]: {e}")

                save_progress(output_dir, items)
                done_count += 1
                if progress_callback:
                    try:
                        progress_callback(done_count, total_active)
                    except Exception:
                        pass
        finally:
            if engine == "piper":
                _unload_piper_model()
    else:
        raise ValueError(f"不支持的引擎类型: {engine}")

    # 收集结果
    output_files = [os.path.join(output_dir, it["filename"])
                    for it in items if it["status"] == "done"]

    # 全部完成则清理进度
    all_done = all(it["status"] in ("done", "skipped") for it in items)
    if all_done:
        clear_progress(output_dir)
        logger.info("全部完成，已清理进度文件")

    error_count = sum(1 for it in items if it["status"] == "error")
    if error_count:
        logger.warning(f"{error_count} 个文件生成失败")

    return output_files


# ======== 试听 ========

def generate_preview(text, voice, rate="+0%", engine="edge", should_stop=None):
    preview_text = text[:200]
    if not preview_text.strip():
        raise ValueError("没有可预览的文本")
    temp_path = os.path.join(tempfile.gettempdir(), "audiobook_preview.mp3")
    _generate_one_safe(preview_text, voice, rate, temp_path, engine=engine, should_stop=should_stop)
    return temp_path
