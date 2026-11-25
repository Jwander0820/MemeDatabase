from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.anime_memes import db


def _structure(conn: sqlite3.Connection) -> dict:
    tables = {
        row["name"]: row["sql"]
        for row in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name")
    }
    columns = {
        name: [tuple(col) for col in conn.execute(f"PRAGMA table_info({name})")]
        for name in tables
    }
    indexes = sorted(
        (row["name"], row["tbl_name"])
        for row in conn.execute("SELECT name, tbl_name FROM sqlite_master WHERE type = 'index'")
    )
    return {"columns": columns, "indexes": indexes}


def _version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


class DbMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def open(self, name: str) -> sqlite3.Connection:
        conn = db.connect(self.dir / name)
        self.addCleanup(conn.close)
        return conn

    def test_fresh_database_gets_latest_version_and_is_idempotent(self) -> None:
        conn = self.open("fresh.sqlite3")
        db.init_db(conn)
        self.assertEqual(_version(conn), db.ADMIN_SCHEMA_VERSION)
        before = _structure(conn)
        db.init_db(conn)
        self.assertEqual(_version(conn), db.ADMIN_SCHEMA_VERSION)
        self.assertEqual(_structure(conn), before)

    def test_legacy_database_is_migrated(self) -> None:
        conn = self.open("legacy.sqlite3")
        conn.executescript(db.SCHEMA)  # 只有舊結構，缺少遷移補上的表與欄位
        conn.commit()
        self.assertEqual(_version(conn), 0)
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("entry_search_aliases", tables)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(meme_entries)")}
        self.assertNotIn("public_id", columns)

        db.init_db(conn)

        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("entry_search_aliases", tables)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(meme_entries)")}
        self.assertIn("public_id", columns)
        self.assertEqual(_version(conn), db.ADMIN_SCHEMA_VERSION)

    def test_legacy_and_fresh_structures_match(self) -> None:
        fresh = self.open("fresh.sqlite3")
        db.init_db(fresh)
        legacy = self.open("legacy.sqlite3")
        legacy.executescript(db.SCHEMA)
        legacy.commit()
        db.init_db(legacy)
        self.assertEqual(_structure(legacy), _structure(fresh))

    def test_newer_database_is_rejected_and_untouched(self) -> None:
        path = self.dir / "newer.sqlite3"
        conn = self.open("newer.sqlite3")
        conn.execute(f"PRAGMA user_version = {db.ADMIN_SCHEMA_VERSION + 1}")
        conn.commit()
        with self.assertRaises(RuntimeError):
            db.init_db(conn)
        self.assertEqual(_version(conn), db.ADMIN_SCHEMA_VERSION + 1)
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # SCHEMA 為 IF NOT EXISTS，不改既有資料；此處只確認版本號未被降低且未套用遷移
        self.assertNotIn("entry_search_aliases", tables)
        self.assertTrue(path.exists())

    def test_failed_migration_does_not_advance_version(self) -> None:
        conn = self.open("fail.sqlite3")
        db.init_db(conn)

        def broken(c: sqlite3.Connection) -> None:
            c.execute("CREATE TABLE should_roll_back (x INTEGER)")
            raise ValueError("遷移失敗")

        with mock.patch.object(db, "MIGRATIONS", [*db.MIGRATIONS, (db.ADMIN_SCHEMA_VERSION + 1, broken)]):
            with self.assertRaises(ValueError):
                db.init_db(conn)
        self.assertEqual(_version(conn), db.ADMIN_SCHEMA_VERSION)
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("should_roll_back", tables)


if __name__ == "__main__":
    unittest.main()
