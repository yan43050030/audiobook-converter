"""Tests for the v6.0 A5b LocalEngine (audiobook.engines.local).

验证本地引擎已成为独立 Engine 子类、经注册表可取用，且合成与 tts_engine 的
分发一致。实际合成需 espeak-ng + ffmpeg。

Run with: python3 -m unittest tests/test_engines_local.py
"""

import os
import shutil
import tempfile
import unittest

import tts_engine
from audiobook.engines.base import get_engine, Engine
from audiobook.engines.local import LocalEngine, _local_generate


class TestLocalEngineWiring(unittest.TestCase):
    def test_get_engine_returns_local_engine(self):
        e = get_engine("local")
        self.assertIsInstance(e, LocalEngine)
        self.assertIsInstance(e, Engine)
        self.assertEqual(e.id, "local")

    def test_tts_engine_reexports_local_generate(self):
        # _generate_one_safe 的 local 分支仍调用 tts_engine._local_generate（再导入）
        self.assertIs(tts_engine._local_generate, _local_generate)

    def test_is_ready_matches_dispatch(self):
        self.assertEqual(get_engine("local").is_ready(),
                         tts_engine.check_engine_ready("local"))

    def test_list_voices_matches_dispatch(self):
        e = get_engine("local")
        expected = {d: tts_engine.get_voice_id(d, "local")
                    for d in tts_engine.get_voice_list("local")}
        self.assertEqual(e.list_voices(), expected)


def _has_espeak():
    return bool(shutil.which("espeak-ng") or shutil.which("espeak"))


@unittest.skipUnless(_has_espeak() and tts_engine._ffmpeg_path(),
                     "需要 espeak-ng 与 ffmpeg")
class TestLocalEngineSynthesize(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tts_engine.refresh_local_voices()
        cls.vid = next(iter(get_engine("local").list_voices().values()))

    def test_single_segment(self):
        d = tempfile.mkdtemp()
        out = os.path.join(d, "a.mp3")
        get_engine("local").synthesize("第一章 测试。今天天气很好。", self.vid, "+0%", out)
        self.assertTrue(os.path.exists(out))
        self.assertGreater(os.path.getsize(out), 0)
        self.assertGreater(tts_engine.get_audio_duration(out), 0.5)

    def test_multi_segment_merges(self):
        # 超过 split_text 阈值以触发多段 + 合并路径
        long_text = "。".join(f"这是第{i}句用于触发分段合并的中文测试文本" for i in range(400))
        d = tempfile.mkdtemp()
        out = os.path.join(d, "b.mp3")
        get_engine("local").synthesize(long_text, self.vid, "+0%", out)
        self.assertTrue(os.path.exists(out))
        self.assertGreater(os.path.getsize(out), 0)


if __name__ == "__main__":
    unittest.main()
