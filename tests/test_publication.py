from contextlib import closing
import json
from pathlib import Path
import sqlite3
import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from src.anime_memes.db import connect, init_db
from src.anime_memes.r2 import R2_ENV_VARS
from src.anime_memes.publishing import check_database, export_release, update_selection, original_url
from src.anime_memes.public_web import create_public_app
from src.anime_memes.public_data import open_readonly
from src.anime_memes.releases import activate, validate, current_version, prune
from src.anime_memes.queries import query_entries


class PublicationTest(unittest.TestCase):
    def setUp(self):
        # 隔離開發者 shell 的 R2 設定，避免測試真的上傳。
        self.enterContext(patch.dict(os.environ, {name: "" for name in R2_ENV_VARS}))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "admin.sqlite3"
        self.releases = self.root / "releases"
        self.conn = connect(self.db)
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        image = self.root / "thumb.png"
        metadata = PngInfo()
        metadata.add_text("Comment", "PRIVATE_IMAGE_METADATA")
        Image.new("RGB", (600, 300), "red").save(image, pnginfo=metadata)
        for n in range(2):
            self.conn.execute("INSERT INTO discord_messages(id,timestamp,content) VALUES(?, '2026-11-24','PRIVATE_MESSAGE')", (str(n),))
            self.conn.execute("INSERT INTO attachments(id,message_id,thumbnail_path) VALUES(?,?,?)", (str(n), str(n), str(image)))
            self.conn.execute("""INSERT INTO meme_entries(key,attachment_id,source_message_id,quote_text,
                speaker,anime_title,pairing_method,confidence,status,notes) VALUES(?,?,?,'復！活！','烈海王','刃牙','test',1,'ready','PRIVATE_NOTE')""", (f"meme_{n}", str(n), str(n)))
        self.conn.commit()
        self.client = create_public_app(self.releases).test_client()

    def publish(self):
        update_selection(self.conn, "meme_0", {"publish_selected": True})
        result = export_release(self.db, self.releases)
        self.assertTrue(result["exported"])
        activate(self.releases, result["version"])
        return result["version"]

    def test_selection_default_stable_id_and_bad_batch(self):
        self.assertEqual(self.conn.execute("SELECT sum(publish_selected) FROM meme_entries").fetchone()[0], 0)
        first = update_selection(self.conn, "meme_0", {"publish_selected": True})["public_id"]
        update_selection(self.conn, "meme_0", {"publish_selected": False})
        self.assertEqual(update_selection(self.conn, "meme_0", {"publish_selected": True})["public_id"], first)
        self.conn.execute("UPDATE meme_entries SET status='needs_review' WHERE key='meme_0'")
        self.conn.commit()
        self.assertFalse(export_release(self.db, self.releases)["exported"])
        self.assertFalse((self.releases / "current.json").exists())

    def test_public_contract_and_search_matches(self):
        version = self.publish()
        for q in ["復活", "復 活", "烈海王 復活", "复活"]:
            result = self.client.get("/api/v1/entries", query_string={"q": q}).get_json()
            local, _ = query_entries(self.conn, {"q": q}, 1, 24)
            self.assertEqual(result["items"][0]["quote_text"], local[0]["quote_text"])
            self.assertEqual(result["version"], version)
        raw = self.client.get("/api/v1/entries").data
        for forbidden in [b"PRIVATE_NOTE", b"PRIVATE_MESSAGE", b"meme_0", b"local_path", b"ocr_text"]:
            self.assertNotIn(forbidden, raw)
        item = self.client.get("/api/v1/entries").get_json()["items"][0]
        self.assertIsNone(item["original_url"])
        self.assertNotEqual(item["id"], "meme_0")
        image_path = self.releases / "media" / item["thumbnail_url"].split("/")[-1]
        with Image.open(image_path) as im:
            self.assertEqual(im.size, (600, 300))  # 480P 預設不放大
            self.assertFalse(im.getexif())
        self.assertNotIn(b"PRIVATE_IMAGE_METADATA", image_path.read_bytes())
        for path in ["/api/entries", "/mcp", "/public.sqlite3"]:
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post("/api/v1/entries").status_code, 405)
        for args in [{"status": "all"}, {"favorite": "1"}, {"q": "!!!"}, {"page_size": 61}]:
            self.assertEqual(self.client.get("/api/v1/entries", query_string=args).status_code, 400)

    def test_selected_search_order_matches_private_and_public(self):
        from src.anime_memes.management import update_entry, update_tags
        from src.anime_memes.private_mcp import PrivateSearch
        update_entry(self.conn, "meme_0", {"display_title": "烈海王復活", "search_aliases": ["我又回來了"]})
        update_tags(self.conn, "meme_0", {"tags": ["復活"]})
        for key in ["meme_0", "meme_1"]:
            update_selection(self.conn, key, {"publish_selected": True})
        result = export_release(self.db, self.releases)
        activate(self.releases, result["version"])
        mapping = dict(self.conn.execute("SELECT key,public_id FROM meme_entries"))
        private = PrivateSearch(self.db, "http://127.0.0.1:8765")
        for q in ["復活", "我又回來了", "烈海王 復活", "刃牙"]:
            local, total = query_entries(self.conn, {"q": q}, 1, 24)
            public = self.client.get("/api/v1/entries", query_string={"q": q}).get_json()
            self.assertEqual([mapping[r["key"]] for r in local], [r["id"] for r in public["items"]])
            self.assertEqual(total, public["total"])
            self.assertEqual([r["key"] for r in local], [r["id"] for r in private.search(q, 20)["items"]])

    def test_private_activation_validates_before_switching(self):
        from src.anime_memes.web import create_app
        private = create_app(db_path=self.db, release_root=self.releases).test_client()
        old = self.publish()
        version = export_release(self.db, self.releases)["version"]
        route = "/api/publication/activate"
        for payload in [{}, [], {"version": "../bad"}, {"version": "missing"},
                        {"version": version, "root": "other"}]:
            self.assertEqual(private.post(route, json=payload).status_code, 400)
        self.assertEqual(private.post(route, json={"version": version},
                                      headers={"Origin": "https://other.example"}).status_code, 403)
        self.assertEqual(current_version(self.releases), old)
        self.assertEqual(private.post(route, json={"version": version}).get_json(),
                         {"version": version, "count": 1})
        (self.releases / "versions" / old / "public.sqlite3").unlink()
        self.assertEqual(private.post(route, json={"version": old}).status_code, 422)
        self.assertEqual(current_version(self.releases), version)

    def test_hot_switch_rollback_and_corruption(self):
        old = self.publish()
        old_conn = open_readonly(self.releases / "versions" / old / "public.sqlite3")
        self.addCleanup(old_conn.close)
        old_conn.execute("BEGIN")
        self.conn.execute("UPDATE meme_entries SET quote_text='新版台詞' WHERE key='meme_0'")
        self.conn.commit()
        new = export_release(self.db, self.releases)["version"]
        activate(self.releases, new)
        self.assertEqual(old_conn.execute("SELECT quote_text FROM entries").fetchone()[0], "復！活！")
        self.assertEqual(self.client.get("/api/v1/entries").get_json()["items"][0]["quote_text"], "新版台詞")
        self.assertEqual(self.client.get("/api/v1/entries", query_string={"version": old}).get_json()["items"][0]["quote_text"], "復！活！")
        activate(self.releases, old)
        self.assertEqual(current_version(self.releases), old)
        (self.releases / "versions" / new / "public.sqlite3").write_bytes(b"bad")
        with self.assertRaises(ValueError):
            activate(self.releases, new)
        self.assertEqual(current_version(self.releases), old)
        self.assertEqual(self.client.get("/api/v1/entries?version=removed").status_code, 410)
        with self.assertRaises(sqlite3.OperationalError):
            old_conn.execute("DELETE FROM entries")

    def test_urls(self):
        for url in ["http://example.com/a", "https://user:pass@example.com/a", "https://127.0.0.1/a", "https://10.0.0.1/a", "https://localhost/a", "file:///a", "https://[::1]/a"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                original_url(url)
        self.assertEqual(original_url("https://cdn.example.com/a.webp"), "https://cdn.example.com/a.webp")

    def test_admin_publication_api_fixed_output_and_atomic_validation(self):
        from src.anime_memes.web import create_app
        client = create_app(db_path=self.db, release_root=self.releases).test_client()
        self.assertEqual(client.patch("/api/entries/meme_0/publication", json={"publish_selected": True}).status_code, 200)
        self.assertEqual(client.patch("/api/entries/meme_0/publication", json={"publish_selected": False, "public_original_url": "file:///bad"}).status_code, 400)
        self.assertTrue(client.get("/api/entries/meme_0/publication").get_json()["publish_selected"])
        self.assertTrue(client.post("/api/publication/check").get_json()["valid"])
        self.assertEqual(client.post("/api/publication/export", json={"root": "/elsewhere"}).status_code, 400)
        result = client.post("/api/publication/export", json={}).get_json()
        self.assertTrue(result["exported"])
        self.assertFalse((self.releases / "current.json").exists())
        update_selection(self.conn, "meme_1", {"publish_selected": True})
        self.conn.execute("UPDATE attachments SET thumbnail_path=NULL WHERE id='1'")
        self.conn.commit()
        result = client.post("/api/publication/export", json={})
        self.assertEqual(result.status_code, 422)
        self.assertFalse(result.get_json()["exported"])

    def test_publish_lock_and_private_data_absent_from_snapshot(self):
        version = self.publish()
        with closing(open_readonly(self.releases / "versions" / version / "public.sqlite3")) as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables, {"metadata", "entries", "entry_tags"})
        self.assertNotIn(b"PRIVATE_NOTE", (self.releases / "versions" / version / "public.sqlite3").read_bytes())
        (self.releases / ".export.lock").touch()
        with self.assertRaises(ValueError):
            export_release(self.db, self.releases)

    def test_private_path_in_searchable_text_blocks_publication(self):
        update_selection(self.conn, "meme_0", {"publish_selected": True})
        self.conn.execute("UPDATE meme_entries SET quote_text=? WHERE key='meme_0'", (r"C:\Users\owner\private.png",))
        self.conn.commit()
        self.assertFalse(check_database(self.db)["valid"])

    def test_parallel_version_switch(self):
        import concurrent.futures
        old = self.publish()
        new = export_release(self.db, self.releases)["version"]
        app = create_public_app(self.releases)
        def query(_):
            with app.test_client() as client:
                result = client.get("/api/v1/entries").get_json()
                self.assertIn(result["version"], {old, new})
                self.assertEqual(result["total"], 1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            pending = [pool.submit(query, i) for i in range(12)]
            activate(self.releases, new)
            for future in pending:
                future.result()

    def test_portable_media_paths(self):
        import os
        from unittest.mock import patch
        from src.anime_memes.paths import resolve_local
        with patch.dict(os.environ, {"MEMES_THUMBNAIL_DIR": "/data/thumbnails"}):
            self.assertEqual(str(resolve_local(r"D:\old\data\thumbnails\a.webp", "THUMBNAIL")).replace("\\", "/"), "/data/thumbnails/a.webp")
            self.assertIsNone(resolve_local(r"..\secret.webp", "THUMBNAIL"))
        from src.anime_memes.paths import ROOT
        with patch.dict(os.environ, {"MEMES_ATTACHMENT_DIR": "data/attachments"}):
            resolved = resolve_local(r"data\attachments\a.png", "ATTACHMENT")
            self.assertTrue(resolved.is_absolute())
            self.assertEqual(resolved, ROOT / "data" / "attachments" / "a.png")

    def test_prune_keeps_three_and_active(self):
        old = self.publish()
        for _ in range(4):
            export_release(self.db, self.releases)
        with self.assertRaises(ValueError):
            prune(self.releases, service_stopped=False)
        self.assertEqual(len(prune(self.releases, service_stopped=True)), 1)
        validate(self.releases, old)
