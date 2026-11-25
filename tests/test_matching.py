import unittest

from src.anime_memes.matching import (
    clean_content, is_noise, pair_text_for_attachment, parse_meme_text, parse_timestamp,
)


def msg(id_, content, author="a", second=0, attachments=False):
    return {
        "id": id_, "content": content, "author": {"id": author},
        "timestamp": f"2026-11-25T00:00:{second:02d}Z",
        "attachments": [{"id": id_}] if attachments else [],
    }


class CleanAndNoiseTest(unittest.TestCase):
    def test_clean_移除網址與多餘空白(self):
        self.assertEqual(clean_content("  台詞 https://example.com/a?b=1 \r\n"), "台詞")
        self.assertEqual(clean_content(None), "")

    def test_noise_判斷(self):
        for value in [None, "", "  ", "https://x.com", ":smile: :joy:", "ok", "!!"]:
            with self.subTest(value=value):
                self.assertTrue(is_noise(value))
        for value in ["真的假的", "好", "hello world"]:
            with self.subTest(value=value):
                self.assertFalse(is_noise(value))


class ParseMemeTextTest(unittest.TestCase):
    def test_依行數拆解作品角色台詞(self):
        self.assertEqual(parse_meme_text("").quote_text, "")
        one = parse_meme_text("只有台詞")
        self.assertEqual((one.anime_title, one.speaker, one.quote_text), (None, None, "只有台詞"))
        two = parse_meme_text("作品\n台詞")
        self.assertEqual((two.anime_title, two.speaker, two.quote_text), ("作品", None, "台詞"))
        three = parse_meme_text("作品\n角色\n第一行\n\n第二行")
        self.assertEqual((three.anime_title, three.speaker, three.quote_text), ("作品", "角色", "第一行\n第二行"))

    def test_信心調整值隨資訊量增加(self):
        deltas = [parse_meme_text(t).confidence_delta for t in ["", "a b", "x\ny", "x\ny\nz"]]
        self.assertEqual(deltas, sorted(deltas))
        self.assertEqual(deltas[-1], 0.0)


class PairingTest(unittest.TestCase):
    def test_timestamp_支援_Z_結尾(self):
        self.assertEqual(parse_timestamp("2026-11-25T00:00:00Z").utcoffset().total_seconds(), 0)

    def test_同則訊息有台詞直接配對(self):
        messages = [msg("1", "有台詞", attachments=True)]
        pairing = pair_text_for_attachment(messages, 0, set())
        self.assertEqual((pairing.method, pairing.confidence), ("same_message", 0.95))
        self.assertIs(pairing.text_message, messages[0])

    def test_沒有台詞時優先配對前一則同作者文字(self):
        messages = [msg("1", "前面的台詞", second=0), msg("2", "", second=1, attachments=True),
                    msg("3", "後面的台詞", second=2)]
        pairing = pair_text_for_attachment(messages, 1, set())
        self.assertEqual(pairing.method, "nearby_previous")
        self.assertEqual(pairing.text_message["id"], "1")

    def test_只有後面有文字時配對下一則(self):
        messages = [msg("1", "", attachments=True), msg("2", "後面的台詞", second=1)]
        pairing = pair_text_for_attachment(messages, 0, set())
        self.assertEqual((pairing.method, pairing.text_message["id"]), ("nearby_next", "2"))

    def test_排除已使用_不同作者_與超出視窗(self):
        messages = [msg("1", "別人的台詞", author="b"), msg("2", "已用過"), msg("3", "", attachments=True)]
        pairing = pair_text_for_attachment(messages, 2, {"2"})
        self.assertEqual((pairing.method, pairing.text_message), ("unpaired", None))
        far = [msg("1", "太遠")] + [msg(str(i), "", second=i, attachments=True) for i in range(2, 7)]
        self.assertIsNone(pair_text_for_attachment(far, 5, set()).text_message)

    def test_距離越遠信心越低且有下限(self):
        near = [msg("1", "台詞"), msg("2", "", second=1, attachments=True)]
        far = [msg("1", "台詞"), msg("2", "", second=1, attachments=True), msg("3", "", second=2, attachments=True),
               msg("4", "", second=3, attachments=True)]
        c_near = pair_text_for_attachment(near, 1, set()).confidence
        c_far = pair_text_for_attachment(far, 3, set()).confidence
        self.assertGreater(c_near, c_far)
        self.assertGreaterEqual(c_far, 0.45)


if __name__ == "__main__":
    unittest.main()
