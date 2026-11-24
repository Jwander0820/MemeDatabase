from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, abort, g, jsonify, request, send_file

from . import management
from .queries import query_entries, fetch_entry, metadata
from .media import media_path, resolve_media_path, configured_media_root, safe_delete_media_file, file_exists
from .db import connect, init_db
from .tagging import decide_tag_suggestion, list_tag_suggestions


ROOT = Path(__file__).resolve().parents[2]
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
    from waitress import serve
    serve(app, host=host, port=port, threads=4)


def create_app(
    *,
    db_path: str | Path = "data/anime_memes.sqlite3",
    attachment_dir: str | Path = "data/attachments",
    thumbnail_dir: str | Path = "data/thumbnails",
    release_root: str | Path = "data/releases",
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
    app.config["RELEASE_ROOT"] = str(release_root)

    with closing(connect(db_path)) as conn:
        init_db(conn)

    @app.before_request
    def before_request() -> None:
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            check_mutation_origin()
        conn = sqlite3.connect(app.config["DB_PATH"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn

    @app.teardown_request
    def teardown_request(_exc: BaseException | None) -> None:
        conn = getattr(g, "db", None)
        if conn is not None:
            conn.close()

    @app.errorhandler(KeyError)
    def missing_entry(exc):
        return jsonify({"error": "not found"}), 404

    @app.errorhandler(ValueError)
    def invalid_input(exc):
        return jsonify({"error": str(exc)}), 400

    @app.get("/")
    def index():
        return send_file(template_dir / "index.html")

    @app.get("/health/live")
    def live():
        return jsonify(status="ok")

    @app.get("/health/ready")
    def ready():
        g.db.execute("SELECT key FROM meme_entries LIMIT 1")
        return jsonify(status="ok")

    @app.route("/api/entries/<key>/publication", methods=["GET", "PATCH"])
    def publication(key):
        from .publishing import selection, update_selection
        try:
            result = selection(g.db, key) if request.method == "GET" else update_selection(g.db, key, request.get_json())
        except KeyError:
            abort(404)
        return jsonify(result)

    @app.post("/api/publication/check")
    def publication_check():
        from .publishing import check_database
        return jsonify(check_database(app.config["DB_PATH"]))

    @app.post("/api/publication/export")
    def publication_export():
        from .publishing import export_release
        if request.get_data() and request.get_json(silent=True) != {}:
            raise ValueError("匯出不接受路徑或其他參數")
        result = export_release(app.config["DB_PATH"], app.config["RELEASE_ROOT"])
        return jsonify(result), 200 if result["exported"] else 422

    @app.post("/api/publication/activate")
    def publication_activate():
        from .releases import activate
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {"version"}:
            raise ValueError("啟用只接受 version")
        try:
            manifest = activate(Path(app.config["RELEASE_ROOT"]), data["version"])
        except (OSError, sqlite3.Error, KeyError, TypeError, AttributeError):
            return jsonify(error="無法驗證或寫入發布包，請確認檔案完整及目錄權限"), 422
        return jsonify(version=manifest["version"], count=manifest["count"])

    @app.get("/api/meta")
    def api_meta():
        return jsonify(metadata(g.db))

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
        item = serialize_entry(row, include_ocr=True)
        item["tag_suggestions"] = list_tag_suggestions(g.db, key, status="pending")
        return jsonify(item)

    @app.post("/api/entries/manual")
    def api_create_manual_entry():
        row = management.create_manual_entry(g.db, request.form, request.files.get("image"),
            configured_media_root(app, "ATTACHMENT_DIR"), configured_media_root(app, "THUMBNAIL_DIR"))
        return jsonify(serialize_entry(row, include_ocr=True)), 201

    @app.errorhandler(413)
    def upload_too_large(_exc):
        return jsonify({"error": "圖片超過 50 MB 上限"}), 413

    @app.patch("/api/entries/<key>")
    def api_update_entry(key: str):
        row = management.update_entry(g.db, key, request.get_json(silent=True) or {})
        return jsonify(serialize_entry(row, include_ocr=True))

    @app.put("/api/entries/<key>/tags")
    def api_update_tags(key: str):
        row = management.update_tags(g.db, key, request.get_json(silent=True) or {})
        return jsonify(serialize_entry(row, include_ocr=True))

    @app.get("/api/entries/<key>/tag-suggestions")
    def api_tag_suggestions(key: str):
        if fetch_entry(g.db, key) is None:
            abort(404)
        status = (request.args.get("status") or "pending").strip()
        try:
            suggestions = list_tag_suggestions(g.db, key, status=status)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"items": suggestions})

    @app.post("/api/tag-suggestions/<int:suggestion_id>/decision")
    def api_decide_tag_suggestion(suggestion_id: int):
        data = request.get_json(silent=True) or {}
        try:
            suggestion = decide_tag_suggestion(
                g.db,
                suggestion_id,
                decision=str(data.get("decision") or ""),
                reviewed_by=str(data.get("reviewed_by") or "local_user"),
            )
        except LookupError:
            abort(404)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify(suggestion)

    @app.post("/api/entries/<key>/favorite")
    def api_favorite(key: str):
        return jsonify(management.set_favorite(g.db, key, True))

    @app.delete("/api/entries/<key>/favorite")
    def api_unfavorite(key: str):
        return jsonify(management.set_favorite(g.db, key, False))

    @app.delete("/api/entries/<key>")
    def api_delete_entry(key: str):
        return jsonify(management.delete_entry(g.db, key,
            configured_media_root(app, "ATTACHMENT_DIR"), configured_media_root(app, "THUMBNAIL_DIR")))

    @app.get("/media/attachments/<attachment_id>")
    def media_attachment(attachment_id: str):
        path = media_path(g.db, attachment_id, "local_path")
        if path is None:
            abort(404)
        return media_response(path)

    @app.get("/media/thumbnails/<attachment_id>")
    def media_thumbnail(attachment_id: str):
        path = media_path(g.db, attachment_id, "thumbnail_path")
        if path is None:
            abort(404)
        return media_response(path)

    return app


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
