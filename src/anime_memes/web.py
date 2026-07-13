from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, abort, g, jsonify, render_template, request, send_file
from PIL import Image, UnidentifiedImageError
from werkzeug.utils import secure_filename

from .db import init_db
from .tags import add_entry_tag, split_tags


ROOT = Path(__file__).resolve().parents[2]
MANUAL_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_MANUAL_UPLOAD_BYTES = 50 * 1024 * 1024


def run_web_app(
    *,
    db_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    attachment_dir: str | Path = "data/attachments",
    thumbnail_dir: str | Path = "data/thumbnails",
) -> None:
    app = create_app(
        db_path=db_path,
        attachment_dir=attachment_dir,
        thumbnail_dir=thumbnail_dir,
    )
    app.run(host=host, port=port, debug=False)


def create_app(
    *,
    db_path: str | Path = "data/anime_memes.sqlite3",
    attachment_dir: str | Path = "data/attachments",
    thumbnail_dir: str | Path = "data/thumbnails",
) -> Flask:
    template_dir = Path(__file__).with_name("web_templates")
    static_dir = Path(__file__).with_name("web_static")
    app = Flask(
        __name__,
        template_folder=str(template_dir),
        static_folder=str(static_dir),
        static_url_path="/static",
    )
    app.config["DB_PATH"] = str(db_path)
    app.config["ATTACHMENT_DIR"] = str(attachment_dir)
    app.config["THUMBNAIL_DIR"] = str(thumbnail_dir)
    app.config["MAX_CONTENT_LENGTH"] = MAX_MANUAL_UPLOAD_BYTES

    @app.before_request
    def before_request() -> None:
        conn = sqlite3.connect(app.config["DB_PATH"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        init_db(conn)
        g.db = conn

    @app.teardown_request
    def teardown_request(_exc: BaseException | None) -> None:
        conn = getattr(g, "db", None)
        if conn is not None:
            conn.close()

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/meta")
    def api_meta():
        tags = [
            dict(row)
            for row in g.db.execute(
                """
                SELECT tag_name AS name, COUNT(*) AS count
                FROM entry_tags
                GROUP BY tag_name
                ORDER BY count DESC, tag_name
                """
            )
        ]
        statuses = [
            dict(row)
            for row in g.db.execute(
                "SELECT status, COUNT(*) AS count FROM meme_entries GROUP BY status ORDER BY status"
            )
        ]
        return jsonify({"tags": tags, "statuses": statuses})

    @app.get("/api/entries")
    def api_entries():
        page = max(int(request.args.get("page", "1") or "1"), 1)
        page_size = min(max(int(request.args.get("page_size", "48") or "48"), 1), 120)
        entries, total = query_entries(g.db, request.args, page, page_size)
        return jsonify(
            {
                "items": [serialize_entry(row) for row in entries],
                "page": page,
                "page_size": page_size,
                "total": total,
            }
        )

    @app.get("/api/entries/<key>")
    def api_entry(key: str):
        row = fetch_entry(g.db, key)
        if row is None:
            abort(404)
        return jsonify(serialize_entry(row, include_ocr=True))

    @app.post("/api/entries/manual")
    def api_create_manual_entry():
        upload = request.files.get("image")
        if upload is None or not upload.filename:
            return jsonify({"error": "請選擇圖片檔案"}), 400

        original_name = secure_filename(upload.filename) or "manual-image"
        suffix = Path(original_name).suffix.lower()
        if suffix not in MANUAL_IMAGE_SUFFIXES:
            return jsonify({"error": "僅支援 JPG、PNG、WebP 或 GIF 圖片"}), 400

        status = (request.form.get("status") or "ready").strip()
        if status not in {"ready", "needs_review", "ignored"}:
            return jsonify({"error": "狀態格式不正確"}), 400

        token = uuid.uuid4().hex
        message_id = f"manual_message_{token}"
        attachment_id = f"manual_{token}"
        key = f"meme_{attachment_id}"
        attachment_root = configured_media_root(app, "ATTACHMENT_DIR")
        thumbnail_root = configured_media_root(app, "THUMBNAIL_DIR")
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
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            local_path.unlink(missing_ok=True)
            thumbnail_path.unlink(missing_ok=True)
            return jsonify({"error": f"無法讀取圖片：{exc}"}), 400

        anime_title = (request.form.get("anime_title") or "").strip()
        speaker = (request.form.get("speaker") or "").strip()
        quote_text = request.form.get("quote_text") or ""
        notes = (request.form.get("notes") or "").strip()
        tag_values = dedupe(split_tags(request.form.get("tags") or ""))
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        content = "\n".join(value for value in (anime_title, speaker, quote_text) if value)

        try:
            g.db.execute(
                """
                INSERT INTO discord_messages(
                    id, type, timestamp, content, author_id, author_name, author_is_bot
                ) VALUES(?, 'Manual', ?, ?, 'manual-ui', 'Manual UI', 0)
                """,
                (message_id, timestamp, content),
            )
            g.db.execute(
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
                    str(local_path),
                    str(thumbnail_path) if thumbnail_path.exists() else None,
                    image_width,
                    image_height,
                    timestamp,
                ),
            )
            g.db.execute(
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
            for tag in tag_values:
                add_entry_tag(g.db, key, tag, "manual", None, "manual upload")
            g.db.commit()
        except sqlite3.Error:
            g.db.rollback()
            local_path.unlink(missing_ok=True)
            thumbnail_path.unlink(missing_ok=True)
            raise

        return jsonify(serialize_entry(fetch_entry_or_404(g.db, key), include_ocr=True)), 201

    @app.errorhandler(413)
    def upload_too_large(_exc):
        return jsonify({"error": "圖片超過 50 MB 上限"}), 413

    @app.patch("/api/entries/<key>")
    def api_update_entry(key: str):
        data = request.get_json(silent=True) or {}
        fields = {
            "anime_title": data.get("anime_title"),
            "speaker": data.get("speaker"),
            "quote_text": data.get("quote_text"),
            "notes": data.get("notes"),
            "status": data.get("status"),
        }
        status = fields["status"]
        if status not in {None, "", "ready", "ignored", "needs_review"}:
            return jsonify({"error": "invalid status"}), 400
        updates: list[str] = []
        params: list[Any] = []
        for column, value in fields.items():
            if value is None:
                continue
            updates.append(f"{column} = ?")
            params.append(str(value).strip() if column != "quote_text" else str(value))
        if not updates:
            return jsonify({"error": "no fields to update"}), 400
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(key)
        cursor = g.db.execute(f"UPDATE meme_entries SET {', '.join(updates)} WHERE key = ?", params)
        if cursor.rowcount == 0:
            abort(404)
        g.db.commit()
        return jsonify(serialize_entry(fetch_entry_or_404(g.db, key), include_ocr=True))

    @app.put("/api/entries/<key>/tags")
    def api_update_tags(key: str):
        if fetch_entry(g.db, key) is None:
            abort(404)
        data = request.get_json(silent=True) or {}
        tags = data.get("tags")
        if isinstance(tags, str):
            tag_values = split_tags(tags)
        elif isinstance(tags, list):
            tag_values = [str(item).strip() for item in tags if str(item).strip()]
        else:
            return jsonify({"error": "tags must be a string or list"}), 400
        g.db.execute("DELETE FROM entry_tags WHERE entry_key = ? AND source = 'manual'", (key,))
        for tag in dedupe(tag_values):
            add_entry_tag(g.db, key, tag, "manual", None, "web editor")
        g.db.execute("UPDATE meme_entries SET updated_at = CURRENT_TIMESTAMP WHERE key = ?", (key,))
        g.db.commit()
        return jsonify(serialize_entry(fetch_entry_or_404(g.db, key), include_ocr=True))

    @app.post("/api/entries/<key>/favorite")
    def api_favorite(key: str):
        if fetch_entry(g.db, key) is None:
            abort(404)
        g.db.execute("INSERT OR IGNORE INTO entry_favorites(entry_key) VALUES(?)", (key,))
        g.db.commit()
        return jsonify({"key": key, "favorite": True})

    @app.delete("/api/entries/<key>/favorite")
    def api_unfavorite(key: str):
        g.db.execute("DELETE FROM entry_favorites WHERE entry_key = ?", (key,))
        g.db.commit()
        return jsonify({"key": key, "favorite": False})

    @app.delete("/api/entries/<key>")
    def api_delete_entry(key: str):
        row = fetch_entry(g.db, key)
        if row is None:
            abort(404)

        attachment_id = row["attachment_id"]
        attachment = g.db.execute(
            "SELECT id, local_path, thumbnail_path FROM attachments WHERE id = ?",
            (attachment_id,),
        ).fetchone()

        g.db.execute("DELETE FROM entry_favorites WHERE entry_key = ?", (key,))
        g.db.execute("DELETE FROM entry_tags WHERE entry_key = ?", (key,))
        g.db.execute("DELETE FROM review_items WHERE entry_key = ?", (key,))
        g.db.execute("DELETE FROM meme_entries WHERE key = ?", (key,))
        remaining = int(
            g.db.execute(
                "SELECT COUNT(*) FROM meme_entries WHERE attachment_id = ?",
                (attachment_id,),
            ).fetchone()[0]
        )

        removed_attachment = False
        files_to_delete: list[tuple[Path | None, Path]] = []
        if attachment is not None and remaining == 0:
            removed_attachment = True
            g.db.execute("DELETE FROM review_items WHERE attachment_id = ?", (attachment_id,))
            g.db.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
            files_to_delete = [
                (resolve_media_path(attachment["local_path"]), configured_media_root(app, "ATTACHMENT_DIR")),
                (resolve_media_path(attachment["thumbnail_path"]), configured_media_root(app, "THUMBNAIL_DIR")),
            ]

        g.db.commit()

        deleted_files: list[str] = []
        file_errors: list[dict[str, str]] = []
        for path, allowed_root in files_to_delete:
            result = safe_delete_media_file(path, allowed_root)
            if result.deleted_path:
                deleted_files.append(result.deleted_path)
            if result.error:
                file_errors.append({"path": result.path or "", "error": result.error})

        return jsonify(
            {
                "key": key,
                "attachment_id": attachment_id,
                "removed_attachment": removed_attachment,
                "deleted_files": deleted_files,
                "file_errors": file_errors,
            }
        )

    @app.get("/media/attachments/<attachment_id>")
    def media_attachment(attachment_id: str):
        path = media_path(g.db, attachment_id, "local_path")
        if path is None:
            abort(404)
        return send_file(path)

    @app.get("/media/thumbnails/<attachment_id>")
    def media_thumbnail(attachment_id: str):
        path = media_path(g.db, attachment_id, "thumbnail_path")
        if path is None:
            abort(404)
        return send_file(path)

    return app


def query_entries(conn: sqlite3.Connection, args: Any, page: int, page_size: int) -> tuple[list[sqlite3.Row], int]:
    where = ["1=1"]
    params: list[Any] = []
    status = (args.get("status") or "ready").strip()
    if status and status != "all":
        where.append("e.status = ?")
        params.append(status)
    q = (args.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        where.append(
            """
            (e.anime_title LIKE ?
             OR e.speaker LIKE ?
             OR e.quote_text LIKE ?
             OR e.ocr_text LIKE ?)
            """
        )
        params.extend([like, like, like, like])
    tag = (args.get("tag") or "").strip()
    if tag:
        where.append("EXISTS (SELECT 1 FROM entry_tags tx WHERE tx.entry_key = e.key AND tx.tag_name = ?)")
        params.append(tag)
    if truthy(args.get("favorite")):
        where.append("f.entry_key IS NOT NULL")
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

    sort = args.get("sort") or "updated"
    order_by = {
        "random": "RANDOM()",
        "source_old": "m.timestamp ASC, e.key",
        "source_new": "m.timestamp DESC, e.key",
        "title": "e.anime_title COLLATE NOCASE, e.key",
    }.get(sort, "e.updated_at DESC, m.timestamp DESC, e.key")
    offset = (page - 1) * page_size
    rows = conn.execute(
        f"""
        SELECT
            e.*,
            a.file_name,
            a.local_path,
            a.thumbnail_path,
            a.discord_url,
            a.download_status,
            a.image_width,
            a.image_height,
            m.timestamp AS source_timestamp,
            f.entry_key IS NOT NULL AS favorite,
            GROUP_CONCAT(t.tag_name, ',') AS tags
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
        """
        SELECT
            e.*,
            a.file_name,
            a.local_path,
            a.thumbnail_path,
            a.discord_url,
            a.download_status,
            a.image_width,
            a.image_height,
            m.timestamp AS source_timestamp,
            f.entry_key IS NOT NULL AS favorite,
            GROUP_CONCAT(t.tag_name, ',') AS tags
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


def fetch_entry_or_404(conn: sqlite3.Connection, key: str) -> sqlite3.Row:
    row = fetch_entry(conn, key)
    if row is None:
        abort(404)
    return row


def serialize_entry(row: sqlite3.Row, *, include_ocr: bool = False) -> dict[str, Any]:
    item = dict(row)
    tags = [tag for tag in (item.get("tags") or "").split(",") if tag]
    thumbnail_path = resolve_media_path(item.get("thumbnail_path"))
    local_path = resolve_media_path(item.get("local_path"))
    suffix = Path(item.get("file_name") or "").suffix.lower()
    browser_image = suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ""}
    item["tags"] = tags
    item["favorite"] = bool(item.get("favorite"))
    item["thumbnail_url"] = f"/media/thumbnails/{item['attachment_id']}" if file_exists(thumbnail_path) else None
    item["attachment_url"] = f"/media/attachments/{item['attachment_id']}" if file_exists(local_path) else item.get("discord_url")
    item["image_url"] = item["attachment_url"] if browser_image else None
    item["has_local_image"] = file_exists(local_path)
    if not include_ocr:
        item.pop("ocr_text", None)
    return item


def media_path(conn: sqlite3.Connection, attachment_id: str, column: str) -> Path | None:
    if column not in {"local_path", "thumbnail_path"}:
        return None
    row = conn.execute(f"SELECT {column} FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
    if not row or not row[column]:
        return None
    path = resolve_media_path(row[column])
    if path is None:
        return None
    if not path.exists() or path.stat().st_size <= 0:
        return None
    return path


def resolve_media_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path


def configured_media_root(app: Flask, key: str) -> Path:
    path = Path(str(app.config[key]))
    if not path.is_absolute():
        path = ROOT / path
    return path


class DeleteFileResult:
    def __init__(self, *, path: str | None = None, deleted_path: str | None = None, error: str | None = None) -> None:
        self.path = path
        self.deleted_path = deleted_path
        self.error = error


def safe_delete_media_file(path: Path | None, allowed_root: Path) -> DeleteFileResult:
    if path is None:
        return DeleteFileResult()
    try:
        resolved = path.resolve()
        root = allowed_root.resolve()
        if not is_relative_to(resolved, root):
            return DeleteFileResult(path=str(resolved), error="path outside media root")
        if not resolved.exists():
            return DeleteFileResult(path=str(resolved))
        if not resolved.is_file():
            return DeleteFileResult(path=str(resolved), error="not a file")
        resolved.unlink()
        return DeleteFileResult(path=str(resolved), deleted_path=str(resolved))
    except OSError as exc:
        return DeleteFileResult(path=str(path), error=str(exc))


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def file_exists(path: Path | None) -> bool:
    return path is not None and path.exists() and path.stat().st_size > 0


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
