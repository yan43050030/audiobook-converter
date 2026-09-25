"""audiobook.core.text — 与引擎/界面无关的纯文本处理层。

章节识别 · 对话/说话人识别 · 文本分段 · 时长估算 · 文件名清理 · SRT 字幕生成。

v6.0/A4 从 `tts_engine` 迁入本模块（不依赖 ffmpeg/引擎/pydub）；顶层 `tts_engine`
仍再导入这些名字，保持 `from tts_engine import detect_chapters, ...` 等旧用法兼容。
"""

import re
from typing import Optional


# ======== 时长估算 / 文件名 ========

CHARS_PER_SECOND_BASE = 4.5  # 中文朗读基准字符/秒（语速 0% 时）


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
    re.compile(r'[“”\"]([^“”\"]+)[“”\"]'),
    re.compile(r"[‘’']([^‘’']+)[‘’']"),
    re.compile(r'[「]([^」]+)[」]'),
    re.compile(r'"([^"]+)"'),
]
SPEAKER_PATTERN = re.compile(
    r'([^，。！？\n“”‘’「」"\' \t]{1,15})'
    r'(?:问道|喊道|叫道|答道|讲道|嚷道|吼道|叹道|骂道|喝道|回答|'
    r'说|问|道|喊|叫|答|讲|嚷|吼|叹|骂|喝)[：:]'
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
        # 取窗口内最后一个且紧邻对话起点的 "X说：" —— 连续多角色对话时，
        # 取第一个会把当前对话错误归给上一个说话人
        spk_match = None
        for m in SPEAKER_PATTERN.finditer(context_before):
            spk_match = m
        if spk_match and len(context_before) - spk_match.end() <= 2:
            speaker = spk_match.group(1).strip()

        dialogue_text = earliest_match.group(0)
        segments.append({"text": dialogue_text, "type": "dialogue", "speaker": speaker})

        pos = earliest_match.end()

    return segments


def extract_speakers(text: str, max_speakers: int = 20) -> list[str]:
    """从文本中提取对话说话人名单，按出现次数降序。用于角色→音色映射界面。"""
    from collections import Counter
    counter: Counter = Counter()
    for seg in detect_dialogue_segments(text):
        if seg.get("type") == "dialogue" and seg.get("speaker"):
            counter[seg["speaker"]] += 1
    return [name for name, _ in counter.most_common(max_speakers)]


def _resolve_segment_voice(seg: dict, default_voice: str, voice_map: Optional[dict]) -> str:
    """按对话段选择语音。

    voice_map 结构:
      {"narration": vid, "dialogue": vid, "speakers": {角色名: vid}}
    兼容旧格式（角色名直接作为顶层键）。
    """
    if not voice_map:
        return default_voice
    speaker = seg.get("speaker")
    speakers_map = voice_map.get("speakers") or {}
    if speaker:
        if speaker in speakers_map:
            return speakers_map[speaker]
        if speaker in voice_map:  # 旧格式兼容
            return voice_map[speaker]
    seg_type = seg.get("type", "narration")
    return voice_map.get(seg_type, default_voice)


# ======== 文本分段 ========

def split_text(text, max_length=3000):
    if len(text) <= max_length:
        return [text]

    paragraphs = text.split("\n")
    segments, current = [], ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            # 不要因为空行就截断段落 —— 否则在每段间有空行的中文小说中，
            # 每个段落都会变成独立文件，split_by_duration 无法累积到目标时长。
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


# ======== SRT 字幕生成（离线时间轴估算） ========

_SENTENCE_SPLIT_RE = re.compile(r'(?<=[。！？!?；;…])\s*')


def _srt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600 * 1000)
    m, rem = divmod(rem, 60 * 1000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_srt_from_text(text: str, total_duration: float) -> str:
    """按句切分文本、按字数比例分配时间轴，生成 SRT 字幕。

    离线估算方案：无需引擎提供逐词时间戳，各引擎通用；
    与真实语音存在小幅偏差，适合校对与听读跟随。
    """
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    if not sentences or total_duration <= 0:
        return ""
    total_chars = sum(len(s) for s in sentences)
    if total_chars == 0:
        return ""
    blocks = []
    t = 0.0
    for i, sentence in enumerate(sentences, 1):
        dur = total_duration * len(sentence) / total_chars
        blocks.append(f"{i}\n{_srt_timestamp(t)} --> {_srt_timestamp(t + dur)}\n{sentence}\n")
        t += dur
    return "\n".join(blocks)
