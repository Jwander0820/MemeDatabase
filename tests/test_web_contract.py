"""本機瀏覽 /api/v1 與公開 API 的契約一致性：欄位集合、參數驗證與錯誤格式。"""
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from src.anime_memes import public_data
from src.anime_memes.db import connect, init_db
from src.anime_memes.public_web import create_public_app
from src.anime_memes.publishing import export_release, update_selection
from src.anime_memes.r2 import R2_ENV_VARS
from src.anime_memes.releases import activate
from src.anime_memes.web import create_app

ITEM_KEYS = set(public_data.FIELDS) | {"tags", "search_aliases", "resolved_title"}
BAD_QUERIES = ("unknown=1", "page_size=61", "page=0", "page=10001", "page_size=0")


class WebContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.enterContext(patch.dict(os.environ, {name: "" for name in R2_ENV_VARS}))
        db = root / "admin.sqlite3"
        thumb = root / "t.png"
        Image.new("RGB", (300, 200), "red").save(thumb)
        with closing(connect(db)) as conn:
            init_db(conn)
            conn.execute("INSERT INTO discord_messages(id,timestamp,content) VALUES('0','2026-11-24','x')")
            conn.execute("INSERT INTO attachments(id,message_id,thumbnail_path) VALUES('0','0',?)", (str(thumb),))
            conn.execute("""INSERT INTO meme_entries(key,attachment_id,source_message_id,quote_text,speaker,
                anime_title,pairing_method,confidence,status) VALUES('meme_0','0','0','復！活！','烈海王','刃牙','test',1,'ready')""")
            update_selection(conn, "meme_0", {"publish_selected": True})
            conn.commit()
        releases = root / "releases"
        result = export_release(db, releases, store=None)
        activate(releases, result["version"])
        self.local = create_app(db_path=db).test_client()
        self.public = create_public_app(releases).test_client()

    def get(self, client, url):
        response = client.get(url)
        self.addCleanup(response.close)
        return response

    def test_item_keys_match_public_fields(self):
        local = self.get(self.local, "/api/v1/entries").get_json()["items"]
        public = self.get(self.public, "/api/v1/entries").get_json()["items"]
        self.assertEqual(set(local[0]), ITEM_KEYS | {"favorite"})
        self.assertEqual(set(public[0]), ITEM_KEYS)

    def test_invalid_args_share_status_and_error_shape(self):
        for query in BAD_QUERIES:
            local = self.get(self.local, f"/api/v1/entries?{query}")
            public = self.get(self.public, f"/api/v1/entries?{query}")
            self.assertEqual((local.status_code, public.status_code), (400, 400), query)
            for body in (local.get_json(), public.get_json()):
                self.assertEqual(set(body), {"error"})
                self.assertEqual(body["error"]["code"], "invalid_request")
                self.assertIsInstance(body["error"]["message"], str)

    def test_local_accepts_version_and_favorite_but_not_ids(self):
        self.assertEqual(self.get(self.local, "/api/v1/entries?version=local&favorite=1").status_code, 200)
        self.assertEqual(self.get(self.local, "/api/v1/entries?ids=a").status_code, 400)

    def test_local_entry_detail_contract(self):
        self.assertEqual(self.get(self.local, "/api/v1/entries/meme_0?version=local").status_code, 200)
        bad = self.get(self.local, "/api/v1/entries/meme_0?page=1")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.get_json()["error"]["code"], "invalid_request")
        missing = self.get(self.local, "/api/v1/entries/nope")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.get_json()["error"]["code"], "not_found")

    def test_local_routes_return_200(self):
        for url in ("/", "/browse", "/assets/gallery.js", "/assets/favicon.svg",
                    "/api/entries", "/api/v1/entries", "/api/v1/meta"):
            self.assertEqual(self.get(self.local, url).status_code, 200, url)


if __name__ == "__main__":
    unittest.main()
