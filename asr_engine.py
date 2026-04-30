"""ASR（语音转文字）引擎 - 基于 faster-whisper"""

import atexit
import json
import logging
import os
import subprocess
import tempfile
from shutil import which
from typing import Callable, List, Optional

# 可选导入：实际使用 faster-whisper
try:
    from faster_whisper import WhisperModel
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    FASTER_WHISPER_AVAILABLE = False
    WhisperModel = None

# 错误：在 import faster_whisper 之前尝试 import torch 可能失败
try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

logger = logging.getLogger("audiobook_converter")

# 模型元数据：名称 -> 描述
WHISPER_MODELS = {
    "tiny": "tiny（最快，~150MB）",
    "base": "base（推荐，~300MB）",
    "small": "small（~1GB）",
    "medium": "medium（~3GB）",
    "large-v3": "large-v3（最准，~6GB）",
}
WHISPER_DEFAULT_MODEL = "base"


def _cuda_available() -> bool:
    """检测 CUDA 是否可用于 PyTorch"""
    if not TORCH_AVAILABLE:
        return False
    try:
        return torch.cuda.is_available()
    except Exception:
        return False


def get_whisper_model_dir(storage_dir: str) -> str:
    """Whisper 模型缓存目录"""
    path = os.path.join(storage_dir, "whisper-models")
    os.makedirs(path, exist_ok=True)
    return path


def check_asr_ready(storage_dir: str) -> tuple[bool, str]:
    """检测 ASR 引擎是否可用"""
    if not FASTER_WHISPER_AVAILABLE:
        return False, (
            "faster-whisper 未安装。\n"
            "请运行: pip install faster-whisper\n"
            "（需要约 800MB 磁盘空间，首次运行自动下载模型）"
        )
    # 检测 ffmpeg
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        bin_dir = os.path.join(storage_dir, "bin")
        ffmpeg = shutil_which_in(os.path.join(bin_dir, "ffmpeg"))
    if not ffmpeg:
        return False, "ffmpeg 未安装，ASR 需要 ffmpeg 转换音频格式。"
    return True, "ASR 引擎就绪"


def shutil_which_in(path: str):
    """检查文件是否存在且可执行"""
    return path if os.path.isfile(path) and os.access(path, os.X_OK) else None


def convert_audio_to_wav(input_path: str, sample_rate: int = 16000) -> str:
    """将任意音频格式转换为 16kHz 单声道 WAV"""
    from tts_engine import _ffmpeg_path
    ff = _ffmpeg_path()
    if not ff:
        raise RuntimeError("ffmpeg 不可用，无法转换音频格式。")
    output_path = os.path.join(tempfile.gettempdir(), f"asr_input_{os.getpid()}.wav")
    subprocess.run(
        [ff, "-y", "-i", input_path,
         "-ar", str(sample_rate), "-ac", "1", "-sample_fmt", "s16",
         output_path],
        check=True, capture_output=True,
    )
    return output_path


# 模型缓存
_whisper_model_cache: dict = {}


def _load_whisper_model(model_size: str, storage_dir: str,
                         device: str = "auto",
                         compute_type: str = "default") -> "WhisperModel":
    """加载 Whisper 模型（缓存）"""
    if not FASTER_WHISPER_AVAILABLE:
        raise ImportError("faster-whisper 未安装")

    if device == "auto":
        device = "cuda" if _cuda_available() else "cpu"
    if compute_type == "default":
        compute_type = "float16" if device == "cuda" else "int8"

    cache_key = (model_size, device)
    if cache_key in _whisper_model_cache:
        return _whisper_model_cache[cache_key]

    model_dir = get_whisper_model_dir(storage_dir)
    logger.info(f"加载 Whisper 模型: {model_size} (device={device}, compute={compute_type})")
    model = WhisperModel(
        model_size, device=device,
        compute_type=compute_type,
        download_root=model_dir,
    )
    _whisper_model_cache[cache_key] = model
    return model


def unload_whisper_model() -> None:
    """卸载已加载的 Whisper 模型"""
    global _whisper_model_cache
    _whisper_model_cache.clear()
    import gc
    gc.collect()
    if TORCH_AVAILABLE:
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass
    logger.info("Whisper 模型已卸载")


def _format_timestamp(seconds: float) -> str:
    """将秒数格式化为 SRT 时间戳"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def _format_txt(segments: list) -> str:
    return "\n".join(s["text"] for s in segments)


def _format_srt(segments: list) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        start = _format_timestamp(seg["start"])
        end = _format_timestamp(seg["end"])
        lines.append(f"{i}\n{start} --> {end}\n{seg['text']}\n")
    return "\n".join(lines)


def _format_json(segments: list, detected_lang: str = "") -> str:
    return json.dumps({
        "language": detected_lang,
        "segments": segments,
        "text": "\n".join(s["text"] for s in segments),
    }, ensure_ascii=False, indent=2)


def transcribe(
    input_path: str,
    storage_dir: str,
    model_size: str = "base",
    language: str = "auto",
    output_format: str = "txt",
    output_path: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> str:
    """将音频文件转录为文字。

    参数:
        input_path: 音频文件路径
        storage_dir: 便携存储目录（模型缓存）
        model_size: tiny/base/small/medium/large-v3
        language: auto/zh/en/ja/ko 等
        output_format: txt/srt/json
        output_path: 可选，输出文件路径
        progress_callback: 进度回调 (current, total)
        should_stop: 中断检测函数

    返回:
        转录文本
    """
    if not FASTER_WHISPER_AVAILABLE:
        raise ImportError("faster-whisper 未安装")

    # 1. 转换为 WAV
    logger.info(f"ASR: 转换音频格式 {input_path}")
    wav_path = convert_audio_to_wav(input_path)
    try:
        # 2. 自动检测设备
        device = "cuda" if _cuda_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"

        if should_stop and should_stop():
            raise StopRequested()

        # 3. 加载模型
        model = _load_whisper_model(model_size, storage_dir, device, compute_type)

        if should_stop and should_stop():
            raise StopRequested()

        # 4. 运行转录
        lang = None if language == "auto" else language
        segments_gen, info = model.transcribe(
            wav_path, language=lang,
            beam_size=5, vad_filter=True,
        )

        # 5. 收集结果
        result_segments = []
        detected_lang = info.language
        logger.info(f"ASR: 检测到语言 {detected_lang}, 总时长 {info.duration:.1f}s")

        for segment in segments_gen:
            if should_stop and should_stop():
                raise StopRequested()
            result_segments.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text.strip(),
            })
            if progress_callback and info.duration > 0:
                pct = min(int(segment.end / info.duration * 100), 100)
                progress_callback(pct, 100)

        if progress_callback:
            progress_callback(100, 100)

        # 6. 格式化输出
        if output_format == "txt":
            result = _format_txt(result_segments)
        elif output_format == "srt":
            result = _format_srt(result_segments)
        else:
            result = _format_json(result_segments, detected_lang)

        # 7. 保存到文件
        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(result)

        logger.info(f"ASR 完成: {len(result_segments)} 段, 语言={detected_lang}")
        return result

    except StopRequested:
        logger.info("ASR: 用户暂停")
        raise
    finally:
        # 清理临时 WAV
        if os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass


class StopRequested(Exception):
    """用户请求暂停"""


# ======== 外挂 ASR 引擎 ========

def _find_engine_executable(engine_path: str, name: str) -> Optional[str]:
    """在引擎目录中查找可执行文件"""
    import platform as _plat
    for cand in [name, name + ".exe", name + ".bat", name + ".py"]:
        p = os.path.join(engine_path, cand)
        if os.path.isfile(p):
            if name.endswith(".py") or _plat.system() == "Windows" or os.access(p, os.X_OK):
                return p
    # 兼容：目录内任意 .py
    for fn in sorted(os.listdir(engine_path)):
        if fn.endswith(".py"):
            full = os.path.join(engine_path, fn)
            if os.path.isfile(full):
                return full
    return None


def scan_external_asr_engines(storage_dir: str) -> dict[str, dict]:
    """扫描 {storage_dir}/asr-engines/ 下的外挂 ASR 引擎"""
    engines_dir = os.path.join(storage_dir, "asr-engines")
    if not os.path.isdir(engines_dir):
        return {}
    found: dict[str, dict] = {}
    for entry in sorted(os.listdir(engines_dir)):
        epath = os.path.join(engines_dir, entry)
        if not os.path.isdir(epath):
            continue
        exe = _find_engine_executable(epath, entry)
        if exe is None:
            continue
        # 读取 engine.json 元数据
        meta = {}
        meta_path = os.path.join(epath, "engine.json")
        if os.path.isfile(meta_path):
            try:
                import json as _json
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = _json.load(f)
            except Exception:
                pass
        found[entry] = {
            "name": meta.get("name", entry),
            "executable": exe,
            "engine_id": entry,
            "description": meta.get("description", ""),
        }
    return found


def external_asr_transcribe(engine_id: str, input_path: str, output_format: str = "txt",
                            model: str = "", language: str = "auto",
                            should_stop=None, progress_callback=None) -> str:
    """使用外挂 ASR 引擎转录音频"""
    from tts_engine import get_storage_dir, _run_subprocess_interruptible, StopRequested
    engines = scan_external_asr_engines(get_storage_dir())
    info = engines.get(engine_id)
    if not info:
        raise RuntimeError(f"外挂 ASR 引擎 '{engine_id}' 未找到。请放入 asr-engines/{engine_id}/ 目录。")

    exe = info["executable"]
    # 需要先将音频转为 WAV（外挂引擎可能只支持 WAV）
    wav_path = convert_audio_to_wav(input_path)

    import tempfile
    out_path = os.path.join(tempfile.gettempdir(), f"asr_ext_{os.getpid()}.txt")
    cmd = [exe, "--input", wav_path, "--output", out_path, "--format", output_format]
    if model:
        cmd.extend(["--model", model])
    if language and language != "auto":
        cmd.extend(["--language", language])

    try:
        rc, _out, err = _run_subprocess_interruptible(cmd, should_stop=should_stop, timeout=1800)
        if rc != 0:
            stderr = err.decode("utf-8", errors="replace") if err else ""
            raise RuntimeError(f"外挂 ASR 引擎 '{engine_id}' 失败 (code {rc}): {stderr}")
        if os.path.exists(out_path):
            with open(out_path, "r", encoding="utf-8") as f:
                return f.read()
        raise RuntimeError(f"外挂 ASR 引擎 '{engine_id}' 未生成输出文件")
    finally:
        if os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except Exception:
                pass


# 程序退出时确保释放 Whisper 模型，避免 GPU 显存残留
atexit.register(unload_whisper_model)
