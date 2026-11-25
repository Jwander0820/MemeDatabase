import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.anime_memes.aliases import replace_aliases, validate_aliases
from src.anime_memes.db import connect, init_db
from src.anime_memes.search import NORMALIZATION_VERSION, normalize


class AliasesTest(unittest.TestCase):
    def test_驗證回傳原文與正規化結果(self):
        result = validate_aliases([" Hello World ", "別名"])
        self.assertEqual(result[0], ("Hello World", normalize("Hello World")))
        self.assertEqual(result[1], ("別名", normalize("別名")))

    def test_正規化後相同的替代詞只保留第一筆(self):
        result = validate_aliases(["ABC", "abc", "A B C"])
        self.assertEqual(result, [("ABC", normalize("abc"))])

    def test_空清單合法(self):
        self.assertEqual(validate_aliases([]), [])

    def test_無效輸入一律拒絕(self):
        cases = ["文字", None, ["ok", 1], [""], ["   "], ["！？"], ["x" * 201], [str(i) for i in range(51)]]
        for bad in cases:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_aliases(bad)

    def test_長度上限邊界合法(self):
        self.assertEqual(len(validate_aliases(["x" * 200])), 1)
        self.assertEqual(len(validate_aliases([f"a{i}" for i in range(50)])), 50)

    def test_replace_aliases_會整批取代舊資料(self):
        with tempfile.TemporaryDirectory() as tmp, closing(connect(Path(tmp) / "a.sqlite3")) as conn:
            init_db(conn)
            conn.execute("INSERT INTO discord_messages(id,timestamp,content) VALUES('1','2026-11-25','x')")
            conn.execute("INSERT INTO attachments(id,message_id) VALUES('1','1')")
            conn.execute("""INSERT INTO meme_entries(key,attachment_id,source_message_id,quote_text,pairing_method,
                confidence,status) VALUES('k','1','1','台詞','test',1,'ready')""")
            replace_aliases(conn, "k", validate_aliases(["舊的"]))
            replace_aliases(conn, "k", validate_aliases(["新的", "另一個"]))
            rows = conn.execute(
                "SELECT alias_text,normalization_version FROM entry_search_aliases WHERE entry_key='k'"
            ).fetchall()
            self.assertEqual(sorted(r[0] for r in rows), sorted(["新的", "另一個"]))
            self.assertTrue(all(r[1] == NORMALIZATION_VERSION for r in rows))
            replace_aliases(conn, "k", [])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM entry_search_aliases").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
