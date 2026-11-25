"""私人管理 Flask 入口：只負責組裝 app、全域設定與錯誤處理，路由分散在各 Blueprint 模組。

- web_publication：發布相關 API
- web_admin：管理 API 與媒體路由
- web_browse：本機瀏覽（與公開 API 共用參數驗證與錯誤格式）
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from flask import Flask, g, jsonify, request, send_file

from . import settings
from .db import connect, init_db
from .web_admin import admin_bp
from .web_browse import LOCAL_BROWSE_VERSION, browse_bp, browse_item
from .web_common import check_admin_host, check_mutation_origin, dedupe, fetch_entry_or_404, media_response, serialize_entry, truthy
from .web_publication import publication_bp

__all__ = [
    "ROOT", "MAX_MANUAL_UPLOAD_BYTES", "LOCAL_BROWSE_VERSION", "run_web_app", "create_app",
    "check_mutation_origin", "media_response", "fetch_entry_or_404", "serialize_entry",
    "truthy", "dedupe", "browse_item",
]

ROOT = settings.ROOT
MAX_MANUAL_UPLOAD_BYTES = 50 * 1024 * 1024


def run_web_app(
    *,
    db_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 3638,
    attachment_dir: str | Path = settings.DEFAULT_ATTACHMENT_DIR,
    thumbnail_dir: str | Path = settings.DEFAULT_THUMBNAIL_DIR,
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
    db_path: str | Path = settings.DEFAULT_DB,
    attachment_dir: str | Path = settings.DEFAULT_ATTACHMENT_DIR,
    thumbnail_dir: str | Path = settings.DEFAULT_THUMBNAIL_DIR,
    release_root: str | Path = settings.DEFAULT_RELEASE_ROOT,
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
        check_admin_host()
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

    @app.errorhandler(413)
    def upload_too_large(_exc):
        return jsonify({"error": "圖片超過 50 MB 上限"}), 413

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

    app.register_blueprint(publication_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(browse_bp)
    return app
