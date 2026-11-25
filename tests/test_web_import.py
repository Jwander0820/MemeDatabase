import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from PIL import Image
from werkzeug.datastructures import FileStorage

from src.anime_memes import web_import


def png_bytes(fmt="PNG"):
    buf = BytesIO()
    Image.new("RGB", (8, 8), (200, 50, 50)).save(buf, fmt)
    return buf.getvalue()


def fake_fetch(url):
    return FileStorage(BytesIO(png_bytes()), filename="web-meme.png")


class FetchImageTests(unittest.TestCase):
    def _response(self, data):
        resp = MagicMock()
        resp.read.return_value = data
        resp.__enter__.return_value = resp
        return resp

    def test_有效圖片回傳_FileStorage(self):
        with patch.object(web_import, "urlopen", return_value=self._response(png_bytes())) as mocked:
            upload = web_import.fetch_image("https://example.com/a.png")
        self.assertEqual(upload.filename, "web-meme.png")
        self.assertGreater(len(upload.stream.read()), 0)
        self.assertEqual(mocked.call_args.args[0].full_url, "https://example.com/a.png")

    def test_非圖片內容被拒(self):
        with patch.object(web_import, "urlopen", return_value=self._response(b"<html>not image</html>")):
            with self.assertRaises(Exception):
                web_import.fetch_image("https://example.com/a.png")

    def test_超過_32MiB_被拒(self):
        with patch.object(web_import, "urlopen", return_value=self._response(b"x" * (32 * 1024 * 1024 + 1))):
            with self.assertRaisesRegex(ValueError, "32 MiB"):
                web_import.fetch_image("https://example.com/a.png")

    def test_不支援的格式被拒(self):
        with patch.object(web_import, "urlopen", return_value=self._response(png_bytes("BMP"))):
            with self.assertRaisesRegex(ValueError, "不支援"):
                web_import.fetch_image("https://example.com/a.bmp")


class ImportApprovedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.db = self.tmp / "t.db"
        self.jsonl = self.tmp / "approved.jsonl"

    def args(self, rows, apply):
        lines = [r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in rows]
        self.jsonl.write_text("\n".join(lines), encoding="utf-8")
        return SimpleNamespace(approved_jsonl=self.jsonl, apply=apply, db=self.db,
                               attachment_dir=self.tmp / "att", thumbnail_dir=self.tmp / "thumb")

    def row(self, url="https://example.com/a.png", **extra):
        data = {"image_url": url, "source_page": "https://example.com/page", "tags": ["貓", "梗圖"],
                "subject": "測試動畫", "caption": "台詞", "display_title": "標題", "speaker": "角色"}
        data.update(extra)
        return data

    def test_apply_成功匯入並寫入_DB_與檔案(self):
        with patch.object(web_import, "fetch_image", side_effect=fake_fetch):
            results = web_import.import_approved(self.args([self.row()], apply=True))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "imported", results)
        self.assertTrue(results[0]["key"].startswith("meme_manual_"))
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory = sqlite3.Row
            entry = conn.execute("SELECT * FROM meme_entries WHERE key=?", (results[0]["key"],)).fetchone()
            self.assertIsNotNone(entry)
            self.assertEqual(entry["web_image_url"], "https://example.com/a.png")
        self.assertEqual(len(list((self.tmp / "att").glob("*.png"))), 1)

    def test_重複網址標記為_duplicate(self):
        with patch.object(web_import, "fetch_image", side_effect=fake_fetch):
            first = web_import.import_approved(self.args([self.row()], apply=True))
            second = web_import.import_approved(self.args([self.row()], apply=True))
            same_file = web_import.import_approved(self.args([self.row(), self.row()], apply=True))
        self.assertEqual(first[0]["status"], "imported")
        self.assertEqual(second[0]["status"], "duplicate")
        self.assertEqual([r["status"] for r in same_file], ["duplicate", "duplicate"])

    def test_dry_run_不建立資料庫也不寫檔(self):
        with patch.object(web_import, "fetch_image", side_effect=fake_fetch):
            results = web_import.import_approved(self.args([self.row()], apply=False))
        self.assertEqual(results[0]["status"], "ready")
        self.assertFalse(self.db.exists())
        self.assertFalse((self.tmp / "att").exists())

    def test_空行略過且行號保留(self):
        with patch.object(web_import, "fetch_image", side_effect=fake_fetch):
            results = web_import.import_approved(self.args(["", self.row(), "   "], apply=False))
        self.assertEqual([(r["line"], r["status"]) for r in results], [(2, "ready")])

    def test_驗證失敗路徑(self):
        cases = {
            "壞 JSON": "{not json",
            "非物件": "[1, 2]",
            "缺少圖片直連": self.row(url=None),
            "圖片直連不是網址": self.row(url="not a url"),
            "tags 非陣列": self.row(tags="貓"),
            "tags 含非字串": self.row(tags=["貓", 1]),
            "caption 非字串": self.row(caption=5),
            "出處非網址": self.row(source_page="javascript:alert(1)"),
        }
        for name, row in cases.items():
            with self.subTest(name), patch.object(web_import, "fetch_image", side_effect=fake_fetch) as fetch:
                results = web_import.import_approved(self.args([row], apply=False))
                self.assertEqual(results[0]["status"], "error")
                self.assertTrue(results[0]["error"])
                fetch.assert_not_called()

    def test_下載失敗只影響該行(self):
        def flaky(url):
            if "bad" in url:
                raise HTTPError(url, 404, "Not Found", {}, None)
            return fake_fetch(url)

        rows = [self.row("https://example.com/bad.png"), self.row("https://example.com/good.png")]
        with patch.object(web_import, "fetch_image", side_effect=flaky):
            results = web_import.import_approved(self.args(rows, apply=False))
        self.assertEqual(results[0]["status"], "error")
        self.assertEqual(results[0]["error"], "HTTP 404")
        self.assertEqual(results[1]["status"], "ready")

    def test_網路錯誤被記錄而不中斷(self):
        with patch.object(web_import, "fetch_image", side_effect=OSError("連線失敗")):
            results = web_import.import_approved(self.args([self.row()], apply=False))
        self.assertEqual(results[0], {"line": 1, "status": "error", "error": "連線失敗"})


if __name__ == "__main__":
    unittest.main()
