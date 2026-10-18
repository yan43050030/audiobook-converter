"""audiobook.engines.piper — Piper（离线神经网络）TTS 引擎。

本模块持有 Piper 的全部实现：语音列表与官方语音目录、模型下载与线程本地缓存、
Python 包 / CLI 两种合成后端，以及 `PiperEngine`。`tts_engine` 只保留同名再导出
（兼容旧 import、GUI 与 PyInstaller）。

对 `tts_engine` 共享助手（下载器、子进程、ffmpeg、`StopRequested`、配置路径等）一律
在函数内以 `import tts_engine as _te` 惰性引用：既避免模块加载期循环导入，也让测试对
`tts_engine` 属性的 patch 继续生效。

被外部再导出的可变状态（`PIPER_VOICES` / `PIPER_MODEL_PATHS`）只做原地更新、从不重新
赋值，保证 `from tts_engine import PIPER_VOICES` 拿到的始终是同一个对象。
"""

import logging
import os
import threading
import wave
from collections import OrderedDict
from typing import List, Optional

from audiobook.engines.base import Engine
from audiobook.i18n import tr

# 可选导入 - Piper Python 包
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

logger = logging.getLogger("audiobook_converter")

PIPER_MODE_PYTHON = "python"
PIPER_MODE_CLI = "cli"

# ======== 语音列表 ========

PIPER_BUILTIN_VOICES = {
    "Piper中文女声（中等质量）": "zh_CN-huayan-medium",
    "Piper中文女声（较低质量）": "zh_CN-huayan-low",
}
PIPER_VOICES = dict(PIPER_BUILTIN_VOICES)
PIPER_DEFAULT_VOICE = "zh_CN-huayan-medium"

# 已下载模型的 voice_id → 模型文件绝对路径（含额外搜索路径中发现的模型）
PIPER_MODEL_PATHS: dict = {}

# 内置语音的下载地址（Hugging Face，下载器自动回退 hf-mirror）
PIPER_MODEL_URLS = {
    "zh_CN-huayan-medium": "https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx",
    "zh_CN-huayan-low": "https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/low/zh_CN-huayan-low.onnx",
}
# 模型配置URL（与模型文件同名，扩展名.json）
PIPER_CONFIG_URLS = {
    "zh_CN-huayan-medium": "https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx.json",
    "zh_CN-huayan-low": "https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/low/zh_CN-huayan-low.onnx.json",
}

_piper_voices_scanned = False


def _ensure_piper_voices() -> None:
    """懒扫描：首次访问 Piper 语音列表时才扫描模型目录"""
    if not _piper_voices_scanned:
        refresh_piper_voices()


def invalidate_piper_voices() -> None:
    """让语音列表在下次访问时重新扫描（存储目录 / 搜索路径变更后调用）"""
    global _piper_voices_scanned
    _piper_voices_scanned = False


def refresh_piper_voices() -> None:
    """重新扫描 piper-models/ 与额外搜索路径，把已下载模型并入语音列表"""
    import tts_engine as _te
    global _piper_voices_scanned
    _piper_voices_scanned = True
    voices = dict(PIPER_BUILTIN_VOICES)
    paths: dict = {}
    builtin_ids = set(PIPER_BUILTIN_VOICES.values())

    roots = [_te.get_piper_model_dir()] + _te.get_model_search_paths()
    seen_ids = set()
    for root in roots:
        for onnx in _te._search_models_in_tree(root):
            if not os.path.isfile(onnx + ".json"):
                continue  # 缺配置文件的模型无法使用
            vid = os.path.splitext(os.path.basename(onnx))[0]
            if vid in seen_ids:
                continue
            seen_ids.add(vid)
            paths[vid] = os.path.abspath(onnx)
            if vid not in builtin_ids:
                voices[f"Piper {vid}"] = vid

    PIPER_MODEL_PATHS.clear()
    PIPER_MODEL_PATHS.update(paths)
    PIPER_VOICES.clear()
    PIPER_VOICES.update(voices)


# ======== 官方语音目录（voices.json） ========

PIPER_VOICES_JSON_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json"
PIPER_VOICES_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"


def fetch_piper_voice_catalog(force_refresh: bool = False, should_stop=None) -> dict:
    """下载并缓存 Piper 官方语音目录 voices.json（数百个语音，覆盖多语言）。

    返回原始目录 dict：{voice_key: {language, quality, files, ...}}
    """
    import json
    import tts_engine as _te
    cache_path = os.path.join(_te.get_piper_model_dir(), "voices_catalog.json")
    if not force_refresh and os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"语音目录缓存损坏，重新下载: {e}")
    # 强制刷新/缓存损坏时删除旧文件，避免断点续传逻辑向旧文件追加
    if os.path.exists(cache_path):
        try:
            os.remove(cache_path)
        except Exception:
            pass
    _te._download_file_with_progress(PIPER_VOICES_JSON_URL, cache_path,
                                     tr("Piper 语音目录"), should_stop=should_stop)
    with open(cache_path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_piper_catalog_voices(catalog: dict, language_prefix: str = "") -> list:
    """把 voices.json 解析为便于展示的列表，可按语言代码前缀过滤（如 "zh"）。"""
    out = []
    for key, meta in sorted(catalog.items()):
        if not isinstance(meta, dict):
            continue
        lang = meta.get("language", {}) or {}
        code = lang.get("code", "")
        if language_prefix and not code.lower().startswith(language_prefix.lower()):
            continue
        files = meta.get("files", {}) or {}
        out.append({
            "key": key,
            "language_code": code,
            "language_name": lang.get("name_native") or lang.get("name_english") or code,
            "quality": meta.get("quality", ""),
            "num_speakers": meta.get("num_speakers", 1),
            "size_bytes": sum((f or {}).get("size_bytes", 0) for f in files.values()),
        })
    return out


def download_piper_voice_from_catalog(voice_key: str, should_stop=None) -> str:
    """按语音目录下载指定语音的 .onnx + .onnx.json 到 piper-models/，返回模型路径"""
    import tts_engine as _te
    catalog = fetch_piper_voice_catalog(should_stop=should_stop)
    meta = catalog.get(voice_key)
    if not meta:
        raise ValueError(tr('语音目录中没有: {voice_key}', voice_key=voice_key))

    model_dir = _te.get_piper_model_dir()
    onnx_path = None
    for fpath in (meta.get("files", {}) or {}):
        if not (fpath.endswith(".onnx") or fpath.endswith(".onnx.json")):
            continue  # 跳过 MODEL_CARD 等
        fname = os.path.basename(fpath)
        dst = os.path.join(model_dir, fname)
        _te._download_file_with_progress(PIPER_VOICES_BASE_URL + fpath, dst,
                                         f"Piper {fname}", should_stop=should_stop)
        if fpath.endswith(".onnx"):
            onnx_path = dst

    if not onnx_path:
        raise RuntimeError(tr('语音 {voice_key} 的目录条目中没有 .onnx 模型文件', voice_key=voice_key))
    refresh_piper_voices()
    return onnx_path


# ======== 可用性 ========

def _piper_install_hint() -> str:
    return (
        tr("方式 1: pip install piper-tts\n"
        "方式 2: 从 https://github.com/rhasspy/piper/releases 下载 piper 可执行文件，\n"
        "        放入便携 bin 目录或系统 PATH")
    )


def _piper_available() -> bool:
    """动态检测 Piper 是否可用（Python 包或便携 / PATH 中的 CLI）"""
    if PIPER_PYTHON_AVAILABLE:
        return True
    import tts_engine as _te
    return _te._refresh_piper_cli_path() is not None


def _get_piper_mode() -> str:
    """确定Piper运行模式：优先Python包，其次CLI（便携目录优先）"""
    if PIPER_PYTHON_AVAILABLE:
        return PIPER_MODE_PYTHON
    import tts_engine as _te
    if _te._refresh_piper_cli_path():
        return PIPER_MODE_CLI
    raise RuntimeError(tr("Piper TTS 不可用。\n") + _piper_install_hint())


# ======== 模型缓存（LRU，每线程上限 2 个模型） ========

MAX_PIPER_CACHE = 2

# PiperVoice / onnxruntime 会话跨线程共享不安全：并行生成时每个工作线程
# 持有独立的模型实例（线程本地缓存），主线程串行使用时行为与全局缓存一致。
_piper_tls = threading.local()
_piper_all_caches: List[OrderedDict] = []
_piper_caches_lock = threading.Lock()
_piper_download_lock = threading.Lock()


def _get_piper_model_cache() -> OrderedDict:
    cache = getattr(_piper_tls, "cache", None)
    if cache is None:
        cache = OrderedDict()
        _piper_tls.cache = cache
        with _piper_caches_lock:
            _piper_all_caches.append(cache)
    return cache


def _load_piper_model(voice_name, should_stop=None):
    """加载Piper模型，使用缓存避免重复加载"""
    model_path = _ensure_piper_model(voice_name, should_stop=should_stop)
    config_path = model_path + ".json"
    cache_key = (voice_name, model_path)
    cache = _get_piper_model_cache()

    if cache_key in cache:
        logger.debug(f"使用缓存的Piper模型: {voice_name}")
        cache.move_to_end(cache_key)  # LRU: 标记为最近使用
        return cache[cache_key]

    mode = _get_piper_mode()

    if mode == PIPER_MODE_PYTHON:
        if not os.path.exists(config_path):
            raise FileNotFoundError(tr('Piper配置文件不存在: {config_path}', config_path=config_path))
        voice_model = PiperVoice.load(model_path, _piper_config_path(model_path))
        # LRU：达到上限时淘汰最久未使用的
        if len(cache) >= MAX_PIPER_CACHE:
            cache.popitem(last=False)
            logger.debug("LRU淘汰：Piper模型缓存已满")
        cache[cache_key] = voice_model
        logger.info(f"Piper模型已加载并缓存: {voice_name} (缓存大小={len(cache)})")
        return voice_model
    else:
        # CLI模式：仅验证文件存在，不加载到内存
        if not os.path.exists(config_path):
            raise FileNotFoundError(tr('Piper配置文件不存在: {config_path}', config_path=config_path))
        cache[cache_key] = None  # CLI模式缓存标记
        logger.info(f"Piper CLI模式就绪: {voice_name}")
        return None


def _unload_piper_model(voice_name=None):
    """卸载Piper模型，释放内存（清空所有线程的缓存；在批量结束后调用）"""
    with _piper_caches_lock:
        for cache in _piper_all_caches:
            if voice_name is None:
                cache.clear()
            else:
                for key in [k for k in cache if k[0] == voice_name]:
                    del cache[key]
    if voice_name is None:
        logger.info("已卸载所有Piper模型")
    else:
        logger.info(f"已卸载Piper模型: {voice_name}")


# ======== 模型文件 ========

def _piper_config_path(model_path: str) -> str:
    """返回可供 piper 加载的配置文件路径。

    早期发布的语音包（rhasspy/piper GitHub Release v0.0.x，网上教程常引用）把枚举
    序列化成了 `"phoneme_type": "PhonemeType.ESPEAK"`，piper-tts 1.x 只接受
    `"espeak"`，会报 "'PhonemeType.ESPEAK' is not a valid PhonemeType"。遇到这种
    旧写法时写一份规范化副本到临时目录并返回其路径，不改动用户的原文件。
    """
    import hashlib
    import json
    import tempfile
    config_path = model_path + ".json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return config_path  # 读不了就原样交给 piper，由它报出具体错误
    pt = cfg.get("phoneme_type")
    if not (isinstance(pt, str) and pt.startswith("PhonemeType.")):
        return config_path
    cfg["phoneme_type"] = pt.split(".", 1)[1].lower()
    st = os.stat(config_path)
    key = hashlib.sha1(f"{os.path.abspath(config_path)}|{st.st_mtime_ns}|{st.st_size}"
                       .encode("utf-8")).hexdigest()[:12]
    out_dir = os.path.join(tempfile.gettempdir(), "audiobook_piper_configs")
    os.makedirs(out_dir, exist_ok=True)
    fixed = os.path.join(out_dir, f"{os.path.basename(model_path)}.{key}.json")
    if not os.path.isfile(fixed):
        tmp = fixed + f".{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        os.replace(tmp, fixed)  # 原子替换：并行工作线程同时写也不会读到半截文件
    logger.info(f"Piper 配置为旧格式（{pt}），已使用兼容副本: {fixed}")
    return fixed

def _get_piper_model_path(voice_name):
    """获取Piper模型文件路径。已扫描到的模型（含额外搜索路径）优先，
    否则落到 piper-models/ 默认位置（不存在时由下载逻辑补齐）。"""
    import tts_engine as _te
    _ensure_piper_voices()
    known = PIPER_MODEL_PATHS.get(voice_name)
    if known and os.path.isfile(known):
        return known
    return os.path.join(_te.get_piper_model_dir(), f"{voice_name}.onnx")


def _download_piper_model(voice_name, should_stop=None):
    """下载内置 Piper 语音的模型文件与配置"""
    import tts_engine as _te
    if voice_name not in PIPER_MODEL_URLS:
        raise ValueError(tr('不支持的Piper语音: {voice_name}', voice_name=voice_name))

    model_path = _get_piper_model_path(voice_name)
    config_path = model_path + ".json"
    _te._download_file_with_progress(PIPER_MODEL_URLS[voice_name], model_path,
                                     tr('Piper模型 {voice_name}', voice_name=voice_name), should_stop=should_stop)
    _te._download_file_with_progress(PIPER_CONFIG_URLS[voice_name], config_path,
                                     tr('Piper配置 {voice_name}', voice_name=voice_name), should_stop=should_stop)
    logger.info(f"Piper模型下载完成: {voice_name}")


def _ensure_piper_model(voice_name, should_stop=None):
    """确保Piper模型存在，如果不存在则下载。
    加锁防止并行生成时多个工作线程同时下载同一模型导致文件损坏。"""
    model_path = _get_piper_model_path(voice_name)
    if not os.path.exists(model_path):
        with _piper_download_lock:
            if not os.path.exists(model_path):
                logger.info(f"模型不存在，开始下载: {voice_name}")
                _download_piper_model(voice_name, should_stop=should_stop)
    return model_path


# ======== 合成 ========

def _piper_generate_cli(text, voice_name, speed, wav_path, should_stop=None):
    """使用piper CLI命令生成音频（可中断）"""
    import tts_engine as _te
    model_path = _get_piper_model_path(voice_name)
    config_path = _piper_config_path(model_path)
    cli_path = _te._refresh_piper_cli_path()
    if not cli_path:
        raise RuntimeError(tr("Piper CLI 不可用。\n") + _piper_install_hint())

    cmd = [
        cli_path,
        "--model", model_path,
        "--config", config_path,
        "--output_file", wav_path,
    ]
    if speed != 1.0:
        cmd.extend(["--length_scale", str(1.0 / speed)])

    rc, _out, err = _te._run_subprocess_interruptible(
        cmd, should_stop=should_stop, input_bytes=text.encode("utf-8"),
    )
    if rc != 0:
        stderr = err.decode("utf-8", errors="replace") if err else ""
        raise RuntimeError(tr('Piper CLI失败 (code {rc}): {stderr}', rc=rc, stderr=stderr))


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
    import tts_engine as _te
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
                    raise _te.StopRequested(tr("用户暂停"))
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
                raise _te.StopRequested(tr("用户暂停"))
            pcm.extend(audio_bytes)
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(bytes(pcm))
        return

    raise RuntimeError(
        tr("Piper API 不兼容：未找到 synthesize 或 synthesize_stream_raw 方法。"
        "\n请升级或重装 piper-tts。")
    )


def _piper_generate(text, voice, rate, output_path, should_stop=None):
    """用 Piper 把一段文本合成为 MP3（单次尝试，Python 包 / CLI 两种后端）"""
    import tts_engine as _te
    if not _piper_available():
        raise RuntimeError(tr("Piper TTS 不可用。\n") + _piper_install_hint())
    # 与就绪检测一致只要求 ffmpeg：WAV→MP3 由 _wav_to_mp3 负责（有 pydub 用 pydub，否则直接调 ffmpeg）
    if _te._ffmpeg_path() is None:
        raise RuntimeError(tr("ffmpeg 未安装，Piper 引擎需要 ffmpeg 进行 WAV→MP3 转换。\n")
                           + _te._ffmpeg_install_hint())

    voice_model = _load_piper_model(voice, should_stop=should_stop)

    rate_val = int(rate.replace("%", "").replace("+", ""))
    speed = max(0.5, min(2.0, 1.0 + rate_val / 100.0))

    wav_path = os.path.splitext(output_path)[0] + '.wav'
    try:
        if _get_piper_mode() == PIPER_MODE_PYTHON:
            _piper_synthesize_to_wav(voice_model, text, speed, wav_path, should_stop=should_stop)
        else:
            _piper_generate_cli(text, voice, speed, wav_path, should_stop=should_stop)

        if should_stop and should_stop():
            raise _te.StopRequested(tr("用户暂停"))
        _te._wav_to_mp3(wav_path, output_path)
    finally:
        if os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass


class PiperEngine(Engine):
    """Piper 离线神经网络语音引擎（需 .onnx 语音包）。"""

    id = "piper"
    display_name = "Piper（离线高质量）"

    def is_ready(self):
        import tts_engine as _te
        return _te.check_engine_ready("piper")

    def list_voices(self):
        _ensure_piper_voices()
        return dict(PIPER_VOICES)

    def synthesize_segment(self, text, voice, rate, out_path, should_stop=None):
        # 单次尝试：重试由编排层（tts_engine._generate_one_safe）统一负责。
        # 此前这里调用自带重试的实现，与编排层重试叠加，持续失败的章节会尝试 16 次。
        _piper_generate(text, voice, rate, out_path, should_stop=should_stop)

    def batch_parallelism(self) -> int:
        # CLI 模式靠子进程隔离，Python 模式每个工作线程持有独立模型实例，均可并行。
        import tts_engine as _te
        try:
            _get_piper_mode()
        except Exception as e:
            logger.warning(f"Piper 模式检测失败，改用串行路径: {e}")
            return 1
        return _te.get_local_concurrency()

    def release(self) -> None:
        _unload_piper_model()
