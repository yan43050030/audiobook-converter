"""audiobook.engines.base — TTS 引擎抽象接口与注册表。

    class Engine(ABC):
        id: str
        display_name: str
        def is_ready(self) -> tuple[bool, str]
        def list_voices(self) -> dict[str, str]      # 显示名 -> voice_id
        def synthesize_segment(self, text, voice, rate, out_path, should_stop=None) -> None

        # 整批生成的可选钩子
        def run_batch(...) -> bool       # 引擎自带整批编排（Edge 的异步并发）
        def batch_parallelism() -> int   # 通用编排的并行章节数（Piper 按 CPU 自适应）
        def release() -> None            # 整批结束后释放资源（Piper 卸载模型）

内置引擎（local / edge / piper / cosyvoice / clone）各自是独立的 Engine 子类；只有外挂
插件引擎经 `_TtsEngineAdapter` 包住 tts_engine 的外挂引擎调用。

注册表：register_engine(e) / get_engine(id) / all_engines()
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Tuple


class Engine(ABC):
    """一个 TTS 引擎的统一接口。

    子类须提供类属性 `id`（稳定标识，如 "edge"）与 `display_name`（界面名），
    并实现下述三个方法。
    """

    id: str = ""
    display_name: str = ""

    @abstractmethod
    def is_ready(self) -> Tuple[bool, str]:
        """返回 (是否就绪, 说明/安装提示)。"""
        raise NotImplementedError

    @abstractmethod
    def list_voices(self) -> Dict[str, str]:
        """返回 {显示名: voice_id}。未就绪时可返回空字典。"""
        raise NotImplementedError

    @abstractmethod
    def synthesize_segment(self, text: str, voice: str, rate: str, out_path: str,
                           should_stop=None) -> None:
        """把**一段**文本原样合成为单个音频文件（不做分段）。

        这是分发的最小原语：编排层（`_generate_one_safe` / `convert_batch` /
        本类的 `synthesize`）负责分段、合并、重试与进度，引擎只管把给定文本
        合成出来。voice 为 voice_id；rate 形如 "+0%"；should_stop 为可选中断回调。
        """
        raise NotImplementedError

    def synthesize(self, text: str, voice: str, rate: str, out_path: str,
                   should_stop=None) -> None:
        """把 text 合成为单个音频文件（超长自动分段后合并）。

        默认实现基于 `synthesize_segment`：直接/库调用可用；应用内的批量生成走
        `_generate_one_safe`（额外带重试与逐段进度）。不含重试——重试属编排层。
        """
        from audiobook.core.text import split_text
        from audiobook.io.audio import _merge_mp3_files
        import os as _os
        import shutil as _shutil
        import tempfile as _tempfile
        segments = split_text(text)
        if len(segments) == 1:
            self.synthesize_segment(segments[0], voice, rate, out_path, should_stop=should_stop)
            return
        temp_dir = _tempfile.mkdtemp()
        temp_files = []
        try:
            for i, seg in enumerate(segments):
                tp = _os.path.join(temp_dir, f"seg_{i:04d}.mp3")
                self.synthesize_segment(seg, voice, rate, tp, should_stop=should_stop)
                temp_files.append(tp)
            _merge_mp3_files(temp_files, out_path)
        finally:
            _shutil.rmtree(temp_dir, ignore_errors=True)

    # ---- 整批生成的可选钩子（tts_engine.convert_batch 经注册表调用） ----

    def run_batch(self, items, voice: str, rate: str, output_dir: str, *,
                  progress_callback=None, should_stop=None, item_callback=None) -> bool:
        """引擎自带的整批编排。返回 True 表示已处理完整批；默认 False，交由通用编排
        （逐章 `_generate_one_safe`，按 `batch_parallelism()` 串行或线程池并行）。

        覆盖者需自行更新 items 的 status/error、调用 progress_callback、推送逐章事件
        并落盘进度——与通用编排的约定一致（参考 EdgeEngine）。
        """
        return False

    def batch_parallelism(self) -> int:
        """通用编排的并行章节数。默认 1（串行）；只有能安全并发合成的引擎才返回 >1。"""
        return 1

    def release(self) -> None:
        """整批结束（含失败 / 暂停）后释放资源，如卸载模型。默认无操作。"""
        return None

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        return f"<Engine {self.id!r} ({self.display_name})>"


class _TtsEngineAdapter(Engine):
    """把 tts_engine 现有的按引擎分发函数包成 Engine 接口（A5a，零行为变化）。"""

    def __init__(self, engine_id: str, display_name: str):
        self.id = engine_id
        self.display_name = display_name

    def is_ready(self) -> Tuple[bool, str]:
        import tts_engine as _te
        return _te.check_engine_ready(self.id)

    def list_voices(self) -> Dict[str, str]:
        import tts_engine as _te
        return {disp: _te.get_voice_id(disp, self.id)
                for disp in _te.get_voice_list(self.id)}

    def synthesize_segment(self, text: str, voice: str, rate: str, out_path: str,
                           should_stop=None) -> None:
        import tts_engine as _te
        _te._raw_segment_synth(self.id)(text, voice, rate, out_path, should_stop=should_stop)


# ================ 注册表 ================

_REGISTRY: Dict[str, Engine] = {}

# 已具体化为独立 Engine 子类的内置引擎： id -> (模块, 类名)。
# get_engine 优先用它们；只有外挂插件引擎才回退到 _TtsEngineAdapter。
_CONCRETE: Dict[str, Tuple[str, str]] = {
    "local": ("audiobook.engines.local", "LocalEngine"),
    "edge": ("audiobook.engines.edge", "EdgeEngine"),
    "piper": ("audiobook.engines.piper", "PiperEngine"),
    "cosyvoice": ("audiobook.engines.cosyvoice", "CosyVoiceEngine"),
    "clone": ("audiobook.engines.clone", "CloneEngine"),
}


def register_engine(engine: Engine) -> None:
    """注册一个引擎实例（按 engine.id 覆盖）。"""
    if not getattr(engine, "id", ""):
        raise ValueError("engine.id 不能为空")
    _REGISTRY[engine.id] = engine


def get_engine(engine_id: str) -> Engine:
    """按 id 取引擎。顺序：显式注册的 > 已迁移的具体子类（_CONCRETE）> 为
    tts_engine 已注册引擎（内置 + 外挂）构建的薄适配器。找不到则抛 KeyError。"""
    if engine_id in _REGISTRY:
        return _REGISTRY[engine_id]
    spec = _CONCRETE.get(engine_id)
    if spec is not None:
        import importlib
        cls = getattr(importlib.import_module(spec[0]), spec[1])
        engine = cls()
        _REGISTRY[engine_id] = engine
        return engine
    import tts_engine as _te
    engines = _te.get_registered_engines()
    info = engines.get(engine_id)
    if info is None:
        raise KeyError(engine_id)
    adapter = _TtsEngineAdapter(engine_id, info.get("name", engine_id))
    _REGISTRY[engine_id] = adapter
    return adapter


def all_engines() -> List[Engine]:
    """返回当前所有已注册引擎（内置 + 外挂）的 Engine 列表，顺序与
    tts_engine.get_registered_engines() 一致。"""
    import tts_engine as _te
    return [get_engine(eid) for eid in _te.get_registered_engines()]
