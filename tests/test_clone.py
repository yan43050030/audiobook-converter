"""Tests for 声音克隆（v6.0/C1）：音色档案、桥接子进程协议、clone 引擎、CLI。

真实 CosyVoice（torch + 数 GB 模型）无法在 CI 运行。这里用一个仿官方接口的替身 `cosyvoice`
包（AutoModel 按 yaml 选型、CosyVoice2/3 子类、add_zero_shot_spk、prompt_wav 传路径、输出
{'tts_speech': [1,N]}）真实启动桥接子进程端到端跑通，替身把每次调用写进日志供断言。
需要 ffmpeg（规整参考音频 / 转 MP3）与 numpy（桥接端），缺失时跳过。

Run with: python3 -m unittest tests/test_clone.py
"""

import io
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import textwrap
import time
import unittest
import wave
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

import tts_engine
from audiobook.cloning import profiles as P
from audiobook.cloning import client as C

try:
    import numpy  # noqa: F401
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False
_HAS_FFMPEG = bool(tts_engine._ffmpeg_path())

FAKE_COSYVOICE = textwrap.dedent('''
    import json, os, sys, time
    import numpy as np
    LOG = os.environ.get("FAKE_COSY_LOG", "")

    def _log(**kw):
        if LOG:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(kw, ensure_ascii=False) + "\\n")

    class CosyVoice:
        def __init__(self, model_dir, **kw):
            print("loading model ... (stdout noise must not break protocol)")
            sys.stdout.flush()
            if os.path.exists(os.path.join(model_dir, "FAIL_LOAD")):
                raise RuntimeError("模型文件损坏")
            self.model_dir, self.sample_rate = model_dir, 24000
            _log(event="load", cls=type(self).__name__)

        def add_zero_shot_spk(self, prompt_text, prompt_wav, zero_shot_spk_id):
            _log(event="add_spk", prompt_text=prompt_text, prompt_wav=prompt_wav, spk=zero_shot_spk_id)
            return True

        def _speech(self, text, n):
            if "BOOM" in text:
                raise ValueError("合成失败")
            if "CRASH" in text:
                os._exit(3)
            if "SLOW" in text:
                time.sleep(30)
            yield {"tts_speech": np.full((1, n), 0.1, dtype=np.float32)}
            yield {"tts_speech": np.full((1, n), -0.1, dtype=np.float32)}

        def inference_zero_shot(self, tts_text, prompt_text, prompt_wav, zero_shot_spk_id="",
                                stream=False, speed=1.0, text_frontend=True):
            _log(event="zero_shot", text=tts_text, prompt_text=prompt_text,
                 prompt_wav=prompt_wav, spk=zero_shot_spk_id, speed=speed)
            return self._speech(tts_text, 1200)

        def inference_cross_lingual(self, tts_text, prompt_wav, zero_shot_spk_id="",
                                    stream=False, speed=1.0, text_frontend=True):
            _log(event="cross", text=tts_text, prompt_wav=prompt_wav, speed=speed)
            return self._speech(tts_text, 1200)

    class CosyVoice2(CosyVoice):
        pass

    class CosyVoice3(CosyVoice2):
        pass

    class OldApiCosyVoice2(CosyVoice2):
        """旧版接口：提示音频必须是张量（这里用 dict 代替），传路径会 AttributeError。"""
        def add_zero_shot_spk(self, *a):
            raise AttributeError("no add_zero_shot_spk in old api")

        def inference_zero_shot(self, tts_text, prompt_text, prompt_speech_16k, stream=False, speed=1.0):
            if isinstance(prompt_speech_16k, str):
                raise AttributeError("'str' object has no attribute 'shape'")
            _log(event="zero_shot_old", prompt=prompt_speech_16k)
            return self._speech(tts_text, 1200)

    def AutoModel(**kwargs):
        d = kwargs["model_dir"]
        if os.path.exists(os.path.join(d, "OLD_API")):
            return OldApiCosyVoice2(**kwargs)
        for marker, cls in (("cosyvoice3.yaml", CosyVoice3), ("cosyvoice2.yaml", CosyVoice2),
                            ("cosyvoice.yaml", CosyVoice)):
            if os.path.exists(os.path.join(d, marker)):
                return cls(**kwargs)
        raise TypeError("No valid model type found!")
''')

FAKE_FILE_UTILS = textwrap.dedent('''
    def load_wav(wav, target_sr):
        return {"tensor_of": wav, "sr": target_sr}
''')


def make_wav(path, seconds, rate=44100, channels=2):
    n = int(seconds * rate)
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            v = int(8000 * math.sin(2 * math.pi * 440 * i / rate))
            frames += struct.pack("<h", v) * channels
        w.writeframes(bytes(frames))
    return path


class _Env(unittest.TestCase):
    """隔离的配置文件 / 存储目录 + 替身 CosyVoice 仓库。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ab_clone_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.storage = os.path.join(self.tmp, "store")
        os.makedirs(self.storage)
        self.cfg_path = os.path.join(self.tmp, "config.json")
        old_cache = tts_engine._config_cache
        tts_engine._config_cache = None
        self.addCleanup(setattr, tts_engine, "_config_cache", old_cache)
        for p in (mock.patch.object(tts_engine, "CONFIG_PATH", self.cfg_path),
                  mock.patch.object(tts_engine, "get_storage_dir", lambda: self.storage)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(C.shutdown)

        # 替身 CosyVoice 仓库（经 --repo 加入桥接端 sys.path）
        self.repo = os.path.join(self.tmp, "CosyVoice")
        pkg = os.path.join(self.repo, "cosyvoice")
        os.makedirs(os.path.join(pkg, "cli"))
        os.makedirs(os.path.join(pkg, "utils"))
        for d in (pkg, os.path.join(pkg, "cli"), os.path.join(pkg, "utils")):
            open(os.path.join(d, "__init__.py"), "w").close()
        with open(os.path.join(pkg, "cli", "cosyvoice.py"), "w", encoding="utf-8") as f:
            f.write(FAKE_COSYVOICE)
        with open(os.path.join(pkg, "utils", "file_utils.py"), "w", encoding="utf-8") as f:
            f.write(FAKE_FILE_UTILS)
        self.log = os.path.join(self.tmp, "cosy_calls.jsonl")
        os.environ["FAKE_COSY_LOG"] = self.log
        self.addCleanup(os.environ.pop, "FAKE_COSY_LOG", None)

    def set_cfg(self, **kw):
        cfg = tts_engine._load_config()
        cfg.update(kw)
        tts_engine._save_config(cfg)

    def model(self, name="Fun-CosyVoice3-0.5B", marker="cosyvoice3.yaml", extra=()):
        d = os.path.join(self.storage, "cosyvoice-models", name)
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, marker), "w").close()
        for e in extra:
            open(os.path.join(d, e), "w").close()
        return d

    def calls(self, event=None):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        return [r for r in rows if event is None or r["event"] == event]

    def profile(self, name="测试音色", text="希望你以后能够做的比我还好呦。", seconds=5):
        src = make_wav(os.path.join(self.tmp, f"{name}.wav"), seconds)
        return P.create_profile(name, src, prompt_text=text)

    def configure_bridge(self):
        self.set_cfg(cosyvoice_python=sys.executable, cosyvoice_repo=self.repo)


class TestConfigSet(_Env):
    def _cli(self, argv):
        from audiobook.cli import main
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_set_values_and_reject_unknown(self):
        rc, out, _ = self._cli(["config", "--set", "cosyvoice_python=/opt/py/bin/python",
                                "--set", "wizard_completed=true"])
        self.assertEqual(rc, 0)
        with open(self.cfg_path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["cosyvoice_python"], "/opt/py/bin/python")
        self.assertIs(saved["wizard_completed"], True, "按 schema 校正为布尔")
        rc, _, err = self._cli(["config", "--set", "no_such_key=1"])
        self.assertEqual(rc, 2)
        rc, _, err = self._cli(["config", "--set", "missing_equals"])
        self.assertEqual(rc, 2)


# ======================= 音色档案 =======================

@unittest.skipUnless(_HAS_FFMPEG, "需要 ffmpeg")
class TestProfiles(_Env):
    def test_create_normalizes_audio(self):
        p = self.profile()
        self.assertTrue(p.voice_id.startswith("clone:"))
        with wave.open(p.wav, "rb") as w:
            self.assertEqual((w.getnchannels(), w.getframerate(), w.getsampwidth()), (1, 16000, 2))
        self.assertAlmostEqual(p.duration, 5.0, delta=0.1)
        again = P.get_profile(p.id)
        self.assertEqual((again.name, again.prompt_text), ("测试音色", "希望你以后能够做的比我还好呦。"))
        self.assertEqual([x.id for x in P.list_profiles()], [p.id])

    def test_too_short_rejected_and_cleaned(self):
        with self.assertRaises(P.ProfileError) as cm:
            self.profile(seconds=1.5)
        self.assertIn("太短", str(cm.exception))
        self.assertEqual(os.listdir(P.profiles_dir()), [])

    def test_long_audio_trimmed(self):
        p = self.profile(seconds=40)
        self.assertAlmostEqual(p.duration, P.MAX_SECONDS, delta=0.1)

    def test_not_audio(self):
        bad = os.path.join(self.tmp, "x.mp3")
        with open(bad, "w") as f:
            f.write("not audio")
        with self.assertRaises(P.ProfileError):
            P.create_profile("坏", bad)

    def test_update_delete_and_ids(self):
        p = self.profile()
        P.update_profile(p.id, name="新名字", prompt_text="新文字稿")
        self.assertEqual(P.get_profile(p.id).name, "新名字")
        for bad in ("../etc", "x", "A-B", "a/b"):
            self.assertIsNone(P.get_profile(bad))
            with self.assertRaises(P.ProfileError):
                P.delete_profile(bad)
        self.assertEqual(P.parse_voice_id(p.voice_id), p.id)
        self.assertIsNone(P.parse_voice_id("clone:../x"))
        self.assertIsNone(P.parse_voice_id("zh-CN-Xiaoxiao"))
        P.delete_profile(p.id)
        self.assertEqual(P.list_profiles(), [])

    def test_voice_map_disambiguates_duplicates(self):
        a = self.profile(name="同名")
        b = self.profile(name="同名")
        vm = P.voice_map()
        self.assertEqual(len(vm), 2)
        self.assertEqual(set(vm.values()), {a.voice_id, b.voice_id})


# ======================= 环境解析 / 就绪 =======================

class TestResolution(_Env):
    def test_model_dir_prefers_v3_and_skips_sft(self):
        self.assertEqual(C.resolve_model_dir(), "")
        self.model("CosyVoice-300M-SFT", "cosyvoice.yaml")
        self.assertEqual(C.resolve_model_dir(), "", "SFT 模型不能用于克隆")
        base = self.model("CosyVoice-300M", "cosyvoice.yaml")
        self.assertEqual(C.resolve_model_dir(), base)
        v2 = self.model("CosyVoice2-0.5B", "cosyvoice2.yaml")
        self.assertEqual(C.resolve_model_dir(), v2)
        v3 = self.model()
        self.assertEqual(C.resolve_model_dir(), v3)
        self.set_cfg(clone_model_dir=v2)
        self.assertEqual(C.resolve_model_dir(), v2, "显式配置优先")
        self.set_cfg(clone_model_dir=os.path.join(self.tmp, "missing"))
        self.assertEqual(C.resolve_model_dir(), "")

    def test_python_default_and_frozen(self):
        self.assertEqual(C.resolve_python(), sys.executable)
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(C.resolve_python(), "", "打包版必须显式配置解释器")
        self.set_cfg(cosyvoice_python="/opt/conda/envs/cosyvoice/bin/python")
        self.assertEqual(C.resolve_python(), "/opt/conda/envs/cosyvoice/bin/python")

    @unittest.skipUnless(_HAS_FFMPEG, "需要 ffmpeg")
    def test_readiness_messages(self):
        with mock.patch.object(C, "resolve_python", return_value=""):
            self.assertIn("解释器", C.readiness()[1])
        self.set_cfg(cosyvoice_python=os.path.join(self.tmp, "nope", "python"))
        self.assertIn("不存在", C.readiness()[1])
        self.set_cfg(cosyvoice_python="")
        self.assertIn("模型", C.readiness()[1])
        self.model()
        self.assertIn("还没有克隆音色", C.readiness()[1])
        self.profile()
        ok, msg = C.readiness()
        self.assertTrue(ok)
        self.assertIn("CosyVoice3", msg)
        self.assertEqual(tts_engine.check_engine_ready("clone"), (ok, msg))


# ======================= 桥接子进程（真实子进程 + 替身 CosyVoice） =======================

@unittest.skipUnless(_HAS_NUMPY and _HAS_FFMPEG, "需要 numpy 与 ffmpeg")
class TestBridge(_Env):
    def setUp(self):
        super().setUp()
        self.configure_bridge()
        self.v3 = self.model()
        self.prof = self.profile()

    def synth(self, prof=None, text="收到好友从远方寄来的生日礼物。", **kw):
        out = os.path.join(self.tmp, f"out_{time.time_ns()}.wav")
        resp = C.synthesize(prof or self.prof, text, out, **kw)
        return resp, out

    def test_ping_and_load_despite_stdout_noise(self):
        rep = C.check(load=True)
        self.assertEqual(rep["error"], "")
        self.assertTrue(rep["ping"]["cosyvoice"])
        self.assertEqual(rep["load"]["model_type"], "CosyVoice3")
        self.assertEqual(rep["load"]["sample_rate"], 24000)

    def test_zero_shot_v3_prefix_and_speaker_cache(self):
        resp, out = self.synth(speed=1.2)
        self.assertEqual(resp["mode"], "zero_shot")
        with wave.open(out, "rb") as w:
            self.assertEqual((w.getframerate(), w.getnframes(), w.getnchannels()), (24000, 2400, 1))
        adds = self.calls("add_spk")
        self.assertEqual(len(adds), 1)
        self.assertEqual(adds[0]["prompt_text"],
                         "You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。")
        self.assertEqual(adds[0]["prompt_wav"], self.prof.wav, "参考音频以路径传入")
        zs = self.calls("zero_shot")[-1]
        self.assertEqual((zs["prompt_text"], zs["prompt_wav"]), ("", ""))
        self.assertTrue(zs["spk"].startswith(self.prof.id + "@"))
        self.assertAlmostEqual(zs["speed"], 1.2)

        self.synth()                                   # 同一音色：不再重复登记
        self.assertEqual(len(self.calls("add_spk")), 1)
        self.assertEqual(len(self.calls("load")), 1, "模型只加载一次（常驻进程）")

        P.update_profile(self.prof.id, prompt_text="改过的文字稿")
        future = time.time() + 5
        os.utime(os.path.join(self.prof.path, "profile.json"), (future, future))
        self.synth(prof=P.get_profile(self.prof.id))
        self.assertEqual(len(self.calls("add_spk")), 2, "文字稿改动后应重新登记说话人")

    def test_cross_lingual_without_transcript(self):
        p = P.update_profile(self.prof.id, prompt_text="")
        resp, _ = self.synth(prof=p, text="Hello world")
        self.assertEqual(resp["mode"], "cross_lingual")
        self.assertEqual(self.calls("cross")[-1]["text"],
                         "You are a helpful assistant.<|endofprompt|>Hello world")

    def test_v2_has_no_v3_prefix(self):
        self.set_cfg(clone_model_dir=self.model("CosyVoice2-0.5B", "cosyvoice2.yaml"))
        self.synth()
        self.assertEqual(self.calls("add_spk")[-1]["prompt_text"], "希望你以后能够做的比我还好呦。")

    def test_old_api_falls_back_to_tensor(self):
        self.set_cfg(clone_model_dir=self.model("Old-CosyVoice2", "cosyvoice2.yaml", extra=("OLD_API",)))
        resp, out = self.synth()
        self.assertEqual(self.calls("zero_shot_old")[-1]["prompt"],
                         {"tensor_of": self.prof.wav, "sr": 16000})
        self.assertTrue(os.path.getsize(out) > 44)

    def test_model_error_reported_and_bridge_survives(self):
        with self.assertRaises(C.CloneBridgeError) as cm:
            self.synth(text="BOOM")
        self.assertIn("合成失败", str(cm.exception))
        self.synth()  # 同一进程继续可用
        self.assertEqual(len(self.calls("load")), 1)

    def test_load_failure(self):
        self.set_cfg(clone_model_dir=self.model("Broken", "cosyvoice2.yaml", extra=("FAIL_LOAD",)))
        with self.assertRaises(C.CloneBridgeError) as cm:
            self.synth()
        self.assertIn("模型文件损坏", str(cm.exception))

    def test_crash_then_restart(self):
        with self.assertRaises(C.CloneBridgeError) as cm:
            self.synth(text="CRASH")
        self.assertIn("意外退出", str(cm.exception))
        self.synth()  # 自动重启
        self.assertEqual(len(self.calls("load")), 2)

    def test_stop_kills_bridge_quickly(self):
        t0 = time.time()
        stop_at = t0 + 0.5
        with self.assertRaises(tts_engine.StopRequested):
            self.synth(text="SLOW", should_stop=lambda: time.time() > stop_at)
        self.assertLess(time.time() - t0, 10, "暂停应立即生效，不等推理结束")
        self.synth()  # 下次调用自动重启

    def test_bad_python_reports_error(self):
        C.shutdown()
        self.set_cfg(cosyvoice_python=os.path.join(self.tmp, "no-such-python"))
        with self.assertRaises(C.CloneBridgeError) as cm:
            self.synth()
        self.assertIn("无法启动", str(cm.exception))


# ======================= 引擎 / 批量 / CLI =======================

@unittest.skipUnless(_HAS_NUMPY and _HAS_FFMPEG, "需要 numpy 与 ffmpeg")
class TestCloneEngine(_Env):
    def setUp(self):
        super().setUp()
        self.configure_bridge()
        self.model()
        self.prof = self.profile(name="小明")

    def test_registry_and_voices(self):
        from audiobook.engines.base import get_engine
        from audiobook.engines.clone import CloneEngine
        e = get_engine("clone")
        self.assertIsInstance(e, CloneEngine)
        self.assertEqual(e.display_name, tts_engine.get_registered_engines()["clone"]["name"])
        self.assertEqual(e.list_voices(), {"小明": self.prof.voice_id})
        self.assertEqual(tts_engine.get_voice_list("clone"), ["小明"])
        self.assertEqual(tts_engine.get_voice_id("小明", "clone"), self.prof.voice_id)
        self.assertEqual(tts_engine.get_voice_id(self.prof.voice_id, "clone"), self.prof.voice_id)

    def test_convert_batch_end_to_end(self):
        out = os.path.join(self.tmp, "book")
        files = tts_engine.convert_batch(
            text="第一章 甲\n内容一。\n\n第二章 乙\n内容二。\n", voice=self.prof.voice_id,
            rate="+10%", output_dir=out, engine="clone")
        self.assertEqual(len(files), 2)
        for f in files:
            self.assertTrue(f.endswith(".mp3") and os.path.getsize(f) > 0)
        self.assertAlmostEqual(self.calls("zero_shot")[-1]["speed"], 1.1)
        self.assertEqual(len(self.calls("load")), 1)

    def test_missing_profile(self):
        from audiobook.engines.base import get_engine
        with self.assertRaises(C.CloneBridgeError):
            get_engine("clone").synthesize_segment("你好", "clone:v0-deleted", "+0%",
                                                   os.path.join(self.tmp, "x.mp3"))

    def _cli(self, argv):
        from audiobook.cli import main
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_cli(self):
        rc, out, _ = self._cli(["clone", "list"])
        self.assertIn("小明", out)
        src = make_wav(os.path.join(self.tmp, "b.wav"), 6)
        rc, out, err = self._cli(["clone", "add", "小红", src])
        self.assertEqual(rc, 0)
        self.assertIn("文字稿", err, "未填文字稿应给出提示")
        rc, out, _ = self._cli(["clone", "check", "--load"])
        self.assertEqual(rc, 0, out)
        self.assertIn("CosyVoice3", out)
        mp3 = os.path.join(self.tmp, "say.mp3")
        rc, out, _ = self._cli(["clone", "say", self.prof.id, "你好呀", "-o", mp3])
        self.assertEqual(rc, 0)
        self.assertGreater(os.path.getsize(mp3), 0)
        rc, out, _ = self._cli(["clone", "rm", self.prof.id, "-y"])
        self.assertEqual(rc, 0)
        self.assertIsNone(P.get_profile(self.prof.id))
        rc, _, _ = self._cli(["clone", "rm", self.prof.id, "-y"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
