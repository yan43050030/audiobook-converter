"""Tests for tts_engine.py pure functions.

Run with: python3 -m unittest tests/test_tts_engine.py
Also compatible with: python3 -m pytest tests/ (when pytest is installed)
"""

import json
import os
import tempfile
import unittest

from tts_engine import (
    sanitize_filename,
    estimate_duration,
    split_by_duration,
    split_text,
    detect_chapters,
    load_progress,
    clear_progress,
    save_progress,
    PROGRESS_FILENAME,
)


class TestSanitizeFilename(unittest.TestCase):
    def test_special_chars_replaced(self):
        result = sanitize_filename("a:b/c?d*e\"f<g>h|i")
        for ch in "\\/:*?\"<>|":
            self.assertNotIn(ch, result)

    def test_long_name_truncated(self):
        result = sanitize_filename("a" * 100)
        self.assertLessEqual(len(result), 80)

    def test_empty_returns_untitled(self):
        self.assertEqual(sanitize_filename(""), "untitled")

    def test_only_special_chars_returns_untitled(self):
        result = sanitize_filename("...   ")
        self.assertEqual(result, "untitled")

    def test_normal_name_preserved(self):
        self.assertEqual(sanitize_filename("hello"), "hello")

    def test_trailing_dots_stripped(self):
        self.assertEqual(sanitize_filename("hello."), "hello")


class TestEstimateDuration(unittest.TestCase):
    def test_default_rate(self):
        d = estimate_duration("x" * 250, "+0%")
        self.assertEqual(d, 100.0)

    def test_fast_rate(self):
        d = estimate_duration("x" * 250, "+100%")
        self.assertEqual(d, 50.0)

    def test_slow_rate(self):
        d = estimate_duration("x" * 250, "-50%")
        self.assertEqual(d, 200.0)

    def test_empty_text(self):
        d = estimate_duration("", "+0%")
        self.assertEqual(d, 0.0)

    def test_negative_rate_floor(self):
        d = estimate_duration("x" * 100, "-100%")
        self.assertEqual(d, 200.0)


class TestSplitByDuration(unittest.TestCase):
    def test_default_rate(self):
        # 2.5cps * 60s = 150 chars → floored to 500. Use 600+ chars in 2 paragraphs.
        para = "x" * 300
        text = f"{para}\n\n{para}"
        result = split_by_duration(text, max_seconds=60, rate="+0%")
        self.assertEqual(len(result), 2)

    def test_fast_rate_increases_max_chars(self):
        # +100% = 5 cps * 30s = 150 → floored to 500. Split 600 chars across 2 paras.
        para = "x" * 300
        text = f"{para}\n\n{para}"
        result = split_by_duration(text, max_seconds=30, rate="+100%")
        self.assertEqual(len(result), 2)

    def test_minimum_500_chars(self):
        # Very slow rate still floors at 500. 600 chars in one paragraph won't split.
        # But with 2 paragraphs of 300 each, both under 500, each gets its own segment.
        text = "\n\n".join(["x" * 300, "x" * 300])
        result = split_by_duration(text, max_seconds=1, rate="-80%")
        self.assertEqual(len(result), 2)
        for seg in result:
            self.assertLessEqual(len(seg), 310)

    def test_empty_text(self):
        result = split_by_duration("", max_seconds=60, rate="+0%")
        self.assertEqual(result, [""])


class TestSplitText(unittest.TestCase):
    def test_short_text_single_segment(self):
        result = split_text("hello", max_length=3000)
        self.assertEqual(result, ["hello"])

    def test_paragraph_boundary_split(self):
        text = "para1\n\npara2"
        result = split_text(text, max_length=5)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], "para1")
        self.assertEqual(result[1], "para2")

    def test_long_paragraph_sentence_split(self):
        text = "A sentence。B sentence。C sentence。"
        result = split_text(text, max_length=20)
        combined = "".join(result)
        self.assertIn("A sentence", combined)
        self.assertIn("B sentence", combined)

    def test_empty_text(self):
        self.assertEqual(split_text(""), [""])

    def test_text_exactly_at_max(self):
        result = split_text("a" * 10, max_length=10)
        self.assertEqual(len(result), 1)

    def test_empty_paragraphs_consolidated(self):
        text = "para1\n\n\n\npara2"
        result = split_text(text, max_length=100)
        self.assertGreaterEqual(len(result), 1)


class TestDetectChapters(unittest.TestCase):
    def test_no_chapters_returns_single(self):
        text = "这是一段没有章节标记的文本。"
        result = detect_chapters(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "全文")

    def test_chinese_chapter_numbering(self):
        text = "第一章 开始\n内容A\n第二章 发展\n内容B"
        result = detect_chapters(text)
        self.assertEqual(len(result), 2)
        self.assertIn("第一章", result[0]["title"])
        self.assertIn("第二章", result[1]["title"])

    def test_chinese_cardinal_chapter(self):
        text = "第十回 重逢\n内容A\n第一百章 大战\n内容B"
        result = detect_chapters(text)
        self.assertEqual(len(result), 2)

    def test_prologue_epilogue(self):
        text = "序章 开场\n这是序章的正文内容。\n尾声 结束\n这是尾声的正文内容。"
        result = detect_chapters(text)
        self.assertEqual(len(result), 2)

    def test_extra_chapter(self):
        text = "番外篇：往事\n这是番外的正文内容。"
        result = detect_chapters(text)
        self.assertEqual(len(result), 1)
        self.assertIn("番外", result[0]["title"])

    def test_empty_text(self):
        result = detect_chapters("")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "全文")

    def test_large_text_path(self):
        chapter_marker = "第一章 测试\n" + ("x" * 90) + "\n第二章 测试\n" + ("x" * 90)
        text = (chapter_marker + "\n") * 540
        self.assertGreaterEqual(len(text), 100000)
        result = detect_chapters(text)
        self.assertEqual(len(result), 1080)

    def test_source_map(self):
        text = "第一章 A\n内容\n第二章 B\n内容"
        sm = [(0, "file1.txt"), (10, "file1.txt")]
        result = detect_chapters(text, source_map=sm)
        self.assertEqual(result[0].get("source"), "file1.txt")

    def test_mixed_numbering(self):
        text = "第1章 数字\n内容\n第3节 小节\n内容"
        result = detect_chapters(text)
        self.assertEqual(len(result), 2)


class TestLoadProgress(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _dir(self):
        return self.temp_dir.name

    def test_no_file_returns_none(self):
        result = load_progress(self._dir())
        self.assertIsNone(result)

    def test_old_format_migrated(self):
        old_items = [
            {"title": "ch1", "text": "text1", "filename": "001.mp3"},
            {"title": "ch2", "text": "text2", "filename": "002.mp3"},
        ]
        path = os.path.join(self._dir(), PROGRESS_FILENAME)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(old_items, f, ensure_ascii=False)

        result = load_progress(self._dir())
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["chapter_idx"], 0)
        self.assertEqual(result[1]["chapter_idx"], 1)
        self.assertEqual(result[0]["status"], "pending")

    def test_new_format_preserved(self):
        new_items = [
            {"title": "ch1", "text": "t1", "filename": "001.mp3",
             "status": "done", "chapter_idx": 0},
        ]
        path = os.path.join(self._dir(), PROGRESS_FILENAME)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(new_items, f)

        result = load_progress(self._dir())
        self.assertEqual(result[0]["chapter_idx"], 0)
        self.assertEqual(result[0]["status"], "done")

    def test_mixed_old_new(self):
        items = [
            {"title": "ch1", "text": "t1", "filename": "001.mp3"},
            {"title": "ch2", "text": "t2", "filename": "002.mp3",
             "status": "done", "chapter_idx": 1},
        ]
        path = os.path.join(self._dir(), PROGRESS_FILENAME)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f)

        result = load_progress(self._dir())
        self.assertEqual(result[0]["chapter_idx"], 0)
        self.assertEqual(result[0]["status"], "pending")
        self.assertEqual(result[1]["chapter_idx"], 1)
        self.assertEqual(result[1]["status"], "done")

    def test_empty_list(self):
        path = os.path.join(self._dir(), PROGRESS_FILENAME)
        with open(path, "w", encoding="utf-8") as f:
            json.dump([], f)

        result = load_progress(self._dir())
        self.assertEqual(result, [])

    def test_non_list_json_passed_through(self):
        path = os.path.join(self._dir(), PROGRESS_FILENAME)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": 1}, f)

        result = load_progress(self._dir())
        self.assertEqual(result, {"version": 1})


class TestSaveClearProgress(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _dir(self):
        return self.temp_dir.name

    def test_save_and_load_roundtrip(self):
        items = [
            {"title": "ch1", "text": "t1", "filename": "001.mp3",
             "status": "pending", "chapter_idx": 0},
        ]
        save_progress(self._dir(), items)
        loaded = load_progress(self._dir())
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded[0]["title"], "ch1")

    def test_clear_removes_file(self):
        items = [{"title": "ch1", "status": "done", "chapter_idx": 0}]
        save_progress(self._dir(), items)
        path = os.path.join(self._dir(), PROGRESS_FILENAME)
        self.assertTrue(os.path.exists(path))
        clear_progress(self._dir())
        self.assertFalse(os.path.exists(path))


class TestMergeMp3Id3(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _dir(self):
        return self.temp_dir.name

    def test_merge_two_simple_files(self):
        from tts_engine import _merge_mp3_files
        d = self._dir()
        p1 = os.path.join(d, "a.mp3")
        p2 = os.path.join(d, "b.mp3")
        out = os.path.join(d, "merged.mp3")

        with open(p1, "wb") as f:
            f.write(b"aaaa")
        with open(p2, "wb") as f:
            f.write(b"bbbb")

        _merge_mp3_files([p1, p2], out)

        with open(out, "rb") as f:
            data = f.read()
        self.assertEqual(data, b"aaaabbbb")

    def test_skip_id3_on_second_file(self):
        from tts_engine import _merge_mp3_files
        d = self._dir()
        p1 = os.path.join(d, "a.mp3")
        p2 = os.path.join(d, "b.mp3")
        out = os.path.join(d, "merged.mp3")

        with open(p1, "wb") as f:
            f.write(b"aaaa")
        id3 = b"ID3\x03\x00\x00" + b"\x00\x00\x00\x0a"
        with open(p2, "wb") as f:
            f.write(id3 + b"x" * 10 + b"bbbb")

        _merge_mp3_files([p1, p2], out)

        with open(out, "rb") as f:
            data = f.read()
        self.assertEqual(data, b"aaaabbbb")

    def test_first_file_id3_kept(self):
        from tts_engine import _merge_mp3_files
        d = self._dir()
        p1 = os.path.join(d, "a.mp3")
        p2 = os.path.join(d, "b.mp3")
        out = os.path.join(d, "merged.mp3")

        id3 = b"ID3\x03\x00\x00" + b"\x00\x00\x00\x05"
        with open(p1, "wb") as f:
            f.write(id3 + b"x" * 5 + b"aaaa")
        with open(p2, "wb") as f:
            f.write(b"bbbb")

        _merge_mp3_files([p1, p2], out)

        with open(out, "rb") as f:
            data = f.read()
        self.assertIn(b"ID3", data)
        self.assertIn(b"bbbb", data)

    def test_id3_size_exceeds_data(self):
        from tts_engine import _merge_mp3_files
        d = self._dir()
        p1 = os.path.join(d, "a.mp3")
        p2 = os.path.join(d, "b.mp3")
        out = os.path.join(d, "merged.mp3")

        with open(p1, "wb") as f:
            f.write(b"aaaa")
        huge_id3 = b"ID3\x03\x00\x00" + b"\x00\x00\x00\x64"
        with open(p2, "wb") as f:
            f.write(huge_id3 + b"short")

        _merge_mp3_files([p1, p2], out)
        with open(out, "rb") as f:
            data = f.read()
        self.assertGreaterEqual(len(data), len(b"aaaashort"))


class TestMirrorUrl(unittest.TestCase):
    def test_huggingface_replaced(self):
        from tts_engine import _get_mirror_url, HF_MIRROR_DOMAIN, HF_ORIGIN_DOMAIN
        url = f"https://{HF_ORIGIN_DOMAIN}/some/path"
        result = _get_mirror_url(url)
        self.assertIn(HF_MIRROR_DOMAIN, result)
        self.assertNotIn(HF_ORIGIN_DOMAIN, result)

    def test_non_huggingface_unchanged(self):
        from tts_engine import _get_mirror_url
        url = "https://example.com/file"
        self.assertEqual(_get_mirror_url(url), url)


class TestFindSource(unittest.TestCase):
    def test_find_in_range(self):
        from tts_engine import _find_source
        sm = [(0, "a.txt"), (50, "b.txt"), (100, "c.txt")]
        self.assertEqual(_find_source(sm, 0), "a.txt")
        self.assertEqual(_find_source(sm, 30), "a.txt")
        self.assertEqual(_find_source(sm, 50), "b.txt")
        self.assertEqual(_find_source(sm, 75), "b.txt")
        self.assertEqual(_find_source(sm, 100), "c.txt")

    def test_empty_source_map(self):
        from tts_engine import _find_source
        self.assertEqual(_find_source([], 0), "")


class TestDetectDialogueSegments(unittest.TestCase):
    def test_plain_narration(self):
        from tts_engine import detect_dialogue_segments
        result = detect_dialogue_segments("这是一段普通叙述，没有任何对话。")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "narration")

    def test_chinese_quotes(self):
        from tts_engine import detect_dialogue_segments
        result = detect_dialogue_segments('他说："你好，好久不见。"')
        self.assertTrue(any(s["type"] == "dialogue" for s in result))

    def test_japanese_quotes(self):
        from tts_engine import detect_dialogue_segments
        result = detect_dialogue_segments('她回答：「我知道了。」')
        self.assertTrue(any(s["type"] == "dialogue" for s in result))

    def test_speaker_extraction(self):
        from tts_engine import detect_dialogue_segments
        result = detect_dialogue_segments('老王说："明天见。"')
        dialogue_segs = [s for s in result if s["type"] == "dialogue"]
        self.assertTrue(len(dialogue_segs) > 0)

    def test_mixed_narration_and_dialogue(self):
        from tts_engine import detect_dialogue_segments
        text = '太阳升起来了。小明问：“去哪儿？”小红指着远方。'
        result = detect_dialogue_segments(text)
        types = [s["type"] for s in result]
        self.assertIn("narration", types)
        self.assertIn("dialogue", types)

    def test_empty_text(self):
        from tts_engine import detect_dialogue_segments
        result = detect_dialogue_segments("")
        self.assertEqual(result, [])

    def test_no_quotes_all_narration(self):
        from tts_engine import detect_dialogue_segments
        text = "春天来了，花儿开了，鸟儿在唱歌。"
        result = detect_dialogue_segments(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "narration")
