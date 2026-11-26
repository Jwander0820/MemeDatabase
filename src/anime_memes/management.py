"""本機資料管理交易及上傳流程，無 HTTP 相依。"""
from __future__ import annotations
import json
import sqlite3
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any
from PIL import Image, UnidentifiedImageError
from werkzeug.utils import secure_filename
from .aliases import validate_aliases, replace_aliases
from .db import ISO_NOW_SQL, utc_now_iso
from .tags import add_entry_tag, split_tags
from .queries import fetch_entry
from .media import resolve_media_path, safe_delete_media_file
from .paths import portable_media_path
from .text import dedupe
MANUAL_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def source_page_url(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("圖片出處需為網址")
    value = value.strip()
    if not value:
        return None
    url = urlsplit(value)
    if url.scheme not in {"https", "http"} or not url.hostname or any(c.isspace() for c in value):
        raise ValueError("圖片出處需為完整 HTTP 或 HTTPS 網址")
    return value


def set_favorite(conn, key, enabled):
    if fetch_entry(conn, key) is None:
        raise KeyError(key)
    with conn:
        if enabled:
            conn.execute("INSERT OR IGNORE INTO entry_favorites(entry_key) VALUES(?)", (key,))
        else:
            conn.execute("DELETE FROM entry_favorites WHERE entry_key=?", (key,))
    return {"key": key, "favorite": enabled}

def update_entry(conn, key, data):
    if not isinstance(data, dict):
        raise ValueError("JSON object required")
    aliases = validate_aliases(data["search_aliases"]) if "search_aliases" in data else None
    if "display_title" in data and data["display_title"] is not None and not isinstance(data["display_title"], str):
        raise ValueError("display_title must be text or null")
    fields = {
        "anime_title": data.get("anime_title"),
        "speaker": data.get("speaker"),
        "quote_text": data.get("quote_text"),
        "notes": data.get("notes"),
        "status": data.get("status"),
    }
    status = fields["status"]
    if status is not None and (not isinstance(status, str) or status not in {"", "ready", "ignored", "needs_review"}):
        raise ValueError("invalid status")
    updates: list[str] = []
    params: list[Any] = []
    for column, value in fields.items():
        if value is None:
            continue
        updates.append(f"{column} = ?")
        params.append(str(value).strip() if column != "quote_text" else str(value))
    if "display_title" in data:
        updates.append("display_title = ?")
        params.append((data["display_title"] or "").strip() or None)
    if not updates and aliases is None and "source_page" not in data and "tags" not in data:
        raise ValueError("no fields to update")
    if "source_page" in data:
        updates.append("source_page = ?")
        params.append(source_page_url(data["source_page"]))
    updates.append(f"updated_at = {ISO_NOW_SQL}")
    params.append(key)
    with conn:
        cursor = conn.execute(f"UPDATE meme_entries SET {', '.join(updates)} WHERE key = ?", params)
        if cursor.rowcount == 0:
            raise KeyError(key)
        if aliases is not None:
            replace_aliases(conn, key, aliases)
        if "tags" in data:
            _replace_manual_tags(conn, key, data["tags"])
    return fetch_entry(conn, key)


def update_tags(conn, key, data):
    if not isinstance(data, dict):
        raise ValueError("JSON object required")
    if fetch_entry(conn, key) is None:
        raise KeyError(key)
    return update_entry(conn, key, {"tags": data.get("tags")})


def _replace_manual_tags(conn, key, tags):
    if isinstance(tags, str):
        tag_values = split_tags(tags)
    elif isinstance(tags, list) and all(isinstance(item, str) for item in tags):
        tag_values = [item.strip() for item in tags if item.strip()]
    else:
        raise ValueError("tags must be a string or list")
    conn.execute("DELETE FROM entry_tags WHERE entry_key = ? AND source = 'manual'", (key,))
    for tag in dedupe(tag_values):
        add_entry_tag(conn, key, tag, "manual", None, "web editor")


def delete_entry(conn, key, attachment_root, thumbnail_root):
    row = fetch_entry(conn, key)
    if row is None:
        raise KeyError(key)

    attachment_id = row["attachment_id"]
    conn.execute("INSERT OR IGNORE INTO deleted_attachments(attachment_id) VALUES(?)", (attachment_id,))
    attachment = conn.execute(
        "SELECT id, local_path, thumbnail_path FROM attachments WHERE id = ?",
        (attachment_id,),
    ).fetchone()

    conn.execute("DELETE FROM entry_favorites WHERE entry_key = ?", (key,))
    conn.execute("DELETE FROM tag_suggestions WHERE entry_key = ?", (key,))
    conn.execute("DELETE FROM tagging_run_entries WHERE entry_key = ?", (key,))
    conn.execute("DELETE FROM entry_tags WHERE entry_key = ?", (key,))
    conn.execute("DELETE FROM review_items WHERE entry_key = ?", (key,))
    conn.execute("DELETE FROM meme_entries WHERE key = ?", (key,))
    remaining = int(
        conn.execute(
            "SELECT COUNT(*) FROM meme_entries WHERE attachment_id = ?",
            (attachment_id,),
        ).fetchone()[0]
    )

    removed_attachment = False
    files_to_delete: list[tuple[Path | None, Path]] = []
    if attachment is not None and remaining == 0:
        removed_attachment = True
        conn.execute("DELETE FROM review_items WHERE attachment_id = ?", (attachment_id,))
        conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
        files_to_delete = [
            (resolve_media_path(attachment["local_path"]), attachment_root),
            (resolve_media_path(attachment["thumbnail_path"]), thumbnail_root),
        ]

    conn.commit()

    deleted_files: list[str] = []
    file_errors: list[dict[str, str]] = []
    for path, allowed_root in files_to_delete:
        result = safe_delete_media_file(path, allowed_root)
        if result.deleted_path:
            deleted_files.append(result.deleted_path)
        if result.error:
            file_errors.append({"path": result.path or "", "error": result.error})

    return (
        {
            "key": key,
            "attachment_id": attachment_id,
            "removed_attachment": removed_attachment,
            "deleted_files": deleted_files,
            "file_errors": file_errors,
        }
    )


def create_manual_entry(conn, form, upload, attachment_root, thumbnail_root, *, web_image_url=None, local_import_sha256=None):
    source_page = source_page_url(form.get("source_page"))
    aliases = validate_aliases(json.loads(form.get("search_aliases", "[]")))
    display_title = (form.get("display_title") or "").strip() or None
    if upload is None or not upload.filename:
        raise ValueError("請選擇圖片檔案")

    original_name = secure_filename(upload.filename) or "manual-image"
    suffix = Path(original_name).suffix.lower()
    if suffix not in MANUAL_IMAGE_SUFFIXES:
        raise ValueError("僅支援 JPG、PNG、WebP 或 GIF 圖片")

    status = (form.get("status") or "ready").strip()
    if status not in {"ready", "needs_review", "ignored"}:
        raise ValueError("狀態格式不正確")

    token = uuid.uuid4().hex
    message_id = f"manual_message_{token}"
    attachment_id = f"manual_{token}"
    key = f"meme_{attachment_id}"
    attachment_root.mkdir(parents=True, exist_ok=True)
    thumbnail_root.mkdir(parents=True, exist_ok=True)
    local_path = attachment_root / f"{attachment_id}{suffix}"
    thumbnail_path = thumbnail_root / f"{attachment_id}.webp"

    try:
        upload.save(local_path)
        if not local_path.exists() or local_path.stat().st_size <= 0:
            raise ValueError("圖片檔案是空的")
        with Image.open(local_path) as image:
            image.verify()
        with Image.open(local_path) as image:
            image_width, image_height = image.size
            if suffix != ".gif":
                preview = image.convert("RGB")
                preview.thumbnail((420, 420), Image.Resampling.LANCZOS)
                preview.save(thumbnail_path, "WEBP", quality=78, method=6)
    except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError) as exc:
        local_path.unlink(missing_ok=True)
        thumbnail_path.unlink(missing_ok=True)
        raise ValueError(f"無法讀取圖片：{exc}") from exc

    anime_title = (form.get("anime_title") or "").strip()
    speaker = (form.get("speaker") or "").strip()
    quote_text = form.get("quote_text") or ""
    notes = (form.get("notes") or "").strip()
    tag_values = dedupe(split_tags(form.get("tags") or ""))
    timestamp = utc_now_iso()
    content = "\n".join(value for value in (anime_title, speaker, quote_text) if value)

    try:
        conn.execute(
            """
            INSERT INTO discord_messages(
                id, type, timestamp, content, author_id, author_name, author_is_bot
            ) VALUES(?, 'Manual', ?, ?, 'manual-ui', 'Manual UI', 0)
            """,
            (message_id, timestamp, content),
        )
        conn.execute(
            """
            INSERT INTO attachments(
                id, message_id, file_name, file_size_bytes, local_path, thumbnail_path,
                image_width, image_height, download_status, downloaded_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'downloaded', ?)
            """,
            (
                attachment_id,
                message_id,
                original_name,
                local_path.stat().st_size,
                portable_media_path("ATTACHMENT", local_path),
                portable_media_path("THUMBNAIL", thumbnail_path) if thumbnail_path.exists() else None,
                image_width,
                image_height,
                timestamp,
            ),
        )
        conn.execute(
            """
            INSERT INTO meme_entries(
                key, attachment_id, source_message_id, text_message_id, anime_title,
                speaker, quote_text, pairing_method, confidence, status, ocr_status, notes
            ) VALUES(?, ?, ?, ?, ?, ?, ?, 'manual_upload', 1.0, ?, 'skipped', ?)
            """,
            (
                key,
                attachment_id,
                message_id,
                message_id,
                anime_title or None,
                speaker or None,
                quote_text,
                status,
                notes or None,
            ),
        )
        conn.execute("UPDATE meme_entries SET display_title=?, source_page=?, web_image_url=?, local_import_sha256=? WHERE key=?",
                     (display_title, source_page, web_image_url, local_import_sha256, key))
        replace_aliases(conn, key, aliases)
        for tag in tag_values:
            add_entry_tag(conn, key, tag, "manual", None, "manual upload")
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        local_path.unlink(missing_ok=True)
        thumbnail_path.unlink(missing_ok=True)
        raise

    return fetch_entry(conn, key)
