"""管理庫查詢；不依賴 Flask 或圖片處理套件。"""
from __future__ import annotations
import sqlite3
import hashlib
from typing import Any
from .search import ALIASES_SQL, SCORE_ARGS, SCORE_SQL, scorer, search_field

def truthy(value):
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def metadata(conn):
    tags = [dict(r) for r in conn.execute("""SELECT tag_name AS name,COUNT(DISTINCT entry_key) AS count
        FROM entry_tags GROUP BY tag_name ORDER BY count DESC,tag_name""")]
    statuses = [dict(r) for r in conn.execute("SELECT status,COUNT(*) AS count FROM meme_entries GROUP BY status ORDER BY status")]
    # 有 AI／Agent 候選標籤待審的梗圖數（不分狀態），供管理頁快速篩選顯示數量。
    pending = conn.execute("""SELECT COUNT(DISTINCT s.entry_key) FROM tag_suggestions s
        JOIN meme_entries e ON e.key=s.entry_key WHERE s.status='pending'""").fetchone()[0]
    return {"tags": tags, "statuses": statuses, "pending_suggestions": pending}


def ready_tag_counts(conn: sqlite3.Connection) -> list[dict]:
    """只計算狀態為 ready 的條目的標籤數量，供本機瀏覽 meta 使用。"""
    return [dict(r) for r in conn.execute("""SELECT t.tag_name AS name, COUNT(DISTINCT t.entry_key) AS count
        FROM entry_tags t JOIN meme_entries e ON e.key=t.entry_key WHERE e.status='ready'
        GROUP BY t.tag_name ORDER BY count DESC, t.tag_name""")]

def query_entries(conn: sqlite3.Connection, args: Any, page: int, page_size: int) -> tuple[list[sqlite3.Row], int]:
    where = ["1=1"]
    params: list[Any] = []
    status = (args.get("status") or "ready").strip()
    if status and status != "all":
        where.append("e.status = ?")
        params.append(status)
    q = args.get("q") or ""
    field = search_field(args.get("field") or None)  # 搜尋範圍：沒寫前綴的詞只比對此欄位
    if q:
        conn.create_function("meme_score", SCORE_ARGS, scorer(q, field), deterministic=True)
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
    if truthy(args.get("pending_suggestions")):
        where.append("EXISTS (SELECT 1 FROM tag_suggestions ps WHERE ps.entry_key = e.key AND ps.status = 'pending')")
    if truthy(args.get("no_tags")):
        where.append("NOT EXISTS (SELECT 1 FROM entry_tags nt WHERE nt.entry_key = e.key)")
    if truthy(args.get("missing_image")):
        where.append("(a.local_path IS NULL OR a.local_path = '' OR a.download_status NOT IN ('cached', 'downloaded'))")

    where_sql = " AND ".join(where)
    if q:
        return _query_scored(conn, args, where_sql, params, page, page_size)
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
    offset = (page - 1) * page_size
    rows = conn.execute(
        f"""
        {_SELECT_ENTRY}
        {_FROM_ENTRY}
        WHERE {where_sql}
        GROUP BY e.key
        ORDER BY {order_by}
        LIMIT ? OFFSET ?
        """,
        [*params, page_size, offset],
    ).fetchall()
    return rows, total


_SELECT_ENTRY = f"""
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
            GROUP_CONCAT(DISTINCT CASE WHEN t.source='manual' THEN t.tag_name END) AS manual_tags"""
_FROM_ENTRY = """
        FROM meme_entries e
        JOIN attachments a ON a.id = e.attachment_id
        JOIN discord_messages m ON m.id = e.source_message_id
        LEFT JOIN entry_favorites f ON f.entry_key = e.key
        LEFT JOIN entry_tags t ON t.entry_key = e.key"""


def _query_scored(conn, args, where_sql, params, page, page_size):
    """有查詢字：每列只計分一次（具體化 CTE），並以視窗函式同時取得總數與該頁資料。"""
    cte = f"""WITH scored AS MATERIALIZED (
            SELECT e.key AS k, {SCORE_SQL} AS score
            FROM meme_entries e
            JOIN attachments a ON a.id = e.attachment_id
            LEFT JOIN entry_favorites f ON f.entry_key = e.key
            WHERE {where_sql})"""
    sort = args.get("sort") or "relevance"
    if sort == "random":
        seed = str(args.get("seed") or "default")[:128]
        conn.create_function("shuffle_key", 1, lambda key: hashlib.sha256(f"{seed}:{key}".encode()).hexdigest(), deterministic=True)
    order_by = {
        "random": "shuffle_key(e.key), e.key",
        "source_old": "m.timestamp ASC, e.key",
        "source_new": "m.timestamp DESC, e.key",
        "title": "e.anime_title COLLATE NOCASE, e.key",
        "relevance": "s.score DESC, e.updated_at DESC, COALESCE(e.public_id,e.key) ASC",
    }.get(sort, "e.updated_at DESC, m.timestamp DESC, e.key")
    rows = conn.execute(
        f"""{cte}
        {_SELECT_ENTRY}, COUNT(*) OVER () AS _total_count
        {_FROM_ENTRY.replace("FROM meme_entries e", "FROM scored s JOIN meme_entries e ON e.key = s.k", 1)}
        WHERE s.score > 0
        GROUP BY e.key
        ORDER BY {order_by}
        LIMIT ? OFFSET ?
        """,
        [*params, page_size, (page - 1) * page_size],
    ).fetchall()
    if rows:
        total = int(rows[0]["_total_count"])
        # 去掉內部欄位，維持原本的回傳欄位
        rows = [{k: r[k] for k in r.keys() if k != "_total_count"} for r in rows]
    elif page == 1:
        total = 0
    else:  # 頁碼超出範圍：沒有任何列可取總數，另算一次
        total = int(conn.execute(f"{cte} SELECT COUNT(*) FROM scored WHERE score > 0", params).fetchone()[0])
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
