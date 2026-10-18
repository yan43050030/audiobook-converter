"""audiobook.engines.cosyvoice — CosyVoice（离线神经网络，实验性）TTS 引擎。

使用 CosyVoice-300M-SFT 的预置说话人（中文男女声、粤语、英文、日语、韩语）。CosyVoice 依赖
torch，装在用户自己的 Python 环境里；合成经与「声音克隆」共用的常驻桥接子进程完成
（`audiobook.cloning.client.synthesize_sft`），环境未配置时回退外挂引擎 CLI。
说话人表 / 模型清单等常量仍在 `tts_engine`（语音列表与下载共用），在方法内惰性导入。
"""

import os
import tempfile

from audiobook.engines.base import Engine
from audiobook.engines.clone import speed_from_rate
from audiobook.i18n import tr


def resolve_speaker(voice: str) -> str:
    """界面 / 历史配置里的语音 ID → 模型的预置说话人名。"""
    import tts_engine as _te
    spk = _te._COSYVOICE_SPK_ALIASES.get(voice, voice) or _te.COSYVOICE_DEFAULT_VOICE
    if spk in _te.COSYVOICE_MODELS or "cosyvoice" in spk.lower():
        spk = _te.COSYVOICE_DEFAULT_VOICE  # 旧版把模型目录名当作语音 ID（「CosyVoice (目录)」）
    return spk


class CosyVoiceEngine(Engine):
    """CosyVoice 离线神经网络语音引擎（实验性，预置说话人）。"""

    id = "cosyvoice"
    display_name = "CosyVoice（离线神经）"

    def is_ready(self):
        import tts_engine as _te
        return _te.check_engine_ready("cosyvoice")

    def list_voices(self):
        import tts_engine as _te
        return {disp: _te.get_voice_id(disp, "cosyvoice")
                for disp in _te.get_voice_list("cosyvoice")}

    def synthesize_segment(self, text, voice, rate, out_path, should_stop=None):
        """单段合成：环境与模型就绪时走桥接进程，否则回退外挂引擎 CLI。

        重试由上层 `tts_engine._generate_one_safe` 统一负责，这里只做一次。
        """
        import tts_engine as _te
        from audiobook.cloning import client as _cv
        if should_stop and should_stop():
            raise _te.StopRequested(tr("用户暂停"))
        ok, msg = _cv.sft_readiness()
        if not ok:
            ext = _te._scan_external_engines().get("cosyvoice")
            if not ext:
                raise RuntimeError(tr('CosyVoice 不可用：{msg}', msg=msg))
            _te._external_generate(text, voice, rate, out_path, "cosyvoice",
                                   ext["executable"], should_stop=should_stop)
            return
        fd, wav = tempfile.mkstemp(suffix=".wav", prefix="cosyvoice_")
        os.close(fd)
        try:
            _cv.synthesize_sft(resolve_speaker(voice), text, wav,
                               speed=speed_from_rate(rate), should_stop=should_stop)
            _te._wav_to_mp3(wav, out_path)
        finally:
            try:
                os.remove(wav)
            except OSError:
                pass
