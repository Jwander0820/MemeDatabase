from contextlib import closing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from src.anime_memes.db import connect, init_db
from src.anime_memes.public_data import open_readonly
from src.anime_memes.publishing import check, check_database, export_release, snapshot, update_selection
from src.anime_memes.r2 import R2_ENV_VARS, R2Error


class FakeStore:
    def __init__(self, fail=False):
        self.uploads = {}
        self.fail = fail

    def key_for(self, sha256):
        return f"memes/{sha256}.webp"

    def upload_if_missing(self, key, data):
        if self.fail:
            raise R2Error("R2 PUT 失敗：HTTP 500")
        if key in self.uploads:
            return False
        self.uploads[key] = data
        return True

    def public_url(self, key):
        return "https://img.example.com/" + key


class PublicationMediaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "admin.sqlite3"
        self.releases = self.root / "releases"
        self.web = self.root / "web"
        env = patch.dict(os.environ, {"MEMES_WEB_DIR": str(self.web)})
        env.start()
        self.addCleanup(env.stop)
        # 清空全部 R2 變數（patch.dict 會在測試後還原），避免讀到開發者的真實設定。
        self.enterContext(patch.dict(os.environ, {name: "" for name in R2_ENV_VARS}))
        self.conn = connect(self.db)
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        self.original = self.root / "big.png"
        Image.new("RGB", (1920, 1080), "blue").save(self.original)
        self.thumb = self.root / "thumb.png"
        Image.new("RGB", (300, 200), "red").save(self.thumb)
        self.add("0", local=self.original)
        self.add("1", local=None)

    def add(self, n, local):
        self.conn.execute("INSERT INTO discord_messages(id,timestamp,content) VALUES(?, '2026-11-24','x')", (n,))
        self.conn.execute("INSERT INTO attachments(id,message_id,thumbnail_path,local_path,file_name) VALUES(?,?,?,?,?)",
                          (n, n, str(self.thumb), str(local) if local else None, "big.png" if local else None))
        self.conn.execute("""INSERT INTO meme_entries(key,attachment_id,source_message_id,quote_text,
            pairing_method,confidence,status) VALUES(?,?,?,'台詞','test',1,'ready')""", (f"meme_{n}", n, n))
        self.conn.commit()

    def public_rows(self, version):
        with closing(open_readonly(self.releases / "versions" / version / "public.sqlite3")) as c:
            return c.execute("SELECT original_url, thumbnail_url FROM entries").fetchall()

    def test_export_uploads_once_and_reuses(self):
        update_selection(self.conn, "meme_0", {"publish_selected": True})
        store = FakeStore()
        result = export_release(self.db, self.releases, store=store)
        self.assertTrue(result["exported"])
        self.assertEqual(result["media"]["uploaded"], 1)
        self.assertEqual(len(store.uploads), 1)
        key = next(iter(store.uploads))
        self.assertEqual(self.public_rows(result["version"])[0][0], "https://img.example.com/" + key)
        row = self.conn.execute("SELECT r2_key,r2_url FROM attachments WHERE id='0'").fetchone()
        self.assertEqual((row[0], row[1]), (key, "https://img.example.com/" + key))
        with Image.open(self.web / "0.webp") as image:
            self.assertEqual(image.size, (1280, 720))
        calls = []
        store.upload_if_missing = lambda *a: calls.append(a)
        second = export_release(self.db, self.releases, store=store)
        self.assertTrue(second["exported"])
        self.assertEqual((second["media"]["uploaded"], second["media"]["reused"]), (0, 1))
        self.assertEqual(calls, [])

    def test_manual_url_wins_without_upload(self):
        update_selection(self.conn, "meme_0", {"publish_selected": True, "public_original_url": "https://cdn.example.com/a.webp"})
        store = FakeStore()
        result = export_release(self.db, self.releases, store=store)
        self.assertTrue(result["exported"])
        self.assertEqual(store.uploads, {})
        self.assertEqual(self.public_rows(result["version"])[0][0], "https://cdn.example.com/a.webp")

    def test_failed_check_uploads_nothing(self):
        update_selection(self.conn, "meme_0", {"publish_selected": True})
        self.conn.execute("UPDATE meme_entries SET status='needs_review' WHERE key='meme_0'")
        self.conn.commit()
        store = FakeStore()
        result = export_release(self.db, self.releases, store=store)
        self.assertFalse(result["exported"])
        self.assertEqual(store.uploads, {})
        self.assertFalse((self.web / "0.webp").exists())

    def test_upload_failure_creates_no_version(self):
        update_selection(self.conn, "meme_0", {"publish_selected": True})
        result = export_release(self.db, self.releases, store=FakeStore(fail=True))
        self.assertFalse(result["exported"])
        self.assertEqual(list((self.releases / "versions").glob("*")) if (self.releases / "versions").exists() else [], [])
        self.assertTrue(any("R2" in e for e in result["report"]["items"][0]["errors"]))
        self.assertFalse(result["report"]["valid"])

    def test_thumbnail_is_480p_and_fallback_without_original(self):
        for key in ("meme_0", "meme_1"):
            update_selection(self.conn, key, {"publish_selected": True})
        result = export_release(self.db, self.releases, store=None)
        self.assertTrue(result["exported"])
        self.assertIsNone(result["media"])
        sizes = {}
        for original, thumb in self.public_rows(result["version"]):
            self.assertIsNone(original)
            with Image.open(self.releases / "media" / thumb.split("/")[-1]) as image:
                sizes[image.size] = True
        self.assertEqual(set(sizes), {(854, 480), (300, 200)})

    def test_check_offline_warnings(self):
        for key in ("meme_0", "meme_1"):
            update_selection(self.conn, key, {"publish_selected": True})
        with snapshot(self.db) as conn:
            off = check(conn)
            on = check(conn, r2_enabled=True)
        self.assertTrue(all("僅發布縮圖，無外部原圖" in i["warnings"] for i in off["items"]))
        warned = {i["key"]: i["warnings"] for i in on["items"]}
        self.assertEqual(warned["meme_0"], [])
        self.assertEqual(warned["meme_1"], ["僅發布縮圖，無外部原圖"])
        self.assertEqual(len(on["warnings"]), len(off["warnings"]) + 1)
        self.assertFalse((self.web).exists())

    def test_partial_r2_config_reported(self):
        update_selection(self.conn, "meme_0", {"publish_selected": True})
        with patch.dict(os.environ, {"MEMES_R2_BUCKET": "my-bucket"}):
            report = check_database(self.db)
            self.assertFalse(report["valid"])
            self.assertTrue(report["errors"])
            with self.assertRaises(ValueError):
                export_release(self.db, self.releases)


if __name__ == "__main__":
    unittest.main()
