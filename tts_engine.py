"""TTS引擎封装 - 支持 edge-tts（联网）和本地语音（离线） v2.1.0"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from shutil import which
from typing import Optional

import edge_tts

# 可选导入 - Piper TTS和音频处理
try:
    from piper import PiperVoice
    PIPER_PYTHON_AVAILABLE = True
except ImportError:
    PIPER_PYTHON_AVAILABLE = False
    PiperVoice = None

# 检测 Piper CLI 命令行工具
PIPER_CLI_PATH = which("piper") or which("piper.exe")
PIPER_CLI_AVAILABLE = bool(PIPER_CLI_PATH)
PIPER_AVAILABLE = PIPER_PYTHON_AVAILABLE or PIPER_CLI_AVAILABLE

try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False
    AudioSegment = None

# ffmpeg 可用性缓存
_FFMPEG_AVAILABLE = None

VERSION = "2.3.1"

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

def _detect_local_voices():
    """动态检测 macOS say 可用的中文语音"""
    voices = {}
    try:
        result = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            # 格式: 语音名 (语言描述) 语言代码 # 示例
            # 如: Eddy (中文（中国大陆）)     zh_CN    # 你好！我叫Eddy。
            match = re.match(r'^(\S+)\s+\([^)]*\)\s+(zh_CN|zh_TW|zh_HK)', line)
            if match:
                voice_name = match.group(1)
                lang = match.group(2)
                display = f"{voice_name}（中文）" if lang == "zh_CN" else f"{voice_name}（{lang}）"
                voices[display] = voice_name
    except Exception as e:
        logger.warning(f"检测本地语音失败: {e}")
    return voices


LOCAL_VOICES = _detect_local_voices()
LOCAL_DEFAULT_VOICE = list(LOCAL_VOICES.values())[0] if LOCAL_VOICES else ""

PIPER_VOICES = {
    "Piper中文女声（中等质量）": "zh_CN-huayan-medium",
    "Piper中文女声（较低质量）": "zh_CN-huayan-low",
}
PIPER_DEFAULT_VOICE = "zh_CN-huayan-medium"

# Piper模型存储目录
PIPER_MODEL_DIR = os.path.join(os.path.expanduser("~"), ".piper-tts", "models")
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


def check_engine_ready(engine="edge"):
    """检测引擎是否可用，返回 (ready: bool, message: str)"""
    if engine == "edge":
        return True, "Edge TTS 可用（需要联网）"

    if engine == "local":
        if not LOCAL_VOICES:
            return False, "本地语音不可用：未检测到 macOS 中文语音"
        return True, f"本地语音可用（{len(LOCAL_VOICES)} 个）"

    if engine == "piper":
        if not PIPER_AVAILABLE:
            return False, (
                "Piper 未安装。请:\n"
                "1. 安装 Python 包: pip install piper-tts\n"
                "2. 或下载 CLI 工具: https://github.com/rhasspy/piper/releases"
            )
        if not _check_ffmpeg():
            return False, (
                "ffmpeg 未安装，Piper 需要 ffmpeg 转换音频格式。\n"
                "macOS: brew install ffmpeg"
            )
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


def _check_ffmpeg() -> bool:
    """检查ffmpeg是否可用，结果缓存"""
    global _FFMPEG_AVAILABLE
    if _FFMPEG_AVAILABLE is not None:
        return _FFMPEG_AVAILABLE
    _FFMPEG_AVAILABLE = which("ffmpeg") is not None
    if not _FFMPEG_AVAILABLE:
        logger.warning("未检测到ffmpeg，Piper引擎的WAV->MP3转换将不可用。请安装ffmpeg。")
    return _FFMPEG_AVAILABLE


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
    """确定Piper运行模式：优先Python包，其次CLI"""
    if PIPER_PYTHON_AVAILABLE:
        return PIPER_MODE_PYTHON
    if PIPER_CLI_AVAILABLE:
        return PIPER_MODE_CLI
    raise RuntimeError(
        "Piper TTS不可用。请安装piper-tts包: pip install piper-tts\n"
        "或下载piper命令行工具并加入PATH: https://github.com/rhasspy/piper/releases"
    )


def _load_piper_model(voice_name):
    """加载Piper模型，使用缓存避免重复加载"""
    model_path = _ensure_piper_model(voice_name)
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
    """获取Piper模型文件路径"""
    model_dir = PIPER_MODEL_DIR
    os.makedirs(model_dir, exist_ok=True)

    # 模型文件命名约定：voice_name.onnx
    model_file = f"{voice_name}.onnx"
    return os.path.join(model_dir, model_file)


def _download_file_with_progress(url, filepath, description="", timeout=60):
    """下载文件并显示进度，支持镜像回退"""
    urls_to_try = [url, _get_mirror_url(url)]
    last_exception = None

    for try_url in urls_to_try:
        try:
            _download_file_single(try_url, filepath, description, timeout)
            return
        except Exception as e:
            last_exception = e
            logger.warning(f"下载失败，尝试镜像: {try_url} -> {e}")

    raise RuntimeError(f"所有下载源均失败: {last_exception}")


def _download_file_single(url, filepath, description="", timeout=60):
    """单次下载实现，支持断点续传"""
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
    with open(filepath, mode) as f, tqdm(
        desc=description,
        total=total_size,
        initial=existing_size,
        unit='B',
        unit_scale=True,
        unit_divisor=1024,
    ) as pbar:
        for chunk in response.iter_content(chunk_size=block_size):
            if chunk:
                f.write(chunk)
                pbar.update(len(chunk))

    logger.info(f"下载完成: {filepath}")


def _download_piper_model(voice_name):
    """下载Piper模型文件"""
    if voice_name not in PIPER_MODEL_URLS:
        raise ValueError(f"不支持的Piper语音: {voice_name}")

    model_url = PIPER_MODEL_URLS[voice_name]
    model_path = _get_piper_model_path(voice_name)
    config_url = PIPER_CONFIG_URLS[voice_name]
    config_path = model_path + ".json"

    # 下载模型文件
    _download_file_with_progress(model_url, model_path, f"Piper模型 {voice_name}")

    # 下载配置文件
    _download_file_with_progress(config_url, config_path, f"Piper配置 {voice_name}")

    logger.info(f"Piper模型下载完成: {voice_name}")


def _ensure_piper_model(voice_name):
    """确保Piper模型存在，如果不存在则下载"""
    model_path = _get_piper_model_path(voice_name)
    if not os.path.exists(model_path):
        logger.info(f"模型不存在，开始下载: {voice_name}")
        _download_piper_model(voice_name)
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


def _piper_generate_cli(text, voice_name, speed, wav_path):
    """使用piper CLI命令生成音频"""
    model_path = _get_piper_model_path(voice_name)
    config_path = model_path + ".json"

    cmd = [
        PIPER_CLI_PATH,
        "--model", model_path,
        "--config", config_path,
        "--output_file", wav_path,
    ]
    if speed != 1.0:
        cmd.extend(["--length_scale", str(1.0 / speed)])

    result = subprocess.run(
        cmd,
        input=text.encode("utf-8"),
        capture_output=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        raise RuntimeError(f"Piper CLI失败 (code {result.returncode}): {stderr}")


def _piper_generate(text, voice, rate, output_path):
    """使用Piper TTS生成音频（支持Python包和CLI两种模式）"""
    if not PIPER_AVAILABLE:
        raise RuntimeError(
            "Piper TTS不可用。请安装piper-tts包: pip install piper-tts\n"
            "或下载piper命令行工具并加入PATH: https://github.com/rhasspy/piper/releases"
        )

    if not _check_ffmpeg():
        raise RuntimeError("ffmpeg未安装，Piper引擎需要ffmpeg进行WAV到MP3的转换")

    # 加载/获取模型（使用缓存）
    voice_model = _load_piper_model(voice)

    # 计算速度因子（Piper的speed参数：0.5-2.0，默认1.0）
    rate_val = int(rate.replace("%", "").replace("+", ""))
    speed = 1.0 + rate_val / 100.0
    speed = max(0.5, min(2.0, speed))

    # 临时WAV文件路径
    wav_path = output_path.replace('.mp3', '.wav')

    try:
        mode = _get_piper_mode()

        if mode == PIPER_MODE_PYTHON:
            with open(wav_path, "wb") as f:
                for audio_bytes in voice_model.synthesize_stream_raw(text, speed=speed):
                    f.write(audio_bytes)
        else:
            _piper_generate_cli(text, voice, speed, wav_path)

        # 转换为MP3格式
        _convert_wav_to_mp3(wav_path, output_path)

    finally:
        # 清理临时WAV文件
        if os.path.exists(wav_path):
            os.remove(wav_path)


def _piper_generate_safe(text, voice, rate, output_path):
    """安全的Piper生成函数，包含重试机制"""
    for attempt in range(MAX_RETRIES + 1):
        try:
            logger.info(f"Piper生成 → {output_path} (尝试 {attempt + 1})")
            _piper_generate(text, voice, rate, output_path)

            # 验证
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                logger.info(f"Piper完成: {output_path} ({os.path.getsize(output_path)} bytes)")
                return
            else:
                raise RuntimeError(f"生成文件为空: {output_path}")

        except Exception as e:
            delay = RETRY_DELAY * (2 ** attempt)
            logger.error(f"Piper生成失败 尝试{attempt + 1}/{MAX_RETRIES + 1}: {e}")
            if attempt < MAX_RETRIES:
                logger.info(f"等待 {delay}秒 后重试...")
                import time
                time.sleep(delay)
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

async def _generate_one_item(item, voice, rate, output_dir, semaphore):
    """生成单个章节，由信号量控制并发"""
    out_path = os.path.join(output_dir, item["filename"])
    segments = split_text(item["text"])

    async with semaphore:
        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.info(f"生成 [{item['title']}] → {item['filename']} (尝试 {attempt + 1}/{MAX_RETRIES + 1})")

                if len(segments) == 1:
                    await _edge_generate(segments[0], voice, rate, out_path)
                else:
                    await _edge_generate_multi(segments, voice, rate, out_path)

                # 验证文件
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
                    await asyncio.sleep(delay)
                else:
                    item["status"] = "error"
                    item["error"] = str(e)


async def _edge_batch_generate(items, voice, rate, output_dir, progress_callback, should_stop):
    """
    并发批量生成，同时处理 CONCURRENCY 个章节。
    使用信号量限流 + 批次启动（一次启动CONCURRENCY个，完成一个再启动下一个）。
    """
    pending_items = [it for it in items if it["status"] not in ("done", "skipped")]
    total_active = len(pending_items)
    done_count = 0

    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def _process_item(item):
        nonlocal done_count

        await _generate_one_item(item, voice, rate, output_dir, semaphore)

        done_count += 1
        if progress_callback:
            progress_callback(done_count, total_active)

        # 保存进度
        save_progress(output_dir, items)

    # 逐个启动任务，每个任务内部通过 semaphore 限流
    tasks = []
    for item in pending_items:
        if should_stop and should_stop():
            logger.info("用户暂停生成，停止启动新任务")
            break
        tasks.append(asyncio.create_task(_process_item(item)))

    if tasks:
        await asyncio.gather(*tasks)


# ======== 本地 TTS 引擎 (macOS say) ========

def _local_generate(text, voice, rate, output_path):
    rate_val = int(rate.replace("%", "").replace("+", ""))
    wpm = int(175 * (1 + rate_val / 100.0))
    wpm = max(wpm, 50)

    aiff_path = output_path.rsplit(".", 1)[0] + ".aiff"
    try:
        subprocess.run(
            ["say", "-v", voice, "-r", str(wpm), "-o", aiff_path, text],
            check=True, capture_output=True
        )
        subprocess.run(
            ["afconvert", "-f", "mp4f", "-d", "aac", aiff_path, output_path],
            check=True, capture_output=True
        )
    finally:
        if os.path.exists(aiff_path):
            os.remove(aiff_path)


# ======== 统一生成接口 ========

def _generate_one_safe(text, voice, rate, output_path, engine="edge"):
    """生成单个MP3，带重试和验证"""
    segments = split_text(text)

    for attempt in range(MAX_RETRIES + 1):
        try:
            logger.info(f"生成单文件 → {output_path} (尝试 {attempt + 1})")

            if engine == "local":
                if len(segments) == 1:
                    _local_generate(segments[0], voice, rate, output_path)
                else:
                    temp_dir = tempfile.mkdtemp()
                    temp_files = []
                    try:
                        for i, seg in enumerate(segments):
                            tp = os.path.join(temp_dir, f"seg_{i:04d}.mp3")
                            _local_generate(seg, voice, rate, tp)
                            temp_files.append(tp)
                        _merge_mp3_files(temp_files, output_path)
                    finally:
                        shutil.rmtree(temp_dir, ignore_errors=True)
            elif engine == "piper":
                if len(segments) == 1:
                    _piper_generate_safe(segments[0], voice, rate, output_path)
                else:
                    temp_dir = tempfile.mkdtemp()
                    temp_files = []
                    try:
                        for i, seg in enumerate(segments):
                            tp = os.path.join(temp_dir, f"seg_{i:04d}.mp3")
                            _piper_generate_safe(seg, voice, rate, tp)
                            temp_files.append(tp)
                        _merge_mp3_files(temp_files, output_path)
                    finally:
                        shutil.rmtree(temp_dir, ignore_errors=True)
            else:
                if len(segments) == 1:
                    asyncio.run(_edge_generate(segments[0], voice, rate, output_path))
                else:
                    asyncio.run(_edge_generate_multi(segments, voice, rate, output_path))

            # 验证
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                logger.info(f"完成: {output_path} ({os.path.getsize(output_path)} bytes)")
                return
            else:
                raise RuntimeError(f"生成文件为空: {output_path}")

        except Exception as e:
            delay = RETRY_DELAY * (2 ** attempt)
            logger.error(f"生成失败 尝试{attempt + 1}/{MAX_RETRIES + 1}: {e}")
            if attempt < MAX_RETRIES:
                logger.info(f"等待 {delay}秒 后重试...")
                import time
                time.sleep(delay)
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
        # 本地引擎或Piper引擎同步逐个生成
        done_count = 0
        total_active = sum(1 for it in items if it["status"] not in ("done", "skipped"))

        try:
            for item in items:
                if item["status"] == "done":
                    continue
                if item["status"] == "skipped":
                    continue
                if should_stop and should_stop():
                    logger.info("用户暂停生成")
                    break

                out_path = os.path.join(output_dir, item["filename"])
                try:
                    _generate_one_safe(item["text"], voice, rate, out_path, engine=engine)
                    item["status"] = "done"
                except Exception as e:
                    item["status"] = "error"
                    item["error"] = str(e)
                    logger.error(f"{engine}引擎生成失败 [{item['title']}]: {e}")

                save_progress(output_dir, items)
                done_count += 1
                if progress_callback:
                    progress_callback(done_count, total_active)
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

def generate_preview(text, voice, rate="+0%", engine="edge"):
    preview_text = text[:200]
    if not preview_text.strip():
        raise ValueError("没有可预览的文本")
    temp_path = os.path.join(tempfile.gettempdir(), "audiobook_preview.mp3")
    _generate_one_safe(preview_text, voice, rate, temp_path, engine=engine)
    return temp_path
