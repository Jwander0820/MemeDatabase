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
from werkzeug.exceptions import HTTPException

from . import settings
from .db import connect, init_db
from .web_admin import admin_bp
from .web_browse import browse_bp
from .web_common import check_admin_host, check_mutation_origin, error_response, http_error_response
from .web_publication import publication_bp

__all__ = ["ROOT", "MAX_MANUAL_UPLOAD_BYTES", "run_web_app", "create_app"]

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
        return error_response("not_found", "not found", 404)

    @app.errorhandler(ValueError)
    def invalid_input(exc):
        return error_response("invalid_request", str(exc), 400)

    @app.errorhandler(413)
    def upload_too_large(_exc):
        return error_response("payload_too_large", "圖片超過 50 MB 上限", 413)

    @app.errorhandler(HTTPException)
    def http_error(exc):
        # 只有 /api/ 路徑改回 JSON；頁面與媒體路由維持 Flask 預設行為。
        if request.path.startswith("/api/"):
            return http_error_response(exc)
        return exc

    @app.errorhandler(500)
    def internal_error(_exc):
        # 測試／除錯模式下 Flask 會直接拋出例外而不會進到這裡（PROPAGATE_EXCEPTIONS）；
        # 未預期例外已由 Flask 以 app.logger 記錄堆疊，回應不含例外內容。
        return error_response("internal_error", "伺服器發生錯誤，請稍後再試", 500)

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
