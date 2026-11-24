"""公開資料契約與查詢；僅依賴標準庫及共用搜尋規則。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .search import NORMALIZATION_VERSION, scorer

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE metadata(schema_version INTEGER NOT NULL, normalization_version INTEGER NOT NULL, version TEXT NOT NULL);
CREATE TABLE entries(
 id TEXT PRIMARY KEY, display_title TEXT, speaker TEXT, anime_title TEXT,
 quote_text TEXT NOT NULL, ocr_text TEXT, tags_json TEXT NOT NULL,
 aliases_json TEXT NOT NULL, thumbnail_url TEXT NOT NULL, original_url TEXT,
 updated_at TEXT NOT NULL
);
CREATE TABLE entry_tags(entry_id TEXT NOT NULL REFERENCES entries(id), tag TEXT NOT NULL,
 PRIMARY KEY(entry_id,tag));
CREATE INDEX idx_public_tag ON entry_tags(tag,entry_id);
CREATE INDEX idx_public_updated ON entries(updated_at DESC,id);
"""
FIELDS = ("id", "display_title", "speaker", "anime_title", "quote_text", "thumbnail_url", "original_url", "updated_at")
ALLOWED_ARGS = {"q", "tag", "sort", "page", "page_size", "version"}


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
    if columns != ["id", "display_title", "speaker", "anime_title", "quote_text", "ocr_text", "tags_json", "aliases_json", "thumbnail_url", "original_url", "updated_at"]:
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
    score_sql = "meme_score(e.display_title,e.speaker,e.anime_title,e.quote_text,e.ocr_text,e.tags_json,e.aliases_json)"
    where, params = ["1=1"], []
    if q:
        conn.create_function("meme_score", 7, scorer(q), deterministic=True)
        where.append(score_sql + ">0")
    if args.get("tag"):
        where.append("EXISTS(SELECT 1 FROM entry_tags t WHERE t.entry_id=e.id AND t.tag=?)")
        params.append(args["tag"])
    sort = args.get("sort") or ("relevance" if q else "updated")
    orders = {"updated": "e.updated_at DESC,e.id", "title": "e.anime_title COLLATE NOCASE,e.id",
              "relevance": (score_sql + " DESC,e.updated_at DESC,e.id") if q else "e.updated_at DESC,e.id"}
    if sort not in orders:
        raise ValueError("不支援的排序")
    condition = " AND ".join(where)
    total = conn.execute(f"SELECT count(*) FROM entries e WHERE {condition}", params).fetchone()[0]
    rows = conn.execute(f"SELECT e.* FROM entries e WHERE {condition} ORDER BY {orders[sort]} LIMIT ? OFFSET ?",
                        [*params, size, (page - 1) * size]).fetchall()
    return {"items": [serialize(r) for r in rows], "total": total, "page": page, "page_size": size}
