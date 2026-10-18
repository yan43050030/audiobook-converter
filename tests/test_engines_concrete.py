"""Tests for v6.0/A5b 引擎具体化：edge/piper/cosyvoice 成为独立 Engine 子类。

验证：
- get_engine(id) 返回对应的具体子类（不再是通用 _TtsEngineAdapter）；
- id / display_name 正确，is_ready / list_voices 与 tts_engine 分发一致；
- synthesize_segment 委托到各自模块的合成函数（打桩拦截，不触发真实合成）；
- 外挂插件引擎仍回退 _TtsEngineAdapter。

Run with: python3 -m unittest tests/test_engines_concrete.py
"""

import unittest
from unittest import mock

import tts_engine
from audiobook.engines import edge as edge_mod
from audiobook.engines import piper as piper_mod
from audiobook.engines.base import get_engine, _TtsEngineAdapter
from audiobook.engines.edge import EdgeEngine
from audiobook.engines.piper import PiperEngine
from audiobook.engines.cosyvoice import CosyVoiceEngine
from audiobook.engines.local import LocalEngine


class TestConcreteResolution(unittest.TestCase):
    def test_builtin_engines_resolve_to_concrete_subclasses(self):
        cases = {
            "local": LocalEngine,
            "edge": EdgeEngine,
            "piper": PiperEngine,
            "cosyvoice": CosyVoiceEngine,
        }
        for eid, cls in cases.items():
            e = get_engine(eid)
            self.assertIsInstance(e, cls, f"{eid} 未解析为 {cls.__name__}")
            self.assertNotIsInstance(e, _TtsEngineAdapter,
                                     f"{eid} 仍走通用适配器")
            self.assertEqual(e.id, eid)
            self.assertTrue(e.display_name)

    def test_display_names_match_registry(self):
        # 具体化前这些引擎经适配器用 get_registered_engines() 的 name 展示，
        # 具体化后 display_name 应与之一致，避免界面/CLI 文案变化
        reg = tts_engine.get_registered_engines()
        for eid in ("edge", "piper", "cosyvoice"):
            self.assertEqual(get_engine(eid).display_name, reg[eid]["name"])

    def test_is_ready_and_voices_match_dispatch(self):
        for eid in ("edge", "piper", "cosyvoice"):
            e = get_engine(eid)
            self.assertEqual(e.is_ready(), tts_engine.check_engine_ready(eid))
            expected = {d: tts_engine.get_voice_id(d, eid)
                        for d in tts_engine.get_voice_list(eid)}
            self.assertEqual(e.list_voices(), expected)


class TestSegmentSynthDelegation(unittest.TestCase):
    """synthesize_segment 应委托到各自模块的合成函数（不触发真实合成）。"""

    def test_piper_delegates_single_attempt(self):
        # 单段原语只尝试一次：重试归编排层，避免与 _generate_one_safe 的重试叠加
        with mock.patch.object(piper_mod, "_piper_generate") as m:
            PiperEngine().synthesize_segment("文本", "v", "+0%", "/tmp/x.mp3")
            m.assert_called_once_with("文本", "v", "+0%", "/tmp/x.mp3", should_stop=None)

    def test_cosyvoice_delegates_to_bridge(self):
        from audiobook.cloning import client as C
        with mock.patch.object(C, "sft_readiness", return_value=(True, "ok")), \
             mock.patch.object(C, "synthesize_sft") as m, \
             mock.patch.object(tts_engine, "_wav_to_mp3"):
            CosyVoiceEngine().synthesize_segment("文本", "中文男", "+0%", "/tmp/x.mp3")
        self.assertEqual(m.call_args[0][:2], ("中文男", "文本"))

    def test_edge_delegates_via_asyncio(self):
        async def _fake(*a, **k):
            return None
        with mock.patch.object(edge_mod, "_edge_generate",
                               side_effect=_fake) as m:
            EdgeEngine().synthesize_segment("文本", "zh-CN", "+0%", "/tmp/x.mp3")
            m.assert_called_once_with("文本", "zh-CN", "+0%", "/tmp/x.mp3")


class TestExternalStillAdapter(unittest.TestCase):
    def test_external_engine_uses_adapter(self):
        fake = {"type": "external", "name": "外挂X", "engine_id": "extX",
                "executable": "/bin/true"}
        with mock.patch.object(tts_engine, "get_registered_engines",
                               return_value={"extX": fake}):
            from audiobook.engines import base
            base._REGISTRY.pop("extX", None)
            try:
                e = get_engine("extX")
                self.assertIsInstance(e, _TtsEngineAdapter)
                self.assertEqual(e.id, "extX")
                self.assertEqual(e.display_name, "外挂X")
            finally:
                base._REGISTRY.pop("extX", None)


if __name__ == "__main__":
    unittest.main()
