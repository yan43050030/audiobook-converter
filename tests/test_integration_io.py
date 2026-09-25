"""IO 层集成测试基线（读取 + 音频输出）。

锁定当前 IO 行为，为阶段 1/A4 把读取/输出迁入 `audiobook.io` 提供回归网。
纯逻辑（读取/字幕）始终运行；音频合并/m4b 依赖 ffmpeg，缺失时跳过。

Run with: python3 -m unittest tests/test_integration_io.py
"""

import os
import subprocess
import tempfile
import unittest

import tts_engine
from file_reader import load_file_content


def _ffmpeg():
    return tts_engine._ffmpeg_path()


def _make_silence_mp3(path, seconds=1.0):
    """用 ffmpeg 直接生成一段静音 mp3 作为纯 IO 测试的输入，避免依赖 TTS 引擎。"""
    subprocess.run(
        [_ffmpeg(), "-y", "-f", "lavfi", "-i",
         f"anullsrc=r=22050:cl=mono", "-t", str(seconds), "-q:a", "9", path],
        capture_output=True, check=True,
    )


class TestFileReaders(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_txt_roundtrip(self):
        p = os.path.join(self.d, "a.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("第一章 测试\n正文内容。")
        r = load_file_content(p)
        self.assertEqual(r["name"], "a.txt")
        self.assertIn("正文内容", r["content"])

    def test_markdown_strips_marks(self):
        p = os.path.join(self.d, "b.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# 标题\n\n**加粗** 和 `代码` 与 [链接](http://x)。")
        content = load_file_content(p)["content"]
        self.assertNotIn("#", content)
        self.assertNotIn("**", content)
        self.assertNotIn("`", content)
        self.assertIn("加粗", content)
        self.assertIn("链接", content)

    def test_html_extracts_body_text(self):
        p = os.path.join(self.d, "c.html")
        with open(p, "w", encoding="utf-8") as f:
            f.write("<html><head><style>x{}</style></head>"
                    "<body><p>正文一</p><script>bad()</script><p>正文二</p></body></html>")
        content = load_file_content(p)["content"]
        self.assertIn("正文一", content)
        self.assertIn("正文二", content)
        self.assertNotIn("bad()", content)


class TestSrtGeneration(unittest.TestCase):
    def test_srt_structure(self):
        srt = tts_engine.generate_srt_from_text("第一句。第二句！第三句？", 9.0)
        self.assertIn("00:00:00,000 -->", srt)
        # 三句 → 三个序号块
        self.assertIn("1\n", srt)
        self.assertIn("3\n", srt)

    def test_empty_text(self):
        self.assertEqual(tts_engine.generate_srt_from_text("", 10.0), "")

    def test_zero_duration(self):
        self.assertEqual(tts_engine.generate_srt_from_text("有内容", 0), "")


@unittest.skipUnless(_ffmpeg(), "未安装 ffmpeg，跳过音频合并/m4b 测试")
class TestAudioOutput(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.mp3s = []
        for i in range(3):
            p = os.path.join(self.d, f"{i:03d}_第{i+1}章.mp3")
            _make_silence_mp3(p, seconds=1.0)
            self.mp3s.append(p)

    def test_merge_mp3(self):
        out = os.path.join(self.d, "merged.mp3")
        tts_engine.merge_mp3_files(self.mp3s, out)
        self.assertTrue(os.path.exists(out))
        self.assertGreater(os.path.getsize(out), 0)
        self.assertGreater(tts_engine.get_audio_duration(out), 2.0)

    def test_export_m4b_has_chapters(self):
        out = os.path.join(self.d, "book.m4b")
        tts_engine.export_m4b(self.mp3s, out,
                              titles=["第一章", "第二章", "第三章"], album="测试")
        self.assertTrue(os.path.exists(out))
        # 用 ffprobe 校验章节标记数量
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_chapters", "-of", "csv=p=0", out],
            capture_output=True, text=True,
        )
        chapters = [ln for ln in probe.stdout.splitlines() if ln.strip()]
        self.assertEqual(len(chapters), 3)


if __name__ == "__main__":
    unittest.main()
