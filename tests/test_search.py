import unittest

import test_assessment_fixes as fixtures
from src.anime_memes.db import init_db
from src.anime_memes.search import scorer


class SearchTest(unittest.TestCase):
    setUp = fixtures.AssessmentFixesTest.setUp

    def update(self, key="meme_1", **values):
        return self.client.patch(f"/api/entries/{key}", json=values)

    def find(self, q, **filters):
        return self.client.get("/api/entries", query_string={"q": q, **filters})

    def seed(self):
        response = self.update(display_title="烈海王復活", speaker="烈海王", anime_title="刃牙",
                               quote_text="復！活！", status="ready", search_aliases=["我又回來了"])
        self.assertEqual(response.status_code, 200)

    def test_queries_and_filters(self):
        self.seed()
        for q in ["復活", "復！活！", "復 活", "烈海王", "刃牙", "我又回來了", "烈海王 復活", "复活"]:
            with self.subTest(q=q):
                result = self.find(q).get_json()
                self.assertEqual(result["items"][0]["key"], "meme_1")
        self.assertEqual(self.find("烈海王 不存在詞").get_json()["total"], 0)
        self.assertEqual(self.find("我又回來了", tag="復活").get_json()["total"], 0)
        self.client.put("/api/entries/meme_1/tags", json={"tags": ["復活"]})
        self.assertEqual(self.find("我又回來了", tag="復活").get_json()["total"], 1)
        self.assertEqual(self.client.get("/api/entries/meme_1").get_json()["quote_text"], "復！活！")

    def test_alias_validation_atomicity_and_cascade(self):
        self.seed()
        bad = self.update(display_title="不能保存", search_aliases=["有效", "！！！"])
        self.assertEqual(bad.status_code, 400)
        row = self.client.get("/api/entries/meme_1").get_json()
        self.assertEqual(row["display_title"], "烈海王復活")
        self.assertEqual(row["search_aliases"], ["我又回來了"])
        self.update(search_aliases=["復活", "復！活！"])
        self.assertEqual(len(self.client.get("/api/entries/meme_1").get_json()["search_aliases"]), 1)
        self.update(display_title=None, search_aliases=[])
        self.assertEqual(self.find("我又回來了").get_json()["total"], 0)
        self.update(search_aliases=["再次歸來"])
        self.client.delete("/api/entries/meme_1")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM entry_search_aliases").fetchone()[0], 0)

    def test_ranking_and_duplicate_weights(self):
        self.seed()
        self.conn.execute("UPDATE meme_entries SET ocr_text='復活', status='ready' WHERE key='meme_2'")
        self.conn.commit()
        self.assertEqual(self.find("復活").get_json()["items"][0]["key"], "meme_1")
        score = scorer("復活")
        self.assertEqual(score(None,None,None,None,None,'["復活"]','[]'),
                         score(None,None,None,None,None,'["復活","復活"]','["復活"]'))

    def test_invalid_query_and_literal_symbols(self):
        for q in ["！！！", "   ", "x" * 257]:
            self.assertEqual(self.find(q).status_code, 400)
        self.assertEqual(self.find("% OR 1=1").get_json()["total"], 0)
        self.assertEqual(self.find("missing", page="abc").status_code, 400)

    def test_migration_and_unrelated_update_preserve_metadata(self):
        self.seed()
        init_db(self.conn)
        init_db(self.conn)
        self.update(notes="僅內部備註")
        row = self.client.get("/api/entries/meme_1").get_json()
        self.assertEqual(row["display_title"], "烈海王復活")
        self.assertEqual(row["search_aliases"], ["我又回來了"])
        self.assertEqual(self.find("僅內部備註").get_json()["total"], 0)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
