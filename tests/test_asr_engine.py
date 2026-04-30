"""Tests for asr_engine.py pure functions.

Run with: python3 -m unittest tests/test_asr_engine.py
Also compatible with: python3 -m pytest tests/ (when pytest is installed)
"""

import json
import unittest

from asr_engine import (
    _format_timestamp,
    _format_txt,
    _format_srt,
    _format_json,
)


class TestFormatTimestamp(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(_format_timestamp(0), "00:00:00,000")

    def test_one_second(self):
        self.assertEqual(_format_timestamp(1.0), "00:00:01,000")

    def test_one_minute(self):
        self.assertEqual(_format_timestamp(60), "00:01:00,000")

    def test_one_hour(self):
        self.assertEqual(_format_timestamp(3600), "01:00:00,000")

    def test_complex(self):
        self.assertEqual(_format_timestamp(3661.5), "01:01:01,500")

    def test_sub_millisecond(self):
        result = _format_timestamp(0.0001)
        self.assertTrue(result.startswith("00:00:00,"))

    def test_minute_boundary(self):
        self.assertEqual(_format_timestamp(59.999), "00:00:59,999")
        self.assertEqual(_format_timestamp(60.0), "00:01:00,000")


class TestFormatTxt(unittest.TestCase):
    def test_simple(self):
        segs = [{"text": "hello"}, {"text": "world"}]
        self.assertEqual(_format_txt(segs), "hello\nworld")

    def test_single_segment(self):
        segs = [{"text": "hello"}]
        self.assertEqual(_format_txt(segs), "hello")

    def test_empty(self):
        self.assertEqual(_format_txt([]), "")

    def test_missing_text_key_raises(self):
        with self.assertRaises(KeyError):
            _format_txt([{"no_text": "x"}])


class TestFormatSrt(unittest.TestCase):
    def test_single_segment(self):
        segs = [{"start": 0, "end": 1.5, "text": "hello"}]
        result = _format_srt(segs)
        self.assertIn("1", result)
        self.assertIn("hello", result)
        self.assertIn("00:00:00,000 --> 00:00:01,500", result)

    def test_multiple_segments(self):
        segs = [
            {"start": 0, "end": 2.0, "text": "first"},
            {"start": 2.0, "end": 5.5, "text": "second"},
        ]
        result = _format_srt(segs)
        self.assertIn("1\n", result)
        self.assertIn("2\n", result)
        self.assertIn("first", result)
        self.assertIn("second", result)
        lines = result.strip().split("\n")
        self.assertEqual(lines[0], "1")

    def test_empty(self):
        self.assertEqual(_format_srt([]), "")

    def test_srt_index_starts_at_one(self):
        segs = [
            {"start": 0, "end": 1, "text": "a"},
            {"start": 1, "end": 2, "text": "b"},
        ]
        result = _format_srt(segs)
        self.assertIn("1\n00:00:00,000 --> 00:00:01,000\na", result)
        self.assertIn("2\n00:00:01,000 --> 00:00:02,000\nb", result)


class TestFormatJson(unittest.TestCase):
    def test_structure(self):
        segs = [{"start": 0, "end": 1, "text": "hi"}]
        result = _format_json(segs, detected_lang="zh")
        parsed = json.loads(result)
        self.assertEqual(parsed["language"], "zh")
        self.assertEqual(parsed["text"], "hi")
        self.assertEqual(len(parsed["segments"]), 1)

    def test_empty(self):
        result = _format_json([])
        parsed = json.loads(result)
        self.assertEqual(parsed["language"], "")
        self.assertEqual(parsed["text"], "")
        self.assertEqual(parsed["segments"], [])

    def test_cjk_text(self):
        segs = [{"start": 0, "end": 2, "text": "你好世界"}]
        result = _format_json(segs, detected_lang="zh")
        parsed = json.loads(result)
        self.assertEqual(parsed["text"], "你好世界")

    def test_valid_json_output(self):
        segs = [{"start": 1.5, "end": 3.0, "text": "test"}]
        result = _format_json(segs, detected_lang="en")
        parsed = json.loads(result)
        self.assertIn("language", parsed)
        self.assertIn("text", parsed)
        self.assertIn("segments", parsed)
        self.assertEqual(parsed["segments"][0]["start"], 1.5)
        self.assertEqual(parsed["segments"][0]["end"], 3.0)
