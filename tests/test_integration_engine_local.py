"""本地引擎（espeak-ng）端到端集成测试基线。

锁定 v5.2.1 修复（Linux 语音检测跳过 MBROLA、文件名不含 untitled）与整条
TTS 管线（合成 → mp3 → 章节拆分 → srt → 元数据），为后续重构提供回归网。

依赖 espeak-ng 与 ffmpeg；任一缺失则整体跳过。
Run with: python3 -m unittest tests/test_integration_engine_local.py
"""

import os
import shutil
import tempfile
import unittest

import tts_engine


def _has_espeak():
    return bool(shutil.which("espeak-ng") or shutil.which("espeak"))


_SKIP_REASON = "需要 espeak-ng 与 ffmpeg"


@unittest.skipUnless(_has_espeak() and tts_engine._ffmpeg_path(), _SKIP_REASON)
class TestLocalEngineLinux(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tts_engine.refresh_local_voices()
        cls.voices = tts_engine.get_voice_list("local")

    def test_detects_usable_voice_no_mbrola(self):
        self.assertTrue(self.voices, "未检测到任何本地中文语音")
        for disp in self.voices:
            vid = tts_engine.get_voice_id(disp, "local")
            self.assertNotIn("mb", vid.lower(),
                             f"检测到不可用的 MBROLA 语音: {vid}")

    def test_synthesize_single_mp3(self):
        vid = tts_engine.get_voice_id(self.voices[0], "local")
        d = tempfile.mkdtemp()
        out = os.path.join(d, "seg.mp3")
        tts_engine._generate_one_safe("第一章 测试。今天天气很好。",
                                      vid, "+0%", out, engine="local")
        self.assertTrue(os.path.exists(out))
        self.assertGreater(os.path.getsize(out), 0)
        self.assertGreater(tts_engine.get_audio_duration(out), 0.5)

    def test_convert_batch_chapters_no_untitled(self):
        vid = tts_engine.get_voice_id(self.voices[0], "local")
        d = tempfile.mkdtemp()
        text = ("第一章 启程\n清晨出发。\n\n"
                "第二章 相遇\n途中遇见旅人。\n\n"
                "第三章 抉择\n夜里做出决定。\n")
        files = tts_engine.convert_batch(
            text=text, voice=vid, rate="+0%", output_dir=d,
            split_mode="chapter", file_prefix="测试书", engine="local",
            generate_subtitles=True, write_metadata=True, album_title="测试有声书",
        )
        self.assertEqual(len(files), 3)
        for f in files:
            self.assertTrue(os.path.exists(f))
            self.assertGreater(os.path.getsize(f), 0)
            # v5.2.1 修复：直接输入文本不应把 "untitled" 注入文件名
            self.assertNotIn("untitled", os.path.basename(f))
        # 每章应有对应的 srt 字幕
        srts = [f for f in os.listdir(d) if f.endswith(".srt")]
        self.assertEqual(len(srts), 3)


if __name__ == "__main__":
    unittest.main()
