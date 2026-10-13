"""Tests for audiobook.onboarding（v6.0/C2-U2 首次运行向导的纯逻辑）与新增配置项。

配置文件与便携存储目录都重定向到临时目录，不碰真实用户配置。

Run with: python3 -m unittest tests/test_onboarding.py
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import tts_engine
import audiobook.config as C
from audiobook import onboarding as OB
from audiobook import models as M


class IsolatedEnv(unittest.TestCase):
    """把配置文件 / 存储目录指到临时位置。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ab_onb_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.storage = os.path.join(self.tmp, "store")
        os.makedirs(self.storage)
        self.cfg_path = os.path.join(self.tmp, "config.json")
        old_cache = tts_engine._config_cache
        tts_engine._config_cache = None
        self.addCleanup(setattr, tts_engine, "_config_cache", old_cache)
        for p in (mock.patch.object(tts_engine, "CONFIG_PATH", self.cfg_path),
                  mock.patch.object(tts_engine, "get_storage_dir", lambda: self.storage),
                  mock.patch.object(tts_engine, "get_model_search_paths", lambda: [])):
            p.start()
            self.addCleanup(p.stop)

    def install_piper(self, vid="zh_CN-huayan-medium"):
        d = os.path.join(self.storage, "piper-models", "zh")
        os.makedirs(d, exist_ok=True)
        for fn in (f"{vid}.onnx", f"{vid}.onnx.json"):
            with open(os.path.join(d, fn), "wb") as f:
                f.write(b"\0")

    def saved(self):
        with open(self.cfg_path, encoding="utf-8") as f:
            return json.load(f)


class TestScenarios(IsolatedEnv):
    def test_ids_unique_and_engines_registered(self):
        ids = [s.id for s in OB.SCENARIOS]
        self.assertEqual(len(ids), len(set(ids)))
        registered = tts_engine.get_registered_engines()
        for s in OB.SCENARIOS:
            self.assertIn(s.engine, registered)

    def test_scenario_models_are_downloadable(self):
        keys = {(i.kind, i.id) for i in M.downloadable()}
        for s in OB.SCENARIOS:
            for m in s.models:
                self.assertIn(m, keys, f"{s.id} 引用了不可下载的模型 {m}")

    def test_get_unknown_raises(self):
        with self.assertRaises(KeyError):
            OB.get("nope")


class TestPendingDownloads(IsolatedEnv):
    def test_online_needs_nothing(self):
        self.assertEqual(OB.pending_downloads(OB.get("online")), [])

    def test_offline_then_installed(self):
        pend = OB.pending_downloads(OB.get("offline"))
        self.assertEqual([(i.kind, i.id) for i in pend], [("piper", "zh_CN-huayan-medium")])
        self.install_piper()
        self.assertEqual(OB.pending_downloads(OB.get("offline")), [])

    def test_offline_asr_order_and_partial(self):
        sc = OB.get("offline_asr")
        self.assertEqual([(i.kind, i.id) for i in OB.pending_downloads(sc)],
                         [("piper", "zh_CN-huayan-medium"), ("whisper", "base")])
        self.install_piper()
        self.assertEqual([(i.kind, i.id) for i in OB.pending_downloads(sc)], [("whisper", "base")])


class TestShowAndComplete(IsolatedEnv):
    def test_should_show(self):
        self.assertTrue(OB.should_show({}))
        self.assertFalse(OB.should_show({"wizard_completed": True}))
        # 老用户（用过程序、配置里已有窗口位置）升级后不自动弹出
        self.assertFalse(OB.should_show({"window_geometry": "1200x800+0+0"}))

    def test_should_show_reads_config(self):
        self.assertTrue(OB.should_show())
        OB.mark_completed(None)
        self.assertFalse(OB.should_show())

    def test_mark_completed_with_engine(self):
        OB.mark_completed("piper")
        cfg = self.saved()
        self.assertIs(cfg["wizard_completed"], True)
        self.assertEqual(cfg["default_engine"], "piper")

    def test_mark_completed_without_engine_keeps_default(self):
        OB.mark_completed("edge")
        OB.mark_completed(None)
        self.assertEqual(self.saved()["default_engine"], "edge")


class TestAvailabilityAndRecommend(IsolatedEnv):
    def test_availability_first_line(self):
        with mock.patch.object(tts_engine, "check_engine_ready",
                               return_value=(False, "Piper 未安装。\n请运行 pip install piper-tts")):
            self.assertEqual(OB.availability(OB.get("offline")), (False, "Piper 未安装。"))

    def test_availability_survives_errors(self):
        with mock.patch.object(tts_engine, "check_engine_ready", side_effect=RuntimeError("坏了")):
            self.assertEqual(OB.availability(OB.get("system")), (False, "坏了"))

    def test_recommend_prefers_online_then_offline(self):
        avail = {"online": False, "offline": True, "system": True}
        with mock.patch.object(OB, "availability", lambda s: (avail.get(s.id, False), "")):
            self.assertEqual(OB.recommend(), "offline")
        with mock.patch.object(OB, "availability", lambda s: (True, "")):
            self.assertEqual(OB.recommend(), "online")
        with mock.patch.object(OB, "availability", lambda s: (False, "")):
            self.assertEqual(OB.recommend(), "online")


class TestNewConfigKeys(unittest.TestCase):
    def test_defaults(self):
        d = C.defaults()
        self.assertEqual(d["default_engine"], "")
        self.assertIs(d["wizard_completed"], False)

    def test_bool_coercion(self):
        n = lambda v: C.normalize({"wizard_completed": v})["wizard_completed"]
        self.assertIs(n(True), True)
        self.assertIs(n("true"), True)
        self.assertIs(n("Yes"), True)
        self.assertIs(n(1), True)
        self.assertIs(n("0"), False)
        self.assertIs(n(None), False)        # 坏值回退默认
        self.assertIs(n("maybe"), False)
        self.assertEqual(C.normalize({"concurrency": "5"})["concurrency"], 5)  # int 不受影响


if __name__ == "__main__":
    unittest.main()
