import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from src.anime_memes.db import ADMIN_SCHEMA_VERSION, connect, init_db
from src.anime_memes.paths import ROOT, portable_media_path, resolve_local


class PortableMediaPathTest(unittest.TestCase):
    """媒體路徑一律存成 data/<分類>/<檔名>，Windows 主機與 Docker 容器都能解析。"""

    def test_預設目錄存成相對正斜線路徑(self):
        self.assertEqual(portable_media_path("ATTACHMENT", ROOT / "data" / "attachments" / "a.png"), "data/attachments/a.png")
        self.assertEqual(portable_media_path("THUMBNAIL", Path("data") / "thumbnails" / "a.webp"), "data/thumbnails/a.webp")
        self.assertEqual(portable_media_path("WEB", "data\\web\\a.webp"), "data/web/a.webp")

    def test_容器設定的掛載目錄也存成相對路徑(self):
        with tempfile.TemporaryDirectory() as mount:
            attachments = Path(mount) / "attachments"
            with patch.dict(os.environ, {"MEMES_ATTACHMENT_DIR": str(attachments)}):
                stored = portable_media_path("ATTACHMENT", attachments / "manual_x.jpg")
                self.assertEqual(stored, "data/attachments/manual_x.jpg")
                # 讀取時依設定對應回實際位置。
                self.assertEqual(resolve_local(stored), attachments / "manual_x.jpg")

    def test_其他位置保留實際路徑(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEMES_ATTACHMENT_DIR", None)
            path = Path(tmp) / "a.png"
            self.assertEqual(portable_media_path("ATTACHMENT", path), str(path))


class MediaPathMigrationTest(unittest.TestCase):
    """遷移把既有的非標準路徑轉成可攜格式，觸發器約束之後任何寫入者。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "a.sqlite3"

    def insert(self, conn, attachment_id, local, thumb=None, web=None):
        conn.execute("INSERT OR IGNORE INTO discord_messages(id,timestamp,content) VALUES('m','2026-11-25','')")
        conn.execute("INSERT INTO attachments(id,message_id,local_path,thumbnail_path,web_path) VALUES(?, 'm', ?, ?, ?)",
                     (attachment_id, local, thumb, web))

    def paths(self, conn, attachment_id):
        return tuple(conn.execute("SELECT local_path, thumbnail_path, web_path FROM attachments WHERE id=?",
                                  (attachment_id,)).fetchone())

    def test_既有資料遷移(self):
        with closing(connect(self.db)) as conn:
            init_db(conn)
            # 模擬遷移前的舊資料：先移除觸發器並退回版本 2，再寫入舊格式。
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_path_%'").fetchall():
                conn.execute(f'DROP TRIGGER "{name}"')
            self.insert(conn, "container", "/data/attachments/manual_a.jpg", "/data/thumbnails/manual_a.webp", "/data/web/a.webp")
            self.insert(conn, "windows", "data\\attachments\\b.png", "data\\thumbnails\\b.webp")
            self.insert(conn, "other", "D:\\media\\c.png", "/srv/other/c.webp")
            conn.execute("PRAGMA user_version = 2")
            conn.commit()
            init_db(conn)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], ADMIN_SCHEMA_VERSION)
            self.assertEqual(self.paths(conn, "container"),
                             ("data/attachments/manual_a.jpg", "data/thumbnails/manual_a.webp", "data/web/a.webp"))
            self.assertEqual(self.paths(conn, "windows"), ("data/attachments/b.png", "data/thumbnails/b.webp", None))
            # 不是已知格式的路徑不動。
            self.assertEqual(self.paths(conn, "other"), ("D:\\media\\c.png", "/srv/other/c.webp", None))

    def test_觸發器約束外部工具寫入(self):
        with closing(connect(self.db)) as conn:
            init_db(conn)
        # 外部工具用一般 sqlite3 連線直接寫入舊格式。
        with closing(sqlite3.connect(self.db)) as raw:
            raw.execute("INSERT INTO discord_messages(id,timestamp,content) VALUES('m','2026-11-25','')")
            raw.execute("INSERT INTO attachments(id,message_id,local_path) VALUES('x','m','/data/attachments/x.jpg')")
            raw.execute("UPDATE attachments SET thumbnail_path='data\\thumbnails\\x.webp' WHERE id='x'")
            raw.commit()
            self.assertEqual(raw.execute("SELECT local_path, thumbnail_path FROM attachments WHERE id='x'").fetchone(),
                             ("data/attachments/x.jpg", "data/thumbnails/x.webp"))


if __name__ == "__main__":
    unittest.main()
