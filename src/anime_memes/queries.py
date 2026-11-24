"""管理庫查詢；不依賴 Flask 或圖片處理套件。"""
from __future__ import annotations
import sqlite3
import hashlib
from typing import Any
from .search import ALIASES_SQL, SCORE_SQL, scorer

def truthy(value):
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def metadata(conn):
    tags = [dict(r) for r in conn.execute("""SELECT tag_name AS name,COUNT(DISTINCT entry_key) AS count
        FROM entry_tags GROUP BY tag_name ORDER BY count DESC,tag_name""")]
    statuses = [dict(r) for r in conn.execute("SELECT status,COUNT(*) AS count FROM meme_entries GROUP BY status ORDER BY status")]
    return {"tags": tags, "statuses": statuses}

def query_entries(conn: sqlite3.Connection, args: Any, page: int, page_size: int) -> tuple[list[sqlite3.Row], int]:
    where = ["1=1"]
    params: list[Any] = []
    status = (args.get("status") or "ready").strip()
    if status and status != "all":
        where.append("e.status = ?")
        params.append(status)
    q = args.get("q") or ""
    if q:
        conn.create_function("meme_score", 7, scorer(q), deterministic=True)
        where.append(f"{SCORE_SQL} > 0")
    tag = (args.get("tag") or "").strip()
    if tag:
        where.append("EXISTS (SELECT 1 FROM entry_tags tx WHERE tx.entry_key = e.key AND tx.tag_name = ?)")
        params.append(tag)
    if truthy(args.get("favorite")):
        where.append("f.entry_key IS NOT NULL")
    published = str(args.get("published") or "").strip()
    if published in {"0", "1"}:
        where.append("e.publish_selected = ?")
        params.append(int(published))
    if truthy(args.get("no_tags")):
        where.append("NOT EXISTS (SELECT 1 FROM entry_tags nt WHERE nt.entry_key = e.key)")
    if truthy(args.get("missing_image")):
        where.append("(a.local_path IS NULL OR a.local_path = '' OR a.download_status NOT IN ('cached', 'downloaded'))")

    where_sql = " AND ".join(where)
    total = int(
        conn.execute(
            f"""
            SELECT COUNT(*)
            FROM meme_entries e
            JOIN attachments a ON a.id = e.attachment_id
            LEFT JOIN entry_favorites f ON f.entry_key = e.key
            WHERE {where_sql}
            """,
            params,
        ).fetchone()[0]
    )

    sort = args.get("sort") or ("relevance" if q else "updated")
    if sort == "random":
        seed = str(args.get("seed") or "default")[:128]
        conn.create_function("shuffle_key", 1, lambda key: hashlib.sha256(f"{seed}:{key}".encode()).hexdigest(), deterministic=True)
    order_by = {
        "random": "shuffle_key(e.key), e.key",
        "source_old": "m.timestamp ASC, e.key",
        "source_new": "m.timestamp DESC, e.key",
        "title": "e.anime_title COLLATE NOCASE, e.key",
    }.get(sort, "e.updated_at DESC, m.timestamp DESC, e.key")
    if sort == "relevance" and q:
        order_by = f"{SCORE_SQL} DESC, e.updated_at DESC, COALESCE(e.public_id,e.key) ASC"
    offset = (page - 1) * page_size
    rows = conn.execute(
        f"""
        SELECT
            e.*,
            {ALIASES_SQL} AS search_aliases_json,
            a.file_name,
            a.local_path,
            a.thumbnail_path,
            a.discord_url,
            a.download_status,
            a.image_width,
            a.image_height,
            m.timestamp AS source_timestamp,
            f.entry_key IS NOT NULL AS favorite,
            GROUP_CONCAT(DISTINCT t.tag_name) AS tags,
            GROUP_CONCAT(DISTINCT CASE WHEN t.source='manual' THEN t.tag_name END) AS manual_tags
        FROM meme_entries e
        JOIN attachments a ON a.id = e.attachment_id
        JOIN discord_messages m ON m.id = e.source_message_id
        LEFT JOIN entry_favorites f ON f.entry_key = e.key
        LEFT JOIN entry_tags t ON t.entry_key = e.key
        WHERE {where_sql}
        GROUP BY e.key
        ORDER BY {order_by}
        LIMIT ? OFFSET ?
        """,
        [*params, page_size, offset],
    ).fetchall()
    return rows, total


def fetch_entry(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute(
        f"""
        SELECT
            e.*,
            {ALIASES_SQL} AS search_aliases_json,
            a.file_name,
            a.local_path,
            a.thumbnail_path,
            a.discord_url,
            a.download_status,
            a.image_width,
            a.image_height,
            m.timestamp AS source_timestamp,
            f.entry_key IS NOT NULL AS favorite,
            GROUP_CONCAT(DISTINCT t.tag_name) AS tags,
            GROUP_CONCAT(DISTINCT CASE WHEN t.source='manual' THEN t.tag_name END) AS manual_tags
        FROM meme_entries e
        JOIN attachments a ON a.id = e.attachment_id
        JOIN discord_messages m ON m.id = e.source_message_id
        LEFT JOIN entry_favorites f ON f.entry_key = e.key
        LEFT JOIN entry_tags t ON t.entry_key = e.key
        WHERE e.key = ?
        GROUP BY e.key
        """,
        (key,),
    ).fetchone()
