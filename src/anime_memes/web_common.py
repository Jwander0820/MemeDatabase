from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import abort, request, send_file

from .media import resolve_media_path, file_exists
from .queries import fetch_entry


def check_mutation_origin() -> None:
    def origin(value: str, *, referer: bool = False):
        try:
            if any(char.isspace() for char in value):
                return None
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                return None
            if not referer and (parsed.path or parsed.query or parsed.fragment):
                return None
            return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError:
            return None

    expected = origin(request.host_url.rstrip("/"))
    supplied = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    if supplied is not None:
        if origin(supplied) is None or origin(supplied) != expected:
            abort(403)
    elif referer is not None:
        if origin(referer, referer=True) is None or origin(referer, referer=True) != expected:
            abort(403)
    if request.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
        abort(403)


def media_response(path: Path):
    # Explicit passive MIME types prevent imported documents from running as the editor.
    mime = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
        ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
        ".webm": "video/webm",
    }.get(path.suffix.lower())
    response = send_file(path, mimetype=mime or "application/octet-stream", as_attachment=mime is None)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return response


def fetch_entry_or_404(conn: sqlite3.Connection, key: str) -> sqlite3.Row:
    row = fetch_entry(conn, key)
    if row is None:
        abort(404)
    return row


def serialize_entry(row: sqlite3.Row, *, include_ocr: bool = False) -> dict[str, Any]:
    item = dict(row)
    item["search_aliases"] = json.loads(item.pop("search_aliases_json", "[]"))
    item["resolved_title"] = item.get("display_title") or " ".join(v for v in (item.get("speaker"), item.get("quote_text")) if v).strip() or item.get("anime_title") or "未命名梗圖"
    tags = [tag for tag in (item.get("tags") or "").split(",") if tag]
    thumbnail_path = resolve_media_path(item.get("thumbnail_path"))
    local_path = resolve_media_path(item.get("local_path"))
    suffix = Path(item.get("file_name") or "").suffix.lower()
    browser_image = suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ""}
    item["tags"] = tags
    item["manual_tags"] = [tag for tag in (item.get("manual_tags") or "").split(",") if tag]
    item["favorite"] = bool(item.get("favorite"))
    item["thumbnail_url"] = f"/media/thumbnails/{item['attachment_id']}" if file_exists(thumbnail_path) else None
    item["attachment_url"] = f"/media/attachments/{item['attachment_id']}" if file_exists(local_path) else item.get("discord_url")
    item["image_url"] = item["attachment_url"] if browser_image else None
    item["has_local_image"] = file_exists(local_path)
    if not include_ocr:
        item.pop("ocr_text", None)
    return item


def truthy(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            output.append(value)
    return output
