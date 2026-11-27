"""公開資料契約與查詢；僅依賴標準庫及共用搜尋規則。"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from threading import Lock

from .search import NORMALIZATION_VERSION, field_scorer, normalize, normalized_fields, search_blob, search_field

# 第 2 版：entries 新增縮圖寬高，前端可在圖片載入前預留版面並依高度排列。
# 第 3 版：移除 ocr_text；OCR 不參與搜尋，也不回傳，沒有理由放進公開快照。
# 第 4 版：entries 新增 created_at（建立時間，供「最近新增」排序）與 home_random（1＝可進首頁隨機；
#   匯出時依 MEMES_HOME_EXCLUDED_TAGS 排除標籤計算，不對外回傳）。
SCHEMA_VERSION = 4
SCHEMA = """
CREATE TABLE metadata(schema_version INTEGER NOT NULL, normalization_version INTEGER NOT NULL, version TEXT NOT NULL);
CREATE TABLE entries(
 id TEXT PRIMARY KEY, display_title TEXT, speaker TEXT, anime_title TEXT,
 quote_text TEXT NOT NULL, tags_json TEXT NOT NULL,
 aliases_json TEXT NOT NULL, thumbnail_url TEXT NOT NULL, original_url TEXT,
 updated_at TEXT NOT NULL, thumbnail_width INTEGER NOT NULL, thumbnail_height INTEGER NOT NULL,
 created_at TEXT NOT NULL, home_random INTEGER NOT NULL
);
CREATE TABLE entry_tags(entry_id TEXT NOT NULL REFERENCES entries(id), tag TEXT NOT NULL,
 PRIMARY KEY(entry_id,tag));
CREATE INDEX idx_public_tag ON entry_tags(tag,entry_id);
CREATE INDEX idx_public_updated ON entries(updated_at DESC,id);
CREATE INDEX idx_public_created ON entries(created_at DESC,id);
"""
FIELDS = ("id", "display_title", "speaker", "anime_title", "quote_text", "thumbnail_url", "original_url", "updated_at",
          "thumbnail_width", "thumbnail_height", "created_at")
ALLOWED_ARGS = {"q", "field", "tag", "sort", "page", "page_size", "version", "ids", "seed"}
SEED_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,32}")
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
    if columns != ["id", "display_title", "speaker", "anime_title", "quote_text", "tags_json", "aliases_json", "thumbnail_url", "original_url", "updated_at", "thumbnail_width", "thumbnail_height", "created_at", "home_random"]:
        raise ValueError("公開欄位不符合白名單")
    for table, names in {"metadata": ["schema_version", "normalization_version", "version"], "entry_tags": ["entry_id", "tag"]}.items():
        if [r[1] for r in conn.execute(f"PRAGMA table_info({table})")] != names:
            raise ValueError("公開欄位不符合白名單")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger') LIMIT 1").fetchone():
        raise ValueError("公開快照不可包含 view 或 trigger")


def resolve_title(row) -> str:
    """顯示標題：display_title → 講者＋台詞 → anime_title → 「未命名梗圖」（公開與管理端共用）。"""
    return (row["display_title"]
            or " ".join(filter(None, [row["speaker"], row["quote_text"]])).strip()
            or row["anime_title"] or "未命名梗圖")


def serialize(row) -> dict:
    item = {field: row[field] for field in FIELDS}
    item["tags"] = json.loads(row["tags_json"])
    item["search_aliases"] = json.loads(row["aliases_json"])
    item["resolved_title"] = resolve_title(row)
    return item


def int_param(args, name: str, default: int) -> int:
    """讀取整數查詢參數；格式錯誤時回傳給使用者看的中文訊息，不外露 Python 的轉換錯誤。"""
    value = args.get(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必須是整數") from None


def shuffle_key(seed: str, key: str) -> str:
    """隨機排序鍵：同 seed 順序固定，換 seed 順序改變（公開與本機瀏覽共用）。"""
    return hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()


def read_seed(args) -> str:
    seed = args.get("seed")
    if not seed or not SEED_PATTERN.fullmatch(str(seed)):
        raise ValueError("sort=random 需要 seed（1–32 字元，僅限英數、底線與連字號）")
    return str(seed)


# 搜尋索引：發布快照不可變，每個版本只把搜尋欄位正規化一次（id → normalized_fields），
# 之後每次搜尋只做字串比對。以檔案路徑、版本與檔案狀態為鍵，只保留最近兩版（切版時新舊並存一下）。
_INDEX_CACHE: dict[tuple, dict[str, tuple]] = {}  # 值：id → (fields, blob)
_INDEX_LOCK = Lock()
_INDEX_KEEP = 2


def _build_index(conn: sqlite3.Connection) -> dict[str, tuple]:
    norm = lru_cache(maxsize=None)(normalize.__wrapped__)  # 本次建置內去重（作品、角色、標籤重複很多）
    index = {}
    for row in conn.execute("SELECT id,display_title,speaker,anime_title,quote_text,tags_json,aliases_json FROM entries"):
        fields = normalized_fields(*row[1:], norm=norm)
        index[row[0]] = (fields, search_blob(fields))
    return index


def search_index(conn: sqlite3.Connection) -> dict[str, tuple]:
    """取得此連線所屬快照的搜尋索引；記憶體或暫存資料庫（沒有檔案路徑）不快取。"""
    path = next((r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main"), "")
    if not path:
        return _build_index(conn)
    stat = Path(path).stat()
    version = conn.execute("SELECT version FROM metadata").fetchone()[0]
    key = (path, version, stat.st_ino, stat.st_mtime_ns, stat.st_size)
    with _INDEX_LOCK:  # 同時多個請求遇到新版本時只建一次
        index = _INDEX_CACHE.get(key)
        if index is None:
            index = _build_index(conn)
            _INDEX_CACHE[key] = index
            while len(_INDEX_CACHE) > _INDEX_KEEP:
                _INDEX_CACHE.pop(next(iter(_INDEX_CACHE)))
        return index


def search(conn: sqlite3.Connection, args) -> dict:
    unknown = set(args) - ALLOWED_ARGS
    if unknown:
        raise ValueError("不支援的查詢參數：" + ", ".join(sorted(unknown)))
    page, size = int_param(args, "page", 1), int_param(args, "page_size", 24)
    if page < 1 or not 1 <= size <= 60 or page > 10000:
        raise ValueError("page 或 page_size 超出範圍")
    q = args.get("q") or ""
    field = search_field(args.get("field") or None)
    score_sql = "meme_score(e.id)"
    where, params = ["1=1"], []
    if q:
        # 分數條件不放進 where：有查詢字時在下方 CTE 外層以 score>0 過濾，每列只計分一次。
        score_fields, index = field_scorer(q, field), search_index(conn)
        conn.create_function("meme_score", 1, lambda key: score_fields(*index[key]), deterministic=True)
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
    if sort == "random":
        seed = read_seed(args)
        conn.create_function("shuffle_key", 1, lambda key: shuffle_key(seed, key), deterministic=True)
        if not q and not args.get("tag") and "ids" not in args:
            where.append("e.home_random=1")  # 首頁隨機：沒有任何篩選時才排除；主動篩選仍找得到
    orders = {"updated": "e.updated_at DESC,e.id", "created": "e.created_at DESC,e.id", "random": "shuffle_key(e.id),e.id", "title": "e.anime_title COLLATE NOCASE,e.id",
              "relevance": (score_sql + " DESC,e.updated_at DESC,e.id") if q else "e.updated_at DESC,e.id"}
    if sort not in orders:
        raise ValueError("不支援的排序")
    if q:
        # 有查詢字：每列只計分一次（具體化的 CTE）。總數放在單列子查詢再 LEFT JOIN 該頁資料，
        # 頁碼超出範圍時仍拿得到總數，不必再算一次分數。
        condition = " AND ".join(where)
        order = re.sub(r"\be\.", "", orders[sort].replace(score_sql, "score"))
        offset = (page - 1) * size
        rows = conn.execute(
            f"WITH scored AS MATERIALIZED (SELECT e.*, {score_sql} AS score FROM entries e WHERE {condition}), "
            f"hits AS (SELECT * FROM scored WHERE score>0) "
            f"SELECT p.*, c.total_count FROM (SELECT count(*) AS total_count FROM hits) c LEFT JOIN "
            f"(SELECT *, row_number() OVER (ORDER BY {order}) AS rn FROM hits ORDER BY {order} LIMIT ? OFFSET ?) p "
            f"ON 1 ORDER BY p.rn", [*params, size, offset]).fetchall()
        total = rows[0]["total_count"]
        items = [serialize(r) for r in rows if r["id"] is not None]
        return {"items": items, "total": total, "page": page, "page_size": size}
    condition = " AND ".join(where)
    total = conn.execute(f"SELECT count(*) FROM entries e WHERE {condition}", params).fetchone()[0]
    rows = conn.execute(f"SELECT e.* FROM entries e WHERE {condition} ORDER BY {orders[sort]} LIMIT ? OFFSET ?",
                        [*params, size, (page - 1) * size]).fetchall()
    return {"items": [serialize(r) for r in rows], "total": total, "page": page, "page_size": size}
