"""TTS引擎封装 - 支持 edge-tts（联网）和本地语音（离线） v2.1.0"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Optional

import edge_tts

VERSION = "2.2.0"

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

LOCAL_VOICES = {
    "Ting-Ting（中文）": "Ting-Ting",
    "Eddy（中文）": "Eddy (zh_CN)",
    "Flo（中文）": "Flo (zh_CN)",
    "Grandma（中文）": "Grandma (zh_CN)",
    "Grandpa（中文）": "Grandpa (zh_CN)",
    "Reed（中文）": "Reed (zh_CN)",
    "Rocko（中文）": "Rocko (zh_CN)",
    "Sandy（中文）": "Sandy (zh_CN)",
    "Shelley（中文）": "Shelley (zh_CN)",
}
LOCAL_DEFAULT_VOICE = "Ting-Ting"

CHARS_PER_SECOND_BASE = 2.5
PROGRESS_FILENAME = ".audiobook_progress.json"
MAX_RETRIES = 3  # 生成失败时重试次数
RETRY_DELAY = 5  # 重试前等待秒数（指数退避基础值）
CONCURRENCY = 3  # 同时处理的章节数（降低并发避免触发限流/网络拥塞）


# ======== 工具函数 ========

def get_voice_list(engine="edge"):
    if engine == "local":
        return list(LOCAL_VOICES.keys())
    return list(EDGE_VOICES.keys())


def get_voice_id(display_name, engine="edge"):
    if engine == "local":
        return LOCAL_VOICES.get(display_name, LOCAL_DEFAULT_VOICE)
    return EDGE_VOICES.get(display_name, EDGE_DEFAULT_VOICE)


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
    else:
        # 本地引擎同步逐个生成
        done_count = 0
        total_active = sum(1 for it in items if it["status"] not in ("done", "skipped"))

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
                _generate_one_safe(item["text"], voice, rate, out_path, engine="local")
                item["status"] = "done"
            except Exception as e:
                item["status"] = "error"
                item["error"] = str(e)
                logger.error(f"本地引擎生成失败 [{item['title']}]: {e}")

            save_progress(output_dir, items)
            done_count += 1
            if progress_callback:
                progress_callback(done_count, total_active)

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
