"""公開 API 的收藏查詢（ids）與版本化快取標頭。"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from src.anime_memes.db import connect, init_db
from src.anime_memes.public_web import create_public_app
from src.anime_memes.publishing import export_release, update_selection
from src.anime_memes.r2 import R2_ENV_VARS
from src.anime_memes.releases import activate


class PublicFavoritesCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.enterContext(patch.dict(os.environ, {name: "" for name in R2_ENV_VARS}))
        db = root / "admin.sqlite3"
        conn = connect(db)
        self.addCleanup(conn.close)
        init_db(conn)
        thumb = root / "t.png"
        Image.new("RGB", (300, 200), "red").save(thumb)
        for n in range(3):
            conn.execute("INSERT INTO discord_messages(id,timestamp,content) VALUES(?, '2026-11-24','x')", (str(n),))
            conn.execute("INSERT INTO attachments(id,message_id,thumbnail_path) VALUES(?,?,?)", (str(n), str(n), str(thumb)))
            conn.execute("""INSERT INTO meme_entries(key,attachment_id,source_message_id,quote_text,
                pairing_method,confidence,status) VALUES(?,?,?,'台詞','test',1,'ready')""", (f"meme_{n}", str(n), str(n)))
            update_selection(conn, f"meme_{n}", {"publish_selected": True})
        releases = root / "releases"
        result = export_release(db, releases, store=None)
        activate(releases, result["version"])
        self.version = result["version"]
        self.ids = [r[0] for r in conn.execute("SELECT public_id FROM meme_entries ORDER BY key")]
        self.client = create_public_app(releases).test_client()

    def get(self, url):
        response = self.client.get(url)
        self.addCleanup(response.close)
        return response

    def test_ids_returns_only_requested_entries(self):
        data = self.get("/api/v1/entries?ids=" + ",".join(self.ids[:2])).get_json()
        self.assertEqual({item["id"] for item in data["items"]}, set(self.ids[:2]))
        # 已下架（不存在）的 ID 會被忽略，總數只算查得到的。
        data = self.get(f"/api/v1/entries?ids={self.ids[0]},{'0' * 32}").get_json()
        self.assertEqual(data["total"], 1)
        # 空的收藏清單不回傳任何結果，而不是全部。
        self.assertEqual(self.get("/api/v1/entries?ids=").get_json()["total"], 0)

    def test_ids_validation(self):
        for bad in ("not-an-id", ",".join(["a" * 32] * 201)):
            with self.subTest(bad=bad[:20]):
                self.assertEqual(self.get("/api/v1/entries?ids=" + bad).status_code, 400)

    def test_versioned_responses_are_cacheable_and_unversioned_are_not(self):
        latest = self.get("/api/v1/entries")
        self.assertEqual(latest.headers["Cache-Control"], "no-store")
        for path in (f"/api/v1/entries?version={self.version}", f"/api/v1/meta?version={self.version}",
                     f"/api/v1/entries/{self.ids[0]}?version={self.version}"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path).headers["Cache-Control"], "public, max-age=31536000, immutable")
        # 錯誤回應（例如版本已不存在）不可被長期快取。
        self.assertEqual(self.get("/api/v1/entries?version=missing_version").headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()


class PublicBuildInfoTest(PublicFavoritesCacheTest):
    """部署版號：只出現在不快取的 meta 回應，避免長期快取留住舊版號。"""

    def test_meta_reports_build_only_when_uncached(self):
        sha = "0123456789abcdef0123456789abcdef01234567"
        with patch.dict(os.environ, {"MEMEDB_BUILD_REVISION": sha, "MEMEDB_BUILD_TIME": "2026-11-27T09:30:00Z"}):
            response = self.get("/api/v1/meta")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.get_json()["build"], {"revision": sha, "short": sha[:7], "built_at": "2026-11-27T09:30:00Z"})
            cached = self.get(f"/api/v1/meta?version={self.version}")
            self.assertIn("immutable", cached.headers["Cache-Control"])
            self.assertNotIn("build", cached.get_json())

    def test_meta_build_is_null_without_valid_revision(self):
        for value in ("", "not-a-sha"):
            with self.subTest(value=value), patch.dict(os.environ, {"MEMEDB_BUILD_REVISION": value}):
                self.assertIsNone(self.get("/api/v1/meta").get_json()["build"])
