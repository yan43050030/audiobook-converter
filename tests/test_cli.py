"""Tests for the v6.0/B2 command-line interface (audiobook.cli).

列表类命令（engines/voices）始终可测；convert 端到端需 espeak-ng + ffmpeg。

Run with: python3 -m unittest tests/test_cli.py
"""

import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from audiobook.cli import main, build_parser


def _run(argv):
    """运行 CLI，返回 (rc, stdout)。"""
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue()


class TestCliListing(unittest.TestCase):
    def test_engines_lists_builtins(self):
        rc, out = _run(["engines"])
        self.assertEqual(rc, 0)
        for eid in ("edge", "local", "piper"):
            self.assertIn(eid, out)

    def test_voices_local(self):
        rc, out = _run(["voices", "--engine", "local"])
        # 本地引擎在 CI（装了 espeak-ng）应有语音；无则返回 1 且不崩溃
        self.assertIn(rc, (0, 1))

    def test_version(self):
        # --version 触发 argparse 的 SystemExit(0)
        with self.assertRaises(SystemExit) as cm:
            build_parser().parse_args(["--version"])
        self.assertEqual(cm.exception.code, 0)

    def test_no_subcommand_errors(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args([])


class TestCliConvertValidation(unittest.TestCase):
    def test_missing_input_file(self):
        rc, _ = _run(["convert", "/no/such/file_xyz.txt", "--engine", "local",
                      "--out", tempfile.mkdtemp()])
        self.assertEqual(rc, 1)  # 顶层捕获 FileNotFoundError → 1

    def test_empty_input_returns_2(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "empty.txt")
        open(p, "w").close()
        rc, _ = _run(["convert", p, "--engine", "local", "--out", d])
        self.assertEqual(rc, 2)


def _has_espeak():
    return bool(shutil.which("espeak-ng") or shutil.which("espeak"))


import tts_engine  # noqa: E402


@unittest.skipUnless(_has_espeak() and tts_engine._ffmpeg_path(),
                     "需要 espeak-ng 与 ffmpeg")
class TestCliConvertEndToEnd(unittest.TestCase):
    def test_convert_local_produces_outputs(self):
        d = tempfile.mkdtemp()
        book = os.path.join(d, "book.txt")
        with open(book, "w", encoding="utf-8") as f:
            f.write("第一章 甲\n内容一。\n\n第二章 乙\n内容二。\n")
        out_dir = os.path.join(d, "out")
        rc, stdout = _run(["convert", book, "--engine", "local",
                           "--out", out_dir, "--prefix", "书", "--m4b", "--srt"])
        self.assertEqual(rc, 0)
        mp3s = [f for f in os.listdir(out_dir) if f.endswith(".mp3")]
        srts = [f for f in os.listdir(out_dir) if f.endswith(".srt")]
        m4bs = [f for f in os.listdir(out_dir) if f.endswith(".m4b")]
        self.assertEqual(len(mp3s), 2)
        self.assertEqual(len(srts), 2)
        self.assertEqual(len(m4bs), 1)
        # 直接输入文本，文件名不应含 untitled（v5.2.1 修复）
        self.assertFalse(any("untitled" in f for f in mp3s))


if __name__ == "__main__":
    unittest.main()
