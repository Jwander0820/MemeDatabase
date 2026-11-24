from contextlib import closing, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.anime_memes import publishing
from src.anime_memes.cli import main
from src.anime_memes.db import connect, init_db
from src.anime_memes.publishing import bulk_update_selection
from src.anime_memes.r2 import R2_ENV_VARS
from src.anime_memes.web import create_app

HEADERS = {"Origin": "http://localhost", "Host": "localhost"}


class BulkPublicationTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {name: "" for name in R2_ENV_VARS}))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "admin.sqlite3"
        self.conn = connect(self.db)
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        for n, status in enumerate(["ready", "ready", "ready", "needs_review", "ignored"]):
            self.conn.execute("INSERT INTO discord_messages(id,timestamp,content) VALUES(?, '2026-11-24','m')", (str(n),))
            self.conn.execute("INSERT INTO attachments(id,message_id) VALUES(?,?)", (str(n), str(n)))
            self.conn.execute("""INSERT INTO meme_entries(key,attachment_id,source_message_id,quote_text,
                speaker,anime_title,pairing_method,confidence,status) VALUES(?,?,?,'q','s','a','test',1,?)""",
                              (f"meme_{n}", str(n), str(n), status))
        self.conn.commit()

    def state(self):
        return {r[0]: (r[1], r[2]) for r in self.conn.execute(
            "SELECT key,publish_selected,public_id FROM meme_entries")}

    def test_keys_mode_assigns_distinct_ids_and_keeps_existing(self):
        self.conn.execute("UPDATE meme_entries SET public_id='keepme' WHERE key='meme_0'")
        self.conn.commit()
        result = bulk_update_selection(self.conn, selected=True, keys=["meme_0", "meme_1", "meme_1", "nope"])
        self.assertEqual(result["matched"], 2)
        self.assertEqual(result["changed"], 2)
        self.assertEqual(result["missing"], ["nope"])
        self.assertEqual(result["sample"], ["meme_0", "meme_1"])
        self.assertFalse(result["dry_run"])
        state = self.state()
        self.assertEqual(state["meme_0"], (1, "keepme"))
        self.assertEqual(state["meme_1"][0], 1)
        self.assertTrue(state["meme_1"][1])
        self.assertNotEqual(state["meme_0"][1], state["meme_1"][1])
        self.assertEqual(state["meme_2"], (0, None))
        again = bulk_update_selection(self.conn, selected=True, keys=["meme_0"])
        self.assertEqual(again["changed"], 0)

    def test_filter_defaults_to_ready(self):
        result = bulk_update_selection(self.conn, selected=True, filters={})
        self.assertEqual(result["matched"], 3)
        state = self.state()
        self.assertEqual([k for k, v in state.items() if v[0]], ["meme_0", "meme_1", "meme_2"])
        self.assertTrue(all(v[1] for k, v in state.items() if v[0]))
        result = bulk_update_selection(self.conn, selected=True, filters={"status": "all"})
        self.assertEqual(result["matched"], 5)
        self.assertEqual(result["changed"], 2)

    def test_dry_run_writes_nothing(self):
        result = bulk_update_selection(self.conn, selected=True, filters={"status": "all"}, dry_run=True)
        self.assertEqual((result["matched"], result["changed"], result["dry_run"]), (5, 5, True))
        self.assertEqual(set(self.state().values()), {(0, None)})

    def test_unselect(self):
        bulk_update_selection(self.conn, selected=True, filters={})
        result = bulk_update_selection(self.conn, selected=False, keys=["meme_0", "meme_3"])
        self.assertEqual((result["matched"], result["changed"]), (2, 1))
        state = self.state()
        self.assertEqual(state["meme_0"][0], 0)
        self.assertTrue(state["meme_0"][1])  # public_id 保留

    def test_published_filter(self):
        bulk_update_selection(self.conn, selected=True, keys=["meme_0"])
        result = bulk_update_selection(self.conn, selected=True, filters={"published": "0"}, dry_run=True)
        self.assertEqual(result["matched"], 2)

    def test_validation_errors(self):
        bad = [
            dict(selected=True),
            dict(selected=True, keys=["meme_0"], filters={}),
            dict(selected=1, keys=["meme_0"]),
            dict(selected=True, filters={"bogus": "x"}),
            dict(selected=True, filters={"q": 5}),
            dict(selected=True, keys=[]),
            dict(selected=True, keys=[""]),
            dict(selected=True, keys=[f"k{i}" for i in range(publishing.MAX_BULK_KEYS + 1)]),
        ]
        for kwargs in bad:
            with self.subTest(kwargs=list(kwargs)), self.assertRaises(ValueError):
                bulk_update_selection(self.conn, **kwargs)
        with patch.object(publishing, "MAX_BULK_KEYS", 2), self.assertRaises(ValueError):
            bulk_update_selection(self.conn, selected=True, filters={})
        self.assertEqual(set(self.state().values()), {(0, None)})

    def test_web_endpoint(self):
        client = create_app(db_path=self.db, attachment_dir=self.root, thumbnail_dir=self.root / "t").test_client()
        response = client.post("/api/publication/bulk", json={"publish_selected": True, "filter": {}, "dry_run": True},
                               headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["matched"], 3)
        response = client.post("/api/publication/bulk", json={"publish_selected": True, "keys": ["meme_4"]},
                               headers=HEADERS)
        self.assertEqual(response.get_json()["changed"], 1)
        for body in [{"publish_selected": True}, {"publish_selected": True, "keys": ["a"], "filter": {}},
                     {"publish_selected": "yes", "keys": ["a"]}, {"publish_selected": True, "keys": ["a"], "x": 1},
                     {"keys": ["a"]}, [1]]:
            response = client.post("/api/publication/bulk", json=body, headers=HEADERS)
            self.assertEqual(response.status_code, 400, body)
            self.assertIn("error", response.get_json())

    def run_cli(self, *extra):
        out = StringIO()
        with redirect_stdout(out):
            main(["--db", str(self.db), "publication", "bulk", *extra])
        return json.loads(out.getvalue())

    def test_cli(self):
        result = self.run_cli("--selected", "yes", "--dry-run")
        self.assertEqual((result["matched"], result["dry_run"]), (3, True))
        self.assertEqual(set(self.state().values()), {(0, None)})
        keys_file = self.root / "keys.txt"
        keys_file.write_text("# 註解\n\nmeme_3\n", encoding="utf-8")
        result = self.run_cli("--selected", "yes", "--keys", "meme_0", "--keys-file", str(keys_file))
        self.assertEqual(result["matched"], 2)
        self.assertEqual(self.state()["meme_3"][0], 1)


if __name__ == "__main__":
    unittest.main()
