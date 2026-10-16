"""Tests for audiobook.models（v6.0/C2-U1 模型管理器后端）。

在临时目录里伪造便携存储布局（Piper / CosyVoice / Whisper 各种形态），验证盘点、占用统计、
删除的安全边界（外部目录 / 符号链接逃逸不可删）与删除副作用、下载分发。不联网、不加载模型。

Run with: python3 -m unittest tests/test_models.py
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import tts_engine
from audiobook import models as M
from audiobook.engines import asr


def _write(path, nbytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * nbytes)


class _StorageCase(unittest.TestCase):
    def setUp(self):
        self.storage = tempfile.mkdtemp(prefix="ab_store_")
        self.external = tempfile.mkdtemp(prefix="ab_ext_")
        self.extra_paths = []
        p1 = mock.patch.object(tts_engine, "get_storage_dir", lambda: self.storage)
        p2 = mock.patch.object(tts_engine, "get_model_search_paths", lambda: list(self.extra_paths))
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        self.addCleanup(shutil.rmtree, self.storage, True)
        self.addCleanup(shutil.rmtree, self.external, True)

    # 构造各类模型
    def piper(self, vid, root=None, with_json=True, size=1000):
        root = root or os.path.join(self.storage, "piper-models")
        onnx = os.path.join(root, "zh", f"{vid}.onnx")
        _write(onnx, size)
        if with_json:
            _write(onnx + ".json", 10)
        return onnx

    def cosy(self, name, size=500):
        d = os.path.join(self.storage, "cosyvoice-models", name)
        _write(os.path.join(d, "llm.pt"), size)
        return d

    def whisper(self, dirname, complete=True, size=300):
        d = os.path.join(self.storage, "whisper-models", dirname)
        if dirname.startswith("models--"):
            _write(os.path.join(d, "snapshots", "abc", "model.bin" if complete else "x.tmp"), size)
        else:
            _write(os.path.join(d, "model.bin"), size)
            if complete:
                _write(os.path.join(d, ".complete"), 0)
        return d


class TestListInstalled(_StorageCase):
    def test_empty_storage(self):
        self.assertEqual(M.list_installed(), [])

    def test_inventory_all_kinds(self):
        self.piper("zh_CN-huayan-medium", size=1000)
        self.piper("broken-no-json", with_json=False)          # 缺 .json → 不算
        self.cosy("CosyVoice-300M-SFT", size=500)
        self.whisper("base", complete=True, size=300)
        self.whisper("models--Systran--faster-whisper-tiny", complete=True, size=200)
        os.makedirs(os.path.join(self.storage, "whisper-models", ".locks"))  # 隐藏目录忽略

        entries = M.list_installed()
        got = [(e.kind, e.id) for e in entries]
        self.assertEqual(got, [
            ("piper", "zh_CN-huayan-medium"),
            ("cosyvoice", "CosyVoice-300M-SFT"),
            ("whisper", "base"),
            ("whisper", "models--Systran--faster-whisper-tiny"),
        ])
        by = {(e.kind, e.id): e for e in entries}
        self.assertEqual(by[("piper", "zh_CN-huayan-medium")].size_bytes, 1010)  # onnx + json
        self.assertEqual(by[("cosyvoice", "CosyVoice-300M-SFT")].size_bytes, 500)
        self.assertEqual(by[("whisper", "base")].name, "Whisper base")
        self.assertIn("HF 缓存", by[("whisper", "models--Systran--faster-whisper-tiny")].name)
        self.assertTrue(all(e.managed for e in entries))
        self.assertEqual(M.total_size(entries), 1010 + 500 + 300 + 200)

    def test_partial_cosyvoice_download_listed_and_deletable(self):
        d = self.cosy(".Fun-CosyVoice3-0.5B.partial", size=700)
        os.makedirs(os.path.join(self.storage, "cosyvoice-models", ".cache", "x"))  # 其它隐藏目录忽略
        (e,) = M.list_installed()
        self.assertEqual(e.id, ".Fun-CosyVoice3-0.5B.partial")
        self.assertIn("Fun-CosyVoice3-0.5B", e.name)
        self.assertIn("未下载完整", e.name)
        self.assertTrue(e.managed)
        installed = {(i.kind, i.id): i.installed for i in M.downloadable()}
        self.assertFalse(installed[("cosyvoice", "Fun-CosyVoice3-0.5B")], "半成品不算已安装")
        M.delete_model(e)
        self.assertFalse(os.path.exists(d))

    def test_incomplete_whisper_is_labelled(self):
        self.whisper("small", complete=False)
        (e,) = M.list_installed()
        self.assertIn("未下载完整", e.name)

    def test_external_piper_is_readonly(self):
        self.extra_paths.append(self.external)
        self.piper("en_US-lessac-medium", root=self.external)
        (e,) = M.list_installed()
        self.assertFalse(e.managed)
        with self.assertRaises(PermissionError):
            M.delete_model(e)
        self.assertTrue(os.path.exists(e.paths[0]), "外部模型不应被删除")

    def test_duplicate_piper_ids_listed_once(self):
        self.extra_paths.append(self.external)
        self.piper("zh_CN-huayan-medium")
        self.piper("zh_CN-huayan-medium", root=self.external)
        self.assertEqual(len(M.list_installed()), 1)

    @unittest.skipIf(sys.platform.startswith("win"), "符号链接需管理员权限")
    def test_symlink_escape_not_deletable(self):
        target = os.path.join(self.external, "real-model")
        _write(os.path.join(target, "llm.pt"), 10)
        link = os.path.join(self.storage, "cosyvoice-models", "linked")
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.symlink(target, link)
        (e,) = M.list_installed()
        self.assertFalse(e.managed)
        with self.assertRaises(PermissionError):
            M.delete_model(e)
        self.assertTrue(os.path.isfile(os.path.join(target, "llm.pt")))


class TestDelete(_StorageCase):
    def test_delete_piper_unloads_and_refreshes(self):
        onnx = self.piper("zh_CN-huayan-medium")
        (e,) = M.list_installed()
        with mock.patch.object(tts_engine, "_unload_piper_model") as unload, \
             mock.patch.object(tts_engine, "refresh_piper_voices") as refresh:
            M.delete_model(e)
        unload.assert_called_once_with("zh_CN-huayan-medium")
        refresh.assert_called_once()
        self.assertFalse(os.path.exists(onnx))
        self.assertFalse(os.path.exists(onnx + ".json"))
        self.assertEqual(M.list_installed(), [])

    def test_delete_cosyvoice(self):
        d = self.cosy("CosyVoice-300M-SFT")
        (e,) = M.list_installed()
        with mock.patch.object(tts_engine, "_unload_cosyvoice_model") as unload, \
             mock.patch.object(tts_engine, "refresh_cosyvoice_voices") as refresh:
            M.delete_model(e)
        unload.assert_called_once()
        refresh.assert_called_once()
        self.assertFalse(os.path.exists(d))

    def test_delete_whisper(self):
        d = self.whisper("base")
        (e,) = M.list_installed()
        with mock.patch.object(asr, "unload_whisper_model") as unload:
            M.delete_model(e)
        unload.assert_called_once()
        self.assertFalse(os.path.exists(d))


class TestDownloadable(_StorageCase):
    def test_marks_installed(self):
        self.piper("zh_CN-huayan-medium")
        self.whisper("base", complete=True)
        self.whisper("small", complete=False)                # 未完整 → 不算已装
        items = {(i.kind, i.id): i for i in M.downloadable()}
        self.assertTrue(items[("piper", "zh_CN-huayan-medium")].installed)
        self.assertFalse(items[("piper", "zh_CN-huayan-low")].installed)
        self.assertTrue(items[("whisper", "base")].installed)
        self.assertFalse(items[("whisper", "small")].installed)
        self.assertIn(("cosyvoice", "CosyVoice-300M-SFT"), items)

    def test_hf_cache_layout_counts_as_installed(self):
        self.whisper("models--Systran--faster-whisper-tiny", complete=True)
        items = {(i.kind, i.id): i for i in M.downloadable()}
        self.assertTrue(items[("whisper", "tiny")].installed)


class TestDownloadDispatch(_StorageCase):
    def test_piper_builtin(self):
        with mock.patch.object(tts_engine, "_ensure_piper_model") as m, \
             mock.patch.object(tts_engine, "refresh_piper_voices"):
            M.download("piper", "zh_CN-huayan-low")
        m.assert_called_once_with("zh_CN-huayan-low", should_stop=None)

    def test_piper_catalog_key(self):
        with mock.patch.object(tts_engine, "download_piper_voice_from_catalog") as m, \
             mock.patch.object(tts_engine, "refresh_piper_voices"):
            M.download("piper", "en_US-lessac-medium")
        m.assert_called_once_with("en_US-lessac-medium", should_stop=None)

    def test_cosyvoice(self):
        with mock.patch.object(tts_engine, "_ensure_cosyvoice_model") as m, \
             mock.patch.object(tts_engine, "refresh_cosyvoice_voices"):
            M.download("cosyvoice", "CosyVoice-300M-SFT")
        m.assert_called_once_with("CosyVoice-300M-SFT", should_stop=None)

    def test_whisper(self):
        with mock.patch.object(asr, "_ensure_whisper_model") as m:
            M.download("whisper", "base")
        m.assert_called_once_with("base", self.storage, should_stop=None)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            M.download("whisper", "huge-v9")
        with self.assertRaises(ValueError):
            M.download("nope", "x")


class TestModelsCli(_StorageCase):
    def _run(self, argv):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        from audiobook.cli import main
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv)
        return rc, out.getvalue()

    def test_list_empty_and_populated(self):
        rc, out = self._run(["models", "list"])
        self.assertEqual(rc, 0)
        self.assertIn("尚未安装", out)
        self.cosy("CosyVoice-300M-SFT", size=2048)
        rc, out = self._run(["models", "list"])
        self.assertIn("CosyVoice-300M-SFT", out)
        self.assertIn("合计 1 个", out)

    def test_available(self):
        rc, out = self._run(["models", "available"])
        self.assertEqual(rc, 0)
        self.assertIn("zh_CN-huayan-medium", out)
        self.assertIn("large-v3", out)

    def test_rm_yes_and_missing(self):
        d = self.whisper("base")
        with mock.patch.object(asr, "unload_whisper_model"):
            rc, out = self._run(["models", "rm", "whisper", "base", "-y"])
        self.assertEqual(rc, 0)
        self.assertIn("已删除", out)
        self.assertFalse(os.path.exists(d))
        rc, _ = self._run(["models", "rm", "whisper", "base", "-y"])
        self.assertEqual(rc, 1)

    def test_get_dispatches(self):
        with mock.patch.object(M, "download") as dl:
            rc, out = self._run(["models", "get", "whisper", "tiny"])
        self.assertEqual(rc, 0)
        dl.assert_called_once_with("whisper", "tiny")


class TestFormatSize(unittest.TestCase):
    def test_units(self):
        self.assertEqual(M.format_size(0), "0 B")
        self.assertEqual(M.format_size(2048), "2 KB")
        self.assertEqual(M.format_size(5 * 1024 * 1024), "5.0 MB")
        self.assertEqual(M.format_size(3 * 1024 ** 3), "3.0 GB")


if __name__ == "__main__":
    unittest.main()
