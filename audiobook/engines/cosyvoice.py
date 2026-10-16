"""audiobook.engines.cosyvoice — CosyVoice（离线神经网络，实验性）TTS 引擎。

使用 CosyVoice-300M-SFT 的预置说话人（中文男女声、粤语、英文、日语、韩语）。CosyVoice 依赖
torch，装在用户自己的 Python 环境里；合成经与「声音克隆」共用的常驻桥接子进程完成
（`audiobook.cloning.client.synthesize_sft`），环境未配置时回退外挂引擎 CLI。
单段合成实现为 `tts_engine._cosyvoice_generate_safe`。对 `tts_engine` 的引用在方法内惰性导入。
"""

from audiobook.engines.base import Engine


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
        import tts_engine as _te
        _te._cosyvoice_generate_safe(text, voice, rate, out_path, should_stop=should_stop)
