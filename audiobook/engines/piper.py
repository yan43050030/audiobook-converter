"""audiobook.engines.piper — Piper（离线神经网络）TTS 引擎。

v6.0/A5b：把 Piper 具体化为独立的 `Engine` 子类，取代通用 `_TtsEngineAdapter`。
就绪检测 / 语音列表沿用 `tts_engine` 既有分发；单段合成复用 `_piper_generate_safe`
（含重试、Python/CLI 两种后端、模型缓存与并发——这些实现暂留在 `tts_engine`，
后续可整体迁入本模块）。对 `tts_engine` 的引用在方法内惰性导入，避免循环导入。
"""

from audiobook.engines.base import Engine


class PiperEngine(Engine):
    """Piper 离线神经网络语音引擎（需 .onnx 语音包）。"""

    id = "piper"
    display_name = "Piper（离线高质量）"

    def is_ready(self):
        import tts_engine as _te
        return _te.check_engine_ready("piper")

    def list_voices(self):
        import tts_engine as _te
        return {disp: _te.get_voice_id(disp, "piper")
                for disp in _te.get_voice_list("piper")}

    def synthesize_segment(self, text, voice, rate, out_path, should_stop=None):
        import tts_engine as _te
        _te._piper_generate_safe(text, voice, rate, out_path, should_stop=should_stop)
