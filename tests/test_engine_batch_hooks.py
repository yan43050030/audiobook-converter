"""引擎实现迁入各自模块后的行为测试：整批钩子、重试归属、再导出状态一致性。

- Engine 基类的可选钩子（run_batch / batch_parallelism / release）默认值；
- Piper 单段原语只尝试一次，重试只发生在编排层（修复此前的嵌套重试）；
- 未知引擎直接报 ValueError，不进入重试循环；
- Edge 未安装时整批生成给出明确错误；
- tts_engine 再导出的 Piper 可变状态与引擎模块是同一个对象，刷新后不会过期。
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import tts_engine
from audiobook.engines import edge as edge_mod
from audiobook.engines import piper as piper_mod
from audiobook.engines.base import get_engine
from audiobook.engines.edge import EdgeEngine
from audiobook.engines.local import LocalEngine
from audiobook.engines.piper import PiperEngine


class TestDefaultBatchHooks(unittest.TestCase):
    def test_defaults_are_generic_sequential(self):
        e = LocalEngine()
        self.assertFalse(e.run_batch([], "v", "+0%", "/tmp"))
        self.assertEqual(e.batch_parallelism(), 1)
        self.assertIsNone(e.release())


class TestPiperHooks(unittest.TestCase):
    def test_parallelism_follows_local_concurrency(self):
        with mock.patch.object(piper_mod, "_get_piper_mode", return_value=piper_mod.PIPER_MODE_CLI), \
             mock.patch.object(tts_engine, "get_local_concurrency", return_value=3):
            self.assertEqual(PiperEngine().batch_parallelism(), 3)

    def test_parallelism_falls_back_to_serial_when_unavailable(self):
        with mock.patch.object(piper_mod, "_get_piper_mode", side_effect=RuntimeError("无 Piper")):
            self.assertEqual(PiperEngine().batch_parallelism(), 1)

    def test_release_unloads_models(self):
        with mock.patch.object(piper_mod, "_unload_piper_model") as m:
            PiperEngine().release()
        m.assert_called_once_with()


class TestRetryOwnership(unittest.TestCase):
    """重试只在编排层发生：持续失败的 Piper 段尝试次数 = MAX_RETRIES + 1。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_piper_failure_is_not_retried_twice(self):
        out = os.path.join(self.tmp, "x.mp3")
        with mock.patch.object(tts_engine, "MAX_RETRIES", 2), \
             mock.patch.object(tts_engine, "RETRY_DELAY", 0), \
             mock.patch.object(piper_mod, "_piper_generate",
                               side_effect=RuntimeError("合成失败")) as m:
            with self.assertRaises(RuntimeError):
                tts_engine._generate_one_safe("一句话。", "v", "+0%", out, engine="piper")
        # 旧实现引擎层也重试：(2+1) × (2+1) = 9 次
        self.assertEqual(m.call_count, 3)


class TestUnknownEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_generate_one_raises_without_retry(self):
        with mock.patch.object(tts_engine, "_interruptible_sleep") as sleep:
            with self.assertRaises(ValueError):
                tts_engine._generate_one_safe("文本", "v", "+0%",
                                              os.path.join(self.tmp, "x.mp3"),
                                              engine="no-such-engine")
        sleep.assert_not_called()

    def test_convert_batch_raises(self):
        with self.assertRaises(ValueError):
            tts_engine.convert_batch(text="第一章\n内容。", voice="v", rate="+0%",
                                     output_dir=self.tmp, engine="no-such-engine")


class TestEdgeBatch(unittest.TestCase):
    def test_run_batch_requires_edge_tts(self):
        with mock.patch.object(edge_mod, "EDGE_TTS_AVAILABLE", False):
            with self.assertRaises(RuntimeError) as cm:
                EdgeEngine().run_batch([], "v", "+0%", "/tmp")
        self.assertIn("edge-tts", str(cm.exception))

    def test_registry_resolves_edge_with_batch_hook(self):
        self.assertIsInstance(get_engine("edge"), EdgeEngine)


class TestPiperLegacyConfig(unittest.TestCase):
    """早期语音包的 "PhonemeType.ESPEAK" 写法应得到规范化副本，原文件不动。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.model = os.path.join(self.tmp, "zh_CN-old-medium.onnx")
        open(self.model, "wb").close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_cfg(self, phoneme_type):
        import json
        with open(self.model + ".json", "w", encoding="utf-8") as f:
            json.dump({"phoneme_type": phoneme_type, "audio": {"sample_rate": 22050}}, f)

    def test_legacy_enum_repr_is_normalized(self):
        import json
        self._write_cfg("PhonemeType.ESPEAK")
        fixed = piper_mod._piper_config_path(self.model)
        self.assertNotEqual(fixed, self.model + ".json")
        with open(fixed, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["phoneme_type"], "espeak")
        with open(self.model + ".json", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["phoneme_type"], "PhonemeType.ESPEAK")

    def test_modern_config_is_used_as_is(self):
        self._write_cfg("espeak")
        self.assertEqual(piper_mod._piper_config_path(self.model), self.model + ".json")

    def test_unreadable_config_is_passed_through(self):
        with open(self.model + ".json", "w", encoding="utf-8") as f:
            f.write("{不是 json")
        self.assertEqual(piper_mod._piper_config_path(self.model), self.model + ".json")


class TestReexportedStateStaysFresh(unittest.TestCase):
    def test_piper_voices_identity_survives_refresh(self):
        before = tts_engine.PIPER_VOICES
        tts_engine.refresh_piper_voices()
        self.assertIs(tts_engine.PIPER_VOICES, piper_mod.PIPER_VOICES)
        self.assertIs(tts_engine.PIPER_VOICES, before)
        self.assertIs(tts_engine.PIPER_MODEL_PATHS, piper_mod.PIPER_MODEL_PATHS)
        for vid in piper_mod.PIPER_BUILTIN_VOICES.values():
            self.assertIn(vid, tts_engine.PIPER_VOICES.values())

    def test_invalidate_scan_cache_triggers_rescan(self):
        tts_engine.refresh_piper_voices()
        self.assertTrue(piper_mod._piper_voices_scanned)
        tts_engine._invalidate_scan_cache()
        self.assertFalse(piper_mod._piper_voices_scanned)


if __name__ == "__main__":
    unittest.main()
