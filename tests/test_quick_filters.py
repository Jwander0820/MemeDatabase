import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.anime_memes.db import connect, init_db
from src.anime_memes.publishing import bulk_update_selection
from src.anime_memes.queries import metadata, query_entries
from src.anime_memes.web import create_app


class QuickFilterTest(unittest.TestCase):
    """管理頁快速篩選：AI／Agent 候選標籤待處理。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "a.sqlite3"
        with closing(connect(self.db)) as conn:
            init_db(conn)
            tag_id = conn.execute("SELECT id FROM tags WHERE id IS NOT NULL LIMIT 1").fetchone()[0]
            conn.execute("""INSERT INTO tagging_runs(id,actor_type,actor_name,taxonomy_version,input_path)
                VALUES('run1','agent','測試 Agent',1,'jobs.jsonl')""")
            # k0：ready 有待處理候選；k1：needs_review 有待處理候選；k2：候選已接受；k3：沒有候選。
            for n, (status, decision) in enumerate([("ready", "pending"), ("needs_review", "pending"),
                                                    ("ready", "accepted"), ("ready", None)]):
                conn.execute("INSERT INTO discord_messages(id,timestamp,content) VALUES(?, '2026-11-25','x')", (str(n),))
                conn.execute("INSERT INTO attachments(id,message_id) VALUES(?,?)", (str(n), str(n)))
                conn.execute("""INSERT INTO meme_entries(key,attachment_id,source_message_id,quote_text,pairing_method,
                    confidence,status) VALUES(?,?,?,'台詞','test',1,?)""", (f"k{n}", str(n), str(n), status))
                if decision:
                    conn.execute("""INSERT INTO tag_suggestions(run_id,entry_key,tag_id,confidence,reason,status)
                        VALUES('run1',?,?,0.9,'理由',?)""", (f"k{n}", tag_id, decision))
            conn.commit()

    def keys(self, args):
        with closing(connect(self.db)) as conn:
            rows, total = query_entries(conn, args, 1, 50)
        return sorted(r["key"] for r in rows), total

    def test_篩選待處理候選並可搭配狀態(self):
        self.assertEqual(self.keys({"status": "all", "pending_suggestions": "1"}), (["k0", "k1"], 2))
        self.assertEqual(self.keys({"status": "ready", "pending_suggestions": "1"}), (["k0"], 1))
        self.assertEqual(self.keys({"status": "needs_review"}), (["k1"], 1))
        self.assertEqual(self.keys({"status": "all"})[1], 4)

    def test_meta_回傳待處理候選數量(self):
        with closing(connect(self.db)) as conn:
            meta = metadata(conn)
        self.assertEqual(meta["pending_suggestions"], 2)
        self.assertEqual({s["status"]: s["count"] for s in meta["statuses"]}["needs_review"], 1)
        response = create_app(db_path=self.db).test_client().get("/api/meta")
        self.assertEqual(response.get_json()["pending_suggestions"], 2)

    def test_管理_API_支援篩選參數(self):
        client = create_app(db_path=self.db).test_client()
        data = client.get("/api/entries?status=all&pending_suggestions=1").get_json()
        self.assertEqual(sorted(item["key"] for item in data["items"]), ["k0", "k1"])

    def test_批次公開接受新篩選與搜尋範圍(self):
        with closing(connect(self.db)) as conn:
            result = bulk_update_selection(conn, selected=True, dry_run=True,
                                           filters={"status": "all", "pending_suggestions": "1"})
            self.assertEqual(result["matched"], 2)
            # 搜尋範圍 field 先前不在白名單，會被誤判為不支援的欄位。
            bulk_update_selection(conn, selected=True, dry_run=True,
                                  filters={"status": "all", "q": "台詞", "field": "quote"})


if __name__ == "__main__":
    unittest.main()
