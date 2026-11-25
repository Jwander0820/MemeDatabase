import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.anime_memes.db import connect, init_db
from src.anime_memes.web import create_app


class LocalBrowseTest(unittest.TestCase):
    """本機簡潔瀏覽：私人後端以公開前端的 /api/v1 契約提供本機「可用」館藏。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "admin.sqlite3"
        with closing(connect(self.db)) as conn:
            init_db(conn)
            conn.execute("INSERT OR IGNORE INTO tags(name,source) VALUES('測試標籤','manual')")
            for n, status in enumerate(("ready", "needs_review")):
                conn.execute("INSERT INTO discord_messages(id,timestamp,content) VALUES(?, '2026-11-24','x')", (str(n),))
                conn.execute("INSERT INTO attachments(id,message_id,discord_url) VALUES(?,?,?)",
                             (str(n), str(n), "https://cdn.discordapp.com/x.png"))
                conn.execute("""INSERT INTO meme_entries(key,attachment_id,source_message_id,quote_text,speaker,
                    anime_title,pairing_method,confidence,status) VALUES(?,?,?,'復！活！','烈海王','刃牙','test',1,?)""",
                             (f"meme_{n}", str(n), str(n), status))
                conn.execute("INSERT INTO entry_tags(entry_key,tag_name,source) VALUES(?, '測試標籤', 'manual')", (f"meme_{n}",))
            conn.commit()
        self.client = create_app(db_path=self.db).test_client()

    def test_meta_declares_local_mode_and_counts_ready_only(self):
        data = self.client.get("/api/v1/meta").get_json()
        self.assertEqual(data["mode"], "local")
        self.assertEqual(data["admin_url"], "/")
        self.assertEqual(data["tags"], [{"name": "測試標籤", "count": 1}])

    def test_entries_only_ready_and_use_public_fields(self):
        data = self.client.get("/api/v1/entries?q=烈海王&page=1&version=local").get_json()
        self.assertEqual(data["total"], 1)
        item = data["items"][0]
        self.assertEqual(item["id"], "meme_0")
        self.assertEqual(item["resolved_title"], "烈海王 復！活！")
        self.assertIsNone(item["original_url"], "不可外連 Discord CDN 當大圖")
        self.assertNotIn("notes", item)
        self.assertEqual(self.client.get("/api/v1/entries?sort=random").status_code, 400)

    def test_detail_hides_non_ready(self):
        self.assertEqual(self.client.get("/api/v1/entries/meme_0").get_json()["item"]["id"], "meme_0")
        self.assertEqual(self.client.get("/api/v1/entries/meme_1").status_code, 404)

    def test_admin_published_filter_and_flag(self):
        from src.anime_memes.publishing import update_selection
        with closing(connect(self.db)) as conn:
            update_selection(conn, "meme_0", {"publish_selected": True})
            conn.commit()
        def keys(query):
            data = self.client.get("/api/entries?status=all&" + query).get_json()
            return {item["key"]: item["publish_selected"] for item in data["items"]}
        self.assertEqual(keys("published=1"), {"meme_0": 1})
        self.assertEqual(keys("published=0"), {"meme_1": 0})
        self.assertEqual(set(keys("")), {"meme_0", "meme_1"})

    def test_tag_catalog_lists_taxonomy_aliases_and_outside_tags(self):
        data = self.client.get("/api/tags/catalog").get_json()
        self.assertIn("反應／情緒", [c["label"] for c in data["categories"]])
        self.assertEqual(data["max_tags_per_entry"], 6)
        tags = {t["name"]: t for t in data["tags"]}
        self.assertIn("毒舌", tags["嗆人"]["aliases"])
        self.assertTrue(tags["嗆人"]["in_taxonomy"])
        # 測試資料用的分類外標籤，兩筆（不分狀態）都在使用。
        self.assertEqual((tags["測試標籤"]["in_taxonomy"], tags["測試標籤"]["count"]), (False, 2))

    def test_browse_favorites_share_admin_favorites(self):
        with closing(connect(self.db)) as conn:
            conn.execute("INSERT INTO entry_favorites(entry_key) VALUES('meme_0')")
            conn.commit()
        data = self.client.get("/api/v1/entries?favorite=1").get_json()
        self.assertEqual([(i["id"], i["favorite"]) for i in data["items"]], [("meme_0", True)])
        self.assertIs(self.client.get("/api/v1/entries/meme_0").get_json()["item"]["favorite"], True)

    def test_browse_page_and_assets(self):
        page = self.client.get("/browse")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"/assets/gallery.js", page.data)
        page.close()
        for name in ("gallery.js", "gallery.css", "catalog.css", "favicon.svg"):
            response = self.client.get("/assets/" + name)
            self.assertEqual(response.status_code, 200, name)
            response.close()
        self.assertEqual(self.client.get("/assets/../web.py").status_code, 404)


if __name__ == "__main__":
    unittest.main()
