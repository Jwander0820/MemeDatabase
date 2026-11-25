"""公開資料契約與查詢；僅依賴標準庫及共用搜尋規則。"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .search import NORMALIZATION_VERSION, SCORE_ARGS, scorer, search_field

# 第 2 版：entries 新增縮圖寬高，前端可在圖片載入前預留版面並依高度排列。
# 第 3 版：移除 ocr_text；OCR 不參與搜尋，也不回傳，沒有理由放進公開快照。
SCHEMA_VERSION = 3
SCHEMA = """
CREATE TABLE metadata(schema_version INTEGER NOT NULL, normalization_version INTEGER NOT NULL, version TEXT NOT NULL);
CREATE TABLE entries(
 id TEXT PRIMARY KEY, display_title TEXT, speaker TEXT, anime_title TEXT,
 quote_text TEXT NOT NULL, tags_json TEXT NOT NULL,
 aliases_json TEXT NOT NULL, thumbnail_url TEXT NOT NULL, original_url TEXT,
 updated_at TEXT NOT NULL, thumbnail_width INTEGER NOT NULL, thumbnail_height INTEGER NOT NULL
);
CREATE TABLE entry_tags(entry_id TEXT NOT NULL REFERENCES entries(id), tag TEXT NOT NULL,
 PRIMARY KEY(entry_id,tag));
CREATE INDEX idx_public_tag ON entry_tags(tag,entry_id);
CREATE INDEX idx_public_updated ON entries(updated_at DESC,id);
"""
FIELDS = ("id", "display_title", "speaker", "anime_title", "quote_text", "thumbnail_url", "original_url", "updated_at",
          "thumbnail_width", "thumbnail_height")
ALLOWED_ARGS = {"q", "field", "tag", "sort", "page", "page_size", "version", "ids"}
# 訪客收藏清單一次查詢的上限（收藏存在訪客瀏覽器，以公開 ID 查最新資料）。
MAX_IDS = 200


def open_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def validate_schema(conn: sqlite3.Connection, version: str) -> None:
    meta = conn.execute("SELECT * FROM metadata").fetchall()
    if len(meta) != 1 or tuple(meta[0]) != (SCHEMA_VERSION, NORMALIZATION_VERSION, version):
        raise ValueError("不相容的公開資料版本")
    expected = {"metadata", "entries", "entry_tags"}
    if {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} != expected:
        raise ValueError("公開資料表不符合白名單")
    columns = [r[1] for r in conn.execute("PRAGMA table_info(entries)")]
    if columns != ["id", "display_title", "speaker", "anime_title", "quote_text", "tags_json", "aliases_json", "thumbnail_url", "original_url", "updated_at", "thumbnail_width", "thumbnail_height"]:
        raise ValueError("公開欄位不符合白名單")
    for table, names in {"metadata": ["schema_version", "normalization_version", "version"], "entry_tags": ["entry_id", "tag"]}.items():
        if [r[1] for r in conn.execute(f"PRAGMA table_info({table})")] != names:
            raise ValueError("公開欄位不符合白名單")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger') LIMIT 1").fetchone():
        raise ValueError("公開快照不可包含 view 或 trigger")


def serialize(row) -> dict:
    item = {field: row[field] for field in FIELDS}
    item["tags"] = json.loads(row["tags_json"])
    item["search_aliases"] = json.loads(row["aliases_json"])
    item["resolved_title"] = row["display_title"] or " ".join(filter(None, [row["speaker"], row["quote_text"]])) or row["anime_title"] or "未命名梗圖"
    return item


def search(conn: sqlite3.Connection, args) -> dict:
    unknown = set(args) - ALLOWED_ARGS
    if unknown:
        raise ValueError("不支援的查詢參數：" + ", ".join(sorted(unknown)))
    page, size = int(args.get("page", 1)), int(args.get("page_size", 24))
    if page < 1 or not 1 <= size <= 60 or page > 10000:
        raise ValueError("page 或 page_size 超出範圍")
    q = args.get("q") or ""
    field = search_field(args.get("field") or None)
    score_sql = "meme_score(e.display_title,e.speaker,e.anime_title,e.quote_text,e.tags_json,e.aliases_json)"
    where, params = ["1=1"], []
    if q:
        # 分數條件不放進 where：有查詢字時在下方 CTE 外層以 score>0 過濾，每列只計分一次。
        conn.create_function("meme_score", SCORE_ARGS, scorer(q, field), deterministic=True)
    if args.get("tag"):
        where.append("EXISTS(SELECT 1 FROM entry_tags t WHERE t.entry_id=e.id AND t.tag=?)")
        params.append(args["tag"])
    if "ids" in args:
        ids = [value for value in str(args.get("ids") or "").split(",") if value]
        if len(ids) > MAX_IDS or any(not re.fullmatch(r"[0-9a-f]{32}", value) for value in ids):
            raise ValueError(f"ids 必須是最多 {MAX_IDS} 個以逗號分隔的公開 ID")
        if ids:
            where.append(f"e.id IN ({','.join('?' * len(ids))})")
            params.extend(ids)
        else:
            where.append("0")  # 空的收藏清單：不回傳任何結果
    sort = args.get("sort") or ("relevance" if q else "updated")
    orders = {"updated": "e.updated_at DESC,e.id", "title": "e.anime_title COLLATE NOCASE,e.id",
              "relevance": (score_sql + " DESC,e.updated_at DESC,e.id") if q else "e.updated_at DESC,e.id"}
    if sort not in orders:
        raise ValueError("不支援的排序")
    if q:
        # 有查詢字：每列只計分一次（具體化的 CTE），同時以視窗函式取得總數與該頁資料。
        condition = " AND ".join(where)
        order = orders[sort].replace(score_sql, "score")
        cte = f"WITH scored AS MATERIALIZED (SELECT e.*, {score_sql} AS score FROM entries e WHERE {condition})"
        offset = (page - 1) * size
        rows = conn.execute(f"{cte} SELECT *, COUNT(*) OVER () AS total_count FROM scored WHERE score>0 "
                            f"ORDER BY {re.sub(r'\be\.', '', order)} LIMIT ? OFFSET ?", [*params, size, offset]).fetchall()
        if rows:
            total = rows[0]["total_count"]
        elif page == 1:
            total = 0
        else:  # 頁碼超出範圍：外層沒有任何列可取總數，另算一次
            total = conn.execute(f"{cte} SELECT count(*) FROM scored WHERE score>0", params).fetchone()[0]
        return {"items": [serialize(r) for r in rows], "total": total, "page": page, "page_size": size}
    condition = " AND ".join(where)
    total = conn.execute(f"SELECT count(*) FROM entries e WHERE {condition}", params).fetchone()[0]
    rows = conn.execute(f"SELECT e.* FROM entries e WHERE {condition} ORDER BY {orders[sort]} LIMIT ? OFFSET ?",
                        [*params, size, (page - 1) * size]).fetchall()
    return {"items": [serialize(r) for r in rows], "total": total, "page": page, "page_size": size}
