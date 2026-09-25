"""audiobook.io.audio — 音频输出层（MP3 合并 · m4b 有声书 · ID3 元数据 · 响度归一化）。

v6.0/A4 从 `tts_engine` 迁入本模块。这些函数依赖 tts_engine 内部的 ffmpeg 定位、
子进程助手与 pydub 配置，为避免模块加载期循环导入并复用同一批已配置对象，均在
函数内以 `import tts_engine as _te` 惰性引用（asr_engine 采用同一模式）。
顶层 `tts_engine` 再导入这些函数，保持 `from tts_engine import export_m4b, ...`
等旧用法与内部调用不变。
"""

import os
import re
import shutil
import subprocess
import tempfile
from typing import List, Optional


def _merge_mp3_files(file_paths, output_path):
    """合并多个MP3文件为一个，跳过后续文件的ID3标签"""
    import tts_engine as _te
    with open(output_path, "wb") as outfile:
        for idx, path in enumerate(file_paths):
            with open(path, "rb") as infile:
                data = infile.read()
            if not data:
                _te.logger.warning(f"合并时跳过空文件: {path}")
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
                        _te.logger.warning(f"ID3 标签尺寸异常({size})，按原样写入: {path}")
                        outfile.write(data)
                else:
                    outfile.write(data)


def merge_mp3_files(file_paths, output_path):
    """公开接口：合并多个MP3文件"""
    _merge_mp3_files(file_paths, output_path)


def get_audio_duration(path: str) -> float:
    """获取音频时长（秒）。优先 ffprobe，回退 pydub，最后按 128kbps 码率估算。"""
    import tts_engine as _te
    fp = _te._which_portable("ffprobe")
    if fp:
        try:
            rc, out, _err = _te._run_subprocess_interruptible(
                [fp, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                timeout=30)
            if rc == 0:
                return float(out.decode("ascii", errors="ignore").strip())
        except Exception:
            pass
    if _te.PYDUB_AVAILABLE and _te._configure_pydub_ffmpeg():
        try:
            return len(_te.AudioSegment.from_file(path)) / 1000.0
        except Exception:
            pass
    try:
        return os.path.getsize(path) * 8 / (128 * 1000)
    except Exception:
        return 0.0


def _ffmeta_escape(value: str) -> str:
    """FFMETADATA 值转义（=、;、#、\\ 和换行）"""
    out = []
    for ch in str(value):
        if ch in "=;#\\":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\\n")
        else:
            out.append(ch)
    return "".join(out)


def build_ffmetadata_chapters(titles: List[str], durations: List[float],
                              album: str = "") -> str:
    """按章节标题和时长生成 FFMETADATA 章节描述内容"""
    lines = [";FFMETADATA1"]
    if album:
        lines.append(f"title={_ffmeta_escape(album)}")
        lines.append(f"album={_ffmeta_escape(album)}")
    start_ms = 0
    for title, dur in zip(titles, durations):
        end_ms = start_ms + max(int(dur * 1000), 1)
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start_ms}",
            f"END={end_ms}",
            f"title={_ffmeta_escape(title)}",
        ]
        start_ms = end_ms
    return "\n".join(lines) + "\n"


def export_m4b(file_paths: List[str], output_path: str,
               titles: Optional[List[str]] = None, album: str = "",
               should_stop=None, progress_callback=None) -> str:
    """把多个 MP3 合并为带章节标记的 .m4b 有声书（需 ffmpeg）。

    titles 缺省时用文件名（去掉序号前缀和扩展名）作为章节名。
    """
    import tts_engine as _te
    ff = _te._ffmpeg_path()
    if not ff:
        raise RuntimeError("ffmpeg 未安装，无法导出 m4b。\n" + _te._ffmpeg_install_hint())
    if not file_paths:
        raise ValueError("没有要合并的文件")

    if titles is None:
        titles = []
        for p in file_paths:
            base = os.path.splitext(os.path.basename(p))[0]
            titles.append(re.sub(r'^\d{1,4}[_\-. ]*', '', base) or base)

    durations = []
    for i, p in enumerate(file_paths):
        if should_stop and should_stop():
            raise _te.StopRequested("用户暂停")
        durations.append(get_audio_duration(p))
        if progress_callback:
            try:
                progress_callback(i + 1, len(file_paths) + 1)
            except Exception:
                pass

    work_dir = tempfile.mkdtemp(prefix="m4b_")
    list_path = os.path.join(work_dir, "concat.txt")
    meta_path = os.path.join(work_dir, "chapters.ffmeta")
    try:
        with open(list_path, "w", encoding="utf-8") as f:
            for p in file_paths:
                escaped = os.path.abspath(p).replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(build_ffmetadata_chapters(titles, durations, album=album))

        cmd = [ff, "-y", "-f", "concat", "-safe", "0", "-i", list_path,
               "-i", meta_path, "-map_metadata", "1", "-map", "0:a",
               "-c:a", "aac", "-b:a", "64k", "-f", "mp4", output_path]
        rc, _out, err = _te._run_subprocess_interruptible(cmd, should_stop=should_stop,
                                                          timeout=3600 * 4)
        if rc != 0:
            stderr = err.decode("utf-8", errors="replace") if err else ""
            raise RuntimeError(f"m4b 导出失败 (rc={rc}): {stderr[-500:]}")
        if progress_callback:
            try:
                progress_callback(len(file_paths) + 1, len(file_paths) + 1)
            except Exception:
                pass
        return output_path
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def write_id3_tags(path: str, title: str = "", album: str = "", artist: str = "",
                   track: Optional[int] = None, cover_path: str = "") -> None:
    """用 ffmpeg 流复制方式写入 ID3v2 元数据（无损、快速，需 ffmpeg）"""
    import tts_engine as _te
    ff = _te._ffmpeg_path()
    if not ff:
        raise RuntimeError("ffmpeg 未安装，无法写入元数据。\n" + _te._ffmpeg_install_hint())

    tmp_path = path + ".tag.tmp.mp3"
    cmd = [ff, "-y", "-i", path]
    if cover_path and os.path.isfile(cover_path):
        cmd += ["-i", cover_path, "-map", "0:a", "-map", "1:v",
                "-c", "copy", "-id3v2_version", "3",
                "-metadata:s:v", "title=Album cover",
                "-metadata:s:v", "comment=Cover (front)"]
    else:
        cmd += ["-c", "copy", "-id3v2_version", "3"]
    for key, val in (("title", title), ("album", album), ("artist", artist)):
        if val:
            cmd += ["-metadata", f"{key}={val}"]
    if track is not None:
        cmd += ["-metadata", f"track={track}"]
    cmd.append(tmp_path)

    try:
        rc, _out, err = _te._run_subprocess_interruptible(cmd, timeout=300)
        if rc != 0:
            stderr = err.decode("utf-8", errors="replace") if err else ""
            raise RuntimeError(f"写入元数据失败 (rc={rc}): {stderr[-300:]}")
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def normalize_loudness(input_path: str, output_path: Optional[str] = None,
                       target_lufs: float = -16.0, target_tp: float = -1.5,
                       target_lra: float = 11.0) -> str:
    """对单个 MP3 做 EBU R128 响度归一化（通过 ffmpeg loudnorm 滤镜）。

    target_lufs/tp/lra 参数与 ffmpeg loudnorm 一致。
    返回归一化后的输出路径。原地处理时会用临时文件再原子替换。
    """
    import tts_engine as _te
    ff = _te._ffmpeg_path()
    if not ff:
        raise RuntimeError("ffmpeg 未安装，无法做响度归一化。\n" + _te._ffmpeg_install_hint())
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
    _te.logger.info(f"响度归一化: {input_path} -> {work_path}")
    rc = subprocess.run(cmd, capture_output=True, **_te._quiet_popen_kwargs()).returncode
    if rc != 0 or not os.path.exists(work_path):
        raise RuntimeError(f"loudnorm 失败 (rc={rc})")
    if in_place:
        os.replace(work_path, output_path)
    return output_path
