"""audiobook.engines.edge — Edge（微软在线）TTS 引擎。

v6.0/A5b：把 Edge 具体化为独立的 `Engine` 子类，取代通用 `_TtsEngineAdapter`。
就绪检测 / 语音列表沿用 `tts_engine` 既有分发；单段合成复用 `_edge_generate`
（异步，内部 `asyncio.run`）。批量生成仍走 `tts_engine._generate_one_safe` 的
Edge 专用异步批处理路径（单事件循环），不在本类职责内——与具体化前行为一致。

对 `tts_engine` 的引用一律在方法内惰性导入，避免模块加载期循环导入。
"""

import asyncio

from audiobook.engines.base import Engine


class EdgeEngine(Engine):
    """Edge 在线云端语音引擎（需联网）。"""

    id = "edge"
    display_name = "Edge（联网）"

    def is_ready(self):
        import tts_engine as _te
        return _te.check_engine_ready("edge")

    def list_voices(self):
        import tts_engine as _te
        return {disp: _te.get_voice_id(disp, "edge")
                for disp in _te.get_voice_list("edge")}

    def synthesize_segment(self, text, voice, rate, out_path, should_stop=None):
        # 单段原语：与具体化前 _raw_segment_synth("edge") 的直调路径一致
        # （Edge 为网络合成，不支持 should_stop 中断，故忽略该参数）。
        import tts_engine as _te
        asyncio.run(_te._edge_generate(text, voice, rate, out_path))
