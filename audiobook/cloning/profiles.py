"""音色档案：一段参考音频（规整为 16 kHz 单声道 WAV）+ 该音频的文字稿。

磁盘布局：<存储目录>/voice-profiles/<id>/{prompt.wav, profile.json}

参考音频建议 5–15 秒、单人、无背景音乐；短于 3 秒拒收，长于 30 秒自动截取前 30 秒
（CosyVoice 零样本复刻对过长的提示音频效果反而变差）。文字稿可留空（此时走跨语种
模式，只用音色不对齐内容），但填写准确的文字稿效果明显更好。
"""

import json
import os
import re
import shutil
import subprocess
import time
import uuid
import wave
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

PROFILE_DIRNAME = "voice-profiles"
VOICE_PREFIX = "clone:"
MIN_SECONDS = 3.0
MAX_SECONDS = 30.0
SAMPLE_RATE = 16000
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,40}$")


class ProfileError(ValueError):
    """档案创建 / 读取的可预期错误（信息可直接展示给用户）。"""


@dataclass
class VoiceProfile:
    id: str
    name: str
    prompt_text: str = ""
    duration: float = 0.0
    created_at: float = 0.0
    path: str = ""          # 档案目录（不落盘，加载时填充）

    @property
    def wav(self) -> str:
        return os.path.join(self.path, "prompt.wav")

    @property
    def voice_id(self) -> str:
        return VOICE_PREFIX + self.id


def _storage(storage_dir: Optional[str]) -> str:
    if storage_dir:
        return storage_dir
    import tts_engine as _te
    return _te.get_storage_dir()


def profiles_dir(storage_dir: Optional[str] = None) -> str:
    return os.path.join(_storage(storage_dir), PROFILE_DIRNAME)


def parse_voice_id(voice: str) -> Optional[str]:
    """`clone:<id>` → id；不是克隆音色返回 None。"""
    if isinstance(voice, str) and voice.startswith(VOICE_PREFIX):
        pid = voice[len(VOICE_PREFIX):]
        return pid if _ID_RE.match(pid) else None
    return None


def _check_id(pid: str) -> str:
    if not isinstance(pid, str) or not _ID_RE.match(pid):
        raise ProfileError(f"无效的音色 ID: {pid!r}")
    return pid


def _load(dirpath: str) -> Optional[VoiceProfile]:
    try:
        with open(os.path.join(dirpath, "profile.json"), encoding="utf-8") as f:
            meta = json.load(f)
        if not os.path.isfile(os.path.join(dirpath, "prompt.wav")):
            return None
        return VoiceProfile(
            id=meta["id"], name=meta.get("name") or meta["id"],
            prompt_text=meta.get("prompt_text", ""), duration=float(meta.get("duration", 0.0)),
            created_at=float(meta.get("created_at", 0.0)), path=dirpath,
        )
    except Exception:
        return None


def _save(profile: VoiceProfile) -> None:
    meta = asdict(profile)
    meta.pop("path", None)
    tmp = os.path.join(profile.path, "profile.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp, os.path.join(profile.path, "profile.json"))


def list_profiles(storage_dir: Optional[str] = None) -> List[VoiceProfile]:
    root = profiles_dir(storage_dir)
    if not os.path.isdir(root):
        return []
    out = []
    for entry in os.listdir(root):
        if not _ID_RE.match(entry):
            continue
        p = _load(os.path.join(root, entry))
        if p is not None:
            out.append(p)
    return sorted(out, key=lambda p: (p.created_at, p.id))


def get_profile(pid: str, storage_dir: Optional[str] = None) -> Optional[VoiceProfile]:
    if not isinstance(pid, str) or not _ID_RE.match(pid):
        return None
    return _load(os.path.join(profiles_dir(storage_dir), pid))


def voice_map(storage_dir: Optional[str] = None) -> Dict[str, str]:
    """{界面显示名: voice_id}；重名的档案在显示名后附短 ID 以区分。"""
    profiles = list_profiles(storage_dir)
    counts: Dict[str, int] = {}
    for p in profiles:
        counts[p.name] = counts.get(p.name, 0) + 1
    out: Dict[str, str] = {}
    for p in profiles:
        disp = p.name if counts[p.name] == 1 else f"{p.name}（{p.id[-4:]}）"
        out[disp] = p.voice_id
    return out


# ---------------- 音频规整 ----------------

def _wav_duration(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate() or 1)


def _normalize_audio(src: str, dst: str) -> None:
    """转成 16 kHz 单声道 16-bit WAV，并截取前 MAX_SECONDS 秒。"""
    import tts_engine as _te
    ff = _te._ffmpeg_path()
    if not ff:
        raise ProfileError("需要 ffmpeg 才能处理参考音频。\n" + _te._ffmpeg_install_hint())
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", src,
           "-t", str(MAX_SECONDS), "-ac", "1", "-ar", str(SAMPLE_RATE),
           "-sample_fmt", "s16", dst]
    r = subprocess.run(cmd, capture_output=True, **_te._quiet_popen_kwargs())
    if r.returncode != 0 or not os.path.isfile(dst):
        err = r.stderr.decode("utf-8", errors="replace").strip()
        raise ProfileError(f"无法读取参考音频（格式不支持或文件损坏）：{err[:200]}")


# ---------------- 增删改 ----------------

def create_profile(name: str, audio_path: str, prompt_text: str = "",
                   storage_dir: Optional[str] = None) -> VoiceProfile:
    name = (name or "").strip()
    if not name:
        raise ProfileError("请给音色起个名字")
    if not audio_path or not os.path.isfile(audio_path):
        raise ProfileError(f"参考音频不存在: {audio_path}")

    root = profiles_dir(storage_dir)
    os.makedirs(root, exist_ok=True)
    pid = f"v{int(time.time()):x}-{uuid.uuid4().hex[:6]}"
    dirpath = os.path.join(root, pid)
    os.makedirs(dirpath)
    try:
        wav = os.path.join(dirpath, "prompt.wav")
        _normalize_audio(audio_path, wav)
        duration = _wav_duration(wav)
        if duration < MIN_SECONDS:
            raise ProfileError(f"参考音频太短（{duration:.1f} 秒），至少需要 {MIN_SECONDS:.0f} 秒，"
                               "建议 5–15 秒的清晰人声。")
        profile = VoiceProfile(id=pid, name=name, prompt_text=(prompt_text or "").strip(),
                               duration=round(duration, 2), created_at=time.time(), path=dirpath)
        _save(profile)
        return profile
    except Exception:
        shutil.rmtree(dirpath, ignore_errors=True)
        raise


def update_profile(pid: str, name: Optional[str] = None, prompt_text: Optional[str] = None,
                   storage_dir: Optional[str] = None) -> VoiceProfile:
    p = get_profile(_check_id(pid), storage_dir)
    if p is None:
        raise ProfileError(f"音色不存在: {pid}")
    if name is not None:
        if not name.strip():
            raise ProfileError("名字不能为空")
        p.name = name.strip()
    if prompt_text is not None:
        p.prompt_text = prompt_text.strip()
    _save(p)
    return p


def delete_profile(pid: str, storage_dir: Optional[str] = None) -> None:
    _check_id(pid)
    dirpath = os.path.join(profiles_dir(storage_dir), pid)
    if not os.path.isdir(dirpath):
        raise ProfileError(f"音色不存在: {pid}")
    shutil.rmtree(dirpath)
