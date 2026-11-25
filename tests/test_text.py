import unittest
from unittest.mock import patch

from src.anime_memes import text
from src.anime_memes.text import dedupe, to_traditional


class DedupeTest(unittest.TestCase):
    def test_保留第一次出現的順序(self):
        self.assertEqual(dedupe(["b", "a", "b", "c", "a"]), ["b", "a", "c"])

    def test_空輸入與產生器(self):
        self.assertEqual(dedupe([]), [])
        self.assertEqual(dedupe(x % 3 for x in range(7)), [0, 1, 2])


class ToTraditionalTest(unittest.TestCase):
    def test_None_與空字串回傳空字串(self):
        self.assertEqual(to_traditional(None), "")
        self.assertEqual(to_traditional(""), "")

    def test_簡體轉繁體(self):
        if text._opencc_converter() is None:
            self.skipTest("未安裝 OpenCC")
        self.assertEqual(to_traditional("动画"), "動畫")
        self.assertEqual(to_traditional("動畫 abc"), "動畫 abc")

    def test_沒有轉換器時原樣回傳(self):
        with patch.object(text, "_opencc_converter", return_value=None):
            self.assertEqual(to_traditional("动画"), "动画")

    def test_轉換器拋例外時原樣回傳(self):
        class Broken:
            def convert(self, _):
                raise RuntimeError("boom")

        with patch.object(text, "_opencc_converter", return_value=Broken()):
            self.assertEqual(to_traditional("动画"), "动画")


if __name__ == "__main__":
    unittest.main()
