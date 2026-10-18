"""TTS引擎封装 - 支持 edge-tts（联网）、系统语音（跨平台离线）、Piper（离线高质量）、CosyVoice（离线神经，经桥接进程）"""

import json
import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
from shutil import which
from typing import Callable, List, Optional

from audiobook.i18n import tr

try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False
    AudioSegment = None

# v6.0/A4：纯文本处理层已迁至 audiobook.core.text；此处再导入以保持模块内部
# 调用与 `from tts_engine import detect_chapters, ...` 等旧用法兼容。
from audiobook.core.text import (  # noqa: E402,F401
    CHARS_PER_SECOND_BASE,
    CHAPTER_PATTERNS,
    DIALOGUE_PATTERNS,
    SPEAKER_PATTERN,
    _SENTENCE_SPLIT_RE,
    estimate_duration,
    sanitize_filename,
    detect_chapters,
    _find_source,
    detect_dialogue_segments,
    extract_speakers,
    _resolve_segment_voice,
    split_text,
    _split_by_sentences,
    split_by_duration,
    generate_srt_from_text,
    _srt_timestamp,
)

# v6.0/A4：音频输出层已迁至 audiobook.io.audio；此处再导入以保持模块内部调用与
# `from tts_engine import merge_mp3_files, export_m4b, ...` 等旧用法兼容。
# （audio.py 在函数内惰性引用 tts_engine 的 ffmpeg/子进程助手，无模块级循环导入。）
from audiobook.io.audio import (  # noqa: E402,F401
    _merge_mp3_files,
    merge_mp3_files,
    get_audio_duration,
    _ffmeta_escape,
    build_ffmetadata_chapters,
    export_m4b,
    write_id3_tags,
    normalize_loudness,
)

# Piper 实现已迁至 audiobook.engines.piper；此处再导入以保持模块内部调用与
# `from tts_engine import refresh_piper_voices, ...` 等旧用法兼容。
# （piper.py 在函数内惰性引用 tts_engine 的共享助手，无模块级循环导入。）
from audiobook.engines.piper import (  # noqa: E402,F401
    PIPER_PYTHON_AVAILABLE,
    PiperVoice,
    PIPER_MODE_PYTHON,
    PIPER_MODE_CLI,
    PIPER_BUILTIN_VOICES,
    PIPER_VOICES,
    PIPER_DEFAULT_VOICE,
    PIPER_MODEL_PATHS,
    PIPER_MODEL_URLS,
    PIPER_CONFIG_URLS,
    PIPER_VOICES_JSON_URL,
    PIPER_VOICES_BASE_URL,
    MAX_PIPER_CACHE,
    fetch_piper_voice_catalog,
    list_piper_catalog_voices,
    download_piper_voice_from_catalog,
    refresh_piper_voices,
    invalidate_piper_voices,
    _ensure_piper_voices,
    _piper_available,
    _piper_install_hint,
    _get_piper_mode,
    _get_piper_model_cache,
    _load_piper_model,
    _unload_piper_model,
    _get_piper_model_path,
    _download_piper_model,
    _ensure_piper_model,
    _piper_generate,
)

# Edge 实现（语音表、异步合成、整批编排）已迁至 audiobook.engines.edge，同样再导入。
from audiobook.engines.edge import (  # noqa: E402,F401
    EDGE_TTS_AVAILABLE,
    edge_tts,
    EDGE_VOICES,
    EDGE_DEFAULT_VOICE,
    _edge_generate,
    _edge_generate_multi,
    _edge_generate_dialogue,
    _edge_batch_generate,
)

VERSION = "6.0.0"

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
                raw = json.load(f) or {}
        else:
            raw = {}
    except Exception as e:
        logger.warning(f"读取配置失败，使用默认值: {e}")
        raw = {}
    # 按 schema 规范化（坏值回退默认、未知键保留）；规范化本身失败则退回原始值
    try:
        from audiobook.config import normalize as _normalize_config
        _config_cache = _normalize_config(raw)
    except Exception as e:
        logger.warning(f"配置规范化失败，使用原始值: {e}")
        _config_cache = raw if isinstance(raw, dict) else {}
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
    # Piper 语音列表随目录变更失效，下次访问时重新扫描
    invalidate_piper_voices()


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
        missing.append(tr("ffmpeg（Piper/本地语音 MP3 转换依赖）"))
    if not PIPER_PYTHON_AVAILABLE and piper_cli is None:
        missing.append(tr("Piper 可执行文件（pip 未装 piper-tts 时需外置 piper）"))
    if not models:
        missing.append(tr("Piper 语音包（.onnx 模型）"))

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
        return False, tr("外部引擎 '{engine_id}' 未安装。\n请将引擎可执行文件放入便携存储目录的 engines/{engine_id}/ 中。", engine_id=engine_id)
    executable = info["executable"]
    if not os.path.isfile(executable):
        return False, tr('引擎可执行文件不存在: {executable}', executable=executable)
    # 尝试获取语音列表验证
    voices = _list_external_voices(engine_id, executable)
    if not voices:
        # 不视为错误：引擎可能还在初始化
        pass
    return True, tr('{v1} 可用（{v2} 个语音）', v1=info['name'], v2=len(voices))


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
        raise RuntimeError(tr("外部引擎 '{engine_id}' 失败 (code {rc}): {stderr}", engine_id=engine_id, rc=rc, stderr=stderr))
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(tr("外部引擎 '{engine_id}' 生成文件为空: {output_path}", engine_id=engine_id, output_path=output_path))


# ======== 通用配置 ========

# （Edge 语音表已迁至 audiobook.engines.edge，顶部再导入）

# ======== 跨平台本地系统语音 ========

# 运行时诊断信息，便于 UI 展示具体失败原因
_LOCAL_UNAVAILABLE_REASON: str = ""

# Windows 上避免子进程弹出黑色控制台窗口
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
                display = tr('{voice_name}（中文）', voice_name=voice_name) if lang == "zh_CN" else tr('{voice_name}（{lang}）', voice_name=voice_name, lang=lang)
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
                gender_tag = tr("女声") if "Female" in gender else (tr("男声") if "Male" in gender else "")
                display = (tr("{name}（{culture}，{gender}）", name=name, culture=culture, gender=gender_tag)
                           if gender_tag else tr("{name}（{culture}）", name=name, culture=culture))
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
            if len(cols) < 4:
                continue
            lang = cols[1]
            voice_name = cols[3]
            voice_file = cols[4] if len(cols) >= 5 else ""
            # 只保留中文语系（普通话 cmn / 粤语 yue / 旧式 zh 别名）
            if not lang.lower().startswith(("zh", "cmn", "yue")):
                continue
            # 跳过 MBROLA 语音（如 chinese-mb-cn1）：需额外安装 mbrola 二进制与
            # 语音数据，新版 espeak-ng 默认缺失会导致合成直接失败。
            if voice_file.startswith("mb/") or "-mb-" in voice_name:
                continue
            # 用语言标识符（cmn / cmn-latn-pinyin 等）作为 voice_id：espeak-ng 能
            # 直接以 `-v <lang>` 解析出可用语音，避免选到不可用的 VoiceName。
            display = tr('{voice_name}（{lang}）', voice_name=voice_name, lang=lang)
            voices[display] = lang
        # 至少保留一个默认选项（bare `zh` 由 espeak-ng 解析为可用的普通话语音）
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
                tr("macOS 未检测到中文语音。\n"
                "请在 系统设置 → 辅助功能 → 语音内容 → 系统语音 中下载中文语音包。")
            )
        return voices

    if _PLATFORM == "Windows":
        ps = which("powershell") or which("pwsh")
        if not ps:
            _LOCAL_UNAVAILABLE_REASON = tr("未找到 powershell，无法调用 Windows 系统语音。")
            return {}
        voices = _detect_local_voices_windows()
        if not voices:
            _LOCAL_UNAVAILABLE_REASON = (
                tr("Windows 未检测到中文语音。\n"
                "请在 设置 → 时间和语言 → 语言 → 添加中文 → 安装语音包；\n"
                "或 设置 → 辅助功能 → 讲述人 → 添加自然语音 中下载中文语音。")
            )
        return voices

    if _PLATFORM == "Linux":
        espeak = which("espeak-ng") or which("espeak")
        if not espeak:
            _LOCAL_UNAVAILABLE_REASON = (
                tr("未检测到 espeak-ng。请安装：\n"
                "  Debian/Ubuntu: sudo apt install espeak-ng\n"
                "  Fedora:        sudo dnf install espeak-ng\n"
                "  Arch:          sudo pacman -S espeak-ng")
            )
            return {}
        return _detect_local_voices_linux()

    _LOCAL_UNAVAILABLE_REASON = tr('暂不支持的平台：{_PLATFORM}', _PLATFORM=_PLATFORM)
    return {}


_local_voices_lock = threading.Lock()
_local_voices_detected = False


def refresh_local_voices() -> None:
    """重新扫描本地语音（在用户安装语音包后可手动触发）"""
    global LOCAL_VOICES, LOCAL_DEFAULT_VOICE, _local_voices_detected
    with _local_voices_lock:
        LOCAL_VOICES = _detect_local_voices()
        LOCAL_DEFAULT_VOICE = list(LOCAL_VOICES.values())[0] if LOCAL_VOICES else ""
        _local_voices_detected = True


def _ensure_local_voices() -> None:
    """懒加载：首次用到系统语音时才枚举。枚举需要调外部进程
    （say / PowerShell / espeak-ng，Windows 上最长 15s），放在导入期会拖慢启动。"""
    if not _local_voices_detected:
        refresh_local_voices()


LOCAL_VOICES: dict = {}
LOCAL_DEFAULT_VOICE = ""

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
# CosyVoice 依赖 torch，装在用户自己的 Python 环境里，本程序经常驻桥接子进程调用
# （audiobook.cloning.client，与「声音克隆」共用环境设置）。本引擎使用 CosyVoice-300M-SFT
# 的预置说话人；零样本复刻见 clone 引擎。

# 界面显示名 → CosyVoice-300M-SFT 预置说话人（官方 spk2info 中的名字）
COSYVOICE_SFT_SPEAKERS = {
    "中文女声": "中文女",
    "中文男声": "中文男",
    "粤语女声": "粤语女",
    "英文女声": "英文女",
    "英文男声": "英文男",
    "日语男声": "日语男",
    "韩语女声": "韩语女",
}
COSYVOICE_VOICES: dict[str, str] = {}
COSYVOICE_DEFAULT_VOICE = ""


def get_cosyvoice_model_dir() -> str:
    """CosyVoice 模型目录（跟随存储目录）"""
    path = os.path.join(get_storage_dir(), "cosyvoice-models")
    os.makedirs(path, exist_ok=True)
    return path


def refresh_cosyvoice_voices() -> None:
    """刷新 CosyVoice 语音列表（预置说话人是固定的；保留此函数供模型增删后统一调用）"""
    global COSYVOICE_VOICES, COSYVOICE_DEFAULT_VOICE
    COSYVOICE_VOICES = dict(COSYVOICE_SFT_SPEAKERS)
    COSYVOICE_DEFAULT_VOICE = next(iter(COSYVOICE_VOICES.values()))


refresh_cosyvoice_voices()
# CosyVoice 始终注册为 builtin（未安装时 UI 显示"需安装"）
register_builtin_engine("cosyvoice", "CosyVoice（离线神经）")
register_builtin_engine("clone", "声音克隆（CosyVoice）")


# CosyVoice 官方模型仓库（整库下载：HuggingFace → hf-mirror → ModelScope 自动回退）。
# 目录名与官方说明的 pretrained_models/<名> 一致；前两个支持零样本复刻（声音克隆），
# 300M-SFT 为预置说话人模型（「CosyVoice」引擎）。
COSYVOICE_MODELS = {
    "Fun-CosyVoice3-0.5B": {
        "repos": {"huggingface": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
                  "modelscope": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"},
        "description": "Fun-CosyVoice3-0.5B（声音克隆推荐，效果最好；数 GB）",
        "skip": ("*.rl.pt",),  # 强化学习版权重，推理默认用 llm.pt
    },
    "CosyVoice2-0.5B": {
        "repos": {"huggingface": "FunAudioLLM/CosyVoice2-0.5B",
                  "modelscope": "iic/CosyVoice2-0.5B"},
        "description": "CosyVoice2-0.5B（声音克隆；数 GB）",
    },
    "CosyVoice-300M-SFT": {
        "repos": {"huggingface": "FunAudioLLM/CosyVoice-300M-SFT",
                  "modelscope": "iic/CosyVoice-300M-SFT"},
        "description": "CosyVoice-300M-SFT（预置中文男女声等，供「CosyVoice」引擎）",
    },
}
# 声音克隆默认下载的模型
COSYVOICE_CLONE_MODEL = "Fun-CosyVoice3-0.5B"


# 历史版本保存过的语音 ID → 预置说话人名（老配置 / 老进度文件仍可能带着它们）
_COSYVOICE_SPK_ALIASES = {
    "default_female": "中文女",
    "default_male": "中文男",
}


def _cosyvoice_install_hint() -> str:
    """CosyVoice 安装引导提示"""
    return (
        tr("CosyVoice 需要单独安装（依赖 torch）：\n"
        "1. 按 README「声音克隆」一节安装 CosyVoice，并在「🎙 克隆音色… → 环境设置」里填写其 Python 与仓库路径；\n"
        "2. 在「模型管理」下载 CosyVoice-300M-SFT 模型；\n"
        "3. 或将 CosyVoice 可执行文件放入 engines/cosyvoice/ 作为外挂引擎")
    )


def _download_cosyvoice_model(model_key: str, should_stop=None):
    """整库下载 CosyVoice 模型到 cosyvoice-models/<model_key>/（可续传，完成后才出现目录）"""
    meta = COSYVOICE_MODELS.get(model_key)
    if meta is None:
        raise ValueError(tr('不支持的 CosyVoice 模型: {model_key}（可选：{v1}）', model_key=model_key, v1=', '.join(COSYVOICE_MODELS)))
    from audiobook.io import hub
    extract_dir = os.path.join(get_cosyvoice_model_dir(), model_key)
    hub.snapshot_download(meta["repos"], extract_dir, f"CosyVoice {model_key}",
                          should_stop=should_stop, skip=meta.get("skip", ()))
    logger.info(f"CosyVoice 模型就绪: {extract_dir}")
    return extract_dir


def _ensure_cosyvoice_model(model_key: str = "CosyVoice-300M-SFT", should_stop=None):
    """确保 CosyVoice 模型存在，不存在则下载"""
    model_dir = os.path.join(get_cosyvoice_model_dir(), model_key)
    if not os.path.isdir(model_dir) or not os.listdir(model_dir):
        logger.info(f"CosyVoice 模型不存在，开始下载: {model_key}")
        _download_cosyvoice_model(model_key, should_stop=should_stop)
    return model_dir



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
                raise StopRequested(tr("用户暂停"))
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
        _ensure_local_voices()
        return list(LOCAL_VOICES.keys())
    elif engine == "piper":
        _ensure_piper_voices()
        return list(PIPER_VOICES.keys())
    elif engine == "cosyvoice":
        return list(COSYVOICE_VOICES.keys())
    elif engine == "clone":
        from audiobook.cloning.profiles import voice_map
        return list(voice_map().keys())
    # 外部引擎
    ext_engines = _scan_external_engines()
    if engine in ext_engines:
        return ext_engines[engine].get("voices", [])
    return list(EDGE_VOICES.keys())


def get_voice_id(display_name, engine="edge"):
    if engine == "local":
        _ensure_local_voices()
        return LOCAL_VOICES.get(display_name, LOCAL_DEFAULT_VOICE)
    elif engine == "piper":
        _ensure_piper_voices()
        return PIPER_VOICES.get(display_name, PIPER_DEFAULT_VOICE)
    elif engine == "cosyvoice":
        return COSYVOICE_VOICES.get(display_name, COSYVOICE_DEFAULT_VOICE)
    elif engine == "clone":
        from audiobook.cloning.profiles import voice_map, VOICE_PREFIX
        if isinstance(display_name, str) and display_name.startswith(VOICE_PREFIX):
            return display_name  # 已是 voice_id
        vm = voice_map()
        return vm.get(display_name) or next(iter(vm.values()), "")
    # 外部引擎：display_name 本身就是 voice ID
    ext_engines = _scan_external_engines()
    if engine in ext_engines:
        return display_name
    return EDGE_VOICES.get(display_name, EDGE_DEFAULT_VOICE)


def _ffmpeg_path() -> Optional[str]:
    """动态解析 ffmpeg 路径（便携目录优先）"""
    return _which_portable("ffmpeg")


def _ffmpeg_install_hint() -> str:
    if _PLATFORM == "Darwin":
        return "macOS: brew install ffmpeg"
    if _PLATFORM == "Windows":
        return tr("Windows: 从 https://www.gyan.dev/ffmpeg/builds/ 下载 ffmpeg.exe，放入便携 bin 目录或系统 PATH")
    return tr("Linux: sudo apt install ffmpeg（或对应发行版包管理器）")


def check_engine_ready(engine="edge"):
    """检测引擎是否可用，返回 (ready: bool, message: str)"""
    if engine == "edge":
        if not EDGE_TTS_AVAILABLE:
            return False, tr("Edge TTS 未安装。\n请运行: pip install edge-tts")
        return True, tr("Edge TTS 可用（微软在线语音，需要联网）")

    if engine == "local":
        _ensure_local_voices()
        if LOCAL_VOICES:
            plat_name = {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}.get(_PLATFORM, _PLATFORM)
            return True, tr('{plat_name} 系统语音可用（{v1} 个中文语音）', plat_name=plat_name, v1=len(LOCAL_VOICES))
        reason = _LOCAL_UNAVAILABLE_REASON or tr("未检测到中文系统语音")
        return False, tr('本地语音不可用：\n{reason}', reason=reason)

    if engine == "piper":
        if not _piper_available():
            return False, tr("Piper 未安装。\n") + _piper_install_hint()
        if _ffmpeg_path() is None:
            return False, tr("ffmpeg 未安装（Piper 需要 ffmpeg 转换音频）。\n") + _ffmpeg_install_hint()
        return True, tr("Piper 可用（首次使用将自动下载语音模型）")

    if engine == "clone":
        from audiobook.cloning.client import readiness
        return readiness()

    if engine == "cosyvoice":
        from audiobook.cloning.client import sft_readiness
        ok, msg = sft_readiness()
        if ok:
            return True, msg
        ext = _scan_external_engines()
        if "cosyvoice" in ext:
            return True, tr('CosyVoice 外挂引擎可用（{v1}）', v1=ext['cosyvoice'].get('name'))
        return False, msg + "\n\n" + _cosyvoice_install_hint()

    # 外部引擎
    if _is_external_engine(engine):
        return check_external_engine_ready(engine)

    return False, tr('未知引擎: {engine}', engine=engine)


# estimate_duration / sanitize_filename 已迁至 audiobook.core.text（顶部再导入）


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


# ======== 对话识别（多人对话） ========


# ======== 文本分段 ========


def _download_file_with_progress(url, filepath, description="", timeout=60, should_stop=None):
    """下载文件并显示进度，支持镜像回退"""
    urls_to_try = [url, _get_mirror_url(url)]
    last_exception = None

    for try_url in urls_to_try:
        if should_stop and should_stop():
            raise StopRequested(tr("用户暂停"))
        try:
            _download_file_single(try_url, filepath, description, timeout, should_stop=should_stop)
            return
        except StopRequested:
            raise
        except Exception as e:
            last_exception = e
            logger.warning(f"下载失败，尝试镜像: {try_url} -> {e}")

    raise RuntimeError(tr('所有下载源均失败: {last_exception}', last_exception=last_exception))


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
                raise StopRequested(tr("用户暂停"))
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


# ======== 音频文件合并 ========


# ======== 音频时长 / m4b 有声书 / ID3 元数据 / 字幕 ========


# ======== 并发数配置 ========

def _config_concurrency(key: str) -> int:
    """从 config.json 读取并发数覆盖值，未配置或非法时返回 0"""
    try:
        val = int(_load_config().get(key, 0))
        return val if val > 0 else 0
    except Exception:
        return 0


def get_edge_concurrency() -> int:
    """Edge 在线引擎并发数：网络绑定，默认保守值，config.json 的
    edge_concurrency 可覆盖（上限 10，避免触发微软限流）"""
    override = _config_concurrency("edge_concurrency")
    return min(override, 10) if override else CONCURRENCY


def get_local_concurrency() -> int:
    """本地引擎（Piper 等）并行数：默认按 CPU 核数自适应，
    config.json 的 concurrency 可覆盖（上限 16）"""
    override = _config_concurrency("concurrency")
    if override:
        return min(override, 16)
    cpu = os.cpu_count() or 2
    return max(2, min(4, cpu // 2))


# ======== 本地 TTS 引擎 (跨平台) ========

def _wav_to_mp3(wav_path: str, mp3_path: str) -> None:
    """用 pydub/ffmpeg 或直接调用 ffmpeg 将 WAV 转为 MP3"""
    if PYDUB_AVAILABLE and _configure_pydub_ffmpeg():
        audio = AudioSegment.from_wav(wav_path)
        audio.export(mp3_path, format="mp3", bitrate="128k")
        return

    ff = _ffmpeg_path()
    if not ff:
        raise RuntimeError(tr("ffmpeg 不可用，无法转换为 MP3。\n") + _ffmpeg_install_hint())
    subprocess.run(
        [ff, "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-b:a", "128k", mp3_path],
        check=True, capture_output=True,
        **_quiet_popen_kwargs(),
    )


# PowerShell：使用 SAPI 合成到 WAV


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
                raise StopRequested(tr("用户暂停"))

            seg_text = seg["text"]
            seg_voice = _resolve_segment_voice(seg, default_voice, voice_map)

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


def _resolve_engine(engine):
    """按 id 从注册表取引擎实例；未知 id 报出与旧版一致的 ValueError。"""
    from audiobook.engines.base import get_engine
    try:
        return get_engine(engine)
    except KeyError:
        raise ValueError(tr('不支持的引擎类型: {engine}', engine=engine)) from None


def _raw_segment_synth(engine):
    """返回外挂插件引擎的「单段原语」合成函数：callable(text, voice, rate, out_path, should_stop=None)。

    内置引擎（local/edge/piper/cosyvoice/clone）都已是独立的 Engine 子类、各自实现
    synthesize_segment；本函数只服务经 `_TtsEngineAdapter` 包装的外挂插件引擎。
    """
    ext_info = _scan_external_engines().get(engine)
    if not ext_info:
        raise RuntimeError(tr("外部引擎 '{engine}' 不可用", engine=engine))
    executable = ext_info["executable"]

    def _ext(text, voice, rate, out_path, should_stop=None):
        _external_generate(text, voice, rate, out_path, engine, executable, should_stop=should_stop)
    return _ext


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
    # 先解析引擎：未知 id 直接报错，不进入重试循环
    # （旧版会把任何未知 id 静默当成 Edge 处理）
    seg_synth = _resolve_engine(engine).synthesize_segment

    def _notify(i):
        if seg_progress and total_segs > 1:
            try:
                seg_progress(i + 1, total_segs)
            except Exception:
                pass

    for attempt in range(MAX_RETRIES + 1):
        if should_stop and should_stop():
            raise StopRequested(tr("用户暂停"))
        try:
            logger.info(f"生成单文件 → {output_path} (尝试 {attempt + 1}, {total_segs} 段)")

            # 引擎只负责单段合成；分段 / 合并 / 进度 / 重试的编排都在本函数
            if total_segs == 1:
                seg_synth(segments[0], voice, rate, output_path, should_stop=should_stop)
            else:
                temp_dir = tempfile.mkdtemp()
                temp_files = []
                try:
                    for i, seg in enumerate(segments):
                        if should_stop and should_stop():
                            raise StopRequested(tr("用户暂停"))
                        tp = os.path.join(temp_dir, f"seg_{i:04d}.mp3")
                        seg_synth(seg, voice, rate, tp, should_stop=should_stop)
                        temp_files.append(tp)
                        _notify(i)
                    _merge_mp3_files(temp_files, output_path)
                finally:
                    shutil.rmtree(temp_dir, ignore_errors=True)

            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                logger.info(f"完成: {output_path} ({os.path.getsize(output_path)} bytes)")
                return
            else:
                raise RuntimeError(tr('生成文件为空: {output_path}', output_path=output_path))

        except StopRequested:
            raise
        except Exception as e:
            delay = RETRY_DELAY * (2 ** attempt)
            logger.error(f"生成失败 尝试{attempt + 1}/{MAX_RETRIES + 1}: {e}")
            if attempt < MAX_RETRIES:
                logger.info(f"等待 {delay}秒 后重试...")
                if _interruptible_sleep(delay, should_stop):
                    raise StopRequested(tr("用户暂停"))
            else:
                raise


# ======== 进度管理 ========

def save_progress(output_dir, items):
    path = os.path.join(output_dir, PROGRESS_FILENAME)
    # 以下划线开头的键是单次运行内的临时标记（如 _hold），不落盘
    clean = [{k: v for k, v in it.items() if not str(k).startswith("_")}
             if isinstance(it, dict) else it for it in items]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)


# ======== 生成队列事件（供界面逐章展示状态） ========

def _is_active(item) -> bool:
    """本次运行需要处理的条目：未完成、未跳过，且未被 only_indices 暂挂。"""
    return item["status"] not in ("done", "skipped") and not item.get("_hold")


def _queue_snapshot(items) -> list:
    return [{"index": i, "title": it.get("title", ""), "filename": it.get("filename", ""),
             "status": it.get("status", "pending"), "error": it.get("error", "")}
            for i, it in enumerate(items)]


def _emit_item(item_callback, event: str, **data) -> None:
    """向 item_callback(event, data) 推送逐章事件；回调异常不影响生成。

    事件：init / final（data["items"] 为全量快照）、running、done、error（含 error）、
    pending（已开始但被暂停，回到等待）。
    """
    if not item_callback:
        return
    try:
        item_callback(event, data)
    except Exception as e:
        logger.debug(f"item_callback 异常（已忽略）: {e}")


def _emit_item_result(item_callback, index: int, item, started: bool) -> None:
    status = item.get("status")
    if status == "done":
        _emit_item(item_callback, "done", index=index)
    elif status == "error":
        _emit_item(item_callback, "error", index=index, error=item.get("error", ""))
    elif started:
        _emit_item(item_callback, "pending", index=index)


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

def _run_items_parallel(items, voice, rate, output_dir, engine, workers,
                        progress_callback, should_stop, item_callback):
    """通用编排·并行：线程池逐章 `_generate_one_safe`（引擎须能安全并发合成）。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    pending = [(i, it) for i, it in enumerate(items) if _is_active(it)]
    total_active = len(pending)
    done_count = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        fut_map = {}
        for idx, item in pending:
            if should_stop and should_stop():
                logger.info("用户暂停生成")
                break

            def _one(it=item, i=idx):
                it.pop("error", None)  # 重试时清掉上次的错误信息
                _emit_item(item_callback, "running", index=i)
                p = os.path.join(output_dir, it["filename"])
                _generate_one_safe(it["text"], voice, rate, p,
                                   engine=engine, should_stop=should_stop,
                                   voice_map=it.get("voice_map"),
                                   dialogue_segments=it.get("segments"))
                it["status"] = "done"
            fut_map[executor.submit(_one)] = idx

        for future in as_completed(fut_map):
            idx = fut_map[future]
            try:
                future.result()
                _emit_item(item_callback, "done", index=idx)
            except StopRequested:
                logger.info(f"用户暂停：[{items[idx]['title']}] 未完成")
                _emit_item(item_callback, "pending", index=idx)
                executor.shutdown(wait=False, cancel_futures=True)
                break
            except Exception as e:
                items[idx]["status"] = "error"
                items[idx]["error"] = str(e)
                logger.error(f"{engine}引擎生成失败 [{items[idx]['title']}]: {e}")
                _emit_item(item_callback, "error", index=idx, error=str(e))

            save_progress(output_dir, items)
            done_count += 1
            if progress_callback:
                try:
                    progress_callback(done_count, total_active)
                except Exception:
                    pass


def _run_items_sequential(items, voice, rate, output_dir, engine,
                          progress_callback, should_stop, item_callback):
    """通用编排·串行：逐章 `_generate_one_safe`，章内段级进度并入主进度回调。"""
    done_count = 0
    total_active = sum(1 for it in items if _is_active(it))

    for idx, item in enumerate(items):
        if not _is_active(item):
            continue
        if should_stop and should_stop():
            logger.info("用户暂停生成")
            break

        out_path = os.path.join(output_dir, item["filename"])
        item.pop("error", None)  # 重试时清掉上次的错误信息
        _emit_item(item_callback, "running", index=idx)
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
            _emit_item(item_callback, "done", index=idx)
        except StopRequested:
            logger.info(f"用户暂停：[{item['title']}] 未完成")
            _emit_item(item_callback, "pending", index=idx)
            save_progress(output_dir, items)
            break
        except Exception as e:
            item["status"] = "error"
            item["error"] = str(e)
            logger.error(f"{engine}引擎生成失败 [{item['title']}]: {e}")
            _emit_item(item_callback, "error", index=idx, error=str(e))

        save_progress(output_dir, items)
        done_count += 1
        if progress_callback:
            try:
                progress_callback(done_count, total_active)
            except Exception:
                pass



def convert_batch(
    text,
    voice,
    rate,
    output_dir,
    split_mode="chapter",
    time_minutes=30,
    file_prefix=None,
    selected_indices=None,
    engine="edge",
    progress_callback=None,
    should_stop=None,
    resume=False,
    normalize_audio: bool = False,
    dialogue_detection: bool = False,
    voice_map: dict | None = None,
    generate_subtitles: bool = False,
    write_metadata: bool = False,
    album_title: str = "",
    item_callback=None,
    only_indices=None,
):
    """批量生成有声书。

    item_callback(event, data)：可选的逐章事件回调（生成队列可视化用），见 _emit_item。
    only_indices：可选，仅处理这些条目下标（配合 resume=True 实现单项 / 失败项重试），
    其余未完成条目本次保持原状态不动。
    """
    os.makedirs(output_dir, exist_ok=True)
    if not file_prefix:
        file_prefix = tr("有声读物")

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
                # 仅当章节有真实来源文件名时才注入来源标签；直接输入的文本
                # source 为空，sanitize_filename("") 会返回 "untitled"，因此需先判空。
                raw_source = (ch.get("source") or "").strip()
                source_tag = sanitize_filename(raw_source) if raw_source else ""
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
            # 合并所有选中章节为整段文本，再按时长拆分，确保每个文件接近
            # 用户设定的目标时长（之前的"按章节再拆分"会让短章节生成几秒到十几秒
            # 的碎片文件）。
            max_sec = time_minutes * 60
            selected_chapters = [
                ch for ch_idx, ch in enumerate(chapters)
                if sel_set is None or ch_idx in sel_set
            ]
            # 用双换行拼接，保留段落边界，章节标题也作为文本一部分朗读
            joined_parts = []
            for ch in selected_chapters:
                title = (ch.get("title") or "").strip()
                body = (ch.get("text") or "").strip()
                if not body:
                    continue
                if title and title != "全文" and not body.startswith(title):
                    joined_parts.append(f"{title}\n\n{body}")
                else:
                    joined_parts.append(body)
            joined_text = "\n\n".join(joined_parts).strip()

            if not joined_text:
                logger.warning("按时间拆分：没有可用文本")
                parts = []
            else:
                parts = split_by_duration(joined_text, max_sec, rate)

            total_parts = len(parts)
            for pi, part in enumerate(parts):
                if not part or not part.strip():
                    continue
                file_idx = pi + 1
                if total_parts > 1:
                    label = tr('{file_prefix}_第{file_idx}部分', file_prefix=file_prefix, file_idx=file_idx)
                else:
                    label = file_prefix
                fn = f"{file_idx:03d}_{sanitize_filename(label)}.mp3"
                items.append({
                    "title": label, "text": part, "filename": fn,
                    "status": "pending", "chapter_idx": 0, "part_idx": pi,
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

    # 单项 / 失败项重试：只处理 only_indices，其余未完成条目本次暂挂（状态不变、不落盘）
    for it in items:
        it.pop("_hold", None)
    if only_indices is not None:
        only = set(only_indices)
        for i, it in enumerate(items):
            if i not in only and it["status"] not in ("done", "skipped"):
                it["_hold"] = True

    logger.info(f"批量生成开始: {sum(1 for it in items if _is_active(it))} 个待处理, 引擎={engine}")
    _emit_item(item_callback, "init", items=_queue_snapshot(items))

    # 经注册表分发：引擎可自带整批编排（Edge 的异步并发），否则走通用编排——
    # 按引擎声明的并行度用线程池并行（Piper）或逐章串行（其余引擎）。
    eng = _resolve_engine(engine)
    try:
        handled = eng.run_batch(items, voice, rate, output_dir,
                                progress_callback=progress_callback,
                                should_stop=should_stop, item_callback=item_callback)
        if not handled:
            workers = eng.batch_parallelism()
            if workers > 1:
                _run_items_parallel(items, voice, rate, output_dir, engine, workers,
                                    progress_callback, should_stop, item_callback)
            else:
                _run_items_sequential(items, voice, rate, output_dir, engine,
                                      progress_callback, should_stop, item_callback)
    finally:
        eng.release()

    # 生成阶段结束：解除暂挂标记，推送全量快照让界面与真实状态对齐
    # （并行 / 暂停等路径下个别逐项事件可能缺失，以 final 为准）
    for it in items:
        it.pop("_hold", None)
    _emit_item(item_callback, "final", items=_queue_snapshot(items))

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
                                              seg_title=tr("响度归一化"))
                        except TypeError:
                            try:
                                progress_callback(i + 1, len(output_files))
                            except Exception:
                                pass
                except Exception as e:
                    logger.error(f"响度归一化失败 {p}: {e}")

    # 可选：生成 srt 字幕（按句估算时间轴，引擎通用）
    done_items = [it for it in items if it["status"] == "done"]
    if generate_subtitles and done_items:
        logger.info(f"生成 {len(done_items)} 个 srt 字幕文件")
        for it in done_items:
            if should_stop and should_stop():
                break
            mp3_path = os.path.join(output_dir, it["filename"])
            try:
                duration = get_audio_duration(mp3_path)
                srt = generate_srt_from_text(it.get("text", ""), duration)
                if srt:
                    srt_path = os.path.splitext(mp3_path)[0] + ".srt"
                    with open(srt_path, "w", encoding="utf-8") as f:
                        f.write(srt)
            except Exception as e:
                logger.error(f"字幕生成失败 {mp3_path}: {e}")

    # 可选：写入 ID3 元数据（标题=章节名，专辑=书名，音轨号=顺序）
    if write_metadata and done_items:
        if _ffmpeg_path() is None:
            logger.warning("已勾选写入元数据但未找到 ffmpeg，跳过")
        else:
            album = album_title or file_prefix
            logger.info(f"写入 ID3 元数据: {len(done_items)} 个文件, 专辑={album}")
            for track_no, it in enumerate(done_items, 1):
                if should_stop and should_stop():
                    break
                mp3_path = os.path.join(output_dir, it["filename"])
                try:
                    write_id3_tags(mp3_path, title=it.get("title", ""),
                                   album=album, track=track_no)
                except Exception as e:
                    logger.error(f"元数据写入失败 {mp3_path}: {e}")

    # 全部完成则清理进度
    all_done = all(it["status"] in ("done", "skipped") for it in items)
    if all_done:
        clear_progress(output_dir)
        logger.info("全部完成，已清理进度文件")

    error_count = sum(1 for it in items if it["status"] == "error")
    if error_count:
        logger.warning(f"{error_count} 个文件生成失败")
        # 全部失败时向上层抛出详细错误，让 GUI 能弹出提示
        active_count = sum(1 for it in items if it["status"] in ("pending", "error"))
        if error_count == active_count and not output_files:
            first_errors = []
            for it in items:
                if it.get("error"):
                    first_errors.append(f"  [{it.get('title', '?')}] {it['error'][:200]}")
                    if len(first_errors) >= 3:
                        break
            detail = "\n".join(first_errors)
            raise RuntimeError(
                tr('全部 {error_count} 个文件生成失败，请检查后重试。\n\n{detail}', error_count=error_count, detail=detail)
            )

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
        raise ValueError(tr("没有可预览的文本"))
    suffix = "_full" if max_chars <= 0 else "_short"
    temp_path = os.path.join(tempfile.gettempdir(), f"audiobook_preview{suffix}.mp3")
    _generate_one_safe(preview_text, voice, rate, temp_path, engine=engine, should_stop=should_stop)
    return temp_path
