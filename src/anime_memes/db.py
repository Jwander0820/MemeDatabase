from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from . import settings


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS deleted_attachments (
    attachment_id TEXT PRIMARY KEY,
    deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

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
    id TEXT UNIQUE,
    category TEXT NOT NULL DEFAULT 'legacy',
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    model_selectable INTEGER NOT NULL DEFAULT 0,
    taxonomy_version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS entry_tags (
    entry_key TEXT NOT NULL,
    tag_name TEXT NOT NULL,
    source TEXT NOT NULL,
    tag_id TEXT,
    source_type TEXT,
    source_ref TEXT,
    confidence REAL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entry_key, tag_name, source),
    FOREIGN KEY (entry_key) REFERENCES meme_entries(key),
    FOREIGN KEY (tag_name) REFERENCES tags(name),
    FOREIGN KEY (tag_id) REFERENCES tags(id)
);

CREATE TABLE IF NOT EXISTS tagging_runs (
    id TEXT PRIMARY KEY,
    actor_type TEXT NOT NULL,
    actor_name TEXT NOT NULL,
    model_name TEXT,
    taxonomy_version INTEGER NOT NULL,
    input_path TEXT NOT NULL,
    output_path TEXT,
    status TEXT NOT NULL DEFAULT 'exported',
    exported_count INTEGER NOT NULL DEFAULT 0,
    processed_count INTEGER NOT NULL DEFAULT 0,
    suggestion_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    imported_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS tagging_run_entries (
    run_id TEXT NOT NULL,
    entry_key TEXT NOT NULL,
    image_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'exported',
    processed_at TEXT,
    PRIMARY KEY (run_id, entry_key),
    FOREIGN KEY (run_id) REFERENCES tagging_runs(id),
    FOREIGN KEY (entry_key) REFERENCES meme_entries(key)
);

CREATE TABLE IF NOT EXISTS tag_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    entry_key TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    confidence REAL NOT NULL,
    reason TEXT NOT NULL,
    image_summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, entry_key, tag_id),
    FOREIGN KEY (run_id) REFERENCES tagging_runs(id),
    FOREIGN KEY (entry_key) REFERENCES meme_entries(key),
    FOREIGN KEY (tag_id) REFERENCES tags(id)
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


def backup_database(db_path: str | Path, backup_dir: str | Path) -> Path | None:
    path = Path(db_path)
    if not path.exists():
        return None
    directory = Path(backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"{path.stem}_before_sync_{datetime.now():%Y%m%d_%H%M%S_%f}{path.suffix}"
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(output)) as destination:
            source.backup(destination)
    return output


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    migrate_db(conn)
    sync_default_taxonomy(conn)
    conn.commit()


def migrate_db(conn: sqlite3.Connection) -> None:
    ensure_columns(conn, "meme_entries", {"publish_selected": "INTEGER NOT NULL DEFAULT 0", "public_id": "TEXT"})
    ensure_columns(conn, "attachments", {"public_original_url": "TEXT"})
    ensure_columns(conn, "attachments", {"web_path": "TEXT", "web_sha256": "TEXT", "r2_key": "TEXT"})
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_public_id ON meme_entries(public_id)")
    ensure_columns(conn, "meme_entries", {"display_title": "TEXT", "source_page": "TEXT"})
    ensure_columns(conn, "meme_entries", {"web_image_url": "TEXT"})
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_web_image_url ON meme_entries(web_image_url) WHERE web_image_url IS NOT NULL")
    conn.execute("""CREATE TABLE IF NOT EXISTS entry_search_aliases (
        entry_key TEXT NOT NULL REFERENCES meme_entries(key) ON DELETE CASCADE,
        alias_text TEXT NOT NULL CHECK(length(trim(alias_text)) > 0),
        alias_normalized TEXT NOT NULL CHECK(length(alias_normalized) > 0),
        normalization_version INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(entry_key, alias_normalized)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entry_tags_name_entry ON entry_tags(tag_name, entry_key)")
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
    ensure_columns(
        conn,
        "tags",
        {
            "id": "TEXT",
            "category": "TEXT NOT NULL DEFAULT 'legacy'",
            "description": "TEXT NOT NULL DEFAULT ''",
            "enabled": "INTEGER NOT NULL DEFAULT 1",
            "model_selectable": "INTEGER NOT NULL DEFAULT 0",
            "taxonomy_version": "INTEGER NOT NULL DEFAULT 1",
            "updated_at": "TEXT",
        },
    )
    ensure_columns(
        conn,
        "entry_tags",
        {
            "tag_id": "TEXT",
            "source_type": "TEXT",
            "source_ref": "TEXT",
        },
    )
    for index in conn.execute("PRAGMA index_list(tags)").fetchall():
        if index["name"] == "idx_tags_id" and not index["unique"]:
            conn.execute("DROP INDEX idx_tags_id")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_id ON tags(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entry_tags_tag_id ON entry_tags(tag_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_suggestions_entry_status ON tag_suggestions(entry_key, status)")
    conn.execute("UPDATE tags SET updated_at = COALESCE(created_at, CURRENT_TIMESTAMP) WHERE updated_at IS NULL")
    conn.execute(
        """
        UPDATE entry_tags
        SET source_type = CASE source
            WHEN 'auto' THEN 'rule'
            WHEN 'manual' THEN 'manual'
            WHEN 'local_llm' THEN 'suggestion'
            ELSE source
        END
        WHERE source_type IS NULL OR source_type = ''
        """
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


def sync_default_taxonomy(conn: sqlite3.Connection) -> None:
    taxonomy_path = settings.TAXONOMY_PATH
    from .tags import load_taxonomy, sync_taxonomy

    sync_taxonomy(conn, load_taxonomy(taxonomy_path))
