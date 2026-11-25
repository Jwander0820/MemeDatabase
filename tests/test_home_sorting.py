"""第 4 版公開快照與首頁排序：created／random 排序、seed 驗證、首頁排除標籤。"""
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
from src.anime_memes.queries import query_entries
from src.anime_memes.r2 import R2_ENV_VARS
from src.anime_memes.releases import activate, release_path
from src.anime_memes.web import create_app

COUNT = 8
EXCLUDED = {3: "content_perverted", 5: "content_sexual_innuendo"}


class HomeSortingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.enterContext(patch.dict(os.environ, {name: "" for name in R2_ENV_VARS}))
        self.db = root / "admin.sqlite3"
        thumb = root / "t.png"
        Image.new("RGB", (30, 20), "red").save(thumb)
        with closing(connect(self.db)) as conn:
            init_db(conn)
            conn.execute("INSERT INTO tags(name) VALUES('特殊標籤')")
            for i in range(COUNT):
                conn.execute("INSERT INTO discord_messages(id,timestamp,content) VALUES(?,?,'x')", (str(i), "2026-11-24"))
                conn.execute("INSERT INTO attachments(id,message_id,thumbnail_path) VALUES(?,?,?)", (str(i), str(i), str(thumb)))
                conn.execute("""INSERT INTO meme_entries(key,attachment_id,source_message_id,quote_text,speaker,
                    anime_title,pairing_method,confidence,status,created_at,updated_at)
                    VALUES(?,?,?,?,'角色','刃牙','test',1,'ready',?,?)""",
                             (f"meme_{i}", str(i), str(i), f"台詞{i}", f"2026-11-0{i + 1}T00:00:00.000+00:00",
                              f"2026-11-1{7 - i}T00:00:00.000+00:00"))
                tag_id = EXCLUDED.get(i)
                if tag_id:
                    conn.execute("INSERT INTO entry_tags(entry_key,tag_name,source,tag_id) VALUES(?,?,'manual',?)",
                                 (f"meme_{i}", "特殊標籤", tag_id))
                update_selection(conn, f"meme_{i}", {"publish_selected": True})
            conn.commit()
        self.releases = root / "releases"
        result = export_release(self.db, self.releases, store=None)
        activate(self.releases, result["version"])
        self.version = result["version"]
        self.local = create_app(db_path=self.db).test_client()
        self.public = create_public_app(self.releases).test_client()
        with closing(connect(self.db)) as conn:
            self.ids = {r["key"]: r["public_id"] for r in conn.execute("SELECT key,public_id FROM meme_entries")}
        self.bad_ids = {self.ids[f"meme_{i}"] for i in EXCLUDED}
        self.local_bad = {f"meme_{i}" for i in EXCLUDED}

    def status(self, client, query):
        response = client.get(f"/api/v1/entries?{query}")
        self.addCleanup(response.close)
        return response.status_code

    def items(self, client, query):
        response = client.get(f"/api/v1/entries?{query}")
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200, query)
        return response.get_json()["items"]

    def pages(self, client, query, size=3):
        ids, page = [], 1
        while True:
            items = self.items(client, f"{query}&page={page}&page_size={size}")
            if not items:
                return ids
            ids += [item["id"] for item in items]
            page += 1

    def test_exported_snapshot_has_created_at_and_home_random(self):
        with closing(public_data.open_readonly(release_path(self.releases, self.version) / "public.sqlite3")) as conn:
            rows = {r["id"]: r for r in conn.execute("SELECT * FROM entries")}
        self.assertEqual(len(rows), COUNT)
        for key, public_id in self.ids.items():
            index = int(key.split("_")[1])
            self.assertEqual(rows[public_id]["home_random"], 0 if index in EXCLUDED else 1)
            self.assertEqual(rows[public_id]["created_at"], f"2026-11-0{index + 1}T00:00:00.000+00:00")

    def test_created_sort_public_and_local(self):
        expected = [f"meme_{i}" for i in reversed(range(COUNT))]
        self.assertEqual([i["id"] for i in self.items(self.local, "sort=created&page_size=60")], expected)
        public = self.items(self.public, "sort=created&page_size=60")
        self.assertEqual([i["id"] for i in public], [self.ids[k] for k in expected])
        self.assertIn("created_at", public[0])

    def test_random_stable_disjoint_and_seed_dependent(self):
        for client, bad in ((self.public, self.bad_ids), (self.local, self.local_bad)):
            first = self.pages(client, "sort=random&seed=abc")
            self.assertEqual(first, self.pages(client, "sort=random&seed=abc"))
            self.assertEqual(len(first), len(set(first)))
            self.assertEqual(len(first), COUNT - len(EXCLUDED))
            self.assertFalse(set(first) & bad)
            others = [self.pages(client, f"sort=random&seed=s{n}") for n in range(5)]
            self.assertTrue(any(other != first for other in others))

    def test_seed_validation(self):
        for client in (self.public, self.local):
            for query in ("sort=random", "sort=random&seed=", "sort=random&seed=a%20b", "sort=random&seed=" + "a" * 33):
                self.assertEqual(self.status(client, query), 400, query)
            self.assertEqual(self.status(client, "sort=random&seed=" + "a" * 32), 200)

    def test_filters_do_not_exclude(self):
        self.assertEqual(set(self.pages(self.public, "sort=random&seed=x&tag=特殊標籤")), self.bad_ids)
        self.assertEqual(set(self.pages(self.local, "sort=random&seed=x&tag=特殊標籤")), self.local_bad)
        self.assertEqual(len(self.pages(self.public, "sort=random&seed=x&q=刃牙")), COUNT)
        self.assertEqual(len(self.pages(self.local, "sort=random&seed=x&q=刃牙")), COUNT)
        ids = ",".join(self.ids.values())
        self.assertEqual(len(self.pages(self.public, f"sort=random&seed=x&ids={ids}")), COUNT)

    def test_excluded_tags_env_override(self):
        with patch.dict(os.environ, {"MEMES_HOME_EXCLUDED_TAGS": "content_perverted"}):
            ids = self.pages(self.local, "sort=random&seed=x")
        self.assertNotIn("meme_3", ids)
        self.assertIn("meme_5", ids)

    def test_admin_query_sort_created(self):
        with closing(connect(self.db)) as conn:
            rows, total = query_entries(conn, {"sort": "created"}, 1, 60)
        self.assertEqual(total, COUNT)
        self.assertEqual([r["key"] for r in rows], [f"meme_{i}" for i in reversed(range(COUNT))])
        response = create_app(db_path=self.db).test_client().get("/api/entries?sort=created&page_size=3")
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
