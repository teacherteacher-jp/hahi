import unittest

from validate_vtt import parse_timestamp, parse_vtt, validate


RAW_VTT = """WEBVTT

00:01.140 --> 00:06.940
聞こえてない?今。

00:06.940 --> 00:07.940
部屋の外。
"""

TAGGED_VTT = """WEBVTT

00:00:04.300 --> 00:00:15.440
<v はるか>こんにちは。はるかです。

00:00:16.700 --> 00:00:20.617
<v ひとし>ひとしです。
"""


class TestParseTimestamp(unittest.TestCase):
    def test_full_format(self):
        self.assertEqual(parse_timestamp("00:01:02.345"), 62345)

    def test_short_format_without_hours(self):
        self.assertEqual(parse_timestamp("01:02.345"), 62345)

    def test_invalid_format_raises(self):
        with self.assertRaises(ValueError):
            parse_timestamp("1.234")


class TestParseVtt(unittest.TestCase):
    def test_raw_vtt_without_speaker_tags(self):
        cues = parse_vtt(RAW_VTT)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["start_ms"], 1140)
        self.assertEqual(cues[0]["end_ms"], 6940)
        self.assertIsNone(cues[0]["speaker"])
        self.assertEqual(cues[0]["text"], "聞こえてない?今。")

    def test_tagged_vtt_with_speaker(self):
        cues = parse_vtt(TAGGED_VTT)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["speaker"], "はるか")
        self.assertEqual(cues[0]["text"], "こんにちは。はるかです。")
        self.assertEqual(cues[0]["start_raw"], "00:00:04.300")

    def test_missing_header_raises(self):
        with self.assertRaises(ValueError):
            parse_vtt("00:01.000 --> 00:02.000\nテキスト\n")

    def test_cue_block_without_timestamp_raises(self):
        with self.assertRaises(ValueError):
            parse_vtt("WEBVTT\n\nタイムスタンプのないブロック\n")


ORIGINAL = """WEBVTT

00:01.140 --> 00:06.940
えーっと、聞こえてない?今。

00:06.940 --> 00:07.940
部屋の外。
"""

GOOD_EDIT = """WEBVTT

00:00:01.140 --> 00:00:06.940
<v はるか>聞こえてない？今。

00:00:06.940 --> 00:00:07.940
<v ひとし>部屋の外。
"""

SPEAKERS = ["はるか", "ひとし"]


class TestValidate(unittest.TestCase):
    def test_good_edit_passes(self):
        self.assertEqual(validate(ORIGINAL, GOOD_EDIT, SPEAKERS), [])

    def test_timestamp_change_detected(self):
        bad = GOOD_EDIT.replace("00:00:06.940 --> 00:00:07.940",
                                "00:00:06.940 --> 00:00:08.000")
        issues = validate(ORIGINAL, bad, SPEAKERS)
        self.assertTrue(any("タイムスタンプ" in i for i in issues))

    def test_short_timestamp_format_detected(self):
        bad = GOOD_EDIT.replace("00:00:01.140 --> 00:00:06.940",
                                "00:01.140 --> 00:06.940")
        issues = validate(ORIGINAL, bad, SPEAKERS)
        self.assertTrue(any("HH:MM:SS.mmm" in i for i in issues))

    def test_cue_count_mismatch_detected(self):
        bad = GOOD_EDIT.rsplit("\n\n", 1)[0] + "\n"
        issues = validate(ORIGINAL, bad, SPEAKERS)
        self.assertTrue(any("cue数" in i for i in issues))

    def test_unknown_speaker_detected(self):
        bad = GOOD_EDIT.replace("<v ひとし>", "<v ヒトシ>")
        issues = validate(ORIGINAL, bad, SPEAKERS)
        self.assertTrue(any("allowlist" in i for i in issues))

    def test_missing_speaker_tag_detected(self):
        bad = GOOD_EDIT.replace("<v ひとし>", "")
        issues = validate(ORIGINAL, bad, SPEAKERS)
        self.assertTrue(any("話者タグ" in i for i in issues))

    def test_excessive_change_detected(self):
        bad = GOOD_EDIT.replace("聞こえてない？今。", "全然違う話をここに書く。")
        issues = validate(ORIGINAL, bad, SPEAKERS)
        self.assertTrue(any("変更量" in i for i in issues))

    def test_empty_text_detected(self):
        bad = GOOD_EDIT.replace("部屋の外。", "")
        issues = validate(ORIGINAL, bad, SPEAKERS)
        self.assertTrue(any("空" in i for i in issues))

    def test_broken_edited_vtt_reported(self):
        issues = validate(ORIGINAL, "こわれたファイル", SPEAKERS)
        self.assertTrue(any("パース" in i for i in issues))


if __name__ == "__main__":
    unittest.main()
