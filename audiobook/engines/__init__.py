"""audiobook.engines —— TTS/ASR 引擎抽象与实现

目标（阶段 1/A5）：定义统一的 `Engine` 抽象基类（list_voices / ready /
synthesize），内置引擎（edge/local/piper/cosyvoice）与外挂引擎走同一注册表接口。

迁移状态（6.0.0-dev）：抽象基类尚未落地，此处先以延迟再导出暴露现有的引擎
就绪检查与语音枚举入口，保持功能可用；`Engine` 基类将在 A5 加入本子包。
"""

_REEXPORT = {
    "check_engine_ready": ("tts_engine", "check_engine_ready"),
    "get_voice_list": ("tts_engine", "get_voice_list"),
    "get_registered_engines": ("tts_engine", "get_registered_engines"),
    "check_asr_ready": ("asr_engine", "check_asr_ready"),
    "transcribe": ("asr_engine", "transcribe"),
}


def __getattr__(name):
    import importlib
    target = _REEXPORT.get(name)
    if target is None:
        raise AttributeError(f"module 'audiobook.engines' has no attribute {name!r}")
    return getattr(importlib.import_module(target[0]), target[1])


def __dir__():
    return sorted(_REEXPORT)
