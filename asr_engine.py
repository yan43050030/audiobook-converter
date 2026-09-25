"""兼容垫片 — ASR 实现已迁至 `audiobook.engines.asr`（v6.0/A4）。

保留此顶层模块，让旧的 `from asr_engine import ...` 与 PyInstaller 导入图继续
可用。新代码请改用 `from audiobook.engines.asr import ...`。
"""

# 再导出全部公开名（常量与函数：transcribe / check_asr_ready / WHISPER_* 等）
from audiobook.engines.asr import *  # noqa: F401,F403

# 显式再导出下划线私有名：星号导入不含下划线名，而测试直接引用这些格式化函数
from audiobook.engines.asr import (  # noqa: F401
    _format_timestamp,
    _format_txt,
    _format_srt,
    _format_json,
)
