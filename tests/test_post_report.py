import unittest

from post_report import split_message


class TestSplitMessage(unittest.TestCase):
    def test_short_text_is_single_chunk(self):
        self.assertEqual(split_message("こんにちは"), ["こんにちは"])

    def test_splits_on_line_boundary(self):
        text = "a" * 1000 + "\n" + "b" * 1000
        chunks = split_message(text, limit=1500)
        self.assertEqual(chunks, ["a" * 1000, "b" * 1000])

    def test_force_splits_overlong_line(self):
        text = "x" * 4000
        chunks = split_message(text, limit=1900)
        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(len(c) <= 1900 for c in chunks))

    def test_empty_text_returns_no_chunks(self):
        self.assertEqual(split_message(""), [])


if __name__ == "__main__":
    unittest.main()
