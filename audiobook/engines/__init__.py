"""audiobook.engines —— TTS/ASR 引擎抽象与实现

目标（阶段 1/A5）：定义统一的 `Engine` 抽象基类（list_voices / ready /
synthesize），内置引擎（edge/local/piper/cosyvoice）与外挂引擎走同一注册表接口。

迁移状态：ASR 实现在 `audiobook.engines.asr`；统一的 `Engine` 抽象接口与注册表在
`audiobook.engines.base`。内置 TTS 引擎各自是 Engine 子类并持有自己的实现：本地 →
`local.LocalEngine`、Edge → `edge.EdgeEngine`（含整批异步编排）、Piper →
`piper.PiperEngine`（含模型缓存 / 语音目录 / 双后端）、CosyVoice →
`cosyvoice.CosyVoiceEngine`。只有外挂插件引擎仍由 base 的薄适配器包住 tts_engine 分发。
"""

_REEXPORT = {
    # A5a：Engine 抽象接口与注册表
    "Engine": ("audiobook.engines.base", "Engine"),
    "register_engine": ("audiobook.engines.base", "register_engine"),
    "get_engine": ("audiobook.engines.base", "get_engine"),
    "all_engines": ("audiobook.engines.base", "all_engines"),
    # A5b：已具体化的内置引擎
    "LocalEngine": ("audiobook.engines.local", "LocalEngine"),
    "EdgeEngine": ("audiobook.engines.edge", "EdgeEngine"),
    "PiperEngine": ("audiobook.engines.piper", "PiperEngine"),
    "CosyVoiceEngine": ("audiobook.engines.cosyvoice", "CosyVoiceEngine"),
    # 现有分发入口（兼容再导出）
    "check_engine_ready": ("tts_engine", "check_engine_ready"),
    "get_voice_list": ("tts_engine", "get_voice_list"),
    "get_registered_engines": ("tts_engine", "get_registered_engines"),
    "check_asr_ready": ("audiobook.engines.asr", "check_asr_ready"),
    "transcribe": ("audiobook.engines.asr", "transcribe"),
}


def __getattr__(name):
    import importlib
    target = _REEXPORT.get(name)
    if target is None:
        raise AttributeError(f"module 'audiobook.engines' has no attribute {name!r}")
    return getattr(importlib.import_module(target[0]), target[1])


def __dir__():
    return sorted(_REEXPORT)
