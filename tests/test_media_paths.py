import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.anime_memes import media, paths

ENV_KEYS = ("MEMES_ATTACHMENT_DIR", "MEMES_THUMBNAIL_DIR")


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    env.update(extra)
    return patch.dict(os.environ, env, clear=True)


class ResolveLocalTests(unittest.TestCase):
    def test_空值回傳_None(self):
        with clean_env():
            self.assertIsNone(paths.resolve_local(None))
            self.assertIsNone(paths.resolve_local(""))

    def test_含上層穿越被拒(self):
        with clean_env():
            for value in ("../secret.txt", "data/attachments/../../x", "data\\attachments\\..\\..\\x", "C:\\a\\..\\b.png"):
                with self.subTest(value=value):
                    self.assertIsNone(paths.resolve_local(value))
        with clean_env(MEMES_ATTACHMENT_DIR="/data/attachments"):
            self.assertIsNone(paths.resolve_local("../../etc/passwd", "ATTACHMENT"))

    def test_未設定根目錄時相對路徑以專案根目錄為基準(self):
        with clean_env():
            self.assertEqual(paths.resolve_local("data/attachments/a.png"), paths.ROOT / "data/attachments/a.png")

    def test_未設定根目錄時絕對路徑原樣(self):
        absolute = Path(tempfile.gettempdir()).resolve() / "a.png"
        with clean_env():
            self.assertEqual(paths.resolve_local(str(absolute)), absolute)

    def test_設定根目錄只取_basename(self):
        with tempfile.TemporaryDirectory() as tmp:
            with clean_env(MEMES_ATTACHMENT_DIR=tmp):
                self.assertEqual(paths.resolve_local("/other/attachments/a.png", "ATTACHMENT"), Path(tmp) / "a.png")
                self.assertEqual(paths.resolve_local("C:\\x\\attachments\\a.png"), Path(tmp) / "a.png")
                self.assertEqual(paths.resolve_local("data/attachments/b.png"), Path(tmp) / "b.png")

    def test_縮圖依路徑分類使用縮圖根目錄(self):
        with tempfile.TemporaryDirectory() as att, tempfile.TemporaryDirectory() as thumb:
            with clean_env(MEMES_ATTACHMENT_DIR=att, MEMES_THUMBNAIL_DIR=thumb):
                self.assertEqual(paths.resolve_local("C:\\x\\thumbnails\\t.webp"), Path(thumb) / "t.webp")
                self.assertEqual(paths.resolve_local("x/y/t.webp", "THUMBNAIL"), Path(thumb) / "t.webp")

    def test_相對設定值以專案根目錄為基準(self):
        with clean_env(MEMES_ATTACHMENT_DIR="data\\attachments"):
            self.assertEqual(paths.resolve_local("C:\\x\\attachments\\a.png"), paths.ROOT / "data" / "attachments" / "a.png")

    def test_容器絕對路徑設定值維持原樣(self):
        with clean_env(MEMES_ATTACHMENT_DIR="/data/attachments"):
            self.assertEqual(paths.resolve_local("C:\\x\\attachments\\a.png"), Path("/data/attachments") / "a.png")

    def test_無法分類且未指定_kind_時不套用設定根目錄(self):
        with tempfile.TemporaryDirectory() as tmp:
            with clean_env(MEMES_ATTACHMENT_DIR=tmp):
                result = paths.resolve_local("elsewhere/a.png")
                self.assertEqual(result, paths.ROOT / "elsewhere/a.png")


class ConfiguredRootTests(unittest.TestCase):
    def test_相對值以專案根目錄為基準(self):
        app = SimpleNamespace(config={"K": "data/attachments"})
        self.assertEqual(media.configured_media_root(app, "K"), media.ROOT / "data/attachments")

    def test_絕對值原樣(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = SimpleNamespace(config={"K": tmp})
            self.assertEqual(media.configured_media_root(app, "K"), Path(tmp))


class SafeDeleteTests(unittest.TestCase):
    def test_None_無動作(self):
        result = media.safe_delete_media_file(None, Path("."))
        self.assertIsNone(result.path)
        self.assertIsNone(result.deleted_path)
        self.assertIsNone(result.error)

    def test_根目錄內檔案可刪除(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "media"
            root.mkdir()
            target = root / "a.png"
            target.write_bytes(b"x")
            result = media.safe_delete_media_file(target, root)
            self.assertIsNone(result.error)
            self.assertEqual(result.deleted_path, str(target.resolve()))
            self.assertFalse(target.exists())

    def test_根目錄外檔案不刪除(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "media"
            root.mkdir()
            outside = Path(tmp) / "outside.png"
            outside.write_bytes(b"x")
            result = media.safe_delete_media_file(outside, root)
            self.assertEqual(result.error, "path outside media root")
            self.assertIsNone(result.deleted_path)
            self.assertTrue(outside.exists())

    def test_穿越路徑解析後在根目錄外不刪除(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "media"
            root.mkdir()
            outside = Path(tmp) / "outside.png"
            outside.write_bytes(b"x")
            result = media.safe_delete_media_file(root / ".." / "outside.png", root)
            self.assertEqual(result.error, "path outside media root")
            self.assertTrue(outside.exists())

    def test_同名前綴的兄弟目錄不算在根目錄內(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "media"
            sibling = Path(tmp) / "media-evil"
            root.mkdir()
            sibling.mkdir()
            target = sibling / "a.png"
            target.write_bytes(b"x")
            result = media.safe_delete_media_file(target, root)
            self.assertEqual(result.error, "path outside media root")
            self.assertTrue(target.exists())

    def test_不存在的檔案不報錯(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = media.safe_delete_media_file(root / "missing.png", root)
            self.assertIsNone(result.error)
            self.assertIsNone(result.deleted_path)

    def test_目錄不刪除(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sub = root / "sub"
            sub.mkdir()
            result = media.safe_delete_media_file(sub, root)
            self.assertEqual(result.error, "not a file")
            self.assertTrue(sub.exists())

    def test_根目錄本身不刪除(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = media.safe_delete_media_file(root, root)
            self.assertEqual(result.error, "not a file")
            self.assertTrue(root.exists())


class FileExistsAndMediaPathTests(unittest.TestCase):
    def test_file_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            full = Path(tmp) / "a.png"
            empty = Path(tmp) / "empty.png"
            full.write_bytes(b"x")
            empty.write_bytes(b"")
            self.assertTrue(media.file_exists(full))
            self.assertFalse(media.file_exists(empty))
            self.assertFalse(media.file_exists(Path(tmp) / "none.png"))
            self.assertFalse(media.file_exists(None))

    def test_resolve_media_path_委派給_resolve_local(self):
        with clean_env():
            self.assertIsNone(media.resolve_media_path(None))
            self.assertIsNone(media.resolve_media_path("../x.png"))
            self.assertEqual(media.resolve_media_path("data/a.png"), paths.ROOT / "data/a.png")

    def test_media_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            att = Path(tmp) / "att"
            att.mkdir()
            (att / "a.png").write_bytes(b"x")
            (att / "empty.png").write_bytes(b"")
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("CREATE TABLE attachments(id TEXT PRIMARY KEY, local_path TEXT, thumbnail_path TEXT)")
                conn.executemany("INSERT INTO attachments VALUES(?,?,?)", [
                    ("ok", str(att / "a.png"), str(att / "a.png")),
                    ("empty", str(att / "empty.png"), None),
                    ("gone", str(att / "gone.png"), ""),
                    ("trav", "../a.png", None),
                ])
                with clean_env():
                    self.assertEqual(media.media_path(conn, "ok", "local_path"), att / "a.png")
                    self.assertEqual(media.media_path(conn, "ok", "thumbnail_path"), att / "a.png")
                    self.assertIsNone(media.media_path(conn, "empty", "local_path"))
                    self.assertIsNone(media.media_path(conn, "gone", "local_path"))
                    self.assertIsNone(media.media_path(conn, "gone", "thumbnail_path"))
                    self.assertIsNone(media.media_path(conn, "trav", "local_path"))
                    self.assertIsNone(media.media_path(conn, "missing", "local_path"))
                    # 非白名單欄位名稱不可被帶入 SQL
                    self.assertIsNone(media.media_path(conn, "ok", "id; DROP TABLE attachments"))
                    self.assertIsNone(media.media_path(conn, "ok", "id"))
                with clean_env(MEMES_ATTACHMENT_DIR=str(att)):
                    # 設定根目錄後只取 basename
                    self.assertEqual(media.media_path(conn, "ok", "local_path"), att / "a.png")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
