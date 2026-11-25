from contextlib import closing
from pathlib import Path
import sqlite3
from unittest.mock import patch
import unittest

import test_assessment_fixes as fixtures
from src.anime_memes.db import init_db
from src.anime_memes.search import Term, normalize, parse_query, scorer


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
        found = self.find("復活").get_json()
        self.assertEqual(found["items"][0]["key"], "meme_1")
        self.assertNotIn("meme_2", {item["key"] for item in found["items"]})  # OCR 不參與搜尋
        score = scorer("復活")
        self.assertEqual(score(None,None,None,None,'["復活"]','[]'),
                         score(None,None,None,None,'["復活","復活"]','["復活"]'))

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

    def test_query_syntax_through_api(self):
        self.seed()
        self.conn.execute("UPDATE meme_entries SET status='ready', anime_title='其他作品', quote_text='烈海王來了' WHERE key='meme_2'")
        self.conn.commit()
        keys = lambda q: {item["key"] for item in self.find(q).get_json()["items"]}
        self.assertEqual(keys("烈海王"), {"meme_1", "meme_2"})
        self.assertEqual(keys("烈海王 -刃牙"), {"meme_2"})
        self.assertEqual(keys("烈海王 -作品:刃牙"), {"meme_2"})
        self.assertEqual(keys("角色:烈海王"), {"meme_1"})
        self.assertEqual(keys('台詞:"烈海王 來了"'), {"meme_2"})
        excluded = self.find("-作品:刃牙").get_json()
        self.assertNotIn("meme_1", {item["key"] for item in excluded["items"]})
        self.assertEqual(excluded["total"], self.find("").get_json()["total"] - 1)
        self.assertEqual({i["key"] for i in self.find("烈海王", field="speaker").get_json()["items"]}, {"meme_1"})
        self.assertEqual({i["key"] for i in self.find("烈海王", field="quote").get_json()["items"]}, {"meme_2"})
        self.assertEqual(self.find("烈海王", field="ocr").status_code, 400)
        self.assertEqual(self.find("", field="work").status_code, 200)


ROW = dict(title=None, speaker=None, work=None, quote=None, tags="[]", aliases="[]")


def score(q, field=None, **fields):
    values = {**ROW, **fields}
    return scorer(q, field)(*values.values())


class QuerySyntaxTest(unittest.TestCase):
    def test_parse_terms(self):
        work = "超超超超超喜歡你的100個女朋友"
        parsed = parse_query(f"朋友 -作品:{work}")
        self.assertEqual(parsed.terms, (Term("朋友"), Term(normalize(work), "work", negative=True)))
        self.assertEqual(parsed.whole, "朋友")
        self.assertEqual(parse_query('"復 活" 烈海王').terms, (Term("復活", phrase=True), Term("烈海王")))
        self.assertEqual(parse_query("作品：「刃牙」").terms, (Term("刃牙", "work", phrase=True),))
        self.assertEqual(parse_query("－标签:复活").terms, (Term("復活", "tag", negative=True),))
        self.assertEqual(parse_query('-"烈 海王"').terms, (Term("烈海王", negative=True, phrase=True),))
        self.assertEqual(parse_query("TITLE:x Quote:y Alias:z").terms,
                         (Term("x", "title"), Term("y", "quote"), Term("z", "alias")))
        self.assertEqual(parse_query("OCR:z 文字:w").terms, (Term("ocrz"), Term("文字w")))  # OCR 已不是欄位

    def test_literal_fallbacks(self):
        self.assertEqual(parse_query("Re:ゼロ 12:30").terms, (Term("reゼロ"), Term("1230")))
        self.assertEqual(parse_query("- 朋友").terms, (Term("朋友"),))
        self.assertEqual(parse_query('"-_-"').terms, (Term("_", phrase=True),))
        self.assertEqual(parse_query("朋友 作品: -").terms, (Term("朋友"),))  # 輸入途中的殘片略過
        self.assertEqual(parse_query('作品:"刃牙').terms, (Term("刃牙", "work", phrase=True),))  # 未閉合引號
        self.assertEqual(parse_query("朋友 朋友 -x -x").terms, (Term("朋友"), Term("x", negative=True)))

    def test_parse_validation(self):
        for q in ["-", "作品:", "  -  ", "！！！", "x" * 257]:
            with self.subTest(q=q), self.assertRaises(ValueError):
                parse_query(q)
        with self.assertRaises(ValueError):
            parse_query(" ".join(f"t{i}" for i in range(16)) + " -t16")
        parse_query(" ".join(f"t{i}" for i in range(16)))

    def test_exclusion(self):
        work = "超超超超超喜歡你的100個女朋友"
        q = f"朋友 -{work}"
        self.assertEqual(score(q, quote="朋友", work=work), 0)
        self.assertGreater(score(q, quote="朋友", work="其他"), 0)
        self.assertGreater(score(f"朋友 -作品:{work}", quote=f"朋友{work}"), 0)  # 限定欄位只看作品
        self.assertEqual(score(f"朋友 -{work}", quote=f"朋友{work}"), 0)
        self.assertEqual(score("朋友 -女朋友", quote="我的女朋友"), 0)  # 子字串排除
        self.assertEqual(score("-標籤:復活", tags='["復活"]'), 0)
        self.assertEqual(score("-標籤:復活", quote="復活"), 1)
        self.assertEqual(score("-別名:回來", aliases='["我又回來了"]'), 0)

    def test_field_scope_and_phrase(self):
        self.assertEqual(score("台詞:烈海王", speaker="烈海王", quote="復活"), 0)
        self.assertGreater(score("角色:烈海王", speaker="烈海王", quote="復活"), 0)
        self.assertGreater(score("烈海王 復活", speaker="烈海王", quote="復活"), 0)
        self.assertEqual(score('"烈海王 復活"', speaker="烈海王", quote="復活"), 0)
        self.assertGreater(score('"烈海王 復活"', quote="烈海王復活了嗎"), 0)
        self.assertEqual(score('"烈海王 復活"', quote="烈海王會復活嗎"), 0)  # 必須連續
        self.assertEqual(score('"復活 烈海王"', quote="烈海王復活了嗎"), 0)  # 片語有順序
        self.assertEqual(score("文字:復活", quote="復活"), 0)

    def test_ranking_bands(self):
        # 完全相等 > 人工欄位部分命中 > OCR；同為完全相等時依欄位權重。
        ladder = [dict(title="朋友"), dict(tags='["朋友"]'), dict(speaker="朋友"), dict(quote="朋友"),
                  dict(title="朋友們"), dict(speaker="好朋友"), dict(quote="你沒有朋友吧對吧")]
        scores = [score("朋友", **row) for row in ladder]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(len(set(scores)), len(scores))
        # 詞佔欄位比例越高、在開頭命中，分數越高。
        self.assertGreater(score("朋友", title="我的朋友"), score("朋友", title="我們永遠都是最好的朋友"))
        self.assertGreater(score("朋友", title="朋友真好"), score("朋友", title="真好朋友"))
        # 相鄰勝過分散；整句完全相等置頂。
        self.assertGreater(score("烈海王 復活", quote="烈海王復活了"), score("烈海王 復活", quote="烈海王說會復活"))
        # 但角色完全相等的強訊號，仍勝過只在台詞裡相鄰。
        self.assertGreater(score("烈海王 復活", speaker="烈海王", quote="他復活了"), score("烈海王 復活", quote="烈海王復活了"))
        self.assertGreater(score("烈海王 復活", title="烈海王復活"),
                           score("烈海王 復活", title="烈海王", tags='["復活"]', quote="烈海王復活了"))

    def test_field_scoped_ranking_ignores_other_fields(self):
        self.assertEqual(score("作品:刃牙", work="刃牙", title="刃牙"), score("作品:刃牙", work="刃牙"))

    def test_search_scope(self):
        parsed = parse_query("烈海王 -刃牙 台詞:復活", "speaker")
        self.assertEqual(parsed.terms, (Term("烈海王", "speaker"), Term("刃牙", "speaker", negative=True),
                                        Term("復活", "quote")))  # 明寫的前綴優先
        self.assertEqual(parsed.free, (Term("烈海王", "speaker"),))
        self.assertEqual(score("烈海王", "work", speaker="烈海王"), 0)
        self.assertGreater(score("烈海王", "speaker", speaker="烈海王"), 0)
        self.assertGreater(score("刃牙", "speaker", speaker="烈海王", work="刃牙"), -1)
        self.assertEqual(score("刃牙", "speaker", speaker="烈海王", work="刃牙"), 0)
        self.assertGreater(score("烈海王 -刃牙", "speaker", speaker="烈海王", work="刃牙"), 0)  # 排除也只看角色
        # 範圍內的整句完全相等才置頂；範圍外的完全相等不加分。
        self.assertEqual(score("烈海王", "speaker", speaker="烈海王", title="烈海王"),
                         score("烈海王", "speaker", speaker="烈海王"))
        self.assertGreater(score("烈海王", "speaker", speaker="烈海王"), 10000)
        for bad in ["ocr", "notes", "x"]:
            with self.subTest(field=bad), self.assertRaises(ValueError):
                parse_query("烈海王", bad)


class SingleScoreTest(unittest.TestCase):
    """有查詢字時每列只計分一次，且總數、順序與逐列計分的舊寫法一致。"""

    N = 30

    def counting(self, module):
        calls = []
        real = module.scorer

        def wrapped(q, field):
            inner = real(q, field)

            def fn(*args):
                calls.append(args)
                return inner(*args)
            return fn
        return calls, patch.object(module, "scorer", wrapped)

    def test_public_single_score_and_same_results(self):
        from src.anime_memes import public_data as pd
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(pd.SCHEMA)
        for i in range(self.N):
            quote = f"烈海王{i}" if i % 3 == 0 else f"路人{i}"
            conn.execute("INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         (f"{i:032x}", None, "角色", "刃牙" if i % 2 else "Abc", quote, "[]", "[]",
                          "t", None, f"2026-01-{i + 1:02d}", 1, 1))
            if i % 2 == 0:
                conn.execute("INSERT INTO entry_tags VALUES(?,?)", (f"{i:032x}", "復活"))
        matched = [i for i in range(self.N) if i % 3 == 0]
        cases = [
            ({"q": "烈海王", "sort": "updated"}, 1, sorted(matched, reverse=True)),
            ({"q": "烈海王", "sort": "title"}, 1, sorted(matched, key=lambda i: (("刃牙" if i % 2 else "Abc").lower(), f"{i:032x}"))),
            ({"q": "烈海王", "tag": "復活", "sort": "updated"}, 1, [i for i in sorted(matched, reverse=True) if i % 2 == 0]),
        ]
        for args, _, expected in cases:
            with self.subTest(args=args):
                calls, patcher = self.counting(pd)
                with patcher:
                    result = pd.search(conn, {**args, "page_size": 4, "page": 2})
                    self.assertEqual(len(calls), self.N if "tag" not in args else self.N // 2)
                self.assertEqual(result["total"], len(expected))
                self.assertEqual([r["id"] for r in result["items"]], [f"{i:032x}" for i in expected[4:8]])
                self.assertNotIn("score", result["items"][0])
                self.assertNotIn("total_count", result["items"][0])
        with self.subTest("相關度排序與逐列計分的舊寫法一致"):
            from src.anime_memes.search import SCORE_ARGS, scorer, search_field
            conn.create_function("meme_score", SCORE_ARGS, scorer("烈海王", search_field(None)), deterministic=True)
            score = "meme_score(e.display_title,e.speaker,e.anime_title,e.quote_text,e.tags_json,e.aliases_json)"
            ref = [r[0] for r in conn.execute(f"SELECT e.id FROM entries e WHERE {score}>0 ORDER BY {score} DESC,e.updated_at DESC,e.id")]
            got = []
            for page in (1, 2, 3):
                result = pd.search(conn, {"q": "烈海王", "page": page, "page_size": 4})
                self.assertEqual(result["total"], len(ref))
                got += [r["id"] for r in result["items"]]
            self.assertEqual(got, ref)
        with self.subTest("超出頁碼"):
            result = pd.search(conn, {"q": "烈海王", "page": 5, "page_size": 4})
            self.assertEqual((result["items"], result["total"]), ([], len(matched)))
        with self.subTest("無符合"):
            self.assertEqual(pd.search(conn, {"q": "不存在詞"})["total"], 0)

    def test_admin_single_score_and_same_results(self):
        import tempfile
        from src.anime_memes.db import connect
        from src.anime_memes import queries
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        conn = connect(Path(tmp) / "a.sqlite3")
        self.enterContext(closing(conn))
        init_db(conn)
        conn.execute("INSERT INTO tags(name) VALUES('復活')")
        for i in range(self.N):
            conn.execute("INSERT INTO discord_messages(id,timestamp,content) VALUES(?,?,'x')", (str(i), f"2026-02-{i + 1:02d}"))
            conn.execute("INSERT INTO attachments(id,message_id) VALUES(?,?)", (str(i), str(i)))
            conn.execute("""INSERT INTO meme_entries(key,attachment_id,source_message_id,quote_text,speaker,anime_title,
                pairing_method,confidence,status,updated_at) VALUES(?,?,?,?,?,?, 'test',1,'ready',?)""",
                         (f"k{i:02d}", str(i), str(i), f"烈海王{i}" if i % 3 == 0 else f"路人{i}", "角色",
                          "刃牙" if i % 2 else "Abc", f"2026-01-{i + 1:02d}"))
            if i % 2 == 0:
                conn.execute("INSERT INTO entry_tags(entry_key,tag_name,source) VALUES(?,?,'manual')", (f"k{i:02d}", "復活"))
        conn.commit()
        matched = [i for i in range(self.N) if i % 3 == 0]
        cases = [
            ({"q": "烈海王", "sort": "updated"}, sorted(matched, reverse=True), self.N),
            ({"q": "烈海王", "sort": "source_old"}, matched, self.N),
            ({"q": "烈海王", "sort": "title"}, sorted(matched, key=lambda i: (("刃牙" if i % 2 else "Abc").lower(), f"k{i:02d}")), self.N),
            ({"q": "烈海王", "tag": "復活", "sort": "updated"}, [i for i in sorted(matched, reverse=True) if i % 2 == 0], self.N // 2),
        ]
        for args, expected, scored in cases:
            with self.subTest(args=args):
                calls, patcher = self.counting(queries)
                with patcher:
                    rows, total = queries.query_entries(conn, args, 2, 4)
                    self.assertEqual(len(calls), scored)
                self.assertEqual(total, len(expected))
                self.assertEqual([r["key"] for r in rows], [f"k{i:02d}" for i in expected[4:8]])
                self.assertNotIn("_total_count", dict(rows[0]))
                self.assertNotIn("score", dict(rows[0]))
        with self.subTest("相關度分頁加總一致"):
            keys = []
            for page in (1, 2, 3):
                rows, total = queries.query_entries(conn, {"q": "烈海王"}, page, 4)
                self.assertEqual(total, len(matched))
                keys += [r["key"] for r in rows]
            self.assertEqual(sorted(keys), [f"k{i:02d}" for i in matched])
        with self.subTest("超出頁碼"):
            rows, total = queries.query_entries(conn, {"q": "烈海王"}, 9, 4)
            self.assertEqual((list(rows), total), ([], len(matched)))
