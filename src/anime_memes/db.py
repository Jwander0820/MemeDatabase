from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import settings

# 時間格式規範：管理庫所有 `*_at` 時間欄位一律為 ISO 8601 UTC 毫秒格式 `YYYY-MM-DDTHH:MM:SS.sss+00:00`，
# 由遷移 2 建立的觸發器強制（任何寫入者，含外部匯入工具）。
# 例外：discord_messages.timestamp／timestamp_edited 是 Discord 匯出的原始資料（offset 各異），
# Discord 同步會拿它們與新匯出的值比對來判斷訊息是否被編輯，轉換會造成誤判，故保持原樣。
ISO_TIME_FORMAT = "%Y-%m-%dT%H:%M:%f+00:00"
ISO_NOW_SQL = f"strftime('{ISO_TIME_FORMAT}','now')"


def iso_normalize_sql(expr: str) -> str:
    """SQL 運算式：把時間值正規化為標準格式（可解析空格／T 分隔、小數秒、時區 offset；無法解析時為 NULL）。"""
    return f"strftime('{ISO_TIME_FORMAT}', {expr})"


def utc_now_iso() -> str:
    """目前 UTC 時間，格式與 ISO_NOW_SQL 完全相同。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


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


# 管理庫可能同時被多個程序使用（管理頁、CLI 匯入、Docker 私人 API）。遇到其他程序正在寫入時，
# 最多等待這麼久再放棄，避免短暫的鎖定直接變成「database is locked」錯誤。
# 注意：跨 Docker Desktop 檔案共享（Windows 主機與 Linux 容器同時寫同一個檔案）時 SQLite 的鎖定不可靠，
# 等待只能減少衝突，無法保證安全；同一時間請只讓一個程序寫入（見 docs/backend-refactor.md）。
BUSY_TIMEOUT_SECONDS = 30


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)
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
    run_migrations(conn)
    sync_default_taxonomy(conn)
    conn.commit()


def run_migrations(conn: sqlite3.Connection) -> None:
    """依序執行 user_version 之後尚未套用的遷移；每個遷移與版本號在同一交易內完成。"""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    latest = max((version for version, _ in MIGRATIONS), default=0)
    if current > latest:
        raise RuntimeError(
            f"資料庫結構版本 {current} 比此程式支援的版本 {latest} 新，"
            "可能由較新版程式建立；請更新程式後再啟動，避免舊程式改壞資料庫。"
        )
    for version, migration in MIGRATIONS:
        if version <= current:
            continue
        conn.commit()
        conn.execute("BEGIN")
        try:
            migration(conn)
            conn.execute(f"PRAGMA user_version = {int(version)}")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise


def migrate_db(conn: sqlite3.Connection) -> None:
    """執行所有尚未套用的遷移（保留舊名稱）。"""
    run_migrations(conn)


def _migration_1_baseline(conn: sqlite3.Connection) -> None:
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


def iso_time_columns(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """掃描 schema，回傳所有名稱以 `_at` 結尾的時間欄位 (table, column)。

    discord_messages.timestamp／timestamp_edited 不以 `_at` 結尾，故自然排除（見 ISO_TIME_FORMAT 的說明）。
    """
    result: list[tuple[str, str]] = []
    tables = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for table in tables:
        if (table["sql"] or "").lstrip().upper().startswith("CREATE VIRTUAL"):
            continue
        for column in conn.execute(f'PRAGMA table_info("{table["name"]}")').fetchall():
            if column["name"].endswith("_at"):
                result.append((table["name"], column["name"]))
    return result


def iso_trigger_names(table: str, column: str) -> tuple[str, str]:
    return f"trg_iso_{table}_{column}_ins", f"trg_iso_{table}_{column}_upd"


def _migration_2_iso_timestamps(conn: sqlite3.Connection) -> None:
    """時間欄位統一為 ISO 8601 UTC 毫秒格式，並建立觸發器強制任何寫入者都轉成此格式。"""
    for table, column in iso_time_columns(conn):
        old_column = f'"{column}"'
        norm = iso_normalize_sql(old_column)
        conn.execute(
            f'UPDATE "{table}" SET {old_column} = {norm} '
            f"WHERE {old_column} IS NOT NULL AND {norm} IS NOT NULL AND {old_column} != {norm}"
        )
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()["sql"]
        if "WITHOUT ROWID" in sql.upper():
            pk = [row["name"] for row in sorted(
                (r for r in conn.execute(f'PRAGMA table_info("{table}")') if r["pk"]), key=lambda r: r["pk"]
            )]
            where = " AND ".join(f'"{name}" = NEW."{name}"' for name in pk)
        else:
            where = "rowid = NEW.rowid"
        new_norm = iso_normalize_sql(f'NEW."{column}"')
        condition = f'NEW."{column}" IS NOT NULL AND {new_norm} IS NOT NULL AND NEW."{column}" != {new_norm}'
        ins_name, upd_name = iso_trigger_names(table, column)
        for name, event in ((ins_name, "INSERT"), (upd_name, f'UPDATE OF "{column}"')):
            conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
            conn.execute(
                f'CREATE TRIGGER "{name}" AFTER {event} ON "{table}" WHEN {condition} '
                f'BEGIN UPDATE "{table}" SET "{column}" = {new_norm} WHERE {where}; END'
            )


# 媒體路徑欄位：一律存成相對專案根目錄、正斜線的 data/<分類>/<檔名>（見 paths.portable_media_path）。
MEDIA_PATH_COLUMNS = (("attachments", "local_path"), ("attachments", "thumbnail_path"), ("attachments", "web_path"))
_MEDIA_SUBDIRS = ("attachments", "thumbnails", "web")


def media_path_normalize_sql(expr: str) -> str:
    """SQL 運算式：把兩種已知的非標準寫法轉成 data/<分類>/<檔名>，其他路徑原樣保留。

    - 容器內的絕對路徑 /data/attachments/x（Docker 掛載點）→ data/attachments/x
    - Windows 相對路徑 data\\attachments\\x → data/attachments/x
    """
    slashed = f"replace({expr}, char(92), '/')"
    container = " OR ".join(f"{slashed} LIKE '/data/{name}/%'" for name in _MEDIA_SUBDIRS)
    relative = " OR ".join(f"{slashed} LIKE 'data/{name}/%'" for name in _MEDIA_SUBDIRS)
    return f"(CASE WHEN {container} THEN substr({slashed}, 2) WHEN {relative} THEN {slashed} ELSE {expr} END)"


def _migration_3_portable_media_paths(conn: sqlite3.Connection) -> None:
    """媒體路徑統一為可攜的相對路徑，並建立觸發器讓任何寫入者（含外部工具）都轉成此格式。

    手動上傳曾直接存實際寫入位置：在 Docker 中變成 /data/attachments/...，Windows 主機上的工具
    會把它解析成磁碟根目錄的 D:\\data\\... 而找不到檔案。
    """
    for table, column in MEDIA_PATH_COLUMNS:
        norm = media_path_normalize_sql(f'"{column}"')
        conn.execute(f'UPDATE "{table}" SET "{column}" = {norm} WHERE "{column}" IS NOT NULL AND "{column}" != {norm}')
        new_norm = media_path_normalize_sql(f'NEW."{column}"')
        condition = f'NEW."{column}" IS NOT NULL AND NEW."{column}" != {new_norm}'
        for suffix, event in (("ins", "INSERT"), ("upd", f'UPDATE OF "{column}"')):
            name = f"trg_path_{table}_{column}_{suffix}"
            conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
            conn.execute(
                f'CREATE TRIGGER "{name}" AFTER {event} ON "{table}" WHEN {condition} '
                f'BEGIN UPDATE "{table}" SET "{column}" = {new_norm} WHERE rowid = NEW.rowid; END'
            )


# 管理庫結構遷移清單（以 PRAGMA user_version 記錄已套用版本；與公開庫的 SCHEMA_VERSION 無關）。
# 新增遷移的規則：
# - 只能在清單尾端追加新版本，版本號連續遞增。
# - 不可修改已發布的遷移；結構變更一律用新版本表達。
# - 每個遷移必須能在單一交易內完成（不可自行 commit，也不可使用 executescript）。
# - 版本 1 為基準：既有資料庫 user_version 皆為 0，故保持冪等。
MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_1_baseline),
    (2, _migration_2_iso_timestamps),
    (3, _migration_3_portable_media_paths),
]
ADMIN_SCHEMA_VERSION = MIGRATIONS[-1][0]


def ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def sync_default_taxonomy(conn: sqlite3.Connection) -> None:
    taxonomy_path = settings.TAXONOMY_PATH
    from .tags import load_taxonomy, sync_taxonomy

    sync_taxonomy(conn, load_taxonomy(taxonomy_path))
