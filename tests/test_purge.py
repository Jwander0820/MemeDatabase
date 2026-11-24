"""徹底撤回（purge_releases）：舊版本、只被舊版本使用的縮圖與不再需要的 R2 大圖。"""
from contextlib import closing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from src.anime_memes.db import connect, init_db
from src.anime_memes.public_web import create_public_app
from src.anime_memes.publishing import export_release, purge_releases, update_selection
from src.anime_memes.r2 import R2_ENV_VARS, R2Error
from src.anime_memes.releases import activate


class FakeStore:
    prefix = "memes/"

    def __init__(self):
        self.objects = {}
        self.deleted = []
        self.fail_delete = False

    def key_for(self, sha256):
        return f"{self.prefix}{sha256}.webp"

    def owns(self, key):
        return key.startswith(self.prefix)

    def upload_if_missing(self, key, data):
        if key in self.objects:
            return False
        self.objects[key] = data
        return True

    def public_url(self, key):
        return "https://img.example.com/" + key

    def delete(self, key):
        if self.fail_delete:
            raise R2Error("R2 DELETE 失敗：HTTP 500")
        self.deleted.append(key)
        self.objects.pop(key, None)


class PurgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "admin.sqlite3"
        self.releases = self.root / "releases"
        self.enterContext(patch.dict(os.environ, {"MEMES_WEB_DIR": str(self.root / "web")}))
        self.enterContext(patch.dict(os.environ, {name: "" for name in R2_ENV_VARS}))
        self.conn = connect(self.db)
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        self.store = FakeStore()
        # 兩張內容不同的圖，各自會產生不同的縮圖與 R2 物件。
        for n, color in (("0", "blue"), ("1", "green")):
            image = self.root / f"img{n}.png"
            Image.new("RGB", (1280, 720), color).save(image)
            self.conn.execute("INSERT INTO discord_messages(id,timestamp,content) VALUES(?, '2026-11-24','x')", (n,))
            self.conn.execute("INSERT INTO attachments(id,message_id,thumbnail_path,local_path,file_name) VALUES(?,?,?,?,?)",
                              (n, n, str(image), str(image), f"img{n}.png"))
            self.conn.execute("""INSERT INTO meme_entries(key,attachment_id,source_message_id,quote_text,
                pairing_method,confidence,status) VALUES(?,?,?,'台詞','test',1,'ready')""", (f"meme_{n}", n, n))
        self.conn.commit()

    def publish(self, *keys):
        for key in ("meme_0", "meme_1"):
            update_selection(self.conn, key, {"publish_selected": key in keys})
        result = export_release(self.db, self.releases, store=self.store)
        self.assertTrue(result["exported"], result)
        activate(self.releases, result["version"])
        return result["version"]

    def r2(self, attachment_id):
        return self.conn.execute("SELECT r2_key, r2_url FROM attachments WHERE id=?", (attachment_id,)).fetchone()

    def media_count(self):
        return len(list((self.releases / "media").glob("*.webp")))

    def test_withdrawn_entry_is_fully_removed(self):
        old = self.publish("meme_0", "meme_1")
        withdrawn_key, withdrawn_url = self.r2("1")
        new = self.publish("meme_0")
        self.assertEqual(self.media_count(), 2)

        plan = purge_releases(self.db, self.releases, store=self.store, dry_run=True)
        self.assertEqual((plan["active"], plan["kept_versions"], plan["removed_versions"]), (new, [new], [old]))
        self.assertEqual((plan["removed_media"], plan["r2_deleted"]), (1, [withdrawn_url]))
        # 預覽不做任何變更。
        self.assertTrue((self.releases / "versions" / old).exists())
        self.assertEqual(self.store.deleted, [])

        result = purge_releases(self.db, self.releases, store=self.store)
        self.assertEqual(result["errors"], [])
        self.assertFalse((self.releases / "versions" / old).exists())
        self.assertEqual(self.media_count(), 1)
        self.assertEqual(self.store.deleted, [withdrawn_key])
        self.assertEqual(tuple(self.r2("1")), (None, None))
        self.assertIsNotNone(self.r2("0")[0], "仍公開的圖不可刪")
        # 舊版本已不存在，指定舊版本查詢會回報過期。
        client = create_public_app(self.releases).test_client()
        self.assertEqual(client.get(f"/api/v1/entries?version={old}").status_code, 410)
        self.assertEqual(client.get("/api/v1/entries").get_json()["total"], 1)

    def test_keep_recent_retains_old_version_and_its_images(self):
        old = self.publish("meme_0", "meme_1")
        self.publish("meme_0")
        result = purge_releases(self.db, self.releases, store=self.store, keep_recent=1)
        self.assertIn(old, result["kept_versions"])
        self.assertEqual((result["removed_versions"], result["r2_deleted"], self.store.deleted), ([], [], []))

    def test_still_selected_or_shared_key_is_not_deleted(self):
        self.publish("meme_0", "meme_1")
        # 已勾選但尚未重新匯出的圖，大圖仍會用到。
        update_selection(self.conn, "meme_1", {"publish_selected": True})
        self.publish("meme_0")
        update_selection(self.conn, "meme_1", {"publish_selected": True})
        self.assertEqual(purge_releases(self.db, self.releases, store=self.store)["r2_deleted"], [])
        # 內容相同的另一張附件共用同一個物件鍵，只要其中一張仍需要就不刪。
        key, url = self.r2("0")
        self.conn.execute("UPDATE attachments SET r2_key=?, r2_url=? WHERE id='1'", (key, url))
        update_selection(self.conn, "meme_1", {"publish_selected": False})
        self.conn.commit()
        self.assertEqual(purge_releases(self.db, self.releases, store=self.store)["r2_deleted"], [])
        self.assertEqual(self.store.deleted, [])

    def test_without_r2_or_outside_prefix_objects_are_skipped(self):
        self.publish("meme_0", "meme_1")
        self.publish("meme_0")
        _, url = self.r2("1")
        result = purge_releases(self.db, self.releases, store=None)
        self.assertEqual((result["r2_deleted"], result["r2_skipped"]), ([], [url]))
        self.conn.execute("UPDATE attachments SET r2_key='other-project/x.webp' WHERE id='1'")
        self.conn.commit()
        result = purge_releases(self.db, self.releases, store=self.store)
        self.assertEqual((result["r2_deleted"], self.store.deleted), ([], []))

    def test_delete_failure_is_reported_and_record_kept(self):
        self.publish("meme_0", "meme_1")
        self.publish("meme_0")
        self.store.fail_delete = True
        result = purge_releases(self.db, self.releases, store=self.store)
        self.assertEqual(result["r2_deleted"], [])
        self.assertTrue(any("R2" in e for e in result["errors"]))
        self.assertIsNotNone(self.r2("1")[0], "刪除失敗時保留紀錄，之後可重試")

    def test_validation(self):
        self.publish("meme_0")
        for kwargs in ({"keep_recent": -1}, {"keep_recent": 21}, {"keep_recent": True}, {"dry_run": "yes"}):
            with self.subTest(kwargs), self.assertRaises(ValueError):
                purge_releases(self.db, self.releases, store=self.store, **kwargs)


if __name__ == "__main__":
    unittest.main()
