"""公開唯讀 WSGI app；沒有管理、OCR 或模板 import。"""
from __future__ import annotations

from pathlib import Path
import sqlite3

from flask import Flask, abort, g, jsonify, request

from . import settings
from .public_data import open_readonly, search, serialize, validate_schema
from .releases import ReleaseMissing, current_version, release_path, version_name


def create_public_app(release_root=None):
    root = Path(release_root or settings.release_root()).resolve()
    app = Flask(__name__, static_folder=None)

    @app.before_request
    def open_version():
        if request.path == "/health/live":
            return
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            abort(405)
        if request.path not in {"/health/ready", "/api/v1/meta", "/api/v1/entries"} and not request.path.startswith("/api/v1/entries/"):
            abort(404)
        requested = request.args.get("version")
        g.version = version_name(requested) if requested is not None else current_version(root)
        folder = release_path(root, g.version)
        g.db = open_readonly(folder / "public.sqlite3")
        try:
            validate_schema(g.db, g.version)
        except ValueError as exc:
            raise RuntimeError("公開資料結構不相容") from exc
        g.db.execute("BEGIN")

    @app.teardown_request
    def close_version(_exc):
        if getattr(g, "db", None) is not None:
            g.db.close()

    @app.after_request
    def headers(response):
        # 發布版本是不可變快照：指定 version 的成功回應永遠不變，可讓瀏覽器與 CDN 長期快取，
        # 減少回源到 VPS；未指定版本的請求（用來取得目前版本）與錯誤回應一律不快取。
        if request.args.get("version") and response.status_code == 200 and request.path.startswith("/api/v1/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.errorhandler(ReleaseMissing)
    def missing(exc):
        return jsonify(error={"code": "version_expired" if request.args.get("version") else "not_ready", "message": str(exc)}), 410 if request.args.get("version") else 503

    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error={"code": "invalid_request", "message": str(exc)}), 400

    @app.errorhandler(sqlite3.Error)
    @app.errorhandler(OSError)
    @app.errorhandler(RuntimeError)
    def unavailable(exc):
        app.logger.error("公開資料讀取失敗：%s", type(exc).__name__)
        return jsonify(error={"code": "not_ready", "message": "公開資料暫時無法使用"}), 503

    @app.errorhandler(404)
    @app.errorhandler(405)
    def http_error(exc):
        return jsonify(error={"code": "not_found" if exc.code == 404 else "method_not_allowed", "message": exc.name}), exc.code

    @app.get("/health/live")
    def live():
        return jsonify(status="ok")

    @app.get("/health/ready")
    def ready():
        return jsonify(status="ok", version=g.version)

    @app.get("/api/v1/entries")
    def entries():
        return jsonify(version=g.version, **search(g.db, request.args))

    @app.get("/api/v1/entries/<entry_id>")
    def entry(entry_id):
        if set(request.args) - {"version"}:
            raise ValueError("不支援的查詢參數")
        row = g.db.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            abort(404)
        return jsonify(version=g.version, item=serialize(row))

    @app.get("/api/v1/meta")
    def meta():
        if set(request.args) - {"version"}:
            raise ValueError("不支援的查詢參數")
        tags = [dict(r) for r in g.db.execute("SELECT tag AS name,count(*) AS count FROM entry_tags GROUP BY tag ORDER BY count DESC,tag")]
        return jsonify(version=g.version, tags=tags)

    return app
