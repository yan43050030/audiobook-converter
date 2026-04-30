"""TTS引擎封装 - 支持 edge-tts（联网）、系统语音（跨平台离线）、Piper（离线高质量）、CosyVoice（离线神经） v5.0.0"""

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
from collections import OrderedDict
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

VERSION = "5.0.0"

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


def add_model_search_path(path: str) -> bool:
    """将用户选择的文件夹加入模型/程序搜索路径。返回 True 表示新增，False 表示已存在。"""
    cfg = _load_config()
    paths = cfg.get("extra_search_paths", [])
    path = os.path.abspath(path)
    if path in paths:
        return False
    paths.append(path)
    cfg["extra_search_paths"] = paths
    _save_config(cfg)
    _invalidate_scan_cache()
    logger.info(f"添加搜索路径: {path}")
    return True


def get_model_search_paths() -> list[str]:
    """获取用户添加的额外搜索路径列表"""
    cfg = _load_config()
    return cfg.get("extra_search_paths", [])


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

    # 2.5) 用户添加的额外搜索路径
    if result is None:
        for extra in get_model_search_paths():
            if os.path.isdir(extra):
                for cand in candidates:
                    p = os.path.join(extra, cand)
                    if os.path.isfile(p):
                        if _PLATFORM == "Windows" or os.access(p, os.X_OK):
                            result = p
                            break
                if result:
                    break
                # 也递归搜索
                result = _search_in_tree(extra, candidates, max_depth=4)
                if result:
                    break

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

    # 先找已预置的 Piper 模型目录（piper-models/），再递归全目录，再额外搜索路径
    piper_model_dir = get_piper_model_dir()
    models: List[str] = []
    seen = set()
    search_roots = [piper_model_dir, storage] + get_model_search_paths()
    for root in search_roots:
        for candidate in _search_models_in_tree(root):
            key = os.path.abspath(candidate)
            if key not in seen:
                seen.add(key)
                models.append(candidate)

    # 外部引擎检测
    ext_engines = _scan_external_engines()

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
        "external_engines": ext_engines,
        "gpu_status": {
            "cuda_available": _cuda_available(),
            "onnxruntime_gpu": _onnxruntime_gpu_available(),
        },
        "missing": missing,
    }


def _refresh_piper_cli_path() -> Optional[str]:
    """每次使用时重新解析 piper CLI 路径（便携目录可能动态变更）"""
    return _which_portable("piper")


# ======== 外部引擎插件框架 ========

EXTERNAL_ENGINE_DIR_NAME = "engines"

# 所有引擎注册表: engine_id -> {"type": "builtin"/"external", "name": str, ...}
_engine_registry: dict[str, dict] = {}


def register_builtin_engine(engine_id: str, display_name: str) -> None:
    """注册内置引擎（edge, local, piper 等）"""
    _engine_registry[engine_id] = {
        "type": "builtin",
        "name": display_name,
        "engine_id": engine_id,
    }


def _find_engine_executable(engine_path: str, name: str) -> Optional[str]:
    """在引擎目录中查找可执行入口"""
    candidates = [
        os.path.join(engine_path, name),
        os.path.join(engine_path, name + ".exe"),
        os.path.join(engine_path, name + ".bat"),
        os.path.join(engine_path, name + ".py"),
    ]
    for cand in candidates:
        if os.path.isfile(cand):
            if name.endswith(".py") or _PLATFORM == "Windows" or os.access(cand, os.X_OK):
                return cand
    # 兼容：目录内任意 .py 脚本
    for fn in sorted(os.listdir(engine_path)):
        if fn.endswith(".py"):
            full = os.path.join(engine_path, fn)
            if os.path.isfile(full):
                return full
    return None


def _load_engine_metadata(engine_path: str) -> dict:
    """读取引擎的 engine.json 元数据"""
    meta_path = os.path.join(engine_path, "engine.json")
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"读取引擎元数据失败 {meta_path}: {e}")
    return {}


# 外部引擎语音列表缓存（TTL 60s）
_external_voices_cache: dict = {"time": 0, "data": {}}


def _list_external_voices(engine_id: str, executable: str) -> list[str]:
    """通过 --list-voices 获取外部引擎的语音列表（带缓存）"""
    now = time.monotonic()
    if now - _external_voices_cache["time"] < 60 and engine_id in _external_voices_cache["data"]:
        return _external_voices_cache["data"][engine_id]
    try:
        result = subprocess.run(
            [executable, "--list-voices"],
            capture_output=True, text=True, timeout=30,
            **_quiet_popen_kwargs(),
        )
        if result.returncode == 0:
            voices = json.loads(result.stdout.strip())
            if isinstance(voices, list):
                _external_voices_cache["data"][engine_id] = voices
                _external_voices_cache["time"] = now
                return voices
    except Exception as e:
        logger.warning(f"获取外部引擎语音列表失败 {engine_id}: {e}")
    return []


def _scan_external_engines() -> dict[str, dict]:
    """扫描 {storage_dir}/engines/ 目录下的外部插件引擎"""
    engines_dir = os.path.join(get_storage_dir(), EXTERNAL_ENGINE_DIR_NAME)
    if not os.path.isdir(engines_dir):
        return {}
    found: dict[str, dict] = {}
    for entry in sorted(os.listdir(engines_dir)):
        engine_path = os.path.join(engines_dir, entry)
        if not os.path.isdir(engine_path):
            continue
        executable = _find_engine_executable(engine_path, entry)
        if executable is None:
            continue
        meta = _load_engine_metadata(engine_path)
        found[entry] = {
            "type": "external",
            "name": meta.get("name", entry),
            "engine_id": entry,
            "executable": executable,
            "description": meta.get("description", ""),
            "version": meta.get("version", ""),
            "voices": _list_external_voices(entry, executable),
        }
    return found


def get_registered_engines() -> dict[str, dict]:
    """返回所有注册引擎（内置 + 外部插件）"""
    result = dict(_engine_registry)
    for eid, info in _scan_external_engines().items():
        result[eid] = info
    return result


def _is_external_engine(engine: str) -> bool:
    """判断引擎 ID 是否为外部插件"""
    engines = _scan_external_engines()
    return engine in engines


def check_external_engine_ready(engine_id: str) -> tuple[bool, str]:
    """检测外部引擎是否可用"""
    engines = _scan_external_engines()
    info = engines.get(engine_id)
    if not info:
        return False, f"外部引擎 '{engine_id}' 未安装。\n请将引擎可执行文件放入便携存储目录的 engines/{engine_id}/ 中。"
    executable = info["executable"]
    if not os.path.isfile(executable):
        return False, f"引擎可执行文件不存在: {executable}"
    # 尝试获取语音列表验证
    voices = _list_external_voices(engine_id, executable)
    if not voices:
        # 不视为错误：引擎可能还在初始化
        pass
    return True, f"{info['name']} 可用（{len(voices)} 个语音）"


def _external_generate(text: str, voice: str, rate: str, output_path: str,
                       engine_id: str, executable: str, should_stop=None) -> None:
    """使用外部引擎 CLI 生成音频"""
    rate_val = int(rate.replace("%", "").replace("+", ""))
    speed = 1.0 + rate_val / 100.0
    speed = max(0.5, min(2.0, speed))

    cmd = [
        executable,
        "--voice", voice,
        "--text", text,
        "--output", output_path,
    ]
    if speed != 1.0:
        cmd.extend(["--speed", str(speed)])

    rc, _out, err = _run_subprocess_interruptible(cmd, should_stop=should_stop)
    if rc != 0:
        stderr = err.decode("utf-8", errors="replace") if err else ""
        raise RuntimeError(f"外部引擎 '{engine_id}' 失败 (code {rc}): {stderr}")
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(f"外部引擎 '{engine_id}' 生成文件为空: {output_path}")


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
        result = subprocess.run([say_path, "-v", "?"], capture_output=True, text=True, timeout=10,
                                **_quiet_popen_kwargs())
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
            **_quiet_popen_kwargs(),
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
            [espeak, "--voices=zh"], capture_output=True, text=True, timeout=10,
            **_quiet_popen_kwargs(),
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


# 注册内置引擎
register_builtin_engine("edge", "Edge（联网）")
register_builtin_engine("local", "本地（离线）")
register_builtin_engine("piper", "Piper（离线高质量）")

# ======== CosyVoice 引擎配置（可选） ========

# 可选导入
try:
    from cosyvoice import CosyVoice as _CosyVoiceCls
    COSYVOICE_PYTHON_AVAILABLE = True
except ImportError:
    COSYVOICE_PYTHON_AVAILABLE = False
    _CosyVoiceCls = None

COSYVOICE_VOICES: dict[str, str] = {}
COSYVOICE_DEFAULT_VOICE = ""


def _detect_cosyvoice_voices() -> dict:
    """检测 CosyVoice 可用语音。优先从已下载的模型中扫描。"""
    voices: dict = {}

    if COSYVOICE_PYTHON_AVAILABLE:
        voices["默认中文女声"] = "default_female"
        voices["默认中文男声"] = "default_male"

    # 扫描已下载的模型目录
    try:
        model_dir = get_cosyvoice_model_dir()
        if os.path.isdir(model_dir):
            for entry in sorted(os.listdir(model_dir)):
                entry_path = os.path.join(model_dir, entry)
                if os.path.isdir(entry_path) and os.listdir(entry_path):
                    voices[f"CosyVoice ({entry})"] = entry
    except Exception as e:
        logger.warning(f"扫描 CosyVoice 模型目录失败: {e}")

    return voices


def refresh_cosyvoice_voices() -> None:
    """刷新 CosyVoice 语音列表"""
    global COSYVOICE_VOICES, COSYVOICE_DEFAULT_VOICE
    COSYVOICE_VOICES = _detect_cosyvoice_voices()
    COSYVOICE_DEFAULT_VOICE = list(COSYVOICE_VOICES.values())[0] if COSYVOICE_VOICES else ""


refresh_cosyvoice_voices()
# CosyVoice 始终注册为 builtin（未安装时 UI 显示"需安装"）
register_builtin_engine("cosyvoice", "CosyVoice（离线神经）")


# CosyVoice 模型下载 URL（HuggingFace，hf-mirror 自动回退）
COSYVOICE_MODEL_URLS = {
    "CosyVoice-300M-SFT": {
        "url": "https://huggingface.co/FunAudioLLM/CosyVoice-300M-SFT/resolve/main/cosyvoice-300m-sft.tar.gz",
        "description": "CosyVoice-300M-SFT 微调模型（推荐，≈600MB）",
    },
}


def get_cosyvoice_model_dir() -> str:
    """CosyVoice 模型目录（跟随存储目录）"""
    path = os.path.join(get_storage_dir(), "cosyvoice-models")
    os.makedirs(path, exist_ok=True)
    return path


def _cosyvoice_install_hint() -> str:
    """CosyVoice 安装引导提示"""
    return (
        "CosyVoice 未安装。请通过以下方式安装：\n"
        "1. pip install cosyvoice soundfile librosa\n"
        "2. 在 GUI 依赖检测面板点击「下载 CosyVoice 模型」\n"
        "3. 或将 CosyVoice 可执行文件放入 engines/cosyvoice/ 作为外挂引擎"
    )


def _download_cosyvoice_model(model_key: str, should_stop=None):
    """下载并解压 CosyVoice 模型"""
    if model_key not in COSYVOICE_MODEL_URLS:
        raise ValueError(f"不支持的 CosyVoice 模型: {model_key}")

    model_dir = get_cosyvoice_model_dir()
    tar_path = os.path.join(model_dir, f"{model_key}.tar.gz")
    extract_dir = os.path.join(model_dir, model_key)

    if os.path.isdir(extract_dir) and os.listdir(extract_dir):
        logger.info(f"CosyVoice 模型已存在: {extract_dir}")
        return extract_dir

    url = COSYVOICE_MODEL_URLS[model_key]["url"]
    _download_file_with_progress(url, tar_path,
        f"CosyVoice {model_key}", should_stop=should_stop)

    import tarfile
    logger.info(f"解压 CosyVoice 模型: {tar_path}")
    os.makedirs(extract_dir, exist_ok=True)
    with tarfile.open(tar_path, "r:gz") as tar:
        tar.extractall(path=extract_dir)

    try:
        os.remove(tar_path)
    except Exception:
        pass

    logger.info(f"CosyVoice 模型就绪: {extract_dir}")
    return extract_dir


def _ensure_cosyvoice_model(model_key: str = "CosyVoice-300M-SFT", should_stop=None):
    """确保 CosyVoice 模型存在，不存在则下载"""
    model_dir = os.path.join(get_cosyvoice_model_dir(), model_key)
    if not os.path.isdir(model_dir) or not os.listdir(model_dir):
        logger.info(f"CosyVoice 模型不存在，开始下载: {model_key}")
        _download_cosyvoice_model(model_key, should_stop=should_stop)
    return model_dir


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


# Windows 上避免子进程弹出黑色控制台窗口（PyInstaller --windowed 模式下尤为重要）
if _PLATFORM == "Windows":
    _SUBPROCESS_HIDDEN_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
else:
    _SUBPROCESS_HIDDEN_FLAGS = 0


def _quiet_popen_kwargs(extra: Optional[dict] = None) -> dict:
    """所有 subprocess 调用统一附加 Windows 隐藏窗口标志。"""
    kw = dict(extra) if extra else {}
    if _SUBPROCESS_HIDDEN_FLAGS:
        kw.setdefault("creationflags", _SUBPROCESS_HIDDEN_FLAGS)
    return kw


def _run_subprocess_interruptible(cmd, should_stop=None, input_bytes: Optional[bytes] = None,
                                  timeout: Optional[float] = None, poll_interval: float = 0.2,
                                  **popen_kwargs):
    """以 Popen 启动子进程，支持在运行中响应 should_stop 立刻终止。"""
    popen_kwargs.setdefault("stdout", subprocess.PIPE)
    popen_kwargs.setdefault("stderr", subprocess.PIPE)
    if input_bytes is not None:
        popen_kwargs.setdefault("stdin", subprocess.PIPE)
    # Windows 隐藏控制台窗口
    if _SUBPROCESS_HIDDEN_FLAGS and "creationflags" not in popen_kwargs:
        popen_kwargs["creationflags"] = _SUBPROCESS_HIDDEN_FLAGS

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


# ======== GPU 检测 ========


def _cuda_available() -> bool:
    """检测 CUDA 是否可用（PyTorch）"""
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def _onnxruntime_gpu_available() -> bool:
    """检测 onnxruntime-gpu 的 CUDA Execution Provider 是否可用"""
    try:
        import onnxruntime
        return "CUDAExecutionProvider" in onnxruntime.get_available_providers()
    except ImportError:
        return False


# ======== 工具函数 ========

def get_voice_list(engine="edge"):
    if engine == "local":
        return list(LOCAL_VOICES.keys())
    elif engine == "piper":
        return list(PIPER_VOICES.keys())
    elif engine == "cosyvoice":
        return list(COSYVOICE_VOICES.keys())
    # 外部引擎
    ext_engines = _scan_external_engines()
    if engine in ext_engines:
        return ext_engines[engine].get("voices", [])
    return list(EDGE_VOICES.keys())


def get_voice_id(display_name, engine="edge"):
    if engine == "local":
        return LOCAL_VOICES.get(display_name, LOCAL_DEFAULT_VOICE)
    elif engine == "piper":
        return PIPER_VOICES.get(display_name, PIPER_DEFAULT_VOICE)
    elif engine == "cosyvoice":
        return COSYVOICE_VOICES.get(display_name, COSYVOICE_DEFAULT_VOICE)
    # 外部引擎：display_name 本身就是 voice ID
    ext_engines = _scan_external_engines()
    if engine in ext_engines:
        return display_name
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

    if engine == "cosyvoice":
        if COSYVOICE_PYTHON_AVAILABLE:
            model_dir = get_cosyvoice_model_dir()
            models = []
            if os.path.isdir(model_dir):
                models = [d for d in os.listdir(model_dir)
                          if os.path.isdir(os.path.join(model_dir, d))]
            if models:
                return True, f"CosyVoice 可用（Python 包, {len(models)} 个模型）"
            return True, "CosyVoice Python 包已安装（可点击「下载 CosyVoice 模型」获取预训练模型）"
        cli = _which_portable("cosyvoice")
        if cli:
            return True, f"CosyVoice CLI 可用（{cli}）"
        ext = _scan_external_engines()
        if "cosyvoice" in ext:
            return True, f"CosyVoice 外挂引擎可用（{ext['cosyvoice'].get('name')}）"
        return False, "CosyVoice 未安装。\n" + _cosyvoice_install_hint()

    # 外部引擎
    if _is_external_engine(engine):
        return check_external_engine_ready(engine)

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


def detect_chapters(text, source_map=None):
    """检测章节，支持多文件来源追踪。

    source_map: Optional[list[(start_char, source_name)]]
        用于标记每个章节的来源文件名
    """
    # 大文本（>=100k字符）：用正则一次扫描全文，O(n) 而非 O(n*m)
    if len(text) >= 100000:
        combined = re.compile(
            "|".join(f"({p.pattern})" for p in CHAPTER_PATTERNS),
            re.MULTILINE,
        )
        matches = []
        for m in combined.finditer(text):
            # 取匹配所在行的完整行文本（与行遍历模式行为一致）
            line_start = text.rfind('\n', 0, m.start()) + 1
            line_end = text.find('\n', m.end())
            if line_end == -1:
                line_end = len(text)
            full_line = text[line_start:line_end].strip()
            matches.append((line_start, full_line))

        if not matches:
            chapter = {"title": "全文", "start": 0, "end": len(text), "text": text}
            if source_map:
                chapter["source"] = _find_source(source_map, 0)
            return [chapter]

        chapters = []
        for idx, (start, title) in enumerate(matches):
            end = matches[idx + 1][0] if idx + 1 < len(matches) else len(text)
            chapter = {
                "title": title,
                "start": start,
                "end": end,
                "text": text[start:end].strip(),
            }
            if source_map:
                chapter["source"] = _find_source(source_map, start)
            chapters.append(chapter)
        return chapters

    # 小文本：保持现有行遍历逻辑
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
        chapter = {"title": "全文", "start": 0, "end": len(text), "text": text}
        if source_map:
            chapter["source"] = _find_source(source_map, 0)
        return [chapter]

    chapters = []
    for idx, (line_idx, title) in enumerate(chapter_starts):
        char_start = sum(len(lines[j]) + 1 for j in range(line_idx))
        if idx + 1 < len(chapter_starts):
            char_end = sum(len(lines[j]) + 1 for j in range(chapter_starts[idx + 1][0]))
        else:
            char_end = len(text)
        chapter = {
            "title": title,
            "start": char_start,
            "end": char_end,
            "text": text[char_start:char_end].strip(),
        }
        if source_map:
            chapter["source"] = _find_source(source_map, char_start)
        chapters.append(chapter)
    return chapters


def _find_source(source_map, char_pos):
    """在 source_map 中查找字符位置对应的来源文件名"""
    if not source_map:
        return ""
    result = ""
    for start_pos, name in source_map:
        if char_pos >= start_pos:
            result = name
        else:
            break
    return result


# ======== 对话识别（多人对话） ========

DIALOGUE_PATTERNS = [
    re.compile(r'[\u201c\u201d\"]([^\u201c\u201d\"]+)[\u201c\u201d\"]'),
    re.compile(r"[\u2018\u2019']([^\u2018\u2019']+)[\u2018\u2019']"),
    re.compile(r'[\u300c]([^\u300d]+)[\u300d]'),
    re.compile(r'"([^"]+)"'),
]
SPEAKER_PATTERN = re.compile(
    r'([^\uff0c\u3002\uff01\uff1f\n\u201c\u201d\u2018\u2019\u300c\u300d \t]{1,15})'
    r'(?:问道|喊道|叫道|答道|讲道|嚷道|吼道|叹道|骂道|喝道|回答|'
    r'说|问|道|喊|叫|答|讲|嚷|吼|叹|骂|喝)[\uff1a:]'
)


def detect_dialogue_segments(text: str) -> list[dict]:
    """检测文本中的对话和叙述片段，返回带类型标记的段列表。

    返回: [{"text": str, "type": "narration"|"dialogue", "speaker": str|None}]
    """
    if not text.strip():
        return []

    segments = []
    pos = 0
    text_len = len(text)

    while pos < text_len:
        earliest_match = None
        earliest_start = text_len
        for pattern in DIALOGUE_PATTERNS:
            m = pattern.search(text, pos)
            if m and m.start() < earliest_start:
                earliest_start = m.start()
                earliest_match = m

        if earliest_match is None:
            remaining = text[pos:].strip()
            if remaining:
                segments.append({"text": remaining, "type": "narration", "speaker": None})
            break

        if earliest_start > pos:
            narration = text[pos:earliest_start].strip()
            if narration:
                segments.append({"text": narration, "type": "narration", "speaker": None})

        speaker = None
        context_before = text[max(0, earliest_start - 40):earliest_start]
        spk_match = SPEAKER_PATTERN.search(context_before)
        if spk_match:
            speaker = spk_match.group(1).strip()

        dialogue_text = earliest_match.group(0)
        segments.append({"text": dialogue_text, "type": "dialogue", "speaker": speaker})

        pos = earliest_match.end()

    return segments


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


# ======== Piper 模型缓存（LRU，上限 2 个模型） ========

MAX_PIPER_CACHE = 2
_piper_model_cache: OrderedDict = OrderedDict()


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
        _piper_model_cache.move_to_end(cache_key)  # LRU: 标记为最近使用
        return _piper_model_cache[cache_key]

    mode = _get_piper_mode()

    if mode == PIPER_MODE_PYTHON:
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Piper配置文件不存在: {config_path}")
        voice_model = PiperVoice.load(model_path, config_path)
        # LRU：达到上限时淘汰最久未使用的
        if len(_piper_model_cache) >= MAX_PIPER_CACHE:
            _piper_model_cache.popitem(last=False)
            logger.debug("LRU淘汰：Piper模型缓存已满")
        _piper_model_cache[cache_key] = voice_model
        logger.info(f"Piper模型已加载并缓存: {voice_name} (缓存大小={len(_piper_model_cache)})")
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


# ======== CosyVoice TTS 引擎 ========

def _cosyvoice_generate(text: str, voice: str, rate: str, output_path: str,
                        should_stop=None) -> None:
    """使用 CosyVoice Python API 生成音频，失败则回退到外部引擎"""
    if not COSYVOICE_PYTHON_AVAILABLE or _CosyVoiceCls is None:
        raise RuntimeError("CosyVoice Python 包未安装。\n" + _cosyvoice_install_hint())

    import numpy as np
    model_key = voice if voice in COSYVOICE_MODEL_URLS else "CosyVoice-300M-SFT"
    model_dir = _ensure_cosyvoice_model(model_key, should_stop=should_stop)

    rate_val = int(rate.replace("%", "").replace("+", ""))
    speed = 1.0 + rate_val / 100.0
    speed = max(0.5, min(2.0, speed))

    try:
        cosy = _CosyVoiceCls(model_dir=model_dir)
    except TypeError:
        cosy = _CosyVoiceCls(model_dir)

    result = cosy.inference_sft(text, stream=False)

    audio_data = np.array([], dtype=np.float32)
    sample_rate = 22050
    for chunk in result:
        if isinstance(chunk, dict):
            audio_data = np.concatenate([audio_data, chunk.get("tts_speech", np.array([], dtype=np.float32))])
            sample_rate = chunk.get("sample_rate", sample_rate)
        elif isinstance(chunk, np.ndarray):
            audio_data = chunk

    if len(audio_data) == 0:
        raise RuntimeError("CosyVoice 合成返回空音频")

    # 速度调整
    if speed != 1.0:
        try:
            import librosa
            audio_data = librosa.effects.time_stretch(audio_data, rate=speed)
        except ImportError:
            logger.warning("librosa 未安装，跳过语速调整")

    wav_path = output_path.replace('.mp3', '.wav')
    try:
        import soundfile as sf
        sf.write(wav_path, audio_data, int(sample_rate))
        _wav_to_mp3(wav_path, output_path)
    finally:
        if os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass


def _cosyvoice_generate_safe(text, voice, rate, output_path, should_stop=None):
    """安全的 CosyVoice 生成，含重试 + 外部引擎回退"""
    # 优先 Python API
    if COSYVOICE_PYTHON_AVAILABLE and _CosyVoiceCls is not None:
        for attempt in range(MAX_RETRIES + 1):
            if should_stop and should_stop():
                raise StopRequested("用户暂停")
            try:
                logger.info(f"CosyVoice 生成 → {output_path} (尝试 {attempt + 1})")
                _cosyvoice_generate(text, voice, rate, output_path, should_stop=should_stop)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    logger.info(f"CosyVoice 完成: {output_path}")
                    return
            except StopRequested:
                raise
            except Exception as e:
                delay = RETRY_DELAY * (2 ** attempt)
                logger.error(f"CosyVoice 生成失败 尝试{attempt + 1}: {e}")
                if attempt >= MAX_RETRIES:
                    logger.warning("CosyVoice Python API 失败，回退到外部引擎")
                    break
                if _interruptible_sleep(delay, should_stop):
                    raise StopRequested("用户暂停")

    # 回退：外部引擎
    ext = _scan_external_engines()
    cosy_ext = ext.get("cosyvoice")
    if not cosy_ext:
        raise RuntimeError(
            "CosyVoice 不可用：Python 包未安装且未找到外部引擎。\n"
            + _cosyvoice_install_hint()
        )
    _external_generate(text, voice, rate, output_path, "cosyvoice",
                       cosy_ext["executable"], should_stop=should_stop)


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
                # 跳过 ID3v2 标签：synchsafe 整数（每字节最高位忽略，按 7 位拼接）
                if len(data) > 10 and data[:3] == b'ID3':
                    size = (
                        ((data[6] & 0x7f) << 21)
                        | ((data[7] & 0x7f) << 14)
                        | ((data[8] & 0x7f) << 7)
                        | (data[9] & 0x7f)
                    )
                    header_end = 10 + size
                    if 10 < header_end < len(data):
                        outfile.write(data[header_end:])
                    else:
                        # 尺寸异常，整段保留以避免静默丢数据
                        logger.warning(f"ID3 标签尺寸异常({size})，按原样写入: {path}")
                        outfile.write(data)
                else:
                    outfile.write(data)


def merge_mp3_files(file_paths, output_path):
    """公开接口：合并多个MP3文件"""
    _merge_mp3_files(file_paths, output_path)


def normalize_loudness(input_path: str, output_path: Optional[str] = None,
                       target_lufs: float = -16.0, target_tp: float = -1.5,
                       target_lra: float = 11.0) -> str:
    """对单个 MP3 做 EBU R128 响度归一化（通过 ffmpeg loudnorm 滤镜）。

    target_lufs/tp/lra 参数与 ffmpeg loudnorm 一致。
    返回归一化后的输出路径。原地处理时会用临时文件再原子替换。
    """
    ff = _ffmpeg_path()
    if not ff:
        raise RuntimeError("ffmpeg 未安装，无法做响度归一化。\n" + _ffmpeg_install_hint())
    if output_path is None:
        output_path = input_path

    in_place = os.path.abspath(output_path) == os.path.abspath(input_path)
    work_path = output_path + ".tmp.mp3" if in_place else output_path

    cmd = [
        ff, "-y", "-i", input_path,
        "-af", f"loudnorm=I={target_lufs}:TP={target_tp}:LRA={target_lra}",
        "-codec:a", "libmp3lame", "-b:a", "128k",
        work_path,
    ]
    logger.info(f"响度归一化: {input_path} -> {work_path}")
    rc = subprocess.run(cmd, capture_output=True, **_quiet_popen_kwargs()).returncode
    if rc != 0 or not os.path.exists(work_path):
        raise RuntimeError(f"loudnorm 失败 (rc={rc})")
    if in_place:
        os.replace(work_path, output_path)
    return output_path


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
        **_quiet_popen_kwargs(),
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

def _generate_one_safe_multi_voice(
    dialogue_segments, default_voice, rate, output_path, engine,
    should_stop=None, seg_progress=None, voice_map=None,
):
    """为对话识别后的混合片段生成音频，不同片段使用不同语音。"""
    temp_dir = tempfile.mkdtemp()
    temp_files = []
    total = len(dialogue_segments)
    try:
        for i, seg in enumerate(dialogue_segments):
            if should_stop and should_stop():
                raise StopRequested("用户暂停")

            seg_text = seg["text"]
            seg_type = seg.get("type", "narration")
            seg_speaker = seg.get("speaker")

            seg_voice = default_voice
            if voice_map:
                if seg_speaker and seg_speaker in voice_map:
                    seg_voice = voice_map[seg_speaker]
                elif seg_type == "dialogue" and "dialogue" in voice_map:
                    seg_voice = voice_map["dialogue"]
                elif seg_type == "narration" and "narration" in voice_map:
                    seg_voice = voice_map["narration"]

            tp = os.path.join(temp_dir, f"seg_{i:04d}.mp3")
            _generate_one_safe(seg_text, seg_voice, rate, tp, engine=engine,
                               should_stop=should_stop, seg_progress=None,
                               voice_map=None, dialogue_segments=None)
            temp_files.append(tp)
            if seg_progress and total > 1:
                try:
                    seg_progress(i + 1, total)
                except Exception:
                    pass

        _merge_mp3_files(temp_files, output_path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _generate_one_safe(text, voice, rate, output_path, engine="edge", should_stop=None,
                       seg_progress=None, voice_map=None, dialogue_segments=None):
    """生成单个MP3，带重试和验证（可中断）。

    seg_progress(current, total): 当文本被切成多段时，每段生成完毕调用一次。
    voice_map: 对话检测模式时的语音映射 {"narration": voice_id, "dialogue": voice_id, speaker_name: voice_id}
    dialogue_segments: 来自 detect_dialogue_segments() 的预检测段列表
    """
    if dialogue_segments:
        return _generate_one_safe_multi_voice(
            dialogue_segments, voice, rate, output_path, engine,
            should_stop=should_stop, seg_progress=seg_progress, voice_map=voice_map,
        )

    segments = split_text(text)
    total_segs = len(segments)

    def _notify(i):
        if seg_progress and total_segs > 1:
            try:
                seg_progress(i + 1, total_segs)
            except Exception:
                pass

    for attempt in range(MAX_RETRIES + 1):
        if should_stop and should_stop():
            raise StopRequested("用户暂停")
        try:
            logger.info(f"生成单文件 → {output_path} (尝试 {attempt + 1}, {total_segs} 段)")

            if engine == "local":
                if total_segs == 1:
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
                            _notify(i)
                        _merge_mp3_files(temp_files, output_path)
                    finally:
                        shutil.rmtree(temp_dir, ignore_errors=True)
            elif engine == "piper":
                if total_segs == 1:
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
                            _notify(i)
                        _merge_mp3_files(temp_files, output_path)
                    finally:
                        shutil.rmtree(temp_dir, ignore_errors=True)
            elif engine == "cosyvoice":
                if len(segments) == 1:
                    _cosyvoice_generate_safe(segments[0], voice, rate, output_path, should_stop=should_stop)
                else:
                    temp_dir = tempfile.mkdtemp()
                    temp_files = []
                    try:
                        for i, seg in enumerate(segments):
                            if should_stop and should_stop():
                                raise StopRequested("用户暂停")
                            tp = os.path.join(temp_dir, f"seg_{i:04d}.mp3")
                            _cosyvoice_generate_safe(seg, voice, rate, tp, should_stop=should_stop)
                            temp_files.append(tp)
                            _notify(i)
                        _merge_mp3_files(temp_files, output_path)
                    finally:
                        shutil.rmtree(temp_dir, ignore_errors=True)
            elif _is_external_engine(engine):
                ext_info = _scan_external_engines().get(engine)
                if not ext_info:
                    raise RuntimeError(f"外部引擎 '{engine}' 不可用")
                executable = ext_info["executable"]
                if len(segments) == 1:
                    _external_generate(segments[0], voice, rate, output_path, engine, executable, should_stop=should_stop)
                else:
                    temp_dir = tempfile.mkdtemp()
                    temp_files = []
                    try:
                        for i, seg in enumerate(segments):
                            if should_stop and should_stop():
                                raise StopRequested("用户暂停")
                            tp = os.path.join(temp_dir, f"seg_{i:04d}.mp3")
                            _external_generate(seg, voice, rate, tp, engine, executable, should_stop=should_stop)
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
    """读取进度文件。给老版本（无 chapter_idx）的 items 做兼容补全，避免续传逻辑错乱。"""
    path = os.path.join(output_dir, PROGRESS_FILENAME)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        items = json.load(f)
    if not isinstance(items, list):
        return items
    migrated = False
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        if "chapter_idx" not in item:
            # 老格式：每个 item 是一个独立章节（chapter 模式 / single 模式）
            # 给一个能与新逻辑相容的下标值
            item["chapter_idx"] = idx
            migrated = True
        # 兼容：状态字段缺失时按 pending 处理
        item.setdefault("status", "pending")
    if migrated:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            logger.info(f"已迁移老进度文件: {path}（补充 chapter_idx 字段）")
        except Exception as e:
            logger.warning(f"迁移进度文件写回失败: {e}")
    return items


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
    normalize_audio: bool = False,
    dialogue_detection: bool = False,
    voice_map: dict | None = None,
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
        sel_set = set(selected_indices) if selected_indices is not None else None

        if split_mode == "single":
            items = [{
                "title": file_prefix, "text": text, "filename": f"{file_prefix}.mp3",
                "status": "pending", "chapter_idx": 0,
            }]
        elif split_mode == "chapter":
            for idx, ch in enumerate(chapters):
                source_tag = sanitize_filename(ch.get("source", ""))
                title_tag = sanitize_filename(ch['title'])
                if source_tag and file_prefix != source_tag.replace(".txt", "").replace(".md", ""):
                    fn = f"{idx + 1:03d}_{source_tag}_{title_tag}.mp3"
                else:
                    fn = f"{idx + 1:03d}_{title_tag}.mp3"
                items.append({
                    "title": ch["title"], "text": ch["text"], "filename": fn,
                    "status": "pending", "chapter_idx": idx,
                })
        elif split_mode == "time":
            max_sec = time_minutes * 60
            file_idx = 0
            # 修复：按章节下标过滤；时间拆分后的所有片段都属于同一章节
            for ch_idx, ch in enumerate(chapters):
                if sel_set is not None and ch_idx not in sel_set:
                    continue
                parts = split_by_duration(ch["text"], max_sec, rate)
                if not parts:
                    logger.warning(f"章节 [{ch['title']}] 文本为空，跳过")
                    continue
                for pi, part in enumerate(parts):
                    if not part or not part.strip():
                        continue
                    file_idx += 1
                    label = ch["title"] if ch["title"] != "全文" else ""
                    if len(parts) > 1:
                        suffix = f"_第{pi + 1}部分"
                        label = f"{label}{suffix}" if label else f"第{pi + 1}部分"
                    fn = f"{file_idx:03d}_{sanitize_filename(label or file_prefix)}.mp3"
                    items.append({
                        "title": label or file_prefix, "text": part, "filename": fn,
                        "status": "pending", "chapter_idx": ch_idx, "part_idx": pi,
                    })

        # 选择过滤（time 模式上面已按章节过滤；此处覆盖 chapter / single 模式）
        if sel_set is not None and split_mode != "time":
            for item in items:
                if item.get("chapter_idx", 0) not in sel_set:
                    item["status"] = "skipped"

        save_progress(output_dir, items)

    # 对话检测：对每个待处理 item 运行 detect_dialogue_segments
    if dialogue_detection and voice_map:
        for item in items:
            if item["status"] in ("done", "skipped"):
                continue
            segs = detect_dialogue_segments(item.get("text", ""))
            if segs and any(s["type"] == "dialogue" for s in segs):
                item["segments"] = segs
                item["voice_map"] = voice_map
        save_progress(output_dir, items)

    logger.info(f"批量生成开始: {sum(1 for it in items if it['status'] not in ('done','skipped'))} 个待处理, 引擎={engine}")

    if engine == "edge":
        # 用单一事件循环批量生成
        asyncio.run(_edge_batch_generate(items, voice, rate, output_dir, progress_callback, should_stop))
    elif engine in ("local", "piper", "cosyvoice") or _is_external_engine(engine):
        # Piper CLI 模式：用 ThreadPoolExecutor 并行处理（子进程隔离，线程安全）
        _parallel_piper_done = False
        if engine == "piper":
            try:
                if _get_piper_mode() == PIPER_MODE_CLI:
                    from concurrent.futures import ThreadPoolExecutor, as_completed
                    pending = [(i, it) for i, it in enumerate(items)
                               if it["status"] not in ("done", "skipped")]
                    total_active = len(pending)
                    done_count = 0

                    try:
                        with ThreadPoolExecutor(max_workers=3) as executor:
                            fut_map = {}
                            for idx, item in pending:
                                if should_stop and should_stop():
                                    logger.info("用户暂停生成")
                                    break
                                def _piper_cli_one(it=item):
                                    p = os.path.join(output_dir, it["filename"])
                                    _generate_one_safe(it["text"], voice, rate, p,
                                                       engine="piper", should_stop=should_stop,
                                                       voice_map=it.get("voice_map"),
                                                       dialogue_segments=it.get("segments"))
                                    it["status"] = "done"
                                future = executor.submit(_piper_cli_one)
                                fut_map[future] = idx

                            for future in as_completed(fut_map):
                                idx = fut_map[future]
                                try:
                                    future.result()
                                except StopRequested:
                                    logger.info(f"用户暂停：[{items[idx]['title']}] 未完成")
                                    executor.shutdown(wait=False, cancel_futures=True)
                                    break
                                except Exception as e:
                                    items[idx]["status"] = "error"
                                    items[idx]["error"] = str(e)
                                    logger.error(f"Piper CLI生成失败 [{items[idx]['title']}]: {e}")

                                save_progress(output_dir, items)
                                done_count += 1
                                if progress_callback:
                                    try:
                                        progress_callback(done_count, total_active)
                                    except Exception:
                                        pass
                    finally:
                        _unload_piper_model()
                    _parallel_piper_done = True
            except Exception:
                pass

        if not _parallel_piper_done:
            # 本地/外部引擎同步逐个生成（支持暂停）
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
                        # 把段级进度并入主进度回调（携带章节标题）
                        def _seg_cb(cur, total, _t=item.get("title", "")):
                            if progress_callback:
                                try:
                                    progress_callback(done_count, total_active,
                                                      seg_current=cur, seg_total=total,
                                                      seg_title=_t)
                                except TypeError:
                                    # 老回调签名不接受关键字参数
                                    try:
                                        progress_callback(done_count, total_active)
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                        _generate_one_safe(item["text"], voice, rate, out_path,
                                           engine=engine, should_stop=should_stop,
                                           seg_progress=_seg_cb,
                                           voice_map=item.get("voice_map"),
                                           dialogue_segments=item.get("segments"))
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

    # 可选：响度归一化（loudnorm）
    if normalize_audio and output_files:
        if _ffmpeg_path() is None:
            logger.warning("响度归一化已勾选但未找到 ffmpeg，跳过")
        else:
            logger.info(f"开始响度归一化 {len(output_files)} 个文件")
            for i, p in enumerate(output_files):
                if should_stop and should_stop():
                    logger.info("用户暂停归一化")
                    break
                try:
                    normalize_loudness(p)
                    if progress_callback:
                        try:
                            progress_callback(i + 1, len(output_files),
                                              seg_current=i + 1, seg_total=len(output_files),
                                              seg_title="响度归一化")
                        except TypeError:
                            try:
                                progress_callback(i + 1, len(output_files))
                            except Exception:
                                pass
                except Exception as e:
                    logger.error(f"响度归一化失败 {p}: {e}")

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

def generate_preview(text, voice, rate="+0%", engine="edge", should_stop=None, max_chars: int = 200):
    """生成试听音频。

    max_chars > 0 时截取前 max_chars 字符（用于「快速试听」）；
    max_chars <= 0 时使用全部文本（用于「试听全文」）。
    支持 should_stop 回调用于打断生成。
    """
    if max_chars and max_chars > 0:
        preview_text = text[:max_chars]
    else:
        preview_text = text
    if not preview_text.strip():
        raise ValueError("没有可预览的文本")
    suffix = "_full" if max_chars <= 0 else "_short"
    temp_path = os.path.join(tempfile.gettempdir(), f"audiobook_preview{suffix}.mp3")
    _generate_one_safe(preview_text, voice, rate, temp_path, engine=engine, should_stop=should_stop)
    return temp_path
