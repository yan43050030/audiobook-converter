"""audiobook —— 文字转有声读物 核心包（v6.0 起）

v6.0 重构目标结构（迁移分阶段进行，见 ROADMAP.md「v6.0 大版本升级计划」）：

    audiobook/
      core/      章节检测 · 对话识别 · 文本拆分 · 文本处理
      engines/   TTS/ASR 引擎抽象与实现（edge/local/piper/cosyvoice/external）
      io/        文件读取（txt/md/docx/epub/html/pdf）· 输出（mp3/m4b/srt/id3）
      ui/        PySide6/Qt6 界面

当前阶段（6.0.0-dev，阶段 1/A3）：包骨架已建立，`audiobook` 暴露稳定的公共
导入面，实际实现仍位于顶层 `tts_engine` / `asr_engine` / `file_reader` 模块。
后续阶段（A4）将逐步把实现迁入子包，顶层模块保留为薄再导出以保持兼容。

用法（现在即可用，指向 v6.0 的稳定导入路径）：

    from audiobook import VERSION, convert_batch, transcribe

再导出采用 PEP 562 延迟加载：`import audiobook` 本身很轻，只有真正访问某个
名字时才会加载底层重依赖（pydub 等）与其启动日志。
"""

# 稳定公共 API 名单： name -> (底层模块, 属性名)
_REEXPORT = {
    "VERSION": ("tts_engine", "VERSION"),
    # 核心转换
    "convert_batch": ("tts_engine", "convert_batch"),
    "generate_preview": ("tts_engine", "generate_preview"),
    "detect_chapters": ("tts_engine", "detect_chapters"),
    "export_m4b": ("tts_engine", "export_m4b"),
    "merge_mp3_files": ("tts_engine", "merge_mp3_files"),
    "check_engine_ready": ("tts_engine", "check_engine_ready"),
    "get_voice_list": ("tts_engine", "get_voice_list"),
    # ASR
    "transcribe": ("audiobook.engines.asr", "transcribe"),
    "check_asr_ready": ("audiobook.engines.asr", "check_asr_ready"),
    # IO
    "load_file_content": ("audiobook.io.readers", "load_file_content"),
}


def __getattr__(name):  # PEP 562：按需再导出
    import importlib
    target = _REEXPORT.get(name)
    if target is None:
        raise AttributeError(f"module 'audiobook' has no attribute {name!r}")
    module = importlib.import_module(target[0])
    return getattr(module, target[1])


def __dir__():
    return sorted(_REEXPORT)
