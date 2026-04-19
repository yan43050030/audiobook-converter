"""TTS引擎封装 - 支持 edge-tts（联网）和本地语音（离线）"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Optional

import edge_tts

VERSION = "2.0.0"

# ======== 通用配置 ========

# edge-tts 中文语音
EDGE_VOICES = {
    "晓晓（女声，自然）": "zh-CN-XiaoxiaoNeural",
    "云希（男声，自然）": "zh-CN-YunxiNeural",
    "云健（男声，播音）": "zh-CN-YunjianNeural",
    "晓伊（女声，活泼）": "zh-CN-XiaoyiNeural",
    "云扬（男声，温暖）": "zh-CN-YunyangNeural",
    "晓辰（女声，温柔）": "zh-CN-XiaochenNeural",
}
EDGE_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

# macOS 本地中文语音
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

# 估计：中文约 2.5 字/秒（edge-tts rate +0% 时）
CHARS_PER_SECOND_BASE = 2.5

PROGRESS_FILENAME = ".audiobook_progress.json"


# ======== 工具函数 ========

def get_voice_list(engine: str = "edge") -> list[str]:
    if engine == "local":
        return list(LOCAL_VOICES.keys())
    return list(EDGE_VOICES.keys())


def get_voice_id(display_name: str, engine: str = "edge") -> str:
    if engine == "local":
        return LOCAL_VOICES.get(display_name, LOCAL_DEFAULT_VOICE)
    return EDGE_VOICES.get(display_name, EDGE_DEFAULT_VOICE)


def estimate_duration(text: str, rate: str = "+0%") -> float:
    rate_val = int(rate.replace("%", "").replace("+", ""))
    speed_factor = 1 + rate_val / 100.0
    cps = CHARS_PER_SECOND_BASE * speed_factor
    if cps <= 0:
        cps = 0.5
    return len(text) / cps


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    name = name.strip(". ")
    return name[:80] if name else "untitled"


# ======== 章节识别 ========

CHAPTER_PATTERNS = [
    re.compile(r'^(第[一二三四五六七八九十百千\d]+\s*[章节回顾卷集篇幕话])', re.MULTILINE),
    re.compile(r'^(序章|楔子|引子|尾声|后记|番外[篇]?)\s*[：:\s]*', re.MULTILINE),
]


def detect_chapters(text: str) -> list[dict]:
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

def split_text(text: str, max_length: int = 3000) -> list[str]:
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


def _split_by_sentences(text: str, max_length: int) -> list[str]:
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


def split_by_duration(chapter_text: str, max_seconds: int, rate: str = "+0%") -> list[str]:
    rate_val = int(rate.replace("%", "").replace("+", ""))
    max_chars = int(max_seconds * CHARS_PER_SECOND_BASE * (1 + rate_val / 100.0))
    max_chars = max(max_chars, 500)
    return split_text(chapter_text, max_length=max_chars)


# ======== 音频文件合并 ========

def _merge_mp3_files(file_paths: list[str], output_path: str):
    with open(output_path, "wb") as outfile:
        for idx, path in enumerate(file_paths):
            with open(path, "rb") as infile:
                data = infile.read()
                if idx == 0:
                    outfile.write(data)
                else:
                    if data[:3] == b'ID3':
                        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
                        outfile.write(data[10 + size:])
                    else:
                        outfile.write(data)


# ======== Edge TTS 引擎 ========

async def _edge_generate(text: str, voice: str, rate: str, output_path: str):
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_path)


async def _edge_generate_multi(segments: list[str], voice: str, rate: str, output_path: str):
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


# ======== 本地 TTS 引擎 (macOS say) ========

def _local_generate(text: str, voice: str, rate: str, output_path: str):
    """用 macOS say 命令生成音频并转为 MP3"""
    rate_val = int(rate.replace("%", "").replace("+", ""))
    wpm = int(175 * (1 + rate_val / 100.0))
    wpm = max(wpm, 50)

    aiff_path = output_path.rsplit(".", 1)[0] + ".aiff"
    try:
        subprocess.run(
            ["say", "-v", voice, "-r", str(wpm), "-o", aiff_path, text],
            check=True, capture_output=True
        )
        # aiff → mp3
        subprocess.run(
            ["afconvert", "-f", "mp4f", "-d", "aac", aiff_path, output_path],
            check=True, capture_output=True
        )
    finally:
        if os.path.exists(aiff_path):
            os.remove(aiff_path)


def _local_generate_multi(segments: list[str], voice: str, rate: str, output_path: str):
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


# ======== 统一接口 ========

def generate_one(text: str, voice: str, rate: str, output_path: str, engine: str = "edge"):
    """生成单个 MP3 文件"""
    segments = split_text(text)
    if engine == "local":
        if len(segments) == 1:
            _local_generate(segments[0], voice, rate, output_path)
        else:
            _local_generate_multi(segments, voice, rate, output_path)
    else:
        if len(segments) == 1:
            asyncio.run(_edge_generate(segments[0], voice, rate, output_path))
        else:
            asyncio.run(_edge_generate_multi(segments, voice, rate, output_path))


# ======== 进度管理 ========

def save_progress(output_dir: str, items: list[dict]):
    path = os.path.join(output_dir, PROGRESS_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def load_progress(output_dir: str) -> Optional[list]:
    path = os.path.join(output_dir, PROGRESS_FILENAME)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def clear_progress(output_dir: str):
    path = os.path.join(output_dir, PROGRESS_FILENAME)
    if os.path.exists(path):
        os.remove(path)


# ======== 批量生成（支持断点续传 + 选择生成） ========

def convert_batch(
    text: str,
    voice: str,
    rate: str,
    output_dir: str,
    split_mode: str = "chapter",
    time_minutes: int = 30,
    file_prefix: str = "有声读物",
    selected_indices: Optional[list] = None,
    engine: str = "edge",
    progress_callback=None,
    should_stop=None,
    resume: bool = False,
) -> list[str]:
    """
    批量生成 MP3。

    split_mode: "chapter" | "time" | "single"
    selected_indices: None=全部，否则只生成指定索引
    should_stop: 可调用对象，返回 True 时暂停
    resume: True 时从已有进度恢复
    """
    os.makedirs(output_dir, exist_ok=True)

    # 构建任务列表
    if resume:
        items = load_progress(output_dir)
        if items:
            # 保留已有进度
            pass
        else:
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

        # 过滤选择项
        if selected_indices is not None:
            for i, item in enumerate(items):
                if i not in selected_indices:
                    item["status"] = "skipped"

        save_progress(output_dir, items)

    # 执行生成
    output_files = []
    pending = [it for it in items if it["status"] == "pending"]
    done_count = sum(1 for it in items if it["status"] == "done")
    total_active = sum(1 for it in items if it["status"] != "skipped")

    for i, item in enumerate(items):
        if item["status"] == "done":
            output_files.append(os.path.join(output_dir, item["filename"]))
            continue
        if item["status"] == "skipped":
            continue

        if should_stop and should_stop():
            save_progress(output_dir, items)
            break

        out_path = os.path.join(output_dir, item["filename"])
        try:
            generate_one(item["text"], voice, rate, out_path, engine=engine)
            item["status"] = "done"
            output_files.append(out_path)
        except Exception as e:
            item["status"] = "error"
            item["error"] = str(e)

        save_progress(output_dir, items)

        done_count += 1
        if progress_callback:
            progress_callback(done_count, total_active)

    # 如果全部完成，清理进度文件
    all_done = all(it["status"] in ("done", "skipped") for it in items)
    if all_done:
        clear_progress(output_dir)

    return output_files


# ======== 试听 ========

def generate_preview(text: str, voice: str, rate: str = "+0%", engine: str = "edge") -> str:
    preview_text = text[:200]
    if not preview_text.strip():
        raise ValueError("没有可预览的文本")
    temp_path = os.path.join(tempfile.gettempdir(), "audiobook_preview.mp3")
    generate_one(preview_text, voice, rate, temp_path, engine=engine)
    return temp_path
