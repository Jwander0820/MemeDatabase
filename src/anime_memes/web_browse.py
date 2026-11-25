"""本機簡潔瀏覽：沿用公開頁前端，資料來源改為本機完整館藏（僅「可用」）。

參數驗證與錯誤格式與公開 API 一致（見 public_data.ALLOWED_ARGS）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Blueprint, abort, g, jsonify, request, send_file, send_from_directory

from . import public_data
from .queries import fetch_entry, query_entries, ready_tag_counts
from .web_common import error_response, serialize_entry

LOCAL_BROWSE_VERSION = "local"
# 本機瀏覽沒有 ids，但多了 favorite（與管理頁共用 entry_favorites）。
BROWSE_ENTRIES_ARGS = (public_data.ALLOWED_ARGS - {"ids"}) | {"favorite"}

browse_bp = Blueprint("browse", __name__)
_public_ui_dir = Path(__file__).with_name("public_ui")
_static_dir = Path(__file__).with_name("web_static")


@browse_bp.errorhandler(ValueError)
def browse_invalid(exc):
    return error_response("invalid_request", str(exc), 400)


@browse_bp.errorhandler(404)
def browse_not_found(exc):
    return error_response("not_found", exc.name, 404)


def browse_item(item: dict[str, Any]) -> dict[str, Any]:
    """轉成公開前端使用的欄位；大圖只用本機原圖，不外連 Discord CDN。"""
    image = item.get("image_url")
    return {
        "id": item["key"],
        "resolved_title": item["resolved_title"],
        "display_title": item.get("display_title"),
        "anime_title": item.get("anime_title"),
        "speaker": item.get("speaker"),
        "quote_text": item.get("quote_text"),
        "tags": item["tags"],
        "search_aliases": item.get("search_aliases", []),
        "thumbnail_url": item.get("thumbnail_url") or (image if image and image.startswith("/media/") else None),
        "original_url": image if image and image.startswith("/media/") else None,
        "updated_at": item.get("updated_at"),
        "favorite": bool(item.get("favorite")),
        # 原圖寬高與縮圖等比例，前端只用來預留版面比例。
        "thumbnail_width": item.get("image_width"),
        "thumbnail_height": item.get("image_height"),
    }


def _reject_unknown_args(allowed: set[str]) -> None:
    unknown = set(request.args) - allowed
    if unknown:
        raise ValueError("不支援的查詢參數：" + ", ".join(sorted(unknown)))


@browse_bp.get("/browse")
def browse():
    return send_file(_public_ui_dir / "index.html")


@browse_bp.get("/assets/<path:name>")
def browse_assets(name: str):
    if name in {"catalog.css", "favicon.svg"}:
        return send_from_directory(_static_dir, name)
    return send_from_directory(_public_ui_dir, name)


@browse_bp.get("/api/v1/meta")
def browse_meta():
    return jsonify({"version": LOCAL_BROWSE_VERSION, "mode": "local", "admin_url": "/",
                    "browse_url": "/browse", "tags": ready_tag_counts(g.db)})


@browse_bp.get("/api/v1/entries")
def browse_entries():
    _reject_unknown_args(BROWSE_ENTRIES_ARGS)
    page = public_data.int_param(request.args, "page", 1)
    page_size = public_data.int_param(request.args, "page_size", 24)
    if page < 1 or page > 10000 or not 1 <= page_size <= 60:
        raise ValueError("page 或 page_size 超出範圍")
    q = (request.args.get("q") or "").strip()
    sort = request.args.get("sort") or ("relevance" if q else "updated")
    if sort not in {"relevance", "updated", "title"}:
        raise ValueError("不支援的排序")
    args = {"status": "ready", "q": q, "field": request.args.get("field") or "",
            "tag": request.args.get("tag") or "", "sort": sort,
            "favorite": request.args.get("favorite") or ""}
    entries, total = query_entries(g.db, args, page, page_size)
    return jsonify({"items": [browse_item(serialize_entry(row)) for row in entries],
                    "page": page, "page_size": page_size, "total": total, "version": LOCAL_BROWSE_VERSION})


@browse_bp.get("/api/v1/entries/<key>")
def browse_entry(key: str):
    _reject_unknown_args({"version"})
    row = fetch_entry(g.db, key)
    if row is None or row["status"] != "ready":
        abort(404)
    return jsonify({"item": browse_item(serialize_entry(row)), "version": LOCAL_BROWSE_VERSION})
