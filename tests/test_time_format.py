from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.anime_memes import db

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00$")
DISCORD_TS = "2026-11-24T13:33:47.7391683+08:00"


def _seed_entry(conn: sqlite3.Connection, key: str, updated_at: str | None = None) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO discord_messages(id, timestamp, content) VALUES('m1', ?, 'x')", (DISCORD_TS,)
    )
    conn.execute("INSERT OR IGNORE INTO attachments(id, message_id) VALUES('a1', 'm1')")
    columns = "key, attachment_id, source_message_id, quote_text, pairing_method, confidence, status"
    values = [key, "a1", "m1", "q", "same_message", 1.0, "ok"]
    if updated_at is not None:
        columns += ", updated_at"
        values.append(updated_at)
    conn.execute(
        f"INSERT INTO meme_entries({columns}) VALUES({','.join('?' * len(values))})", values
    )


class TimeFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def open(self, name: str) -> sqlite3.Connection:
        conn = db.connect(self.dir / name)
        self.addCleanup(conn.close)
        return conn

    def fresh(self) -> sqlite3.Connection:
        conn = self.open("fresh.sqlite3")
        db.init_db(conn)
        return conn

    def test_scan_finds_expected_columns_and_excludes_discord_raw_times(self) -> None:
        conn = self.fresh()
        columns = set(db.iso_time_columns(conn))
        self.assertIn(("meme_entries", "updated_at"), columns)
        self.assertIn(("export_runs", "exported_at"), columns)
        self.assertIn(("discord_sync_state", "last_success_at"), columns)
        self.assertNotIn(("discord_messages", "timestamp"), columns)
        self.assertNotIn(("discord_messages", "timestamp_edited"), columns)
        triggers = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        for table, column in columns:
            for name in db.iso_trigger_names(table, column):
                self.assertIn(name, triggers)

    def test_default_current_timestamp_is_normalized_on_insert(self) -> None:
        conn = self.fresh()
        _seed_entry(conn, "k1")
        row = conn.execute("SELECT created_at, updated_at FROM meme_entries").fetchone()
        self.assertRegex(row["created_at"], ISO_RE)
        self.assertRegex(row["updated_at"], ISO_RE)
        self.assertRegex(conn.execute("SELECT updated_at FROM attachments").fetchone()[0], ISO_RE)
        self.assertEqual(conn.execute("SELECT timestamp FROM discord_messages").fetchone()[0], DISCORD_TS)

    def test_external_formats_are_converted_on_insert_and_update(self) -> None:
        conn = self.fresh()
        _seed_entry(conn, "k1", "2026-11-25 16:18:47")
        self.assertEqual(
            conn.execute("SELECT updated_at FROM meme_entries").fetchone()[0], "2026-11-25T16:18:47.000+00:00"
        )
        conn.execute("UPDATE meme_entries SET updated_at = ?", ("2026-11-25T12:00:00+08:00",))
        self.assertEqual(
            conn.execute("SELECT updated_at FROM meme_entries").fetchone()[0], "2026-11-25T04:00:00.000+00:00"
        )
        conn.execute("UPDATE meme_entries SET updated_at = ?", ("2026-11-25T04:17:54.434129+00:00",))
        self.assertEqual(
            conn.execute("SELECT updated_at FROM meme_entries").fetchone()[0], "2026-11-25T04:17:54.434+00:00"
        )
        conn.execute(
            "INSERT INTO export_runs(channel_id, status, exported_at) VALUES('c', 'success', ?)", (DISCORD_TS,)
        )
        self.assertEqual(
            conn.execute("SELECT exported_at FROM export_runs").fetchone()[0], "2026-11-24T05:33:47.739+00:00"
        )
        # NULL 與標準格式維持不變
        conn.execute("INSERT INTO export_runs(channel_id, status) VALUES('c', 'success')")
        self.assertIsNone(
            conn.execute("SELECT exported_at FROM export_runs WHERE exported_at IS NULL").fetchone()[0]
        )

    def test_migration_converts_legacy_rows_and_keeps_discord_timestamps(self) -> None:
        conn = self.open("legacy.sqlite3")
        conn.executescript(db.SCHEMA)
        _seed_entry(conn, "k1", "2026-11-25 16:18:47")
        conn.execute("UPDATE meme_entries SET created_at = '2026-11-25T12:00:00+08:00'")
        conn.execute(
            "INSERT INTO export_runs(channel_id, status, exported_at, created_at) "
            "VALUES('c', 'success', ?, '2026-11-25 01:02:03')",
            (DISCORD_TS,),
        )
        conn.execute(
            "UPDATE discord_messages SET timestamp_edited = ?, updated_at = '2026-11-25T04:17:54.434129+00:00'",
            (DISCORD_TS,),
        )
        conn.commit()

        db.init_db(conn)

        row = conn.execute("SELECT created_at, updated_at FROM meme_entries").fetchone()
        self.assertEqual(row["created_at"], "2026-11-25T04:00:00.000+00:00")
        self.assertEqual(row["updated_at"], "2026-11-25T16:18:47.000+00:00")
        run = conn.execute("SELECT exported_at, created_at FROM export_runs").fetchone()
        self.assertEqual(run["exported_at"], "2026-11-24T05:33:47.739+00:00")
        self.assertEqual(run["created_at"], "2026-11-25T01:02:03.000+00:00")
        message = conn.execute("SELECT timestamp, timestamp_edited, updated_at FROM discord_messages").fetchone()
        self.assertEqual(message["timestamp"], DISCORD_TS)
        self.assertEqual(message["timestamp_edited"], DISCORD_TS)
        self.assertEqual(message["updated_at"], "2026-11-25T04:17:54.434+00:00")
        # 遷移後所有時間欄位都是標準格式
        for table, column in db.iso_time_columns(conn):
            bad = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IS NOT NULL AND "{column}" NOT GLOB ?',
                ("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]+00:00",),
            ).fetchone()[0]
            self.assertEqual(bad, 0, f"{table}.{column}")

    def test_utc_now_iso_matches_sql_format(self) -> None:
        conn = self.fresh()
        self.assertRegex(db.utc_now_iso(), ISO_RE)
        self.assertRegex(conn.execute(f"SELECT {db.ISO_NOW_SQL}").fetchone()[0], ISO_RE)

    def test_sorting_by_updated_at_puts_newer_first(self) -> None:
        conn = self.fresh()
        _seed_entry(conn, "old", "2026-11-25 16:18:47")
        conn.execute("INSERT INTO meme_entries(key, attachment_id, source_message_id, quote_text, pairing_method, "
                     "confidence, status, updated_at) VALUES('new','a1','m1','q','p',1.0,'ok',?)",
                     ("2026-11-25T04:17:54.434129+00:00",))
        conn.execute("INSERT INTO meme_entries(key, attachment_id, source_message_id, quote_text, pairing_method, "
                     "confidence, status, updated_at) VALUES('newest','a1','m1','q','p',1.0,'ok',?)",
                     ("2026-11-25T20:00:00+08:00",))
        keys = [r[0] for r in conn.execute("SELECT key FROM meme_entries ORDER BY updated_at DESC")]
        # 16:18:47 UTC > 12:00 UTC (20:00+08:00) > 04:17 UTC
        self.assertEqual(keys, ["old", "newest", "new"])


if __name__ == "__main__":
    unittest.main()
