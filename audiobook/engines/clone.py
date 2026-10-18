"""audiobook.engines.clone — 声音克隆引擎（v6.0/C1）。

音色 = 用户创建的克隆音色档案（voice_id 形如 ``clone:<id>``）；合成经常驻桥接子进程交给
用户环境里的 CosyVoice（Fun-CosyVoice3 / CosyVoice2）零样本复刻，再转 MP3。
"""

import os
import tempfile

from audiobook.engines.base import Engine
from audiobook.i18n import tr


def speed_from_rate(rate: str) -> float:
    """界面语速（如 "+20%"）→ CosyVoice 的 speed 倍率（0.5–2.0）。"""
    try:
        val = int(str(rate).replace("%", "").replace("+", ""))
    except ValueError:
        val = 0
    return max(0.5, min(2.0, 1.0 + val / 100.0))


class CloneEngine(Engine):
    """声音克隆（CosyVoice 零样本复刻）。"""

    id = "clone"
    display_name = "声音克隆（CosyVoice）"

    def is_ready(self):
        from audiobook.cloning.client import readiness
        return readiness()

    def list_voices(self):
        from audiobook.cloning.profiles import voice_map
        return voice_map()

    def synthesize_segment(self, text, voice, rate, out_path, should_stop=None):
        from audiobook.cloning import profiles
        from audiobook.cloning.client import synthesize, CloneBridgeError
        import tts_engine as _te

        pid = profiles.parse_voice_id(voice)
        prof = profiles.get_profile(pid) if pid else None
        if prof is None:
            raise CloneBridgeError(tr('克隆音色不存在或已删除：{voice}', voice=voice))
        fd, wav = tempfile.mkstemp(suffix=".wav", prefix="clone_")
        os.close(fd)
        try:
            synthesize(prof, text, wav, speed=speed_from_rate(rate), should_stop=should_stop)
            _te._wav_to_mp3(wav, out_path)
        finally:
            try:
                os.remove(wav)
            except OSError:
                pass
