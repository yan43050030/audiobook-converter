"""audiobook.engines.cosyvoice — CosyVoice（离线神经网络，实验性）TTS 引擎。

v6.0/A5b：把 CosyVoice 具体化为独立的 `Engine` 子类，取代通用 `_TtsEngineAdapter`。
就绪检测 / 语音列表沿用 `tts_engine` 既有分发；单段合成复用 `_cosyvoice_generate_safe`
（优先 Python API，失败回退外部引擎 CLI；含重试与模型实例缓存——实现暂留在
`tts_engine`，后续可整体迁入本模块）。对 `tts_engine` 的引用在方法内惰性导入。
"""

from audiobook.engines.base import Engine


class CosyVoiceEngine(Engine):
    """CosyVoice 离线神经网络语音引擎（实验性，需 2-5GB 模型）。"""

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
        import tts_engine as _te
        _te._cosyvoice_generate_safe(text, voice, rate, out_path, should_stop=should_stop)
