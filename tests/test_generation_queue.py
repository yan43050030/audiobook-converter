"""Tests for 生成队列事件（v6.0/C2-U3）：convert_batch 的 item_callback 与 only_indices。

覆盖三条分发路径（同步 local / Piper 并行 / Edge 异步）的逐章事件、单项与失败项重试、
暂停回到等待、回调异常不影响生成、临时标记不落盘。合成函数全部打桩，不依赖引擎与网络。

Run with: python3 -m unittest tests/test_generation_queue.py
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import tts_engine

BOOK = "第一章 甲\n内容一。\n\n第二章 乙\n内容二。\n\n第三章 丙\n内容三。\n"


class _Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, event, data):
        self.events.append((event, data))

    def of(self, event):
        return [d for e, d in self.events if e == event]

    def per_index(self):
        """{index: [events...]}（不含 init/final）"""
        out = {}
        for e, d in self.events:
            if e in ("init", "final"):
                continue
            out.setdefault(d["index"], []).append(e)
        return out

    def final_statuses(self):
        return [it["status"] for it in self.of("final")[-1]["items"]]


def _fake_synth(fail_texts=(), stop_texts=()):
    calls = []

    def fake(text, voice, rate, out_path, **kw):
        calls.append(text)
        if any(t in text for t in stop_texts):
            raise tts_engine.StopRequested("暂停")
        if any(t in text for t in fail_texts):
            raise RuntimeError(f"合成失败: {text[:6]}")
        with open(out_path, "wb") as f:
            f.write(b"\x00" * 16)

    fake.calls = calls
    return fake


class _QueueCase(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="ab_queue_")
        self.addCleanup(shutil.rmtree, self.out, True)

    def run_batch(self, rec, engine="local", **kw):
        return tts_engine.convert_batch(
            text=BOOK, voice="v", rate="+0%", output_dir=self.out,
            split_mode="chapter", engine=engine, item_callback=rec, **kw)


class TestSyncPath(_QueueCase):
    def test_events_per_item(self):
        rec = _Recorder()
        fake = _fake_synth(fail_texts=("内容二",))
        with mock.patch.object(tts_engine, "_generate_one_safe", fake):
            files = self.run_batch(rec)
        self.assertEqual(len(files), 2)

        init = rec.of("init")[0]["items"]
        self.assertEqual([it["status"] for it in init], ["pending"] * 3)
        self.assertEqual([it["title"] for it in init], ["第一章 甲", "第二章 乙", "第三章 丙"])
        self.assertEqual(rec.per_index(), {0: ["running", "done"], 1: ["running", "error"],
                                           2: ["running", "done"]})
        self.assertIn("合成失败", rec.of("error")[0]["error"])
        self.assertEqual(rec.final_statuses(), ["done", "error", "done"])

    def test_retry_single_failed_item(self):
        with mock.patch.object(tts_engine, "_generate_one_safe", _fake_synth(fail_texts=("内容二",))):
            self.run_batch(_Recorder())
        rec = _Recorder()
        fake = _fake_synth()
        with mock.patch.object(tts_engine, "_generate_one_safe", fake):
            self.run_batch(rec, resume=True, only_indices={1})
        self.assertEqual(len(fake.calls), 1, "只应重新合成被重试的那一项")
        self.assertEqual(rec.of("init")[0]["items"][1]["status"], "error")
        self.assertEqual(rec.per_index(), {1: ["running", "done"]})
        final = rec.of("final")[-1]["items"]
        self.assertEqual([it["status"] for it in final], ["done"] * 3)
        self.assertEqual(final[1]["error"], "", "成功后应清掉上次的错误信息")
        # 全部完成 → 进度文件清理
        self.assertIsNone(tts_engine.load_progress(self.out))

    def test_only_indices_holds_other_failed_items(self):
        with mock.patch.object(tts_engine, "_generate_one_safe",
                               _fake_synth(fail_texts=("内容一", "内容三"))):
            self.run_batch(_Recorder())
        rec = _Recorder()
        fake = _fake_synth()
        with mock.patch.object(tts_engine, "_generate_one_safe", fake):
            self.run_batch(rec, resume=True, only_indices=[0])
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("内容一", fake.calls[0])
        self.assertEqual(rec.final_statuses(), ["done", "done", "error"])
        # 进度文件保留，且不含临时标记
        with open(os.path.join(self.out, tts_engine.PROGRESS_FILENAME), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual([it["status"] for it in saved], ["done", "done", "error"])
        self.assertFalse(any("_hold" in it for it in saved))

    def test_pause_returns_item_to_pending(self):
        rec = _Recorder()
        with mock.patch.object(tts_engine, "_generate_one_safe", _fake_synth(stop_texts=("内容二",))):
            self.run_batch(rec)
        self.assertEqual(rec.per_index(), {0: ["running", "done"], 1: ["running", "pending"]})
        self.assertEqual(rec.final_statuses(), ["done", "pending", "pending"])

    def test_callback_errors_do_not_break_generation(self):
        def boom(event, data):
            raise ValueError("界面回调出错")
        with mock.patch.object(tts_engine, "_generate_one_safe", _fake_synth()):
            files = tts_engine.convert_batch(text=BOOK, voice="v", rate="+0%", output_dir=self.out,
                                             engine="local", item_callback=boom)
        self.assertEqual(len(files), 3)

    def test_no_callback_still_works(self):
        with mock.patch.object(tts_engine, "_generate_one_safe", _fake_synth()):
            files = tts_engine.convert_batch(text=BOOK, voice="v", rate="+0%",
                                             output_dir=self.out, engine="local")
        self.assertEqual(len(files), 3)


class TestPiperParallelPath(_QueueCase):
    def test_events_per_item(self):
        rec = _Recorder()
        with mock.patch.object(tts_engine, "_get_piper_mode", return_value=tts_engine.PIPER_MODE_PYTHON), \
             mock.patch.object(tts_engine, "get_local_concurrency", return_value=2), \
             mock.patch.object(tts_engine, "_unload_piper_model"), \
             mock.patch.object(tts_engine, "_generate_one_safe", _fake_synth(fail_texts=("内容三",))):
            self.run_batch(rec, engine="piper")
        per = rec.per_index()
        self.assertEqual(per[0], ["running", "done"])
        self.assertEqual(per[1], ["running", "done"])
        self.assertEqual(per[2], ["running", "error"])
        self.assertEqual(rec.final_statuses(), ["done", "done", "error"])


class TestEdgePath(_QueueCase):
    def test_events_per_item(self):
        async def fake_edge(text, voice, rate, out_path):
            if "内容二" in text:
                raise RuntimeError("网络错误")
            with open(out_path, "wb") as f:
                f.write(b"\x00" * 16)

        rec = _Recorder()
        with mock.patch.object(tts_engine, "EDGE_TTS_AVAILABLE", True), \
             mock.patch.object(tts_engine, "MAX_RETRIES", 0), \
             mock.patch.object(tts_engine, "_edge_generate", fake_edge):
            self.run_batch(rec, engine="edge")
        self.assertEqual(rec.per_index(), {0: ["running", "done"], 1: ["running", "error"],
                                           2: ["running", "done"]})
        self.assertIn("网络错误", rec.of("error")[0]["error"])
        self.assertEqual(rec.final_statuses(), ["done", "error", "done"])


class TestEdgeBatchCompletes(_QueueCase):
    """回归：Edge 批量结束时停止监视任务的 CancelledError（BaseException）曾逃逸出 convert_batch。"""

    def test_returns_normally_and_clears_progress(self):
        async def fake_edge(text, voice, rate, out_path):
            with open(out_path, "wb") as f:
                f.write(b"\x00" * 16)

        with mock.patch.object(tts_engine, "EDGE_TTS_AVAILABLE", True), \
             mock.patch.object(tts_engine, "_edge_generate", fake_edge):
            files = tts_engine.convert_batch(text=BOOK, voice="v", rate="+0%",
                                             output_dir=self.out, engine="edge")
        self.assertEqual(len(files), 3)
        self.assertIsNone(tts_engine.load_progress(self.out), "全部完成后应清理进度文件")


class TestSaveProgressStripsTransientKeys(unittest.TestCase):
    def test_strip(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        items = [{"title": "a", "status": "pending", "_hold": True}]
        tts_engine.save_progress(d, items)
        self.assertEqual(tts_engine.load_progress(d)[0].get("_hold"), None)
        self.assertTrue(items[0]["_hold"], "内存中的条目不应被修改")


if __name__ == "__main__":
    unittest.main()
