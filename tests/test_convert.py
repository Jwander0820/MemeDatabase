import json
import tempfile
import unittest
from pathlib import Path

from src.anime_memes.convert import convert_file, prune_discord_export


def sample():
    return {
        "guild": {"id": "g"},
        "dateRange": {"after": None},
        "exportedAt": "2026-11-25",
        "messageCount": 99,
        "messages": [
            {
                "id": "1", "type": "Default", "timestamp": "t", "content": "你好",
                "reactions": [{"emoji": "x"}], "mentions": [],
                "author": {"id": "a", "name": "n", "discriminator": "0", "isBot": False, "avatarUrl": "u", "roles": []},
                "attachments": [{"id": "9", "url": "http://x/y.png", "fileName": "y.png", "fileSizeBytes": 5, "extra": 1}],
            },
            "不是 dict",
        ],
    }


class ConvertTest(unittest.TestCase):
    def test_只保留固定欄位並修正_messageCount(self):
        result = prune_discord_export(sample())
        self.assertNotIn("guild", result)
        self.assertEqual(result["messageCount"], 2)
        message = result["messages"][0]
        self.assertEqual(set(message), {"id", "type", "timestamp", "content", "author", "attachments"})
        self.assertEqual(set(message["author"]), {"id", "name", "discriminator", "isBot"})
        self.assertEqual(set(message["attachments"][0]), {"id", "url", "fileName", "fileSizeBytes"})

    def test_非預期型別原樣保留不報錯(self):
        self.assertEqual(prune_discord_export(sample())["messages"][1], "不是 dict")
        data = {"messages": [{"id": "1", "author": "x", "attachments": ["a", 1]}]}
        message = prune_discord_export(data)["messages"][0]
        self.assertEqual(message["author"], "x")
        self.assertEqual(message["attachments"], ["a", 1])

    def test_缺少或錯誤的_messages_不新增欄位(self):
        self.assertEqual(prune_discord_export({"exportedAt": "x", "other": 1}), {"exportedAt": "x"})
        self.assertEqual(prune_discord_export({"messages": "oops"}), {"messages": "oops"})

    def test_不修改輸入資料(self):
        data = sample()
        prune_discord_export(data)
        self.assertIn("reactions", data["messages"][0])

    def test_convert_file_預設輸出檔名與中文不跳脫(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "chat.json"
            source.write_text(json.dumps(sample()), encoding="utf-8")
            output = convert_file(source)
            self.assertEqual(output.name, "chat.fixed.json")
            text = output.read_text(encoding="utf-8")
            self.assertIn("你好", text)
            self.assertEqual(json.loads(text)["messageCount"], 2)

    def test_convert_file_指定輸出路徑會建立資料夾(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "chat.json"
            source.write_text(json.dumps(sample()), encoding="utf-8")
            target = Path(tmp) / "sub" / "dir" / "out.json"
            self.assertEqual(convert_file(source, target), target)
            self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
