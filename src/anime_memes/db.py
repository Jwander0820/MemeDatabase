from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS export_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL,
    after_value TEXT,
    before_value TEXT,
    raw_path TEXT,
    fixed_path TEXT,
    message_count INTEGER DEFAULT 0,
    status TEXT NOT NULL,
    error TEXT,
    exported_at TEXT,
    first_message_timestamp TEXT,
    first_message_id TEXT,
    last_message_timestamp TEXT,
    last_message_id TEXT,
    new_message_count INTEGER NOT NULL DEFAULT 0,
    changed_message_count INTEGER NOT NULL DEFAULT 0,
    entries_inserted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS discord_sync_state (
    channel_id TEXT PRIMARY KEY,
    last_export_run_id INTEGER,
    last_after_value TEXT,
    covered_through TEXT,
    last_message_timestamp TEXT,
    last_message_id TEXT,
    overlap_days INTEGER NOT NULL DEFAULT 1,
    last_success_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (last_export_run_id) REFERENCES export_runs(id)
);

CREATE TABLE IF NOT EXISTS discord_messages (
    id TEXT PRIMARY KEY,
    type TEXT,
    timestamp TEXT NOT NULL,
    timestamp_edited TEXT,
    call_ended_timestamp TEXT,
    is_pinned INTEGER NOT NULL DEFAULT 0,
    content TEXT NOT NULL DEFAULT '',
    author_id TEXT,
    author_name TEXT,
    author_discriminator TEXT,
    author_is_bot INTEGER NOT NULL DEFAULT 0,
    export_run_id INTEGER,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (export_run_id) REFERENCES export_runs(id)
);

CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    discord_url TEXT,
    file_name TEXT,
    file_size_bytes INTEGER,
    local_path TEXT,
    thumbnail_path TEXT,
    image_width INTEGER,
    image_height INTEGER,
    r2_url TEXT,
    download_status TEXT NOT NULL DEFAULT 'pending',
    download_error TEXT,
    downloaded_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (message_id) REFERENCES discord_messages(id)
);

CREATE TABLE IF NOT EXISTS meme_entries (
    key TEXT PRIMARY KEY,
    attachment_id TEXT NOT NULL,
    source_message_id TEXT NOT NULL,
    text_message_id TEXT,
    anime_title TEXT,
    speaker TEXT,
    quote_text TEXT NOT NULL,
    pairing_method TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    ocr_text TEXT,
    ocr_score REAL,
    ocr_status TEXT NOT NULL DEFAULT 'skipped',
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (attachment_id) REFERENCES attachments(id),
    FOREIGN KEY (source_message_id) REFERENCES discord_messages(id),
    FOREIGN KEY (text_message_id) REFERENCES discord_messages(id)
);

CREATE TABLE IF NOT EXISTS tags (
    name TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS entry_tags (
    entry_key TEXT NOT NULL,
    tag_name TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entry_key, tag_name, source),
    FOREIGN KEY (entry_key) REFERENCES meme_entries(key),
    FOREIGN KEY (tag_name) REFERENCES tags(name)
);

CREATE TABLE IF NOT EXISTS review_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_key TEXT,
    attachment_id TEXT,
    reason TEXT NOT NULL,
    details TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (entry_key) REFERENCES meme_entries(key),
    FOREIGN KEY (attachment_id) REFERENCES attachments(id)
);

CREATE TABLE IF NOT EXISTS entry_favorites (
    entry_key TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (entry_key) REFERENCES meme_entries(key)
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    migrate_db(conn)
    conn.commit()


def migrate_db(conn: sqlite3.Connection) -> None:
    ensure_columns(
        conn,
        "export_runs",
        {
            "first_message_timestamp": "TEXT",
            "first_message_id": "TEXT",
            "last_message_timestamp": "TEXT",
            "last_message_id": "TEXT",
            "new_message_count": "INTEGER NOT NULL DEFAULT 0",
            "changed_message_count": "INTEGER NOT NULL DEFAULT 0",
            "entries_inserted": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    ensure_columns(
        conn,
        "attachments",
        {
            "thumbnail_path": "TEXT",
            "image_width": "INTEGER",
            "image_height": "INTEGER",
            "downloaded_at": "TEXT",
        },
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entry_favorites (
            entry_key TEXT PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (entry_key) REFERENCES meme_entries(key)
        )
        """
    )


def ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
