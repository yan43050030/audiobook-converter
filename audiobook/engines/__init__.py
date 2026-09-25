"""audiobook.engines —— TTS/ASR 引擎抽象与实现

目标（阶段 1/A5）：定义统一的 `Engine` 抽象基类（list_voices / ready /
synthesize），内置引擎（edge/local/piper/cosyvoice）与外挂引擎走同一注册表接口。

迁移状态（6.0.0-dev）：ASR 实现已迁入 `audiobook.engines.asr`（A4）；统一的
`Engine` 抽象接口与注册表已落地于 `audiobook.engines.base`（A5a，薄适配器包住
tts_engine 现有分发，零行为变化）。TTS 引擎实现将于 A5b 迁入本子包。
"""

_REEXPORT = {
    # A5a：Engine 抽象接口与注册表
    "Engine": ("audiobook.engines.base", "Engine"),
    "register_engine": ("audiobook.engines.base", "register_engine"),
    "get_engine": ("audiobook.engines.base", "get_engine"),
    "all_engines": ("audiobook.engines.base", "all_engines"),
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
