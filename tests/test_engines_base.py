"""Tests for the v6.0 A5a Engine abstract interface + registry.

验证统一 Engine 接口与注册表：抽象性、适配器与 tts_engine 现有分发一致、
注册/取用往返。合成走本地引擎（espeak-ng + ffmpeg）时才实际执行。

Run with: python3 -m unittest tests/test_engines_base.py
"""

import os
import shutil
import tempfile
import unittest

import tts_engine
from audiobook.engines.base import (
    Engine, register_engine, get_engine, all_engines, _TtsEngineAdapter,
)


class TestEngineAbstract(unittest.TestCase):
    def test_cannot_instantiate_abstract(self):
        with self.assertRaises(TypeError):
            Engine()  # 抽象类，缺少方法实现

    def test_subclass_must_implement_all(self):
        class Partial(Engine):
            id = "partial"
            def is_ready(self):
                return (True, "")
            # 缺 list_voices / synthesize_segment
        with self.assertRaises(TypeError):
            Partial()


class TestAdapterMatchesDispatch(unittest.TestCase):
    def test_edge_adapter_is_ready_matches(self):
        e = get_engine("edge")
        self.assertIsInstance(e, Engine)
        self.assertEqual(e.id, "edge")
        self.assertTrue(e.display_name)
        self.assertEqual(e.is_ready(), tts_engine.check_engine_ready("edge"))

    def test_list_voices_matches_dispatch(self):
        e = get_engine("edge")
        voices = e.list_voices()
        self.assertIsInstance(voices, dict)
        expected = {d: tts_engine.get_voice_id(d, "edge")
                    for d in tts_engine.get_voice_list("edge")}
        self.assertEqual(voices, expected)

    def test_all_engines_covers_registry(self):
        ids = [e.id for e in all_engines()]
        self.assertEqual(set(ids), set(tts_engine.get_registered_engines().keys()))
        for e in all_engines():
            self.assertIsInstance(e, Engine)

    def test_get_engine_caches_same_instance(self):
        self.assertIs(get_engine("edge"), get_engine("edge"))

    def test_unknown_engine_raises(self):
        with self.assertRaises(KeyError):
            get_engine("nonexistent_engine_xyz")


class TestRegistry(unittest.TestCase):
    def test_register_and_get_roundtrip(self):
        class Stub(Engine):
            id = "stub_test_engine"
            display_name = "Stub"
            def is_ready(self):
                return (True, "ok")
            def list_voices(self):
                return {"voiceA": "a"}
            def synthesize_segment(self, text, voice, rate, out_path, should_stop=None):
                with open(out_path, "wb") as f:
                    f.write(b"x")
        stub = Stub()
        register_engine(stub)
        try:
            self.assertIs(get_engine("stub_test_engine"), stub)
        finally:
            from audiobook.engines import base
            base._REGISTRY.pop("stub_test_engine", None)

    def test_register_rejects_empty_id(self):
        class NoId(Engine):
            id = ""
            def is_ready(self):
                return (True, "")
            def list_voices(self):
                return {}
            def synthesize_segment(self, text, voice, rate, out_path, should_stop=None):
                pass
        with self.assertRaises(ValueError):
            register_engine(NoId())


class TestGenerateOneSafeRouting(unittest.TestCase):
    """A5c：_generate_one_safe 的非 edge 引擎经注册表 synthesize_segment 分发。"""

    def test_routes_through_registry_synthesize_segment(self):
        from unittest import mock
        from audiobook.engines.local import LocalEngine
        calls = []

        class Spy(LocalEngine):
            def synthesize_segment(self, text, voice, rate, out_path, should_stop=None):
                calls.append(text)
                with open(out_path, "wb") as f:
                    f.write(b"\x00" * 16)  # 非空即视为成功

        d = tempfile.mkdtemp()
        out = os.path.join(d, "o.mp3")
        # _generate_one_safe 在函数内 `from audiobook.engines.base import get_engine`，
        # 故打桩该名字即可拦截分发。
        with mock.patch("audiobook.engines.base.get_engine", return_value=Spy()):
            tts_engine._generate_one_safe("一段短文本", "cmn", "+0%", out, engine="local")
        self.assertTrue(calls, "synthesize_segment 未被调用（未走注册表分发）")
        self.assertTrue(os.path.exists(out) and os.path.getsize(out) > 0)


def _has_espeak():
    return bool(shutil.which("espeak-ng") or shutil.which("espeak"))


@unittest.skipUnless(_has_espeak() and tts_engine._ffmpeg_path(),
                     "需要 espeak-ng 与 ffmpeg")
class TestAdapterSynthesize(unittest.TestCase):
    def test_local_synthesize_produces_audio(self):
        tts_engine.refresh_local_voices()
        e = get_engine("local")
        voices = e.list_voices()
        self.assertTrue(voices)
        vid = next(iter(voices.values()))
        d = tempfile.mkdtemp()
        out = os.path.join(d, "a.mp3")
        e.synthesize("第一章 测试。今天天气很好。", vid, "+0%", out)
        self.assertTrue(os.path.exists(out))
        self.assertGreater(os.path.getsize(out), 0)


if __name__ == "__main__":
    unittest.main()
